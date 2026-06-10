# PRD: 批量报销包模式

**版本**: 1.0  
**日期**: 2026-05-17  
**范围**: 出差报销包整理 Agent  
**开发方法**: Superpowers Brainstorming + Test-Driven Development  
**需求质量评分**: 94/100  
**状态**: 待评审

## 1. 背景

当前系统对输入目录使用递归扫描：一个根目录下所有支持的票据文件都会合并成同一个报销包处理。这个逻辑适合“一个出差目录内按类型建子目录”的用法，例如：

```text
上海出差/
  高铁/
  酒店/
  打车/
```

但它不适合“一个总目录下放多次出差或多个人报销”的用法，例如：

```text
待整理报销/
  杭州出差/
  南京出差/
  张三-上海出差/
```

在后一种场景里，继续合并处理会导致出差日期、餐补、住宿晚数、行程闭环、重复票判断和最终 Excel 输出混在一起，风险较高。

## 2. 目标

新增“批量报销包模式”：当用户选择一个根目录时，系统将根目录下每个一级子文件夹视为一个独立报销包/一次出差，分别识别、预览、校对、导出。

核心原则：

- 不改变现有默认行为。默认仍是“单报销包模式”，递归扫描整个输入目录并合并处理。
- 批量模式只按一级子文件夹拆包。每个一级子文件夹内部仍递归扫描。
- 每个报销包独立读取出差信息、独立生成预览、独立导出 Excel 和重命名结果。
- 根目录直属票据不静默混入任意子包，必须有明确处理策略。

## 3. 非目标

- 不引入 React/Vite/Tailwind 或新前端框架。
- 不重写 OCR、字段抽取、Excel 生成、重复票判断、行程校对规则。
- 不在本阶段实现“发票提交后销毁/归档台账”。
- 不在本阶段实现“LLM 发票视觉校验”。
- 不支持无限层级“每个子目录都拆成独立报销包”。批量拆包只识别一级子文件夹。

## 4. 用户与场景

### 4.1 主要用户

本地报销整理人员，一次性整理多个出差文件夹，希望减少重复启动任务和人工分批操作。

### 4.2 典型场景

用户准备一个总目录：

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

选择“批量报销包模式”后，系统生成：

```text
整理结果/
  2026-03-杭州出差/
    00_报销清单.xlsx
    raw_results.json
    rename_plan.json
    trip_audit.json
  2026-04-南京出差/
    00_报销清单.xlsx
    raw_results.json
    rename_plan.json
    trip_audit.json
```

## 5. 产品决策

### 5.1 处理模式

新增字段 `organize_mode`：

- `single`: 单报销包模式，默认值。行为保持当前逻辑。
- `batch_subfolders`: 批量报销包模式。根目录下每个一级子文件夹独立处理。

Web UI 文案：

- 单报销包模式：递归扫描整个文件夹，合并成一次出差。
- 批量报销包模式：每个一级子文件夹作为一次独立出差处理。

CLI 参数：

```bash
python3 -m invoice_agent organize ./待整理报销 --mode batch-subfolders
```

### 5.2 子文件夹识别规则

批量模式下：

- 只枚举输入目录下的一级子文件夹。
- 隐藏目录、输出目录、空目录不作为报销包。
- 每个一级子文件夹内部用现有 `scan_documents()` 递归扫描。
- 如果某个一级子文件夹没有支持的票据文件，该子包标记为 skipped，不阻断其他子包。

### 5.3 根目录直属票据规则

MVP 采用“显式提示，不自动处理”：

- 如果批量模式下根目录直属存在支持的票据文件，任务进入失败状态或返回明确错误。
- 错误提示用户将直属票据移动到某个子文件夹，或切换回单报销包模式。

原因：直属票据属于哪次出差无法可靠推断，静默混入会制造报销风险。

### 5.4 出差信息规则

批量模式下，每个子包按以下优先级解析 `TripInfo`：

1. 子文件夹内的 `trip_info.json`。
2. Web/CLI 表单提供的公共默认值。
3. 缺失必填项时，该子包失败，但不阻断其他子包。

子包的 `project_name` 默认使用子文件夹名。

### 5.5 输出目录规则

批量模式下，用户选择的输出目录作为批量根目录。每个子包输出到：

```text
{out_dir}/{子文件夹名}/
```

如果子文件夹名冲突或输出目录已存在，沿用现有 `_unique_target` 类似思路，生成安全唯一目录名，例如：

```text
杭州出差/
杭州出差_2/
```

### 5.6 任务状态与预览

Web UI 批量任务需要展示两层状态：

- 批量总览：总包数、已完成包数、失败包数、跳过包数。
- 子包详情：每个子包的阶段、文件数、错误、预览、导出状态。

MVP 中预览展示策略：

