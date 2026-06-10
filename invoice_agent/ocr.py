from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .extractor import extract_fields_from_text
from .models import ParsedDocument


def detect_file_type(path: Path) -> int:
    return 0 if path.suffix.lower() == ".pdf" else 1


class PaddleOcrProvider:
    def __init__(
        self,
        api_url: Optional[str] = None,
        access_token: Optional[str] = None,
        timeout_seconds: int = 120,
    ):
        self.api_url = (api_url or os.getenv("PADDLEOCR_DOC_PARSING_API_URL", "")).strip()
        self.access_token = (access_token or os.getenv("PADDLEOCR_ACCESS_TOKEN", "")).strip()
        self.timeout_seconds = timeout_seconds

    def parse(self, path: Path) -> ParsedDocument:
        if not self.api_url or not self.access_token:
            return _error_document(
                path,
                {"code": "CONFIG_ERROR", "message": "Missing PaddleOCR env vars"},
            )
        api_url = self.api_url
        if not api_url.startswith("https://"):
            if api_url.startswith("http://"):
                return _error_document(
                    path,
                    {"code": "CONFIG_ERROR", "message": "PaddleOCR API URL must use https"},
                )
            api_url = f"https://{api_url}"

        body = json.dumps(
            {
                "file": base64.b64encode(path.read_bytes()).decode("utf-8"),
                "fileType": detect_file_type(path),
                "visualize": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Authorization": f"token {self.access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return _error_document(path, {"code": "API_ERROR", "message": str(exc)})

        if result.get("errorCode", 0) != 0:
            return _error_document(
                path,
                {"code": "API_ERROR", "message": result.get("errorMsg", "Unknown")},
                raw_result=result,
            )

        return _document_from_layout_result(path, result)


class PaddleAsyncOcrProvider:
    def __init__(
        self,
        job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
        access_token: Optional[str] = None,
        model: str = "PaddleOCR-VL-1.5",
        poll_interval_seconds: int = 3,
        timeout_seconds: int = 120,
        request_timeout_seconds: int = 30,
        session: Any = None,
    ):
        self.job_url = job_url.strip()
        self.access_token = (access_token or os.getenv("PADDLEOCR_ACCESS_TOKEN", "")).strip()
        self.model = model
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.session = session or requests
        self.optional_payload = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }

    def parse(self, path: Path) -> ParsedDocument:
        return self.parse_many([path], max_workers=1)[0]

    def parse_many(self, paths: List[Path], max_workers: int = 3) -> List[ParsedDocument]:
        if not self.job_url or not self.access_token:
            return [
                _error_document(
                    path,
                    {"code": "CONFIG_ERROR", "message": "Missing PaddleOCR async job config"},
                )
                for path in paths
            ]

        workers = max(1, min(max_workers, len(paths) or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            submitted = list(executor.map(self._submit_job, paths))

        results: Dict[Path, ParsedDocument] = {}
        pending: Dict[str, Tuple[Path, float]] = {}
        for path, job_id, error in submitted:
            if error:
                results[path] = _error_document(path, error)
            else:
                pending[job_id] = (path, time.monotonic())

        while pending:
            for job_id, (path, started_at) in list(pending.items()):
                if time.monotonic() - started_at > self.timeout_seconds:
                    results[path] = _error_document(
                        path,
                        {"code": "TIMEOUT", "message": f"OCR job timed out after {self.timeout_seconds}s"},
                    )
                    del pending[job_id]
                    continue
                document = self._poll_job(path, job_id)
                if document is not None:
                    results[path] = document
                    del pending[job_id]
            if pending:
                time.sleep(self.poll_interval_seconds)

        return [results[path] for path in paths]

    def _submit_job(self, path: Path) -> Tuple[Path, str, Optional[Dict[str, str]]]:
        headers = {"Authorization": f"bearer {self.access_token}"}
        try:
            with path.open("rb") as file_obj:
                response = self.session.post(
                    self.job_url,
                    headers=headers,
                    data={
                        "model": self.model,
                        "optionalPayload": json.dumps(self.optional_payload),
                    },
                    files={"file": file_obj},
                    timeout=self.request_timeout_seconds,
                )
            if response.status_code != 200:
                return path, "", {"code": "SUBMIT_ERROR", "message": response.text}
            job_id = response.json()["data"]["jobId"]
            return path, job_id, None
        except Exception as exc:
            return path, "", {"code": "SUBMIT_ERROR", "message": str(exc)}

    def _poll_job(self, path: Path, job_id: str) -> Optional[ParsedDocument]:
        headers = {"Authorization": f"bearer {self.access_token}"}
        try:
            response = self.session.get(
                f"{self.job_url}/{job_id}",
                headers=headers,
                timeout=self.request_timeout_seconds,
            )
            if response.status_code != 200:
                return _error_document(path, {"code": "POLL_ERROR", "message": response.text})
            payload = response.json()
            data = payload.get("data", {})
            state = data.get("state")
            if state in {"pending", "running"}:
                return None
            if state == "failed":
                return _error_document(path, {"code": "JOB_FAILED", "message": data.get("errorMsg", "Unknown")})
            if state != "done":
                return _error_document(path, {"code": "JOB_STATE_ERROR", "message": f"Unknown state: {state}"})
            json_url = data.get("resultUrl", {}).get("jsonUrl", "")
            return self._download_result(path, json_url, payload)
        except Exception as exc:
            return _error_document(path, {"code": "POLL_ERROR", "message": str(exc)})

    def _download_result(self, path: Path, json_url: str, job_payload: Dict[str, Any]) -> ParsedDocument:
        if not json_url:
            return _error_document(path, {"code": "RESULT_ERROR", "message": "Missing result jsonUrl"})
        try:
            response = self.session.get(json_url, timeout=self.request_timeout_seconds)
            response.raise_for_status()
            texts = []
            raw_lines = []
            for line in response.text.strip().splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                raw_lines.append(item)
                result = item.get("result", {})
                for page in result.get("layoutParsingResults", []):
                    text = page.get("markdown", {}).get("text", "")
                    if text:
                        texts.append(text)
            raw_text = "\n\n".join(texts)
            return ParsedDocument(
                source_path=path,
                raw_text=raw_text,
                raw_result={"job": job_payload, "jsonl": raw_lines},
                fields=extract_fields_from_text(raw_text, path),
                ok=True,
            )
        except Exception as exc:
            return _error_document(path, {"code": "RESULT_ERROR", "message": str(exc)})


def _document_from_layout_result(path: Path, result: Dict[str, Any]) -> ParsedDocument:
    pages = result.get("result", {}).get("layoutParsingResults", [])
    texts = []
    for page in pages:
        text = page.get("markdown", {}).get("text", "")
        if text:
            texts.append(text)
    raw_text = "\n\n".join(texts)
    return ParsedDocument(
        source_path=path,
        raw_text=raw_text,
        raw_result=result,
        fields=extract_fields_from_text(raw_text, path),
        ok=True,
    )


def _error_document(
    path: Path,
    error: Dict[str, str],
    raw_result: Optional[Dict[str, Any]] = None,
) -> ParsedDocument:
    return ParsedDocument(
        source_path=path,
        raw_text="",
        raw_result=raw_result or {},
        fields={},
        ok=False,
        error=error,
    )
