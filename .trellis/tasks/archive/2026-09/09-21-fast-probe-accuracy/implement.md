# 实施计划：本地 PDF 快速探针准确性

1. 阅读 backend 数据模型、错误处理与测试规范，并确认当前快速探针与流水线成功路径。
2. 在 `fast_probe.py` 使用 PyMuPDF 视觉排序；修正金额候选和铁路购买方清洗，保持 API 不变。
3. 在 `pipeline.py` 增加小而明确的本地结果完整性判定，失败时复用现有 OCR 回退。
4. 在 `tests/test_fast_probe.py` 增加两张真实样票的精确字段断言；在流水线测试中覆盖一次不完整快速结果的 OCR 回退。
5. 运行：
   `PYTHONPATH=. /opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest -q tests/test_fast_probe.py tests/test_invoice_agent.py`
6. 运行 `git diff --check`；不修复本任务外已有格式问题，仅报告它们。

## 风险点

- 金额不得使用 `float` 比较，以免破坏财务精度。
- 不得覆盖或回滚工作区中其他未提交改动。
- 真实 PDF 样票路径受 `.gitignore` 影响，测试应在缺失样票时给出明确跳过或错误说明，而不是伪造夹具。