- 批量完成后显示“报销包列表”。
- 每个子包有独立状态卡片。
- 点击子包后展开该子包现有的五块预览：报销清单、类别汇总、重复与风险、行程校对、重命名计划。

### 5.7 导出策略

MVP 采用“逐个确认导出 + 批量全部导出”并存：

- 每个子包可以单独点击“确认导出”。
- 批量任务提供“导出全部可导出报销包”。
- 已失败、已跳过、缺少必填 TripInfo 的子包不会被导出。
- 导出全部时，某个子包导出失败不应阻断后续子包；最终显示失败清单。

### 5.8 复制重命名策略

批量模式下如果勾选“复制并重命名”：

- 每个子包只复制自己子文件夹内的源文件。
- 复制目标仍使用现有四类桶：
  - `01_已识别_重命名`
  - `02_待人工确认`
  - `03_重复疑似`
  - `04_无法识别`
- 四类桶位于每个子包输出目录下，不跨子包混合。

## 6. 功能需求

### FR-1: 单报销包模式保持兼容

系统必须默认使用当前递归合并处理逻辑。

验收标准：

- 不传 `organize_mode` 或 CLI `--mode` 时，行为与当前版本一致。
- 现有测试不需要大规模改写。
- 当前 Web UI 的预览优先、确认导出流程不变。

### FR-2: 发现批量子包

系统在批量模式下能识别根目录下的一级子文件夹作为独立报销包。

验收标准：

- 一级子文件夹 A、B 分别生成独立子任务。
- A/B 内部的二级目录票据仍会被递归扫描。
- 空子文件夹显示 skipped。
- 输出目录自身不会被当成输入子包。

### FR-3: 每个子包独立处理

每个子包应复用现有 `organize_folder()` 主流程，独立执行 OCR、分析、重复标记、行程校对、预览生成。

验收标准：

- A 子包的发票不会出现在 B 子包的 `raw_results.json`。
- A/B 子包分别生成自己的 `00_报销清单.xlsx`。
- 子包内重复票只影响本子包，不跨子包去重。

### FR-4: 子包级错误隔离

单个子包失败不应导致整个批量任务失败。

验收标准：

- A 缺失 TripInfo 时，A 标记 failed。
- B 配置完整时仍能完成预览并导出。
- 批量总览显示失败原因和可继续处理的子包。

### FR-5: 根目录直属票据保护

批量模式下发现根目录直属票据时，系统必须阻止任务继续或明确要求用户处理。

验收标准：

- 根目录直属 `invoice.pdf` 会触发清晰错误。
- 错误提示包含两个可选动作：移动到子文件夹，或使用单报销包模式。
- 不会静默把直属票据合并到某个子包。

### FR-6: Web UI 支持模式选择

Web UI 表单新增处理模式选择。

验收标准：

- 默认选中“单报销包模式”。
- 选择“批量报销包模式”后，提交表单包含 `organize_mode=batch_subfolders`。
- 批量任务状态区显示报销包维度进度。

### FR-7: CLI 支持模式选择

CLI 新增 `--mode` 参数。

验收标准：

- `--mode single` 等价于当前行为。
- `--mode batch-subfolders` 启用批量模式。
- 非法 mode 返回清晰错误。

## 7. 数据模型建议

新增轻量结果模型：

```python
@dataclass
class BatchOrganizeItem:
    name: str
    folder: Path
    output_dir: Path
    state: str  # pending | running | review | done | failed | skipped
    error: str = ""
    result: OrganizeResult | None = None

@dataclass
class BatchOrganizeResult:
    root_folder: Path
    output_dir: Path
    items: list[BatchOrganizeItem]
```

Web `TASKS` 中建议新增：

```python
{
  "mode": "batch_subfolders",
  "state": "running|review|exporting|done|failed",
  "packages": [
    {
      "id": "package-id",
      "name": "杭州出差",
      "state": "review",
      "folder": "...",
      "output_dir": "...",
      "total": 12,
      "completed": 12,
      "preview": {...},
      "can_export": true,
      "error": ""
    }
  ]
}
```

## 8. API 设计

### 8.1 保持现有接口

现有接口继续可用：

- `POST /organize`
- `GET /tasks/:id`
- `POST /tasks/:id/export`

### 8.2 批量导出接口

新增接口：

```http
POST /tasks/:id/packages/:package_id/export
POST /tasks/:id/export-all
```

说明：

- 单包导出只导出指定子包。
- `export-all` 导出所有 `can_export=true` 的子包。
- 为避免破坏旧逻辑，`POST /tasks/:id/export` 在单报销包模式下继续保持当前语义；批量模式下可以返回错误，提示使用 `export-all` 或子包导出。

## 9. TDD 开发策略

严格按 Red-Green-Refactor 推进：每个行为先写失败测试，确认失败原因正确，再写最小实现。

