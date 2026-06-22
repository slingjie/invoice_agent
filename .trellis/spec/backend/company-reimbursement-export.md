# 公司报销单导出规范

## 1. 适用范围 / 触发条件

当修改以下任一内容时，必须遵守本规范：

- `invoice_agent/company_reimbursement.py` 中的分类、金额、分页或模板填充。
- `invoice_agent/pipeline.py` 中的导出产物和部分成功逻辑。
- Web、CLI 或批量模式中的导出响应字段。
- `invoice_agent/templates/company_reimbursement_template.xlsx` 模板。
- Windows Microsoft Excel 转 PDF 流程。

统一导出必须生成原始清单，并尝试生成公司 Excel 和 PDF：

```text
00_报销清单.xlsx
01_公司报销单.xlsx
01_公司报销单.pdf
```

## 2. 函数与数据签名

### 公司数据转换

```python
def build_company_reimbursement_data(
    records: Iterable[ExpenseRecord],
    export_date: date | None = None,
) -> CompanyReimbursementData:
    ...
```

### 公司 Excel

```python
def write_company_workbook(
    path: Path,
    data: CompanyReimbursementData,
    template_path: Path | None = None,
) -> None:
    ...
```

### PDF

```python
def export_company_pdf(
    xlsx_path: Path,
    pdf_path: Path,
    timeout_seconds: int = 90,
) -> PdfExportResult:
    ...
```

### 公共导出

```python
def export_records(
    output_dir: Path,
    records: list[ExpenseRecord],
    apply: bool = False,
    write_excel: bool = True,
    trip_audit=None,
) -> ExportResult:
    ...
```

### 导出结果

```python
@dataclass
class ExportArtifact:
    kind: str       # summary_excel | company_excel | company_pdf
    path: Path
    status: str     # success | skipped | failed
    message: str = ""

@dataclass
class ExportResult:
    status: str     # success | partial_success | failed
    artifacts: list[ExportArtifact]
    warnings: list[str]
```

## 3. 输入、输出与环境契约

### 有效记录

- 只有 `include_in_amount=True` 的票据计入金额。
- `document_type == "行程单"` 不计金额、不增加单据张数。
- 自动餐补计入金额，单据张数固定为 `0`。
- 全部金额使用 `Decimal` 计算。

### 工作表契约

| 工作表 | 纸张 | 方向 | 分页 |
|---|---|---|---|
| 报销明细表 | A4 | 纵向 | 分类行不足时动态扩展 |
| 差旅费报销单 | A5 | 横向 | 每页最多 12 条交通明细 |
| 日常费用报销单 | A5 | 横向 | 每页最多 8 条；无数据不生成 |

### 差旅单总额契约

每张 `差旅费报销单` 的总额只统计该 Sheet 表达的差旅费用，不得使用全部报销总额：

```python
sheet_total = (
    page_transport_total
    + meal_allowance
    + lodging_total
    + toll_total
    + fuel_total
    + refund_total
)
```

- 第一页包含餐补、住宿、过路、油费和退改费。
- 后续分页只统计该页交通明细。
- 材料费、招待费、办公费、礼品礼卡和其他日常费用不得进入差旅单总额。
- 中文大写金额必须由同一个 `sheet_total` 生成。

### 输出目录

相对输出目录基于 Web 服务进程的启动工作目录解析：

```text
./整理结果预览
```

从 worktree 启动时会写入 worktree；从主项目启动时会写入主项目。用户需要固定位置时，前端应填写绝对路径。

### PDF 环境

- 仅 Windows 且安装 Microsoft Excel 时自动生成 PDF。
- 不引入 LibreOffice。
- PDF 转换失败不得删除已生成的 Excel。

## 4. 校验与错误矩阵

| 条件 | 结果 |
|---|---|
| `00_报销清单.xlsx` 生成失败 | 整体失败，停止后续导出 |
| 公司模板缺失、损坏或关键区域不可写 | 清单保留，公司 Excel 标记失败，整体为 `partial_success` |
| 公司 Excel 成功、Excel 未安装 | PDF 标记 `skipped`，整体仍可为 `success`，返回提醒 |
| PDF COM 转换失败或超时 | 两份 Excel 保留，PDF 标记失败，整体为 `partial_success` |
| 项目名、人员、部门为空或为 `1/2/3` | 不阻断导出，在 `warnings` 中提醒 |
| 日常费用为空 | 删除日常费用模板 Sheet，不打印空页 |
| 差旅交通超过 12 条 | 复制差旅 Sheet 分页，不丢记录 |
| 日常费用超过 8 条 | 复制日常 Sheet 分页，不丢记录 |

## 5. Good / Base / Bad 案例

### Good

- 交通 `232`、住宿 `800`、餐补 `100`、材料 `300`。
- 公司总报销金额为 `1432`。
- 差旅单总额为 `1132`。
- 日常费用单总额为 `300`。

### Base

- 只有高铁 `232`，没有日常费用。
- 输出报销明细表和差旅费报销单。
- 不生成日常费用报销单。
- 差旅单总额为 `232`。

### Bad

- 将 `CompanyReimbursementData.total` 直接写入差旅单 `K19`。
- 结果会把材料、招待等费用错误计入差旅单。
- PDF 与中文大写金额也会同步错误。

## 6. 必需测试及断言点

### 数据转换

- 行程单和重复票不计金额、不计张数。
- 餐补金额正确且张数为 `0`。
- 内容优先级为人工备注、说明、销方、凭证类型。

### 分页

- 日常费用 `0/1/8/9/17` 条对应 `0/1/1/2/3` 个 Sheet。
- 差旅交通 `12/13` 条对应 `1/2` 个 Sheet。

### 总额

```python
assert data.total == Decimal("1432.00")
assert travel_sheet["K19"].value == 1132.0
assert travel_sheet["C19"].value == "人民币壹仟壹佰叁拾贰元整"
```

### 导出集成

- 清单、公司 Excel、PDF 的 artifact 状态分别可见。
- 公司 Excel 失败时清单仍存在，结果为 `partial_success`。
- Web 单包和批量 package 保留 `excel_path`，并增加：
  - `company_excel_path`
  - `company_pdf_path`
  - `export_status`
  - `export_warnings`
  - `export_artifacts`

### 真实入口灰度

- 使用真实 `raw_results.json` 重新生成，不需要重复 OCR。
- 核对 Excel 工作表数量、A4/A5 页面和 PDF 页数。
- 核对差旅单 `K19 == G18 + J18 + L18`。

## 7. Wrong vs Correct

### Wrong：使用全部报销总额

```python
sheet["K19"] = float(data.total)
sheet["C19"] = amount_to_chinese_upper(data.total)
```

这会把日常费用错误计入差旅单。

### Correct：使用差旅 Sheet 自身总额

```python
sheet_total = (
    page_total
    + data.meal_allowance.amount
    + data.lodging_total
    + data.toll_total
    + data.fuel_total
    + data.refund_total
)
sheet["K19"] = float(sheet_total)
sheet["C19"] = amount_to_chinese_upper(sheet_total)
```

### Wrong：用相对路径但假设写入主项目

```text
输出目录：./整理结果预览
```

### Correct：需要固定目录时使用绝对路径

```text
D:\Desktop\飞牛同步\2026-05-10invoice agent\整理结果预览
```
