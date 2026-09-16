# Quality & Testing Guidelines

> Testing standards, code hygiene, and cross-platform verification for `invoice_agent`.

---

## Testing Framework & Execution

- **Test Framework**: `pytest`
- **Execution Command**:
  ```bash
  PYTHONPATH=. pytest
  ```
  All tests must pass 100% prior to any git commit. Never weaken or delete tests to bypass failures.

---

## Test Isolation & Fixtures

### 1. File Isolation with `tmp_path`
All file-system interactions (scanned invoices, created workbooks, temporary output dirs) must execute within pytest's built-in `tmp_path` fixture. Never write test outputs to the working tree.

```python
def test_export_records(tmp_path: Path):
    source_file = tmp_path / "sample.pdf"
    source_file.write_bytes(b"%PDF-sample")
    output_dir = tmp_path / "out"
    ...
```

### 2. External Service Mocking with `monkeypatch`
Unit tests must **never** make live network requests to external OCR services (PaddleOCR cloud API) or LLM endpoints:
- Mock OCR providers using lightweight test doubles or `monkeypatch.setattr`.
- Provide mock parsed documents with realistic text and bounding box structures to test extraction logic.

---

## Cross-Platform Compatibility Rules

`invoice_agent` runs on Windows, macOS, and Linux:

### 1. Avoid Global `os.name` Mocking
In Python 3.11+, patching `os.name = "nt"` globally breaks `pathlib.Path` on Unix systems (`NotImplementedError: cannot instantiate 'WindowsPath' on your system`).
- Instead, isolate platform-dependent checks behind dedicated helper functions (e.g. `pdf_export.is_windows()`).
- Patch the helper function locally:
  ```python
  monkeypatch.setattr(pdf_export, "is_windows", lambda: True)
  ```

### 2. Path Handling
- Always use `pathlib.Path` instead of string concatenation with `"/"` or `"\\"`.
- Always call `path.resolve()` when doing relative-to comparisons or boundary checks.
- When generating output filenames, run `pipeline.sanitize_filename()` to remove characters illegal in Windows paths (`\ / : * ? " < > |`).

### 3. Text Line Endings
- All repository files must use standard Unix `LF` line terminators.
- Configure git `core.autocrlf` or `.gitattributes` to prevent unintentional CRLF file conversions that bloat diffs.

---

## Financial Accuracy & Money Rules

1. **Exact Representation**: Store monetary sums as `Decimal` or formatted 2-decimal strings (`"0.00"`).
2. **Never Compare Floats**: Do not use `float` arithmetic for invoice totals or meal allowances to avoid IEEE 754 precision issues (e.g. `0.1 + 0.2 != 0.3`).
3. **Round Half Up**: When rounding is necessary, use `ROUND_HALF_UP` from Python's standard `decimal` module.

---

## Pre-Commit Quality Gate Checklist

Before committing any change:
- [ ] Run `PYTHONPATH=. pytest` and ensure 0 failures.
- [ ] Ensure `.gitignore` is intact and no sensitive tokens (`invoice_agent_config.json`) or private user invoices are staged.
- [ ] Verify that no debugging `print()` statements remain in library code.
- [ ] Verify that new document types or extractor rules have corresponding test coverage in `tests/test_invoice_agent.py`.
