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

### Local Fast-Path Completeness Gate

数字 PDF 可以先由本地文本层探针解析，但只有结果满足票种的最小关键字段要求时才可跳过 OCR。
例如，高铁发票必须同时具备有效金额、发票号码、乘车日期、起点和终点；普通发票必须具备有效金额和发票号码。缺失非致命字段（如购买方名称）不应丢弃票据，但缺失关键字段必须交由既有 OCR provider 继续解析。

这样既避免把不完整的本地结果静默输出，也保持扫描件、复杂 PDF 和未知版式的云端兜底能力。相关回归测试应覆盖真实样票的精确字段，以及本地结果不完整时 provider 被调用的路径。

### Parse Trace and Safe Fallback Notice

每个解析结果应在 `raw_result` 中记录 `parse_source`、`parse_reason_code` 与 `parse_trace`，来源仅限本地快速解析、云端 OCR、MinerU 兜底或失败。该轨迹用于预览诊断、JSON 导出和失败重试；重试成功后必须用新的轨迹替换旧失败信息。

本地失败而云端 OCR 成功时，向 `risk_note` 增加“已由云端 OCR 兜底”的非阻塞提示，不得影响金额汇总或导出。对外显示的错误摘要必须先脱敏访问令牌、API key、Bearer 凭据和 URL 查询参数，并截断异常长的服务响应。

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
