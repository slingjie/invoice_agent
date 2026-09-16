# Data Models & Storage Guidelines

> Data modeling, precision rules, and file-based persistence for `invoice_agent`.

---

## Overview

`invoice_agent` operates without an external relational database or ORM. State is maintained through strongly-typed Python `@dataclass` instances, persisted configuration files (JSON), in-memory task tracking tables, and generated artifacts in standard formats (Excel `.xlsx` and PDF).

---

## Domain Data Models (`invoice_agent/models.py`)

All core domain structures are defined as standard Python dataclasses:

### 1. `ExpenseRecord`
Represents an individual processed invoice, receipt, or itinerary.
Key fields include:
- `sequence`: Integer sequence number assigned during sorting and naming.
- `source_path` & `file_hash`: File location and SHA-256 digest for deduplication.
- `document_date` & `travel_date`: Date on voucher and actual travel date (format `YYYY-MM-DD`).
- `document_type`: Inferred voucher classification (e.g., "高铁发票", "网约车发票", "餐饮发票").
- `reimbursement_category`: Fine-grained category (e.g., "行程交通费", "市区交通费", "住宿费").
- `high_level_category`: Top-level reporting category (e.g., "交通费", "差旅费", "餐饮费").
- `total_with_tax`: Amount as a string representation of exact currency numbers.
- `train_departure_time`, `refund_fee`, `change_fee`: Specialized train ticket attributes.
- `recognition_status`: "已识别" | "待人工确认" | "无法识别".
- `risk_note`: Warning descriptions (e.g., missing critical fields, duplicate invoices).
- `new_name` & `copied_path`: Standardized destination file naming and copy target.

### 2. `TripInfo`
Encapsulates user-supplied business trip metadata:
- `project_name`, `traveler`, `department`
- `trip_start_date`, `trip_end_date` (ISO `YYYY-MM-DD`)
- `daily_meal_allowance` (default: `"50"`)

### 3. `ParsedDocument`
Standard interface for OCR and extraction results:
- `path`: Source file path.
- `text`: Extracted plain text / markdown.
- `raw_result`: Raw JSON response from OCR engines.
- `fields`: Extracted key-value dictionary.
- `ok`: Boolean success indicator.
- `error`: Structured error dictionary `{"code": ..., "message": ...}`.

---

## Precision and Formatting Rules

### Financial Amounts
- Floating point numbers (`float`) must **never** be used for money accumulation or tax calculations to avoid precision errors.
- Always use `Decimal` from the standard library for math (see `parse_amount()` in `extractor.py` and `meal_allowance_total()` in `analysis.py`).
- Amounts stored in `ExpenseRecord` and JSON payloads are formatted as two-decimal strings (e.g., `"125.50"`).
- Empty or non-billable amounts are represented by empty strings `""` rather than `"0.00"`.

### Date Normalization
- All date strings must be normalized using `extractor.normalize_date(text)` into standard `YYYY-MM-DD` format.
- Partial or unparseable dates should remain empty or retain their raw string without crashing.

---

## Storage & Persistence Mechanisms

### 1. Excel Workbook Generation (`excel.py`, `company_reimbursement.py`)
- Standard list: `00_报销清单.xlsx` created with `openpyxl.Workbook`. Contains overview, categorized item sheets, and risk audit logs.
- Company form: `01_公司报销单.xlsx` populated using cell coordinates and dynamic formulas (e.g., `=SUM(G6:G15)`).
- Formatting: Cell styles, fonts, borders, and number formats (`#,##0.00`) are explicitly cloned via `copy()` to preserve visual fidelity.

### 2. File Organization & Safety (`scanner.py`, `pipeline.py`)
- File copying uses `shutil.copy2` to preserve file metadata and timestamps.
- Path boundaries are strictly checked via `scanner.is_inside()` and `scanner.is_strict_child()` to prevent infinite loops when the output directory is a subdirectory of the source folder.
- Filenames are sanitized via `pipeline.sanitize_filename()` to remove invalid characters (`/:*?"<>|`) across Windows and Unix platforms.

### 3. In-Memory Task Management (`web.py`)
- Web background tasks are stored in the module-level dictionary `TASKS: Dict[str, Dict]`.
- All writes and status transitions are synchronized with `with TASK_LOCK:` (`threading.RLock()`).
- Long-running OCR operations run outside the lock, updating task records atomically upon completion.
