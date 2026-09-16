# Directory Structure

> How backend code is organized in the `invoice_agent` project.

---

## Overview

`invoice_agent` is a Python-based intelligent invoice and expense report organization system. The project follows a modular, pipeline-oriented design that separates CLI/Web interfaces, document scanning, OCR parsing, rule-based field extraction, expense categorization, and multi-format reporting (Excel and PDF).

---

## Directory Layout

```
invoice_agent/
├── __init__.py                # Package root
├── __main__.py                # Entry point for python -m invoice_agent
├── cli.py                     # CLI argument parsing and execution dispatch
├── web.py                     # Embedded ThreadingHTTPServer and REST API handlers
├── static/                    # Frontend UI assets (HTML, CSS, JS) served by web.py
│   ├── app.js                 # Interactive review table, preview, retry, and cancellation
│   └── app.css                # Clean, responsive styles for invoice workspace
├── config.py                  # AgentConfig dataclass, JSON loading, environment fallbacks
├── models.py                  # Typed data models (@dataclass: ExpenseRecord, TripInfo, etc.)
├── scanner.py                 # File discovery, hash generation (SHA-256), boundary checking
├── ocr.py                     # OCR providers (PaddleOCR SDK async/threaded, retry wrappers)
├── mineru_fallback.py         # Secondary PDF parsing provider using MinerU CLI
├── extractor.py               # Document type detection, regex-based field parsing
├── analysis.py                # Expense category mapping and meal allowance calculations
├── excel.py                   # Excel workbook generation (summary & overview via openpyxl)
├── company_reimbursement.py   # Specialized company reimbursement Excel form styling & sums
├── pdf_export.py              # Windows COM Excel-to-PDF export via PowerShell
├── cancellation.py            # Thread-safe cooperative task cancellation control
└── trip_audit.py              # Trip policy compliance audit & optional LLM verification

tests/
├── test_invoice_agent.py      # Comprehensive pytest test suite covering full pipeline
```

---

## Module Responsibilities

### 1. Presentation & Interfaces (`cli.py`, `web.py`, `static/`)
- **`cli.py`**: Uses Python's standard `argparse` to handle CLI arguments (`--folder`, `--out`, `--mode`, etc.) and delegates directly to `pipeline.py` or launches the web UI.
- **`web.py`**: Runs a lightweight, zero-dependency `ThreadingHTTPServer`. It handles job polling, record updates, retry requests, and export triggers, storing job state in memory under `TASKS` guarded by `TASK_LOCK`.
- **`static/`**: Client-side single-page UI assets served directly without external web frameworks.

### 2. Pipeline & Workflow (`pipeline.py`, `cancellation.py`)
- Coordinates the end-to-end flow: scan → OCR parse → extract fields → assign categories → generate output artifacts.
- Supports cooperative cancellation through `CancellationControl`.

### 3. Ingestion & Document Parsing (`scanner.py`, `ocr.py`, `mineru_fallback.py`, `extractor.py`)
- **`scanner.py`**: Identifies supported file types (`.pdf`, `.jpg`, `.jpeg`, `.png`), computes file hashes, and prevents directory traversal into output folders.
- **`ocr.py`**: Encapsulates external OCR engines (`SdkOcrProvider`, `PaddleOcrProvider`) with exponential backoff for transient errors and rate limits.
- **`mineru_fallback.py`**: Provides secondary fallback for PDF invoices when PaddleOCR fails.
- **`extractor.py`**: Pure functions taking OCR text and path to extract structured fields (dates, invoice numbers, amounts, routes, parties).

### 4. Domain Logic & Classification (`models.py`, `analysis.py`, `trip_audit.py`)
- **`models.py`**: Central data schemas using `@dataclass`. `ExpenseRecord` is the primary domain object passed through the entire workflow.
- **`analysis.py`**: Business categorization rules (e.g., distinguishing high-level categories like "交通费" and fine-grained categories like "行程交通费", "市区交通费").
- **`trip_audit.py`**: Verifies business constraints such as travel date boundaries and meal allowance calculations.

### 5. Output Generation (`excel.py`, `company_reimbursement.py`, `pdf_export.py`)
- **`excel.py`**: Creates standard `00_报销清单.xlsx` with summary sheets and detailed listings using `openpyxl`.
- **`company_reimbursement.py`**: Generates enterprise-specific reimbursement sheets (`01_公司报销单.xlsx`), preserving formatting, borders, and Excel formula sums.
- **`pdf_export.py`**: Windows-specific COM automation via PowerShell to export finalized Excel sheets to PDF.

---

## Naming & Organization Rules

- **Module Naming**: Snake_case lowercase filenames (e.g., `company_reimbursement.py`, `mineru_fallback.py`).
- **Function Naming**: Action verbs for functions (e.g., `scan_documents()`, `assign_reimbursement_category()`, `export_records()`).
- **Internal Helpers**: Prefix private module helpers with a leading underscore (e.g., `_format_sheet()`, `_record_from_parsed()`).
- **Stateless Modules**: Parsing, extraction, and categorization functions must remain pure and free of global state.
- **Thread Safety**: Any mutable shared state (such as `TASKS` in `web.py`) must be guarded by explicit synchronization primitives (`threading.RLock()`).
