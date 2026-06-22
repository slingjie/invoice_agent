# 出差报销包整理 Agent

本工具递归扫描一次出差文件夹中的发票和行程单，使用 PaddleOCR 文档解析 API 识别内容，生成 Excel 报销清单、原始识别结果和重命名预览。默认不修改原文件；确认后可用 `--apply` 复制并重命名到输出目录。

## PaddleOCR 配置

推荐使用本地配置文件，不需要配置系统环境变量。复制示例文件：

```bash
cp invoice_agent_config.example.json invoice_agent_config.json
```

然后手动编辑 `invoice_agent_config.json`：

```json
{
  "paddleocr_job_url": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
  "paddleocr_model": "PaddleOCR-VL-1.6",
  "paddleocr_access_token": "replace-with-your-token",
  "city_transport_daily_limit": "100",
  "lodging_daily_limit": "",
  "llm_base_url": "",
  "llm_model": "",
  "llm_api_key_env": "INVOICE_AGENT_LLM_API_KEY"
}
```

> **注意：** 本项目仅支持 PaddleOCR 异步 Job API（V1.6 模型）。旧版 Layout API 已不再支持。

也可以继续使用环境变量：

```bash
export PADDLEOCR_ACCESS_TOKEN="your-token"
```

## trip_info.json

```json
{
  "project_name": "上海出差",
  "traveler": "张三",
  "department": "技术部",
  "trip_start_date": "2026-03-01",
  "trip_end_date": "2026-03-03",
  "daily_meal_allowance": "50"
}
```

`project_name` 可省略，省略时使用文件夹名。`daily_meal_allowance` 为单日餐补金额，可省略，默认 `50`。出差餐补按包含式天数计算，例如 `2026-01-01` 至 `2026-01-05` 按 5 天；餐补不需要发票，因此在"类别汇总"中"出差餐补"的张数固定为 `0`。其他字段必须提供，也可以通过 CLI 参数覆盖。

## 报销汇总规范

- **报销清单**使用高维分类（招待费 / 差旅费 / 交通费 / 材料费 / 其他），支持手动编辑。
- **类别汇总**保留细粒度分类（高铁发票 / 网约车发票 / 出租车票 / 通行费发票 / 住宿发票 / 餐饮发票 / 普通发票 / 行程单 / 其他）。
- 高维分类自动映射规则：
  - 差旅费：高铁、住宿、行程单（含票面金额的）
  - 交通费：网约车、出租车、通行费
  - 招待费：餐饮、餐费相关
  - 材料费：材料、配件、办公用品相关
  - 其他：无法归类的票据
- 出差餐补金额 = `(出差结束日期 - 出差开始日期 + 1) * 单日餐补金额`。
- 单日餐补金额默认 `50`，可通过 `trip_info.json`、CLI 参数或 Web UI 调整。
- 出差餐补不需要发票，"类别汇总"中"出差餐补"的张数固定为 `0`。
- Web UI 的"图文核对"支持人工校对识别字段；点击"保存修改"后，预览、`raw_results.json` 和最终导出的 Excel 都以保存后的数据为准。

## 差旅行程校对

系统会在识别后生成"行程校对"结果，用于辅助发现遗漏票据或超标风险。校对不会阻止导出。

- 出差开始/结束日期是否有对应城际交通票。
- 最早城际交通的起点会自动作为出发城市，末段终点应回到该城市。
- 住宿按过夜数校对，例如 `2026-03-01` 至 `2026-03-05` 预计 `4` 晚。
- 市内交通每日默认标准为 `100` 元，可在 Web UI 或配置文件调整。
- 住宿每晚上限默认留空；不填时不判断住宿超标。
- 可勾选"启用大模型行程复核"。大模型只接收结构化票据信息和规则证据，不发送原始 OCR 全文或文件内容；API 不可用时保留本地规则校对结果。

OpenAI 兼容模型配置示例：

```bash
export INVOICE_AGENT_LLM_API_KEY="your-api-key"
```

```json
{
  "llm_base_url": "https://api.example.com/v1",
  "llm_model": "compatible-model",
  "llm_api_key_env": "INVOICE_AGENT_LLM_API_KEY"
}
```

## 预览模式

```bash
python -m invoice_agent organize ./测试发票 \
  --config ./invoice_agent_config.json \
  --trip-info ./trip_info.json \
  --out-dir ./整理结果预览 \
  --max-workers 3 \
  --timeout-seconds 120 \
  --city-transport-daily-limit 100 \
  --lodging-daily-limit 450
```

