# 公司格式报销单同步导出设计

完整规划文件：

- `.trellis/tasks/06-22-company-reimbursement-export/prd.md`
- `.trellis/tasks/06-22-company-reimbursement-export/design.md`
- `.trellis/tasks/06-22-company-reimbursement-export/implement.md`

## 决策摘要

- 保持 `00_报销清单.xlsx` 不变。
- 同步生成 `01_公司报销单.xlsx`。
- Windows 且安装 Microsoft Excel 时生成 `01_公司报销单.pdf`。
- 使用清理后的内置模板动态分页。
- 日常费用每页 8 条，差旅交通每页 12 条。
- 公司 Excel 或 PDF 失败时保留已成功文件并报告部分成功。
