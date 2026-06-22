# 公司格式报销单同步导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有导出流程中同步生成公司格式 Excel，并在 Windows + Microsoft Excel 环境生成合并 PDF。

**Architecture:** 保留现有清单生成逻辑，新增独立的公司报销数据转换、模板渲染和 PDF 导出模块。公司工作簿使用项目内置的清理模板，通过模板复制与受控工作表编辑生成；各导出产物独立记录状态，支持部分成功。

**Tech Stack:** Python 3.11、openpyxl（模板资源准备与工作簿生成/校验）、Microsoft Excel COM（PowerShell 自动化导出 PDF）、pytest。

---

## 文件结构

- Create: `invoice_agent/company_reimbursement.py` — 纯数据转换、中文大写、模板填充和分页。
- Create: `invoice_agent/pdf_export.py` — Windows Excel 检测和 PDF 导出。
- Create: `invoice_agent/templates/company_reimbursement_template.xlsx` — 清理后的正式模板。
- Modify: `invoice_agent/models.py` — 导出产物结果模型。
- Modify: `invoice_agent/pipeline.py` — 公共导出入口及部分成功处理。
- Modify: `invoice_agent/web.py` — 单包、批量导出响应字段。
- Modify: `invoice_agent/static/app.js` — 多产物与部分成功展示。
- Modify: `invoice_agent/cli.py` — CLI 输出全部产物。
- Modify: `tests/test_invoice_agent.py` — 单元、分页、集成和错误场景。
- Modify: `README.md` — 新输出文件和 PDF 平台限制。

### Task 1: 导出数据纯函数

**Files:**
- Create: `invoice_agent/company_reimbursement.py`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

新增测试覆盖：

```python
def test_chinese_currency_uppercase():
    assert amount_to_chinese_upper(Decimal("6157.11")) == "人民币陆仟壹佰伍拾柒元壹角壹分"
    assert amount_to_chinese_upper(Decimal("100.00")) == "人民币壹佰元整"

def test_build_company_data_filters_itineraries_and_maps_categories(tmp_path):
    # 有效高铁、网约车、住宿、材料、行程单和餐补
    data = build_company_reimbursement_data(records, export_date=date(2026, 6, 22))
    assert len(data.travel_rows) == 2
    assert data.lodging_total == Decimal("2380.00")
    assert data.daily_rows[0].content == "购买灭火器材料"
    assert data.meal_allowance.count == 0
```

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py -k "chinese_currency_uppercase or build_company_data" -q`

Expected: FAIL，模块或函数不存在。

- [ ] **Step 3: 最小实现**

实现不可变数据类：

```python
@dataclass(frozen=True)
class CompanyLine:
    date_text: str
    person: str
    location: str
    purpose: str
    amount: Decimal
    count: int

@dataclass(frozen=True)
class CompanyReimbursementData:
    project_name: str
    traveler: str
    department: str
    export_date: date
    travel_rows: list[CompanyLine]
    daily_rows: list[CompanyLine]
    detail_sections: dict[str, list[CompanyLine]]
    meal_allowance: CompanyLine
    lodging_total: Decimal
    total: Decimal
    warnings: list[str]
```

实现 `amount_to_chinese_upper()`、`company_content()`、`build_company_reimbursement_data()`，全部金额使用 `Decimal`。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 2: 模板资源

**Files:**
- Create: `invoice_agent/templates/company_reimbursement_template.xlsx`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

```python
def test_company_template_has_clean_required_sheets():
    path = company_template_path()
    workbook = load_workbook(path, data_only=False, read_only=True)
    assert workbook.sheetnames == ["报销明细表", "差旅费报销单", "日常费用报销单"]
    assert workbook["差旅费报销单"].page_setup.paperSize == "11"
    assert workbook["差旅费报销单"].page_setup.orientation == "landscape"
```

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py::test_company_template_has_clean_required_sheets -q`

Expected: FAIL，模板不存在。

- [ ] **Step 3: 创建模板**

从用户参考文件复制所需三页，清除明细、签名和打印区域外数据；保留样式、合并单元格、行列尺寸及打印设置。日常模板只保留一页。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 3: 公司 Excel 渲染与分页

**Files:**
- Modify: `invoice_agent/company_reimbursement.py`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

新增：

```python
def test_company_workbook_omits_empty_daily_sheet(tmp_path):
    write_company_workbook(tmp_path / "company.xlsx", travel_only_data)
    wb = load_workbook(tmp_path / "company.xlsx", data_only=False)
    assert "日常费用报销单" not in wb.sheetnames

@pytest.mark.parametrize(("count", "expected"), [(1, 1), (8, 1), (9, 2), (17, 3)])
def test_daily_expense_pagination(tmp_path, count, expected):
    ...

@pytest.mark.parametrize(("count", "expected"), [(12, 1), (13, 2)])
def test_travel_pagination(tmp_path, count, expected):
    ...

def test_detail_section_expands_without_losing_total(tmp_path):
    ...
```

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py -k "company_workbook or pagination or detail_section_expands" -q`

Expected: FAIL，渲染函数不存在。

- [ ] **Step 3: 最小实现**

实现：

```python
def write_company_workbook(path: Path, data: CompanyReimbursementData, template_path: Path | None = None) -> None:
    ...