输出：

```text
00_报销清单.xlsx
01_公司报销单.xlsx
01_公司报销单.pdf
raw_results.json
rename_plan.json
```

`01_公司报销单.xlsx` 按公司正式模板生成：报销明细表使用 A4 纵向，差旅费和日常费用报销单使用 A5 横向。日常费用每 8 条自动分页，差旅交通每 12 条自动分页；没有日常费用时不会生成空白日常费用页。

在 Windows 且安装 Microsoft Excel 时，程序会同步生成合并打印文件 `01_公司报销单.pdf`。其他环境会保留两份 Excel 并提示跳过 PDF；PDF 转换失败也不会删除已生成的 Excel。

## 批量报销包模式

默认仍是单报销包模式：系统会递归扫描一个文件夹，并合并成一次出差处理。一次整理多个出差文件夹时，可以启用批量模式，让每个一级子文件夹独立生成预览和输出：

```bash
python -m invoice_agent organize ./待整理报销 \
  --mode batch-subfolders \
  --config ./invoice_agent_config.json \
  --out-dir ./整理结果 \
  --traveler 张三 \
  --department 技术部 \
  --trip-start-date 2026-03-01 \
  --trip-end-date 2026-03-03
```

目录建议：

```text
待整理报销/
  2026-03-杭州出差/
    trip_info.json
    发票1.pdf
    酒店/
      发票2.pdf
  2026-04-南京出差/
    trip_info.json
    发票3.pdf
```

批量模式只识别根目录下的一级子文件夹。每个子文件夹内部仍会递归扫描；空子文件夹会被跳过；根目录直属票据会报错，避免被静默归入错误的出差包。输出会按子文件夹拆分到：

```text
整理结果/
  2026-03-杭州出差/
    00_报销清单.xlsx
    01_公司报销单.xlsx
    01_公司报销单.pdf
    raw_results.json
    rename_plan.json
    trip_audit.json
  2026-04-南京出差/
    00_报销清单.xlsx
    01_公司报销单.xlsx
    01_公司报销单.pdf
    raw_results.json
    rename_plan.json
    trip_audit.json
```

## 执行复制重命名

```bash
python -m invoice_agent organize ./测试发票 \
  --config ./invoice_agent_config.json \
  --trip-info ./trip_info.json \
  --out-dir ./整理结果 \
  --apply
```

原文件不会被修改。复制后的文件会按识别状态进入：

```text
01_已识别_重命名/
02_待人工确认/
03_重复疑似/
04_无法识别/
```

## 本地 Web UI

启动：

```bash
python -m invoice_agent ui
```

> **Windows 用户注意：** 请使用 `python` 而不是 `python3`，且需在项目根目录运行。

浏览器打开：

```text
http://127.0.0.1:8765
```

页面中填写：

- 发票文件夹（可点击"选择"按钮使用系统文件夹选择器）
- `invoice_agent_config.json` 路径
- 输出目录（可点击"选择"按钮使用系统文件夹选择器）
- 人员、部门、出差开始日期、出差结束日期、单日餐补金额
- 是否复制并重命名

Web UI 会先完成识别并展示四块预览：

- 报销清单
- 类别汇总
- 重复与风险
- 行程校对
- 重命名计划

确认预览无误后点击"确认导出"，才会生成 `00_报销清单.xlsx`。如果勾选"复制并重命名"，复制动作也会在确认导出时执行。

UI 提交后会创建后台任务，页面每秒刷新：

- 当前阶段
- 已用时
- 完成数量 / 总数量
- 每个文件的识别状态、类别、金额和提示
- 识别完成后的预览表格
- 确认导出后的 Excel 路径

## 识别速度

默认使用 PaddleOCR 异步 Job API（V1.6 模型）：

- 先批量提交文件
- 再统一轮询任务结果
- 默认并发提交数量为 `3`
- 默认单文件超时为 `120` 秒

如果远端服务限流或断开，可以把 Web UI 中的"并发识别数量"降到 `2`。如果文件很大，可以把"单文件超时秒数"调高。

## 跨平台支持

- **macOS**：使用原生 `osascript` 文件选择器
- **Windows**：使用 Python 内置 `tkinter` 文件选择器
- **Linux**：同 macOS，使用 `osascript`（需安装）

## 依赖安装

```bash
pip install -r requirements.txt
```

主要依赖：

- `requests` — HTTP 请求
- `openpyxl` — Excel 生成
