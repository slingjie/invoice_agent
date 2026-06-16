# OCR Provider Guidelines

> Guidelines for implementing and using OCR providers in this project.

---

## Provider Architecture

OCR providers implement two methods: `parse(path)` for single-file and `parse_many(paths, max_workers)` for batch processing. Both return `List[ParsedDocument]`.

```python
class OcrProvider:
    def parse(self, path: Path) -> ParsedDocument: ...
    def parse_many(self, paths: List[Path], max_workers: int = 3) -> List[ParsedDocument]: ...
```

## Current Providers

| Provider | Source | Status | Notes |
|----------|--------|--------|-------|
| `SdkOcrProvider` | `invoice_agent/ocr.py:396` | Active (default) | Async-based, `max_workers=3` |
| `PaddleAsyncOcrProvider` | `invoice_agent/ocr.py` | Deprecated | Fallback only |
| `PaddleOcrProvider` | `invoice_agent/ocr.py` | Deprecated | Synchronous HTTP |

---

## `SdkOcrProvider` Usage

```python
provider = SdkOcrProvider(
    access_token=config.paddleocr_access_token,
    timeout_seconds=120,
    request_timeout_seconds=60,
)
results = provider.parse_many(pdf_paths, max_workers=3)
```

### Concurrency Limits

**max_workers=3 is the sweet spot for the PaddleOCR API.** Higher values (5+) trigger HTTP 429 rate limiting. This is a server-side limit, not a client-side one.

---

## Gotcha: `asyncio` Import Scope

**Problem**: When using `asyncio.run()` in a class method, the coroutine method (`_run_async`) runs in a separate function scope. If `asyncio` is imported inside the caller method only, the coroutine cannot access it.

```python
# WRONG — asyncio imported in caller scope only
class SdkOcrProvider:
    def parse_many(self, paths, max_workers):
        try:
            import asyncio  # ← local to parse_many
            return asyncio.run(self._run_async(paths, max_workers))  # ✅ asyncio accessible here
        except:
            ...

    async def _run_async(self, paths, max_workers):
        sem = asyncio.Semaphore(max_workers)  # ❌ NameError: asyncio not defined
        ...
```

`_run_async` is a separate method, not a nested function. It cannot access `parse_many`'s local variables. The `asyncio` name must be available at **module scope** or imported inside `_run_async` itself.

```python
# CORRECT — import asyncio at module top level
import asyncio

class SdkOcrProvider:
    def parse_many(self, paths, max_workers):
        return asyncio.run(self._run_async(paths, max_workers))

    async def _run_async(self, paths, max_workers):
        sem = asyncio.Semaphore(max_workers)  # ✅ resolves from module scope
```

---

## Error Handling

| Condition | Behavior | Error Code |
|-----------|----------|------------|
| Missing access token | Returns error document for each path | `CONFIG_ERROR` |
| SDK HTTP error (timeout, 4xx, 5xx) | Logs warning, returns error document | `SDK_ERROR` |
| SDK parse exception | Logs error, returns error document for all paths | `SDK_ERROR` |
| Rate limit (429) | Caught per-file, returns error document for that file | `SDK_ERROR` |

All errors produce `ParsedDocument(ok=False)` — the pipeline continues processing other files and generates output from whatever succeeded.

---

## Performance

Benchmark with 18 mixed invoice PDFs (PaddleOCR-VL-1.6 API):

| Concurrency | Time | Notes |
|-------------|------|-------|
| `max_workers=3` | ~53s | Optimal — no rate limiting |
| `max_workers=5` | ~7s (but 12/18 failed) | HTTP 429 rate limited |
| Previous (manual HTTP) | ~144s (16 invoices) | 3× slower |

> **Key insight**: SDK's async + exponential backoff polling is faster than manual HTTP even at the same concurrency level. The 3× speedup comes from `httpx` connection reuse, `asyncio.gather` parallelism, and optimized polling intervals.
