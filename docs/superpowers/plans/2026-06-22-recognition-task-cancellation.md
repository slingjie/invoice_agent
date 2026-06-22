# 识别任务停止与终止 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Web UI 的单报销包与批量报销包识别任务增加“停止识别”和“终止任务”，保留已完成结果，并禁止不完整任务导出。

**Architecture:** 新增独立的线程安全取消控制对象，通过 Web 任务、流水线和 OCR 提供者逐层传递。停止采用协作式边界检查，终止取消本地异步任务；Web 任务状态机负责把未完成文件归类为已停止或已终止。

**Tech Stack:** Python 3、`threading.Event`、`asyncio`、原生 JavaScript、pytest

---

## 文件结构

- 新建 `invoice_agent/cancellation.py`：取消模式、线程安全状态和取消异常。
- 修改 `invoice_agent/ocr.py`：异步 OCR 响应停止和终止。
- 修改 `invoice_agent/pipeline.py`：逐文件、逐报销包检查取消状态并保留部分结果。
- 修改 `invoice_agent/web.py`：任务取消接口、状态流转和不可导出约束。
- 修改 `invoice_agent/static/app.js`：两个按钮、确认交互和最终状态渲染。
- 修改 `invoice_agent/static/app.css`：取消操作按钮样式。
- 修改 `tests/test_invoice_agent.py`：后端、前端静态资源和真实任务入口回归测试。
- 修改 `README.md`：中文用户指南。

### Task 1: 取消控制对象

**Files:**
- Create: `invoice_agent/cancellation.py`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

新增测试，验证默认模式、停止、停止升级为终止及终止不可降级：

```python
def test_cancellation_control_stop_can_upgrade_to_terminate():
    control = CancellationControl()
    assert control.mode == "none"
    control.request_stop()
    assert control.mode == "stop"
    assert control.should_stop_starting()
    assert not control.should_terminate()
    control.request_terminate()
    control.request_stop()
    assert control.mode == "terminate"
    assert control.should_terminate()
```

- [x] **Step 2: 运行测试并确认因缺少对象失败**

Run: `python -m pytest tests/test_invoice_agent.py::test_cancellation_control_stop_can_upgrade_to_terminate -v`

Expected: FAIL，无法导入 `CancellationControl`。

- [x] **Step 3: 最小实现**

使用锁保护模式，提供 `request_stop()`、`request_terminate()`、`should_stop_starting()` 和 `should_terminate()`；不增加未使用的配置项。

- [x] **Step 4: 运行测试**

Run: `python -m pytest tests/test_invoice_agent.py::test_cancellation_control_stop_can_upgrade_to_terminate -v`

Expected: PASS。

### Task 2: 流水线温和停止

**Files:**
- Modify: `invoice_agent/pipeline.py`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

用可控阻塞 OCR 提供者启动三文件任务；第一个文件开始后请求停止，释放第一个文件，断言只识别第一个文件且返回结果标记为不完整。

- [x] **Step 2: 运行测试确认当前实现继续处理全部文件**

Run: `python -m pytest tests/test_invoice_agent.py::test_organize_folder_stop_finishes_active_file_without_starting_more -v`

Expected: FAIL，提供者被调用超过一次或结果缺少取消状态。

- [x] **Step 3: 最小实现**

为 `OrganizeResult` 增加取消模式字段；为 `organize_folder()` 和 `_parse_records()` 增加可选 `CancellationControl`。存在控制对象时使用可逐项启动的执行路径，停止后不再提交新文件，已完成记录继续进入预览生成。

- [x] **Step 4: 运行目标测试和流水线回归**

Run: `python -m pytest tests/test_invoice_agent.py -k "organize_folder_stop or organize_folder" -v`

Expected: PASS。

### Task 3: OCR 立即终止

**Files:**
- Modify: `invoice_agent/ocr.py`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

替换异步 PaddleOCR 客户端为可控阻塞客户端；启动多个 `process_one` 后请求终止，断言 `parse_many()` 在线程超时内返回，且只返回终止前完成的文档。

- [x] **Step 2: 运行测试确认当前实现持续等待**

Run: `python -m pytest tests/test_invoice_agent.py::test_sdk_ocr_provider_terminate_cancels_pending_tasks -v`

Expected: FAIL，线程未按期结束或返回全部错误文档。

- [x] **Step 3: 最小实现**

`parse_many()` 和 `_run_async()` 接收取消控制对象；为每个异步任务建立监督循环。终止时取消未完成任务，并以 `return_exceptions=True` 收尾；`CancelledError` 不转换为 OCR 失败。

