# Error Handling Guidelines

> Exception handling, retry policies, and graceful degradation in `invoice_agent`.

---

## Core Philosophy

Processing invoices involves handling corrupted files, unreadable scans, network hiccups, and third-party rate limits. The system must **never terminate the entire batch processing job due to a single bad file or transient API error**. Instead:
1. Favor structured result objects with status flags over unhandled exceptions.
2. Automatically retry transient network and API quota errors with backoff and jitter.
3. Fall back gracefully to secondary parsers (e.g. MinerU for PDF files).
4. Flag missing fields as `待人工确认` rather than rejecting them outright.

---

## Pattern 1: Result Objects for Failures

Rather than throwing exceptions during batch document processing, parsing functions return typed result containers that encapsulate success or error:

### Document Parsing (`models.ParsedDocument`)
```python
# In invoice_agent/ocr.py
def _error_document(path: Path, error: Dict[str, Any], raw_result: Optional[Dict[str, Any]] = None) -> ParsedDocument:
    return ParsedDocument(
        path=path,
        text="",
        raw_result=raw_result or {},
        fields=extract_fields_from_text("", path),
        ok=False,
        error=error,
    )
```

### PDF Generation (`pdf_export.PdfExportResult`)
```python
# In invoice_agent/pdf_export.py
@dataclass(frozen=True)
class PdfExportResult:
    status: str          # "success" | "skipped" | "failed"
    path: Path
    message: str = ""
```
If Microsoft Excel or Windows COM automation is unavailable, export is marked as `skipped` with an informative message, allowing the Excel export to complete successfully.

---

## Pattern 2: Transient Network & Rate-Limit Retries

External OCR and LLM calls must withstand transient failures:

### Identifying Retryable Errors (`ocr.SdkOcrProvider._is_retryable_exception`)
The following conditions are treated as transient and retried up to `_RETRY_MAX` times (default 3):
- HTTP 429 (Rate Limit / Quota Exceeded)
- Network timeouts (`TimeoutError`, `asyncio.TimeoutError`)
- Connection resets and aborted connections (`ConnectionResetError`, `ConnectionAbortedError`)
- Client connector and DNS errors (`ClientConnectorError`, `ClientConnectorDNSError`)
- SSL handshake interruptions and incomplete payloads

### Backoff and Jitter
Always apply exponential delay multiplied by random jitter to avoid thundering herd problems:
```python
delay = self._RETRY_BASE_DELAY * (2 ** attempt)
delay *= random.uniform(0.5, 1.5)
await asyncio.sleep(delay)
```

---

## Pattern 3: Layered Fallback Providers

When the primary OCR provider fails on a document, secondary engines can attempt recovery:

### MinerU PDF Fallback (`invoice_agent/web.py:_maybe_parse_with_mineru`)
- If `SdkOcrProvider` fails on a `.pdf` file and `enable_mineru_fallback` is enabled in config, invoke `MinerUFallbackProvider`.
- If MinerU succeeds, the record is accepted and annotated in `risk_note`: `"PaddleOCR失败后使用MinerU兜底解析"`.
- If both fail, combine their diagnostic error messages:
  `"PaddleOCR失败：{paddle_msg}；MinerU兜底失败：{mineru_msg}"`.

---

## Pattern 4: Human-in-the-Loop Confirmation Flags

Missing non-fatal fields must not discard an invoice:
- If a document is recognized as an invoice but lacks an invoice number, date, or total amount:
  - Set `record.recognition_status = "待人工确认"`.
  - Set `record.risk_note = "缺少发票号码/日期/金额"`.
- If a document cannot be parsed at all:
  - Set `record.recognition_status = "无法识别"`.
  - Record the underlying error message in `record.risk_note`.

Users can then manually correct or trigger single/batch re-recognition via Web endpoints:
- `POST /tasks/{id}/records/{sequence}/retry`
- `POST /tasks/{id}/retry-failed`

---

## Pattern 5: Web API Exception Responses

All HTTP handler entry points in `InvoiceAgentHandler` (`invoice_agent/web.py`) must catch domain and validation exceptions, returning standardized JSON error payloads:

```python
try:
    result = update_task_record(task_id, sequence, form)
    self._send_json(result)
except ValueError as exc:
    self._send_json({"error": str(exc)}, status=400)
except Exception as exc:
    logger.exception("Unexpected error processing request")
    self._send_json({"error": "Internal server error: " + str(exc)}, status=500)
```
