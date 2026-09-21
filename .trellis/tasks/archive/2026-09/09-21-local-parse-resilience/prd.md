# 完善本地解析失败防护与诊断

## Goal

降低本地 PDF 快速解析失败或误解析的风险，并让用户能及时发现失败、理解回退过程、进行安全重试或人工处理。

## Confirmed Facts

- 本地快速探针已对金额、发票号及高铁票的日期和起终点设置完整性门槛；不满足时会交给 OCR provider。
- Web 流程支持单条和批量重试；PaddleOCR 失败后可按配置调用 MinerU。
- 记录可携带 `recognition_status` 与 `risk_note`，Web 已展示单文件状态和风险信息。
- 当前没有面向用户的“本地成功 / 本地失败后云端成功 / 所有解析器失败”的结构化统计、原因代码汇总或可导出的诊断报告。

## Requirements

- 防止不完整的本地结果静默进入报销汇总。
- 在预览中清楚说明每张票使用的解析路径及失败/回退原因，且不暴露令牌等敏感信息。
- 云端兜底成功时展示“已由云端 OCR 兜底”提示，但不阻塞审核、导出或后续报销流程。
- 支持用户定位失败票据、重试，并保留可诊断的错误信息。
- 为新增的失败路径和用户可见状态建立自动化测试。

## Acceptance Criteria

- [ ] 本地结果缺少关键字段时不会计入正常本地成功结果。
- [ ] 用户可查看每张票的解析路径与失败/回退原因。
- [ ] 用户可定位并重试失败记录；连续失败的信息可被导出或复制用于排查。
- [ ] 回归测试覆盖本地失败、Paddle 成功、Paddle 失败及人工处理提示。

## Out of Scope

- 不更换 PaddleOCR 或 MinerU 服务商。
- 不保证所有未知票据可被本地规则成功解析；无法可靠处理时应安全回退或待人工确认。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
