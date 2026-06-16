from __future__ import annotations

import html
import json
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import parse_qs, urlparse

from .config import load_agent_config
from .excel import build_preview
from .models import TripInfo
from .ocr import PaddleAsyncOcrProvider
from .pipeline import (
    ORGANIZE_MODE_BATCH_SUBFOLDERS,
    ORGANIZE_MODE_SINGLE,
    _assign_names,
    export_records,
    organize_batch_subfolders,
    organize_folder,
    write_result_files,
)
from .scanner import scan_documents
from .trip_audit import TripAuditPolicy, run_trip_audit


DEFAULT_CONFIG_PATH = "./invoice_agent_config.json"
STATIC_DIR = Path(__file__).with_name("static")
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
PREVIEW_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
TASKS: Dict[str, Dict] = {}
TASK_LOCK = threading.Lock()


def run_ui(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), InvoiceAgentHandler)
    print(f"Invoice Agent UI: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


def render_index(message: str = "", result_html: str = "", start_task_id: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>出差报销包整理 Agent</title>
  <link rel="stylesheet" href="/static/app.css?v=review-edit-v3">
  <script src="/static/app.js?v=review-edit-v3" defer></script>
</head>
<body data-initial-task-id="{html.escape(start_task_id)}" data-task-state="idle">
  <div class="app-shell">
    <header class="app-topbar">
      <div class="brand-lockup" aria-label="应用名称">
        <div class="brand-mark">票</div>
        <div>
          <h1>出差报销包整理 Agent</h1>
          <p>本地发票整理工作台</p>
        </div>
      </div>
      <div class="topbar-actions" aria-label="运行状态">
        <span class="status-pill status-pill-ok"><span class="status-dot"></span>本地运行</span>
        <span class="status-pill">预览优先</span>
      </div>
    </header>

    <main class="layout-shell">
      <section class="hero-panel">
        <p class="eyebrow">Corporate Reimbursement Console</p>
        <h2>扫描、核对并导出报销材料</h2>
        <p class="header-copy">选择发票目录和基础信息后，系统先生成可核对预览；确认无误后才导出 Excel 和可选复制重命名。</p>
      </section>

      {message}

      <div class="workspace-grid">
        <aside class="config-panel">
          <form id="organize-form" class="task-card" method="post" action="/organize">
            <section class="form-section">
              <div class="section-head">
                <span class="section-index">1</span>
                <div>
                  <h2>任务配置</h2>
                  <p>选择输入、输出和报销基础信息。</p>
                </div>
              </div>
              <div class="field-stack">
                {path_field("folder", "发票文件夹", "./测试发票", "folder", "full")}
                {path_field("out_dir", "输出目录", "./整理结果预览", "folder", "full")}
                {select_field("organize_mode", "处理模式", [("single", "单报销包模式：递归扫描整个文件夹"), ("batch_subfolders", "批量报销包模式：一级子文件夹分别处理")], "single", "full")}
                {input_field("project_name", "项目名", "")}
                {input_field("traveler", "人员", "")}
                {input_field("department", "部门", "")}
                <div class="date-grid">
                  {input_field("trip_start_date", "出差开始日期", "", input_type="date")}
                  {input_field("trip_end_date", "出差结束日期", "", input_type="date")}
                </div>
                {input_field("daily_meal_allowance", "单日餐补金额", "50")}
                <div class="date-grid">
                  {input_field("city_transport_daily_limit", "市内交通每日标准", "100")}
                  {input_field("lodging_daily_limit", "住宿每晚上限", "")}
                </div>
                <label class="check"><input type="checkbox" name="enable_llm_review" value="1"> 启用大模型行程复核</label>
              </div>
            </section>

            <details class="form-section advanced-section">
              <summary>
                <span class="section-index">2</span>
                <span>高级 OCR 配置</span>
              </summary>
              <div class="field-stack advanced-grid">
                {path_field("config", "PaddleOCR 配置文件", DEFAULT_CONFIG_PATH, "file", "full")}
                <input type="hidden" name="ocr_provider" value="async_jobs">
                <span>OCR 模式: PaddleOCR-VL-1.6 异步 Job API</span>
                {input_field("max_workers", "并发识别数量", "3")}
                {input_field("timeout_seconds", "单文件超时秒数", "120")}
              </div>
            </details>

            <div class="form-footer">
              <label class="check"><input type="checkbox" name="apply" value="1"> 复制并重命名</label>
              <button id="submit-button" type="submit">开始整理</button>
            </div>
            <p class="hint">默认只生成识别预览；确认后再导出 Excel，不会立即复制或重命名原文件。</p>
          </form>
        </aside>

        <section class="work-panel">
          <div class="panel-card workflow-card">
            <div class="workflow-stepper" aria-label="整理流程">
              <div class="flow-step is-current" data-flow-step="upload">
                <span class="flow-dot">1</span>
                <div><strong>扫描文件</strong><span>读取目录</span></div>
              </div>
              <div class="flow-step" data-flow-step="ocr">
                <span class="flow-dot">2</span>
                <div><strong>OCR识别</strong><span>提取票据信息</span></div>
              </div>
              <div class="flow-step" data-flow-step="review">
                <span class="flow-dot">3</span>
                <div><strong>预览确认</strong><span>核对风险与金额</span></div>
              </div>
              <div class="flow-step" data-flow-step="export">
                <span class="flow-dot">4</span>
                <div><strong>导出Excel</strong><span>生成报销清单</span></div>
              </div>
            </div>
          </div>

          <div class="panel-card status-card">
            <div class="section-head compact">
              <span class="section-index">3</span>
              <div>
                <h2>执行状态</h2>
                <p>实时查看扫描、识别、预览和导出进度。</p>
              </div>
            </div>
            <div class="status-grid">
              <div id="progress-status" class="status active status-idle" role="status" aria-live="polite">等待提交任务。</div>
              <div class="metric-tile">
                <span>文件进度</span>
                <strong id="progress-count">0 / 0</strong>
              </div>
            </div>
            <section id="progress-panel" class="progress-panel">
              <div class="progress-head">
                <div>
                  <div id="progress-title" class="progress-title">等待开始</div>
                  <div id="progress-meta" class="progress-meta"></div>
                </div>
              </div>
              <div class="table-frame">
                <table>
                  <thead>
                    <tr>
                      <th>文件</th>
                      <th>状态</th>
                      <th>类别</th>
                      <th>金额</th>
                      <th>提示</th>
                    </tr>
                  </thead>
                  <tbody id="progress-rows"></tbody>
                </table>
              </div>
            </section>
          </div>

          <section id="preview-panel" class="panel-card preview-panel">
            <div class="preview-header">
              <div>
                <p class="eyebrow">Review Before Export</p>
                <h2>结果预览</h2>
                <p>核对报销清单、类别汇总、风险和重命名计划。</p>
              </div>
            </div>
            <div class="sticky-export">
              <div>
                <strong>确认预览后导出</strong>
                <span>生成 00_报销清单.xlsx，并按勾选项决定是否复制重命名。</span>
              </div>
              <button id="export-button" class="secondary" type="button" disabled>确认导出</button>
            </div>
            <div id="batch-packages" class="batch-packages"></div>
            <div class="preview-tabs" aria-label="预览分区">
              <a href="#preview-main">报销清单</a>
              <a href="#preview-summary">类别汇总</a>
              <a href="#preview-company-form">公司报销表单汇总</a>
              <a href="#preview-risks">重复与风险</a>
              <a href="#preview-trip-audit">行程校对</a>
              <a href="#preview-rename">重命名计划</a>
            </div>
            <div class="preview-block">
              <h3>报销清单</h3>
              <p class="preview-block-copy">默认图文核对；表格总览用于快速定位异常。</p>
              <div id="preview-main"></div>
            </div>
            <div class="preview-block">
              <h3>类别汇总</h3>
              <div id="preview-summary"></div>
            </div>
            <div class="preview-block">
              <h3>公司报销表单汇总</h3>
              <div id="preview-company-form"></div>
            </div>
            <div class="preview-block">
              <h3>重复与风险</h3>
              <div id="preview-risks"></div>
            </div>
            <div class="preview-block">
              <h3>行程校对</h3>
              <div id="preview-trip-audit"></div>
            </div>
            <details class="preview-block collapsible-preview-block" id="preview-rename">
              <summary>重命名计划</summary>
              <div id="preview-rename-content"></div>
            </details>
          </section>
          {result_html}
        </section>
      </div>
    </main>
  </div>
</body>
</html>"""



def input_field(name: str, label: str, value: str, class_name: str = "", input_type: str = "text") -> str:
    css_class = f' class="{class_name}"' if class_name else ""
    return (
        f"<div{css_class}>"
        f'<label for="{name}">{label}</label>'
        f'<input id="{name}" name="{name}" type="{input_type}" value="{html.escape(value)}">'
        "</div>"
    )


def select_field(name: str, label: str, options, selected: str, class_name: str = "") -> str:
    css_class = f' class="{class_name}"' if class_name else ""
    option_html = "".join(
        f'<option value="{html.escape(value)}"{" selected" if value == selected else ""}>{html.escape(text)}</option>'
        for value, text in options
    )
    return (
        f"<div{css_class}>"
        f'<label for="{name}">{label}</label>'
        f'<select id="{name}" name="{name}">{option_html}</select>'
        "</div>"
    )


def path_field(name: str, label: str, value: str, kind: str, class_name: str = "") -> str:
    css_class = f' class="{class_name}"' if class_name else ""
    return f"""
    <div{css_class}>
      <label for="{name}">{label}</label>
      <div class="path-row">
        <input id="{name}" name="{name}" type="text" value="{html.escape(value)}">
        <button class="secondary" type="button" onclick="choosePath('{name}', '{kind}')">选择</button>
      </div>
    </div>
    """


class InvoiceAgentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(render_index())
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path)
            return
        if parsed.path == "/choose-path":
            self._send_json(handle_choose_path(parsed.query))
            return
        package_file = parse_task_package_file_path(parsed.path)
        if package_file:
            task_id, package_id, sequence = package_file
            try:
                self._send_preview_file(task_id, sequence, package_id=package_id)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        package_preview = parse_task_package_preview_path(parsed.path)
        if package_preview:
            task_id, package_id, sequence = package_preview
            try:
                self._send_preview_image(task_id, sequence, package_id=package_id)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        task_file = parse_task_file_path(parsed.path)
        if task_file:
            task_id, sequence = task_file
            try:
                self._send_preview_file(task_id, sequence)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        task_preview = parse_task_preview_path(parsed.path)
        if task_preview:
            task_id, sequence = task_preview
            try:
                self._send_preview_image(task_id, sequence)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        task_id = parse_task_snapshot_path(parsed.path)
        if task_id:
            self._send_json(get_task_snapshot(task_id))
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/":
            self.send_error(404)
            return

    def do_POST(self) -> None:
        package_record = parse_task_package_record_path(self.path)
        if package_record:
            task_id, package_id, sequence = package_record
            try:
                self._send_json(update_task_record(task_id, sequence, self._read_form(), package_id=package_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        package_export = parse_task_package_export_path(self.path)
        if package_export:
            task_id, package_id = package_export
            try:
                self._send_json(export_batch_package(task_id, package_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        export_all = parse_task_export_all_path(self.path)
        if export_all:
            try:
                self._send_json(export_all_batch_packages(export_all))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        task_record = parse_task_record_path(self.path)
        if task_record:
            task_id, sequence = task_record
            try:
                self._send_json(update_task_record(task_id, sequence, self._read_form()))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if self.path.startswith("/tasks/") and self.path.endswith("/export"):
            task_id = self.path.split("/")[2]
            try:
                self._send_json(export_task(task_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if self.path != "/organize":
            self.send_error(404)
            return
        form = self._read_form()
        try:
            task_id = start_organize_task(form)
            if self.headers.get("X-Requested-With") == "fetch":
                self._send_json({"task_id": task_id})
            else:
                self._send_html(render_index(start_task_id=task_id))
        except Exception as exc:
            if self.headers.get("X-Requested-With") == "fetch":
                self._send_json({"error": str(exc)}, status=400)
            else:
                message = f'<p class="message">ERROR: {html.escape(str(exc))}</p>'
                self._send_html(render_index(message=message), status=400)

    def _read_form(self) -> Dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(body, keep_blank_values=True)
        return {key: values[0].strip() for key, values in parsed.items()}

    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, data: Dict[str, str], status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static(self, request_path: str) -> None:
        name = request_path.removeprefix("/static/")
        if "/" in name or "\\" in name:
            self.send_error(404)
            return
        path = STATIC_DIR / name
        content_type = STATIC_TYPES.get(path.suffix)
        if not content_type or not path.is_file():
            self.send_error(404)
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_preview_file(self, task_id: str, sequence: str, package_id: str | None = None) -> None:
        path, content_type = get_task_preview_file(task_id, sequence, package_id=package_id)
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(content)

    def _send_preview_image(self, task_id: str, sequence: str, package_id: str | None = None) -> None:
        content, content_type = get_task_preview_image(task_id, sequence, package_id=package_id)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def parse_task_file_path(path: str) -> Tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "tasks" and parts[2] == "files":
        return parts[1], parts[3]
    return None


def parse_task_package_file_path(path: str) -> Tuple[str, str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 6 and parts[0] == "tasks" and parts[2] == "packages" and parts[4] == "files":
        return parts[1], parts[3], parts[5]
    return None


def parse_task_preview_path(path: str) -> Tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "tasks" and parts[2] == "previews":
        return parts[1], parts[3]
    return None


def parse_task_package_preview_path(path: str) -> Tuple[str, str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 6 and parts[0] == "tasks" and parts[2] == "packages" and parts[4] == "previews":
        return parts[1], parts[3], parts[5]
    return None


def parse_task_record_path(path: str) -> Tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "tasks" and parts[2] == "records":
        return parts[1], parts[3]
    return None


def parse_task_package_record_path(path: str) -> Tuple[str, str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 6 and parts[0] == "tasks" and parts[2] == "packages" and parts[4] == "records":
        return parts[1], parts[3], parts[5]
    return None


def parse_task_package_export_path(path: str) -> Tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[0] == "tasks" and parts[2] == "packages" and parts[4] == "export":
        return parts[1], parts[3]
    return None


def parse_task_export_all_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "export-all":
        return parts[1]
    return None


def parse_task_snapshot_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "tasks":
        return parts[1]
    return None


def get_task_preview_image(task_id: str, sequence: str, package_id: str | None = None) -> Tuple[bytes, str]:
    path, content_type = get_task_preview_file(task_id, sequence, package_id=package_id)
    if content_type.startswith("image/"):
        return path.read_bytes(), content_type
    if content_type != "application/pdf":
        raise ValueError("Unsupported preview file type")
    try:
        return render_pdf_preview_with_fitz(path), "image/png"
    except ValueError:
        return render_pdf_preview_with_quicklook(path), "image/png"


def render_pdf_preview_with_fitz(path: Path) -> bytes:
    try:
        import fitz
    except ImportError as exc:
        raise ValueError("PDF preview rendering requires PyMuPDF") from exc
    document = fitz.open(path)
    try:
        if document.page_count < 1:
            raise ValueError("PDF has no pages")
        page = document.load_page(0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


def render_pdf_preview_with_quicklook(path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        try:
            subprocess.run(
                ["qlmanage", "-t", "-s", "1200", "-o", str(output_dir), str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("PDF preview rendering requires PyMuPDF or macOS Quick Look") from exc
        candidates = [output_dir / f"{path.name}.png", *sorted(output_dir.glob("*.png"))]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_bytes()
    raise ValueError("PDF preview rendering failed")


def get_task_preview_file(task_id: str, sequence: str, package_id: str | None = None) -> Tuple[Path, str]:
    if not sequence.isdigit():
        raise ValueError("Invalid file sequence")
    target_sequence = int(sequence)
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("Task not found")
        records = list(get_task_records(task, package_id))
    for record in records:
        if record.sequence != target_sequence:
            continue
        path = Path(record.source_path)
        content_type = PREVIEW_FILE_TYPES.get(path.suffix.lower())
        if not content_type:
            raise ValueError("Unsupported preview file type")
        if not path.is_file():
            raise ValueError("File not found")
        return path, content_type
    raise ValueError("File not found")


def get_task_records(task: Dict, package_id: str | None = None):
    if package_id:
        package = (task.get("_packages") or {}).get(package_id)
        if not package:
            raise ValueError("Package not found")
        return package.get("records") or []
    return task.get("_records") or []


EDITABLE_RECORD_FIELDS = {
    "project_name": "project_name",
    "document_date": "document_date",
    "document_type": "document_type",
    "reimbursement_category": "high_level_category",  # 前端表单字段 → 存储到 high_level_category
    "invoice_number": "invoice_number",
    "seller_name": "seller_name",
    "buyer_name": "buyer_name",
    "total_with_tax": "total_with_tax",
    "origin": "origin",
    "destination": "destination",
    "description": "description",
    "risk_note": "risk_note",
}


def update_task_record(task_id: str, sequence: str, form: Dict[str, str], package_id: str | None = None) -> Dict:
    if not sequence.isdigit():
        raise ValueError("Invalid record sequence")
    target_sequence = int(sequence)
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("Task not found")
        package = None
        if package_id:
            package = (task.get("_packages") or {}).get(package_id)
            if not package:
                raise ValueError("Package not found")
            records = list(package.get("records") or [])
        else:
            records = list(task.get("_records") or [])
        target = next((record for record in records if record.sequence == target_sequence), None)
        if not target:
            raise ValueError("Record not found")
        for form_key, record_attr in EDITABLE_RECORD_FIELDS.items():
            if form_key in form:
                setattr(target, record_attr, form[form_key].strip())
        if "include_in_amount" in form:
            target.include_in_amount = parse_yes_no(form.get("include_in_amount"))
        _assign_names(records)
        policy = task.get("_trip_audit_policy") or TripAuditPolicy()
        trip_audit = run_trip_audit(records, policy)
        preview = build_preview(records, trip_audit)
        if package_id and package:
            package["trip_audit"] = trip_audit
            _update_public_package(task, package_id, preview=preview, can_export=True)
        else:
            task["preview"] = preview
            task["_trip_audit"] = trip_audit
            task["can_export"] = True
        task["updated_at"] = time.time()
        output_dir = package.get("output_dir") if package else (task.get("_output_dir") or task.get("output_dir"))
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        write_result_files(output_path, records, write_excel=False, trip_audit=trip_audit)
    sync_task_files_from_records(task_id, records, package_id=package_id)
    return {"preview": preview, "record": target.to_json()}


def start_organize_task(form: Dict[str, str]) -> str:
    task_id = uuid.uuid4().hex
    now = time.time()
    with TASK_LOCK:
        TASKS[task_id] = {
            "id": task_id,
            "mode": normalize_organize_mode(form.get("organize_mode")),
            "state": "queued",
            "stage": "排队中",
            "started_at": now,
            "updated_at": now,
            "total": 0,
            "completed": 0,
            "files": [],
            "preview": {},
            "can_export": False,
            "output_dir": "",
            "excel_path": "",
            "error": "",
        }
    thread = threading.Thread(target=run_organize_task, args=(task_id, dict(form)), daemon=True)
    thread.start()
    return task_id


def run_organize_task(task_id: str, form: Dict[str, str]) -> None:
    try:
        update_task(task_id, state="running", stage="扫描文件")
        run_organize_from_form(form, task_id=task_id)
    except Exception as exc:
        import traceback
        error_detail = f"{exc}\n{traceback.format_exc()}"
        print(f"[Task {task_id}] FAILED: {error_detail}")
        update_task(task_id, state="failed", stage="失败", error=str(exc))


def update_task(task_id: str, **updates) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task.update(updates)
        task["updated_at"] = time.time()


def get_task_snapshot(task_id: str) -> Dict:
    with TASK_LOCK:
        task = dict(TASKS.get(task_id) or {})
        if not task:
            return {"state": "missing", "error": "Task not found"}
        task["files"] = [dict(row) for row in task.get("files", [])]
        task["packages"] = [dict(package) for package in task.get("packages", [])]
        for key in list(task):
            if key.startswith("_"):
                task.pop(key)
    task["elapsed_seconds"] = int(time.time() - task.get("started_at", time.time()))
    return task


def run_organize_from_form(form: Dict[str, str], task_id: str | None = None):
    import logging
    _log = logging.getLogger("invoice_agent.web")
    folder = Path(required(form, "folder"))
    organize_mode = normalize_organize_mode(form.get("organize_mode"))
    config_path = optional_path(form.get("config"))
    out_dir = optional_path(form.get("out_dir"))
    _log.info("Loading config from %s", config_path)
    config = load_agent_config(config_path)
    _log.info("Config loaded: request_timeout=%ds, retry=%d, fallback=%s",
              config.request_timeout_seconds, config.retry_max_attempts,
              bool(config.fallback_api_url))
    trip_audit_policy = build_trip_audit_policy(form, config)
    timeout_seconds = parse_positive_int(form.get("timeout_seconds"), 120)
    _log.info("Creating OCR provider: job_url=%s, timeout=%ds, request_timeout=%ds",
              config.paddleocr_job_url[:50], timeout_seconds, config.request_timeout_seconds)
    provider = PaddleAsyncOcrProvider(
        job_url=config.paddleocr_job_url,
        access_token=config.paddleocr_access_token or None,
        model=config.paddleocr_model,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
        retry_max_attempts=config.retry_max_attempts,
        retry_base_delay_seconds=config.retry_base_delay_seconds,
        fallback_api_url=config.fallback_api_url or None,
    )
    _log.info("Provider created, starting OCR for folder=%s mode=%s", folder, organize_mode)
    trip_info = TripInfo(
        project_name=form.get("project_name") or folder.name,
        traveler=required(form, "traveler"),
        department=required(form, "department"),
        trip_start_date=required(form, "trip_start_date"),
        trip_end_date=required(form, "trip_end_date"),
        daily_meal_allowance=form.get("daily_meal_allowance") or "50",
    )
    if organize_mode == ORGANIZE_MODE_BATCH_SUBFOLDERS:
        package_ids: set = set()
        if task_id:
            update_task(
                task_id,
                mode=ORGANIZE_MODE_BATCH_SUBFOLDERS,
                state="running",
                stage="批量识别中",
                output_dir=str(out_dir or ""),
                excel_path="",
                preview={},
                can_export=False,
                total=0,
                completed=0,
                failed=0,
                skipped=0,
                packages=[],
                _packages={},
                _apply=form.get("apply") == "1",
                _trip_audit_policy=trip_audit_policy,
            )

        def publish_batch_item(item):
            if task_id:
                publish_batch_task_item(task_id, item, form.get("apply") == "1", trip_audit_policy, package_ids)

        result = organize_batch_subfolders(
            folder=folder,
            trip_info=trip_info,
            out_dir=out_dir,
            apply=(form.get("apply") == "1") if task_id is None else False,
            ocr_provider=provider,
            max_workers=parse_positive_int(form.get("max_workers"), 3),
            write_excel=task_id is None,
            trip_audit_policy=trip_audit_policy,
            item_callback=publish_batch_item if task_id else None,
        )
        if task_id:
            package_rows, package_state = build_batch_task_packages(result, form.get("apply") == "1", trip_audit_policy)
            total = len(package_rows)
            completed = sum(1 for package in package_rows if package["state"] in {"review", "done", "failed", "skipped"})
            failed = sum(1 for package in package_rows if package["state"] == "failed")
            skipped = sum(1 for package in package_rows if package["state"] == "skipped")
            reviewable = sum(1 for package in package_rows if package["state"] == "review")
            update_task(
                task_id,
                mode=ORGANIZE_MODE_BATCH_SUBFOLDERS,
                state="review" if reviewable else ("failed" if failed and not completed - failed - skipped else "done"),
                stage="等待确认" if reviewable else "批量处理完成",
                output_dir=str(result.output_dir),
                excel_path="",
                preview={},
                can_export=bool(reviewable),
                total=total,
                completed=completed,
                failed=failed,
                skipped=skipped,
                packages=package_rows,
                _packages=package_state,
                _apply=form.get("apply") == "1",
                _trip_audit_policy=trip_audit_policy,
            )
        return result
    if task_id:
        initialize_task_files(task_id, folder)
    result = organize_folder(
        folder=folder,
        trip_info=trip_info,
        out_dir=out_dir,
        apply=(form.get("apply") == "1") if task_id is None else False,
        ocr_provider=provider,
        max_workers=parse_positive_int(form.get("max_workers"), 3),
        progress_callback=(lambda record: mark_task_file_done(task_id, record)) if task_id else None,
        write_excel=task_id is None,
        trip_audit_policy=trip_audit_policy,
    )
    if task_id:
        sync_task_files_from_records(task_id, result.records)
        update_task(
            task_id,
            state="review",
            stage="等待确认",
            output_dir=str(result.output_dir),
            excel_path="",
            preview=result.preview,
            can_export=True,
            completed=len(result.records),
            total=len(result.records),
            _records=result.records,
            _output_dir=result.output_dir,
            _apply=form.get("apply") == "1",
            _trip_audit_policy=trip_audit_policy,
            _trip_audit=result.trip_audit,
        )
    return result


def export_task(task_id: str) -> Dict[str, str]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("Task not found")
        if task.get("mode") == ORGANIZE_MODE_BATCH_SUBFOLDERS:
            raise ValueError("批量报销包模式请使用单个报销包导出或导出全部")
        records = task.get("_records")
        output_dir = task.get("_output_dir")
        apply = bool(task.get("_apply"))
        trip_audit = task.get("_trip_audit")
    if not records or not output_dir:
        raise ValueError("Task is not ready for export")
    update_task(task_id, state="exporting", stage="导出中", can_export=False)
    export_records(output_dir, records, apply=apply, write_excel=True, trip_audit=trip_audit)
    preview = build_preview(records, trip_audit)
    excel_path = str(output_dir / "00_报销清单.xlsx")
    update_task(
        task_id,
        state="done",
        stage="完成",
        excel_path=excel_path,
        preview=preview,
        can_export=False,
    )
    return {"excel_path": excel_path}


def export_batch_package(task_id: str, package_id: str) -> Dict[str, str]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("Task not found")
        package = (task.get("_packages") or {}).get(package_id)
        if not package:
            raise ValueError("Package not found")
        records = package.get("records")
        output_dir = package.get("output_dir")
        apply = bool(package.get("apply"))
        trip_audit = package.get("trip_audit")
    if not records or not output_dir:
        raise ValueError("Package is not ready for export")
    _update_public_package_state(task_id, package_id, state="exporting", can_export=False)
    export_records(output_dir, records, apply=apply, write_excel=True, trip_audit=trip_audit)
    preview = build_preview(records, trip_audit)
    excel_path = str(output_dir / "00_报销清单.xlsx")
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task:
            _update_public_package(task, package_id, state="done", preview=preview, can_export=False, excel_path=excel_path)
            task["state"] = batch_task_state(task)
            task["stage"] = "批量处理完成" if task["state"] == "done" else "等待确认"
            task["can_export"] = any(package.get("can_export") for package in task.get("packages", []))
            task["updated_at"] = time.time()
    return {"excel_path": excel_path}


def export_all_batch_packages(task_id: str) -> Dict:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise ValueError("Task not found")
        packages = list(task.get("packages") or [])
    update_task(task_id, state="exporting", stage="批量导出中", can_export=False)
    exported = []
    failed = []
    for package in packages:
        package_id = package.get("id", "")
        name = package.get("name", "")
        if not package.get("can_export"):
            if package.get("state") in {"failed", "skipped"}:
                failed.append({"package_id": package_id, "name": name, "error": package.get("error") or "不可导出"})
            continue
        try:
            result = export_batch_package(task_id, package_id)
            exported.append({"package_id": package_id, "name": name, "excel_path": result["excel_path"]})
        except Exception as exc:
            failed.append({"package_id": package_id, "name": name, "error": str(exc)})
            _update_public_package_state(task_id, package_id, state="failed", can_export=False, error=str(exc))
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task:
            task["state"] = "done" if exported else "failed"
            task["stage"] = "批量处理完成"
            task["can_export"] = any(package.get("can_export") for package in task.get("packages", []))
            task["updated_at"] = time.time()
    return {"exported": exported, "failed": failed}


def build_batch_task_packages(result, apply: bool, trip_audit_policy: TripAuditPolicy):
    public_packages = []
    package_state = {}
    used_ids = set()
    for index, item in enumerate(result.items, start=1):
        package_id = unique_package_id(item.package_id, used_ids, index)
        records = item.result.records if item.result else []
        trip_audit = item.result.trip_audit if item.result else None
        preview = item.result.preview if item.result else {}
        can_export = item.state == "review" and bool(records)
        public_packages.append(
            {
                "id": package_id,
                "name": item.name,
                "state": item.state,
                "folder": str(item.folder),
                "output_dir": str(item.output_dir),
                "total": len(records),
                "completed": len(records),
                "preview": preview,
                "can_export": can_export,
                "error": item.error,
                "excel_path": "",
            }
        )
        package_state[package_id] = {
            "records": records,
            "output_dir": item.output_dir,
            "apply": apply,
            "trip_audit": trip_audit,
            "trip_audit_policy": trip_audit_policy,
        }
    return public_packages, package_state


def publish_batch_task_item(
    task_id: str,
    item,
    apply: bool,
    trip_audit_policy: TripAuditPolicy,
    used_ids: set,
) -> None:
    package_id = unique_package_id(item.package_id, used_ids, len(used_ids) + 1)
    records = item.result.records if item.result else []
    trip_audit = item.result.trip_audit if item.result else None
    preview = item.result.preview if item.result else {}
    public_package = {
        "id": package_id,
        "name": item.name,
        "state": item.state,
        "folder": str(item.folder),
        "output_dir": str(item.output_dir),
        "total": len(records),
        "completed": len(records),
        "preview": preview,
        "can_export": item.state == "review" and bool(records),
        "error": item.error,
        "excel_path": "",
    }
    package_state = {
        "records": records,
        "output_dir": item.output_dir,
        "apply": apply,
        "trip_audit": trip_audit,
        "trip_audit_policy": trip_audit_policy,
    }
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        packages = task.setdefault("packages", [])
        packages.append(public_package)
        task.setdefault("_packages", {})[package_id] = package_state
        task["mode"] = ORGANIZE_MODE_BATCH_SUBFOLDERS
        task["state"] = "running"
        task["stage"] = f"批量识别中，已完成 {len(packages)} 个报销包"
        task["total"] = len(packages)
        task["completed"] = sum(1 for package in packages if package.get("state") in {"review", "done", "failed", "skipped"})
        task["failed"] = sum(1 for package in packages if package.get("state") == "failed")
        task["skipped"] = sum(1 for package in packages if package.get("state") == "skipped")
        task["can_export"] = False
        task["updated_at"] = time.time()


def unique_package_id(base: str, used_ids: set, index: int) -> str:
    candidate = base or f"package-{index}"
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate
    for suffix in range(2, 1000):
        next_candidate = f"{candidate}-{suffix}"
        if next_candidate not in used_ids:
            used_ids.add(next_candidate)
            return next_candidate
    raise ValueError("Too many duplicate package ids")


def _update_public_package_state(task_id: str, package_id: str, **updates) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if task:
            _update_public_package(task, package_id, **updates)
            task["updated_at"] = time.time()


def _update_public_package(task: Dict, package_id: str, **updates) -> None:
    for package in task.get("packages", []):
        if package.get("id") == package_id:
            package.update(updates)
            return


def batch_task_state(task: Dict) -> str:
    packages = task.get("packages", [])
    if any(package.get("state") == "review" and package.get("can_export") for package in packages):
        return "review"
    if any(package.get("state") == "exporting" for package in packages):
        return "exporting"
    if any(package.get("state") == "done" for package in packages):
        return "done"
    return "failed"


def normalize_organize_mode(value: str | None) -> str:
    normalized = (value or ORGANIZE_MODE_SINGLE).strip()
    if normalized == "batch-subfolders":
        return ORGANIZE_MODE_BATCH_SUBFOLDERS
    if normalized == ORGANIZE_MODE_BATCH_SUBFOLDERS:
        return ORGANIZE_MODE_BATCH_SUBFOLDERS
    return ORGANIZE_MODE_SINGLE


def build_trip_audit_policy(form: Dict[str, str], config) -> TripAuditPolicy:
    return TripAuditPolicy(
        city_transport_daily_limit=(
            form.get("city_transport_daily_limit")
            or getattr(config, "city_transport_daily_limit", "")
            or "100"
        ),
        lodging_daily_limit=form.get("lodging_daily_limit") or getattr(config, "lodging_daily_limit", "") or "",
        enable_llm_review=parse_yes_no(form.get("enable_llm_review")),
        llm_base_url=getattr(config, "llm_base_url", ""),
        llm_model=getattr(config, "llm_model", ""),
        llm_api_key_env=getattr(config, "llm_api_key_env", ""),
    )


def initialize_task_files(task_id: str, folder: Path) -> None:
    paths = scan_documents(folder.expanduser().resolve())
    rows = [
        {
            "path": str(path),
            "name": path.name,
            "status": "等待中",
            "type": "",
            "amount": "",
            "message": "",
        }
        for path in paths
    ]
    update_task(task_id, stage="识别中", total=len(rows), completed=0, files=rows)


def mark_task_file_done(task_id: str | None, record) -> None:
    if not task_id:
        return
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        rows = task.get("files", [])
        for row in rows:
            if row.get("path") == str(record.source_path):
                row.update(
                    {
                        "status": record.recognition_status,
                        "type": record.document_type,
                        "amount": record.total_with_tax,
                        "message": task_file_message(record),
                    }
                )
                break
        task["completed"] = sum(1 for row in rows if row.get("status") not in {"等待中", "识别中"})
        task["stage"] = "识别中"
        task["updated_at"] = time.time()


def sync_task_files_from_records(task_id: str, records, package_id: str | None = None) -> None:
    by_path = {str(record.source_path): record for record in records}
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        if package_id:
            package = next((item for item in task.get("packages", []) if item.get("id") == package_id), None)
            if package is not None:
                package["completed"] = len(records)
                package["total"] = len(records)
            task["updated_at"] = time.time()
            return
        rows = task.get("files", [])
        for row in rows:
            record = by_path.get(row.get("path", ""))
            if not record:
                continue
            row.update(
                {
                    "status": record.recognition_status,
                    "type": record.document_type,
                    "amount": record.total_with_tax,
                    "message": task_file_message(record),
                }
            )
        task["completed"] = sum(1 for row in rows if row.get("status") not in {"等待中", "识别中"})
        task["updated_at"] = time.time()


def task_file_message(record) -> str:
    return "；".join(part for part in [record.duplicate_mark, record.risk_note] if part)


def required(form: Dict[str, str], key: str) -> str:
    value = (form.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def optional_path(value: str | None) -> Path | None:
    value = (value or "").strip()
    return Path(value) if value else None


def parse_positive_int(value: str | None, default: int) -> int:
    if not value:
        return default
    parsed = int(value)
    if parsed < 1:
        raise ValueError("Numeric options must be positive")
    return parsed


def parse_yes_no(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "是"}


def render_result(output_dir: Path, record_count: int, applied: bool) -> str:
    mode = "已复制并重命名" if applied else "预览模式"
    excel = output_dir / "00_报销清单.xlsx"
    return f"""
    <section class="result">
      <p><strong>{html.escape(mode)}完成</strong></p>
      <p>识别文件数：{record_count}</p>
      <p>输出目录：<code>{html.escape(str(output_dir))}</code></p>
      <p>Excel：<code>{html.escape(str(excel))}</code></p>
    </section>
    """


def handle_choose_path(query: str) -> Dict[str, str]:
    import platform

    params = parse_qs(query)
    kind = (params.get("kind") or ["folder"])[0]
    if kind not in ("file", "folder"):
        return {"error": f"Unsupported chooser kind: {kind}"}

    system = platform.system()
    if system == "Windows":
        return _choose_path_windows(kind)
    return _choose_path_macos(kind)


def _choose_path_windows(kind: str) -> Dict[str, str]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            path = filedialog.askdirectory(title="选择文件夹")
        else:
            path = filedialog.askopenfilename(
                title="选择 PaddleOCR 配置文件 invoice_agent_config.json",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            )
    finally:
        root.destroy()
    return {"path": path}


def _choose_path_macos(kind: str) -> Dict[str, str]:
    if kind == "file":
        script = (
            'POSIX path of (choose file with prompt '
            '"选择 PaddleOCR 配置文件 invoice_agent_config.json")'
        )
    else:
        script = 'POSIX path of (choose folder with prompt "选择文件夹")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return {"error": str(exc)}
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if "User canceled" in message or result.returncode == 1:
            return {"path": ""}
        return {"error": message or "选择失败"}
    return {"path": result.stdout.strip()}
