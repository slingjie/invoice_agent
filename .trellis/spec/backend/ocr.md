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

**max_workers=3 is the sweet spot for the PaddleOCR API.** Higher values (5+) trigger HTTP 429 rate limiting. This is a server-side per-token RPM (requests per minute) limit, not a client-side one.

A global request rate limiter (`_MIN_SUBMIT_INTERVAL=2.0`) prevents burst submissions that exceed the server's RPM window. Rate-limited requests are retried with exponential backoff + jitter, recovering most otherwise-failed files.

### Rate Limiting + Retry Strategy

| Layer | Mechanism | Effect |
|-------|-----------|--------|
| Concurrency | `asyncio.Semaphore(max_workers=3)` | At most 3 concurrent parse jobs |
| Rate limiting | Global `asyncio.Lock` with 2s interval | Caps sustained rate at ~30 RPM |
| Retry | 6 attempts, exponential backoff + jitter | 2s→4s→8s→16s→30s→30s, randomly spread |
| Retry scope | `RateLimitError` + `APIError(429)` only | Non-retryable errors fail immediately |

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
| Rate limit (429) | Auto-retry up to 3 times with exponential backoff (1s→2s→4s), returns error document only if all retries exhausted | `SDK_ERROR` |

All errors produce `ParsedDocument(ok=False)` — the pipeline continues processing other files and generates output from whatever succeeded.

---

## Performance

Benchmark with 18 mixed invoice PDFs (PaddleOCR-VL-1.6 API):

| Concurrency + Protection | Time | Success | Notes |
|--------------------------|------|---------|-------|
| `max_workers=3` + rate limit 2s + 6 retries | ~18s | **18/18 (100%)** | Best — eliminates all 429 |
| `max_workers=3` + 5 retries (2s→...→30s) | ~22s | 15/18 (83%) | Retry alone insufficient |
| `max_workers=3` + 3 retries (1s→2s→4s) | ~53s | 15/18 (83%) | Original retry, no rate limit |
| `max_workers=5` | ~7s | 6/18 (33%) | HTTP 429 rate limited heavily |
| Previous (manual HTTP) | ~144s (16 invoices) | — | 3× slower, no retry |

> **Key insight**: Rate limiting + retry with jitter is the winning combination at `max_workers=3`. The global submit interval prevents the initial burst from exceeding the RPM quota, while jittered retries give the server time to replenish and naturally stagger re-attempts.
