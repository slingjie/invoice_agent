# 任务配置侧栏收起 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为桌面端任务配置侧栏增加可访问的展开和收起功能，让右侧状态与预览区域获得更多宽度。

**Architecture:** 在服务端渲染的现有标题区域加入切换按钮；原生 JavaScript 只负责切换工作区状态类和无障碍属性；CSS 根据状态类调整两列网格并隐藏配置表单主体。移动端媒体查询恢复单列布局。

**Tech Stack:** Python 字符串模板、原生 JavaScript、CSS Grid、pytest

---

### Task 1: 锁定页面结构和交互契约

**Files:**
- Modify: `tests/test_invoice_agent.py`

- [ ] **Step 1: Write the failing test**

在 `test_ui_page_contains_required_form_fields` 中增加以下页面、脚本和样式断言：

```python
assert 'id="config-panel-toggle"' in page
assert 'aria-expanded="true"' in page
assert "toggleConfigPanel" in js
assert "is-config-collapsed" in js
assert ".workspace-grid.is-config-collapsed" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_invoice_agent.py::test_ui_page_contains_required_form_fields -q`

Expected: FAIL，因为页面尚无切换按钮。

### Task 2: 实现最小侧栏切换

**Files:**
- Modify: `invoice_agent/web.py`
- Modify: `invoice_agent/static/app.js`
- Modify: `invoice_agent/static/app.css`

- [ ] **Step 1: Add accessible toggle markup**

在“任务配置”标题区域加入 `#config-panel-toggle`，默认 `aria-expanded="true"`，并通过 `aria-controls="organize-form"` 关联表单。

- [ ] **Step 2: Add state switching**

实现 `toggleConfigPanel()`，只切换 `.workspace-grid` 的 `is-config-collapsed` 类、按钮的 `aria-expanded` 和中文说明。

- [ ] **Step 3: Add desktop and mobile layout rules**

桌面收起时将左列改为 56px，隐藏表单主体并保留切换按钮；在 `max-width: 860px` 下取消收起状态的布局影响并隐藏切换按钮。

- [ ] **Step 4: Run focused test**

Run: `python -m pytest tests/test_invoice_agent.py::test_ui_page_contains_required_form_fields -q`

Expected: PASS

### Task 3: 完整验证

**Files:**
- Verify: `tests/test_invoice_agent.py`
- Verify: `http://127.0.0.1:8765/`

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest -q`

Expected: 全部通过。

- [ ] **Step 2: Verify real browser behavior**

刷新本地页面，点击收起按钮，确认左栏缩窄、右栏变宽、按钮说明更新；再次点击确认恢复。
