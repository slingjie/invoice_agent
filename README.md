# 差旅发票智能整理与报销单自动化 Agent

面向企业差旅报销场景的智能发票处理流水线。通过扫描指定差旅发票文件夹，调用 **PaddleOCR 文档解析 API** 深度识别发票与行程单内容，智能核验行程闭环与发票抬头合规性，按公司财务规范批量重命名发票，并自动生成结构化 Excel 清单、公司报销单 Excel、打印就绪的 PDF，以及 **1:1 像素级复刻标杆的 A5 可视化可编辑打印工单（HTML）**。

---

## 核心特性

- 🧾 **全票种智能识别**：深度支持增值税专票/普票/电票、高铁电子客票、航空行程单、网约车电子发票及行程单、客运汽车票、公路通行费发票等。
- 🔍 **四重财务合规性稽核**：
  - **发票抬头核验**：严格校验购买方抬头是否为公司标准抬头（如“杭州勤合能源科技有限公司”），非客运实名票出现抬头不符主动告警。
  - **行程闭环诊断**：智能识别首末段城际大交通，检查常驻城市往返闭环与中间交通连续性。
  - **市内交通超标预警**：自动汇总每日打车金额，超出建议日限额（默认 100 元/天）自动提示。
  - **网约车智能防重**：自动识别同一行程的行程单与电子发票，防止重复报销入账。
- 🌟 **1:1 像素级 A5 可编辑交互工单 (`html_report.py`)**：
  - **物理纸张锁死**：差旅费报销单与日常报销单锁定 **A5 横向（210mm × 148mm）**，明细表锁定 **A4 纵向**，打印绝不分页、绝不挤压。
  - **全量小数无损展示**：严格遵守财务规范，发票本身含多少位小数即原样展示多少位，**严禁四舍五入**。
  - **防遮挡排版设计**：彻底清除 `overflow:hidden` 截断，优化金额列宽，末尾小数清晰饱满。
  - **就地微调与实时重算**：支持鼠标点击单元格直接修改文字/金额，修改后自动毫秒级重算合计与**标准财务人民币大写（角分厘毫全量映射）**。
- 📊 **五大交付物一键闭环**：
  1. `01_公司报销单_A5可编辑打印台.html`：所见即所得交互工单，支持直接打印或微调；
  2. `01_公司报销单.xlsx`：公司财务标准格式报销单；
  3. `01_公司报销单.pdf`：打印就绪版 PDF 单据；
  4. `00_报销清单.xlsx`：全量发票结构化明细大表；
  5. `01_已识别_重命名/`：按财务规范重命名的发票归档库。

---

## 快速上手

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置文件

复制配置样例文件：

```bash
cp invoice_agent_config.example.json invoice_agent_config.json
```

编辑 `invoice_agent_config.json`：

```json
{
  "paddleocr_doc_parsing_api_url": "https://your-api-endpoint.com/layout-parsing",
  "paddleocr_access_token": "your-access-token",
  "city_transport_daily_limit": "100",
  "lodging_daily_limit": "",
  "llm_base_url": "",
  "llm_model": "",
  "llm_api_key_env": "INVOICE_AGENT_LLM_API_KEY"
}
```

也可以通过环境变量提供：

```bash
export PADDLEOCR_ACCESS_TOKEN="your-token"
```

---

## 命令行 CLI 使用

### 1. 预览分析模式（只读预览，零破坏）

```bash
python -m invoice_agent organize ./测试发票/0714-0716福州六和 \
  --config ./invoice_agent_config.json \
  --traveler "石凌杰" \
  --department "项目部" \
  --project-name "福州六和" \
  --trip-start-date "2026-07-14" \
  --trip-end-date "2026-07-16" \
  --daily-meal-allowance 50
```

### 2. 正式落盘生成模式 (`--apply`)

加上 `--apply` 参数后，系统将正式复制重命名发票，并生成全套 Excel、PDF 与 A5 交互式 HTML：

```bash
python -m invoice_agent organize ./测试发票/0714-0716福州六和 \
  --config ./invoice_agent_config.json \
  --traveler "石凌杰" \
  --department "项目部" \
  --project-name "福州六和" \
  --trip-start-date "2026-07-14" \
  --trip-end-date "2026-07-16" \
  --daily-meal-allowance 50 \
  --apply
```

### 3. 启动 Web UI 界面

```bash
python -m invoice_agent ui --port 8000
```

---

## 全局 Agent Skill (`invoice-reimbursement`)

本项目已完整固化为 AI Agent 专属技能，安装在 `~/.agents/skills/invoice-reimbursement/`（已通过 TeamAI 同步）。

在任何 Agent 会话中，无需手动敲命令行，直接使用自然语言即可唤醒：

* “帮我把桌面 `6月合肥出差` 文件夹里的发票报销一下，项目是合肥电站。”
* “整理一下这个发票文件夹：`D:/发票暂存`”

Agent 会自动遵循两阶段 SOP（阶段一：静默识别、审计诊断与起止日期智能推算；阶段二：用户确认后正式落盘并交付全套成果）。

---

## 开源协议

MIT License