### 9.1 RED 阶段测试清单

优先新增或扩展 `tests/test_invoice_agent.py`。

1. `test_batch_mode_discovers_first_level_subfolders_only`
   - 构造根目录 A/B 子文件夹和 A 内二级目录。
   - 期望批量包只包含 A、B，不包含 A 的二级目录作为独立包。

2. `test_batch_mode_fails_when_root_contains_direct_invoice_files`
   - 根目录直接放 `invoice.pdf`。
   - 期望返回清晰错误，不开始混合处理。

3. `test_batch_mode_processes_each_subfolder_as_independent_package`
   - A/B 各自有票据和 `trip_info.json`。
   - 期望 A/B 分别生成 `raw_results.json`，记录不互相混入。

4. `test_batch_mode_keeps_duplicate_detection_within_each_package`
   - A/B 中放相同 hash 或相同发票号。
   - 期望不跨子包标记重复。

5. `test_batch_mode_continues_when_one_package_missing_trip_info`
   - A 缺失必填 TripInfo，B 完整。
   - 期望 A failed，B review/done。

6. `test_cli_accepts_batch_subfolders_mode`
   - 解析 CLI 参数。
   - 期望 `args.mode == "batch-subfolders"`。

7. `test_web_form_contains_organize_mode_control`
   - 渲染首页。
   - 期望包含模式选择字段和中文说明。

8. `test_web_batch_task_snapshot_includes_package_statuses`
   - 模拟批量任务。
   - 期望 `/tasks/:id` 快照包含 packages 列表，且不泄漏内部 `_records`。

9. `test_export_single_batch_package_writes_only_that_package`
   - 构造两个 review 子包。
   - 导出 A 后只生成 A 的 Excel，B 仍可导出。

10. `test_export_all_batch_packages_reports_partial_failures`
    - 一个子包导出失败，一个成功。
    - 期望返回成功/失败明细。

### 9.2 GREEN 阶段实现顺序

1. 增加 mode 解析，不改变默认行为。
2. 增加批量子包发现函数。
3. 增加批量 organize 服务层，复用 `organize_folder()`。
4. 扩展 CLI 调用路径。
5. 扩展 Web 表单和任务状态。
6. 增加单包导出和全部导出接口。
7. 完善前端渲染批量包列表。

### 9.3 REFACTOR 阶段边界

允许的重构：

- 从 `web.py` 中抽出批量任务辅助函数。
- 为 `pipeline.py` 增加批量模式相关 dataclass 和纯函数。
- 将 Web 任务快照组装逻辑拆成小函数。

不允许的重构：

- 不重写 OCR provider。
- 不改变现有 `ExpenseRecord` 核心字段语义。
- 不改变 Excel sheet 名称和现有 preview 字段结构。

## 10. 风险与处理

### 风险 1: 用户把同一次出差按类型分了子文件夹

处理：默认仍是单报销包模式；批量模式文案明确“每个一级子文件夹=一次独立出差”。

### 风险 2: 根目录同时有直属票据和子文件夹

处理：MVP 直接报错，避免误归类。

### 风险 3: 批量任务前端复杂度上升

处理：MVP 先做列表 + 展开详情，不做复杂嵌套 tab 或多任务并发控制。

### 风险 4: OCR 批量处理时间过长

处理：MVP 串行处理子包，沿用每个子包内部的并发 OCR；后续再评估子包级并发。

## 11. 分阶段交付

### Phase 1: CLI/服务层 MVP

- mode 参数。
- 批量发现一级子文件夹。
- 子包独立处理。
- 输出目录分包。
- 测试覆盖核心行为。

### Phase 2: Web 批量预览

- Web 模式选择。
- 批量任务状态快照。
- 子包列表和展开预览。
- 子包级导出。

### Phase 3: 批量导出体验

- 导出全部。
- 部分失败报告。
- 前端展示导出结果清单。

### Phase 4: 生命周期能力衔接

为后续“已提交/已销毁记录”预留报销包 ID 和状态字段，但本 PRD 不实现销毁逻辑。

## 12. 验收口径

功能完成需满足：

- `PYTHONPATH=. pytest` 全部通过。
- 单报销包模式行为与当前版本一致。
- 批量模式下两个子文件夹能生成两个独立输出目录。
- 一个子包失败不会阻止另一个子包进入预览/导出。
- Web UI 能清楚区分当前是单包还是批量模式。
- README 更新包含批量模式示例和子文件夹使用建议。

## 13. 待确认项

以下决策已按保守默认写入 PRD，评审时如有不同偏好再调整：

- 批量模式下根目录直属票据直接报错。
- 子包级处理先串行，不做子包并发。
- 批量导出接口使用 `export-all`，单包导出使用 `packages/:package_id/export`。
- 输出目录用一级子文件夹名，不按日期或人员自动改名。
