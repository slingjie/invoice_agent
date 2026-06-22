from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PdfExportResult:
    status: str
    path: Path
    message: str = ""


def find_excel_executable() -> Path | None:
    executable = shutil.which("excel")
    if executable:
        return Path(executable)
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft Office"
        / "Root"
        / "Office16"
        / "EXCEL.EXE",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Office"
        / "Root"
        / "Office16"
        / "EXCEL.EXE",
    ]
    return next((path for path in candidates if path.exists()), None)


def export_company_pdf(xlsx_path: Path, pdf_path: Path, timeout_seconds: int = 90) -> PdfExportResult:
    if os.name != "nt" or find_excel_executable() is None:
        return PdfExportResult(
            status="skipped",
            path=pdf_path,
            message="当前环境未检测到 Microsoft Excel，已跳过 PDF 生成",
        )
    xlsx_path = xlsx_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    script = _powershell_script(xlsx_path, pdf_path)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PdfExportResult(status="failed", path=pdf_path, message=f"PDF 生成失败：{exc}")
    if result.returncode != 0 or not pdf_path.exists():
        detail = (result.stderr or result.stdout or "未生成 PDF 文件").strip()
        return PdfExportResult(status="failed", path=pdf_path, message=f"PDF 生成失败：{detail}")
    return PdfExportResult(status="success", path=pdf_path)


def _powershell_script(xlsx_path: Path, pdf_path: Path) -> str:
    source = str(xlsx_path).replace("'", "''")
    target = str(pdf_path).replace("'", "''")
    return f"""
$excel = $null
$workbook = $null
try {{
  $excel = New-Object -ComObject Excel.Application
  $excel.Visible = $false
  $excel.DisplayAlerts = $false
  $workbook = $excel.Workbooks.Open('{source}', 0, $true)
  $workbook.ExportAsFixedFormat(0, '{target}')
}} finally {{
  if ($workbook -ne $null) {{ $workbook.Close($false) }}
  if ($excel -ne $null) {{ $excel.Quit() }}
  if ($workbook -ne $null) {{ [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) }}
  if ($excel -ne $null) {{ [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }}
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}}
"""