- [x] **Step 4: 运行 OCR 测试**

Run: `python -m pytest tests/test_invoice_agent.py -k "sdk_ocr_provider or cancellation" -v`

Expected: PASS。

### Task 4: 批量模式取消边界

**Files:**
- Modify: `invoice_agent/pipeline.py`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

创建两个报销包；第一个包处理时请求停止，断言第二个包未启动并被记录为停止。另写终止测试，断言当前包未完成文件和后续包均为终止。

- [x] **Step 2: 运行测试确认当前实现继续处理后续包**

Run: `python -m pytest tests/test_invoice_agent.py -k "batch_mode_stop or batch_mode_terminate" -v`

Expected: FAIL。

- [x] **Step 3: 最小实现**

`organize_batch_subfolders()` 接收取消控制对象，在报销包边界检查模式；为未处理包生成状态明确的 `BatchOrganizeItem`，不扩大现有批量输出逻辑。

- [x] **Step 4: 运行批量回归**

Run: `python -m pytest tests/test_invoice_agent.py -k "batch_mode or batch_subfolders" -v`

Expected: PASS。

### Task 5: Web 取消接口与任务状态

**Files:**
- Modify: `invoice_agent/web.py`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

覆盖：

- `POST /tasks/{id}/stop` 将运行任务设为 `stopping`。
- 重复停止幂等。
- 停止可升级为 `terminating`。
- 最终取消状态不允许导出。
- 已完成任务收到取消请求时状态不变。

- [x] **Step 2: 运行测试确认路由不存在**

Run: `python -m pytest tests/test_invoice_agent.py -k "task_stop_endpoint or task_terminate_endpoint or cancelled_task_cannot_export" -v`

Expected: FAIL，接口未实现或状态未变化。

- [x] **Step 3: 最小实现**

任务创建时保存 `_cancellation`；新增统一的取消请求函数和两个 POST 路由。执行结束后根据取消模式写入 `stopped` 或 `terminated`，将未完成文件改为对应状态，保留已完成预览并强制 `can_export=False`。

- [x] **Step 4: 运行 Web 后端测试**

Run: `python -m pytest tests/test_invoice_agent.py -k "task_stop or task_terminate or export" -v`

Expected: PASS。

### Task 6: 前端按钮与状态展示

**Files:**
- Modify: `invoice_agent/web.py`
- Modify: `invoice_agent/static/app.js`
- Modify: `invoice_agent/static/app.css`
- Test: `tests/test_invoice_agent.py`

- [x] **Step 1: 写失败测试**

静态资源测试断言存在：

- `停止识别` 和 `终止任务` 按钮。
- `/stop` 与 `/terminate` 请求。
- `window.confirm` 终止确认。
- `stopping`、`stopped`、`terminating`、`terminated` 渲染分支。

- [x] **Step 2: 运行测试确认元素不存在**

Run: `python -m pytest tests/test_invoice_agent.py -k "cancel_controls" -v`

Expected: FAIL。

- [x] **Step 3: 最小实现**

在进度面板加入两个按钮；仅任务可取消时显示。停止后禁用停止按钮但保留终止升级；终止确认后禁用两个按钮。最终取消状态停止轮询、恢复开始按钮、禁用导出。

- [x] **Step 4: 运行前端静态测试**

Run: `python -m pytest tests/test_invoice_agent.py -k "web_ui or cancel_controls" -v`

Expected: PASS。

### Task 7: 文档、全量验证与提交

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-06-22-recognition-task-cancellation.md`

- [x] **Step 1: 更新中文用户指南**

说明两个按钮的区别、已完成结果保留、未完成任务不可导出及远端 Job 可能继续运行。

- [x] **Step 2: 运行全量测试**

Run: `python -m pytest -q`

Expected: 全部 PASS，无失败。

- [x] **Step 3: 运行语法与差异检查**

Run: `python -m compileall invoice_agent`

Expected: exit 0。

Run: `git diff --check`

Expected: 无输出，exit 0。

- [x] **Step 4: 灰度验证真实任务入口**

使用可控慢速 OCR 替身调用 `start_organize_task()`：

- 停止后仅完成当前文件。
- 终止后任务在限定时间内结束。
- 最终状态不可导出。

- [x] **Step 5: 更新计划勾选并提交**

```bash
git add invoice_agent tests README.md docs/superpowers/plans/2026-06-22-recognition-task-cancellation.md
git commit -m "feat: 支持停止和终止识别任务"
```
