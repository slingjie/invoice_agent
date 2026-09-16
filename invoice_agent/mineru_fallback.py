from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from .extractor import extract_fields_from_text
from .models import ParsedDocument


class MinerUFallbackProvider:
    """调用本机 MinerU-Skill 脚本，把 PDF 转成 Markdown 后复用字段抽取。"""

    def __init__(self, script_path: Path | str | None = None, timeout_seconds: int = 300):
        self.script_path = Path(script_path).expanduser() if script_path else None
        self.timeout_seconds = timeout_seconds

    def parse(self, path: Path) -> ParsedDocument:
        path = Path(path)
        if path.suffix.lower() != ".pdf":
            return _error_document(
                path,
                {"code": "UNSUPPORTED_TYPE", "message": "MinerU fallback only supports PDF"},
            )
        script = self._resolve_script()
        if script is None:
            return _error_document(
                path,
                {"code": "CONFIG_ERROR", "message": "MinerU-Skill script not found"},
            )
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, str(script), str(path), "--stdout"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return _error_document(
                path,
                {"code": "MINERU_TIMEOUT", "message": f"MinerU timed out after {self.timeout_seconds}s"},
            )
        except Exception as exc:
            return _error_document(path, {"code": "MINERU_ERROR", "message": str(exc)})

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            return _error_document(
                path,
                {"code": "MINERU_ERROR", "message": message or f"MinerU exited with {result.returncode}"},
            )
        markdown = result.stdout.strip()
        if not markdown:
            return _error_document(
                path,
                {"code": "MINERU_EMPTY_RESULT", "message": "MinerU returned empty markdown"},
            )
        return ParsedDocument(
            source_path=path,
            raw_text=markdown,
            raw_result={"provider": "mineru_fallback"},
            fields=extract_fields_from_text(markdown, path),
            ok=True,
        )

    def _resolve_script(self) -> Path | None:
        if self.script_path:
            return self.script_path if self.script_path.exists() else None
        for candidate in _default_script_candidates():
            if candidate.exists():
                return candidate
        return None


def _default_script_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".codex" / "skills" / "MinerU-Skill" / "mineru.py",
        home / ".codex" / "skills" / "mineru-skill" / "mineru.py",
        home / ".agents" / "skills" / "MinerU-Skill" / "mineru.py",
        home / ".agents" / "skills" / "mineru-skill" / "mineru.py",
    ]


def _error_document(path: Path, error: Dict[str, str], raw_result: Dict[str, Any] | None = None) -> ParsedDocument:
    return ParsedDocument(
        source_path=path,
        raw_text="",
        raw_result=raw_result or {},
        fields={},
        ok=False,
        error=error,
    )