```

按 8 条复制日常页，按 12 条复制差旅页；无日常数据删除模板页。报销明细按分类写入，容量不足时插行并更新公式、打印区域。所有工作表设置 `fitToWidth=1`。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 4: PDF 能力

**Files:**
- Create: `invoice_agent/pdf_export.py`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

```python
def test_pdf_export_skips_when_excel_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_export, "find_excel_executable", lambda: None)
    result = export_company_pdf(tmp_path / "a.xlsx", tmp_path / "a.pdf")
    assert result.status == "skipped"

def test_pdf_export_reports_subprocess_failure(monkeypatch, tmp_path):
    ...
```

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py -k "pdf_export" -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 最小实现**

实现 `find_excel_executable()` 和 `export_company_pdf()`。通过临时 PowerShell 脚本创建 Excel COM 对象并调用 `ExportAsFixedFormat(0, pdf_path)`；设置不可见、关闭提示，并在 `finally` 关闭工作簿和 Excel。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 5: 公共导出集成

**Files:**
- Modify: `invoice_agent/models.py`
- Modify: `invoice_agent/pipeline.py`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

```python
def test_export_records_generates_company_excel_and_preserves_summary(tmp_path, monkeypatch):
    result = export_records(tmp_path, records, write_excel=True)
    assert (tmp_path / "00_报销清单.xlsx").exists()
    assert (tmp_path / "01_公司报销单.xlsx").exists()
    assert result.status in {"success", "partial_success"}

def test_export_records_keeps_summary_when_company_export_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(..., side_effect=ValueError("template broken"))
    result = export_records(...)
    assert (tmp_path / "00_报销清单.xlsx").exists()
    assert result.status == "partial_success"
```

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py -k "export_records_generates_company or keeps_summary" -q`

Expected: FAIL，返回值或文件不存在。

- [ ] **Step 3: 最小实现**

在 `models.py` 增加 `ExportArtifact`、`ExportResult`。让 `write_result_files()`/`export_records()` 返回结果，依次生成清单、公司 Excel 和 PDF；捕获公司阶段异常并汇总状态。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 6: Web、批量与 CLI 展示

**Files:**
- Modify: `invoice_agent/web.py`
- Modify: `invoice_agent/static/app.js`
- Modify: `invoice_agent/cli.py`
- Test: `tests/test_invoice_agent.py`

- [ ] **Step 1: 写失败测试**

新增断言：

```python
assert export_result["company_excel_path"].endswith("01_公司报销单.xlsx")
assert "export_artifacts" in export_result
assert "partial_success" in js
assert "01_公司报销单.pdf" in js
```

覆盖单包、单个批量包和 export-all。

- [ ] **Step 2: 验证 RED**

Run: `$env:PYTHONPATH='.'; pytest tests/test_invoice_agent.py -k "web_flow or export_single_batch or export_all_batch or frontend" -q`

Expected: FAIL，新字段不存在。

- [ ] **Step 3: 最小实现**

将 `ExportResult` 转为公开字典，保留 `excel_path` 兼容字段，增加公司文件路径、状态、提醒和产物列表；前端显示多个文件和部分成功提示；CLI逐项打印产物。

- [ ] **Step 4: 验证 GREEN**

Run 同 Step 2，Expected: PASS。

### Task 7: 文档、完整验证与真实灰度

**Files:**
- Modify: `README.md`
- Modify: `.trellis/tasks/06-22-company-reimbursement-export/implement.md`

- [ ] **Step 1: 更新文档**

说明三类输出、分页规则、Windows Excel PDF 限制和部分成功语义。

- [ ] **Step 2: 运行新增功能测试**

Run: `$env:PYTHONPATH='.'; $env:PYTHONIOENCODING='utf-8'; pytest tests/test_invoice_agent.py -k "company or pdf_export or export_records" -q`

Expected: 全部 PASS。

- [ ] **Step 3: 运行完整回归**

Run: `$env:PYTHONPATH='.'; $env:PYTHONIOENCODING='utf-8'; pytest -q`

Expected: 新增测试全部通过；实施前已知的 `test_web_batch_task_publishes_package_preview_before_all_packages_finish` 可能仍因 1 秒时序门限失败，必须单独报告，不能归因于本功能。

- [ ] **Step 4: 真实入口灰度**

使用用户提供的 `00_报销清单.xlsx` 对应记录或真实 Web 导出流程生成：

- `00_报销清单.xlsx`
- `01_公司报销单.xlsx`
- `01_公司报销单.pdf`

检查公司 Excel 总额、Sheet 页数、A4/A5 设置、签字栏和 PDF 页数。

- [ ] **Step 5: 提交实现**

仅暂存本任务文件并提交，提交信息：`feat: 同步导出公司格式报销单`
