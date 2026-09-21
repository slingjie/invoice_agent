# 技术设计：本地解析失败防护与诊断

## 目标状态

为每个 `ExpenseRecord.raw_result` 维护脱敏的解析轨迹：`local_fast_path`、`paddle_ocr`、`mineru_fallback` 或 `failed`，以及稳定的失败/回退原因代码。该轨迹经现有 `ExpenseRecord.to_json()` 自动进入预览、`raw_results.json` 和 Web 任务快照。

## 数据流

1. 本地快速探针成功且完整：标记 `local_fast_path`。
2. 探针无结果或不完整：记录本地失败原因，调用 PaddleOCR。
3. Paddle 成功：标记 `paddle_ocr`，并在 `risk_note` 附加非阻塞提示“已由云端 OCR 兜底”。
4. Paddle 失败且 MinerU 成功：标记 `mineru_fallback`，保留已有兜底提示。
5. 全部失败：保留安全的错误代码、用户可读摘要，状态为 `无法识别`；不得记录令牌、完整请求体或敏感响应。

Web 文件行复用已有 `message` 字段显示风险提示；预览明细复用 `risk_note`。新增汇总只统计各解析路径数量及失败原因代码，不改变金额、重复票或导出确认逻辑。

## 错误与重试

- `无法识别` 保持现有单条/批量重试入口。
- 兜底成功不自动重试、不阻塞；用户仍可在预览中看到来源。
- 重试时应刷新解析轨迹和提示，避免旧失败原因残留。

## 兼容性

- 不修改 PaddleOCR、MinerU 的请求配置或密钥结构。
- 旧结果没有解析轨迹时，展示为“来源未记录”，不影响历史任务读取。
- 不新增外部服务、数据库或遥测。
