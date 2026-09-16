# Logging Guidelines

> Logging conventions and best practices for `invoice_agent`.

---

## Logger Initialization

Each module should obtain its logger using standard Python idioms:

```python
import logging

logger = logging.getLogger(__name__)
```

Do not use `print()` statements for diagnostic output in library or backend code. All operational output must route through `logging`.

---

## Log Levels & Semantics

| Level | When to Use | Examples in Codebase |
|---|---|---|
| **`DEBUG`** | Fine-grained tracing, raw response dumps, internal state inspections | Raw OCR coordinates, regex intermediate token matches |
| **`INFO`** | Major pipeline milestones, configuration loading, lifecycle events | Starting folder scan, task start/finish, artifact export paths |
| **`WARNING`**| Recoverable issues, retry attempts, non-fatal skips, fallback triggers | Transient network errors triggering retry, missing Excel for PDF export |
| **`ERROR`** | Unrecoverable failures for a specific document or operation | File read permission error, invalid corrupted PDF, OCR fatal failure |
| **`CRITICAL`**| Complete failure of the application or background runner | Unhandled thread crash in `run_organize_task` |

---

## Formatting & Conventions

### 1. Use Lazy Formatting
Always pass arguments as parameters rather than using f-strings or `.format()` inside log calls. This defers string formatting until the logger determines the message level is enabled:

```python
# Good:
logger.warning(
    "SDK transient error for %s (attempt %d/%d), retrying in %.1fs: %s",
    path.name, attempt + 1, self._RETRY_MAX, delay, exc_message,
)

# Bad:
logger.warning(f"SDK transient error for {path.name}...")
```

### 2. Contextual Information
Log messages should contain enough context to identify the affected document and batch without guessing:
- Always include `path.name` or `task_id` when logging operations on files or jobs.
- For retry events, include current attempt and max attempts (e.g. `attempt %d/%d`).

### 3. Mask Sensitive Information
- API tokens (e.g. `paddleocr_access_token`, OpenAI keys) must **never** be logged in plain text.
- If tokens must be logged for debugging, display only the first 4 and last 4 characters with masking (e.g. `sk-ab...12cd`).

### 4. Web Server Logging (`web.py`)
- The embedded `InvoiceAgentHandler` inherits from `BaseHTTPRequestHandler`, which prints access logs to `sys.stderr` by default.
- Prefer overriding `log_message(self, format, *args)` to route requests to the structured `invoice_agent.web` logger or suppress spam during periodic client polling (`/tasks/<id>`).
