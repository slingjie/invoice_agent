from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .extractor import extract_fields_from_text
from .models import ParsedDocument

logger = logging.getLogger(__name__)


def detect_file_type(path: Path) -> int:
    return 0 if path.suffix.lower() == ".pdf" else 1


class PaddleOcrProvider:
    """同步 layout-parsing API 提供者（也用作异步 API 的 fallback）。"""

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
    """异步 Job API 提供者，支持自动重试和同步 API 回退。

    重试策略：
    - HTTP 层（urllib3.Retry）：仅对 GET 请求（轮询/下载）自动重试
    - 应用层（_submit_job）：对 POST 请求（文件上传）手动重试，带指数退避
    - 业务层（parse_many）：当 Job 状态为 failed 时重新提交
    - Fallback：异步 API 反复失败后，自动降级到同步 layout-parsing API
    """

    def __init__(
        self,
        job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
        access_token: Optional[str] = None,
        model: str = "PaddleOCR-VL-1.5",
        poll_interval_seconds: int = 3,
        timeout_seconds: int = 120,
        request_timeout_seconds: int = 60,
        session: Any = None,
        retry_max_attempts: int = 3,
        retry_base_delay_seconds: float = 1.0,
        fallback_api_url: Optional[str] = None,
    ):
        self.job_url = job_url.strip()
        self.access_token = (access_token or os.getenv("PADDLEOCR_ACCESS_TOKEN", "")).strip()
        self.model = model
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.retry_max_attempts = retry_max_attempts
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.fallback_api_url = (fallback_api_url or "").strip()

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            if retry_max_attempts > 0:
                # 仅对 GET 请求自动重试（轮询/下载是轻量请求）
                # POST 请求（文件上传）由 _submit_job 手动重试
                retries = Retry(
                    total=retry_max_attempts,
                    backoff_factor=retry_base_delay_seconds,
                    status_forcelist=[500, 502, 503, 504],
                    allowed_methods=["GET"],  # 不包含 POST
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retries)
                self.session.mount("http://", adapter)
                self.session.mount("https://", adapter)

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
        job_retry_counts: Dict[Path, int] = defaultdict(int)
        submit_failed: List[Tuple[Path, Dict[str, str]]] = []
        # 轮询/下载/超时错误 — 循环后会先尝试重新轮询，再走 fallback
        poll_download_failed: List[Tuple[Path, str, Dict[str, str]]] = []

        for path, job_id, error in submitted:
            if error:
                submit_failed.append((path, error))
            else:
                pending[job_id] = (path, time.monotonic())

        while pending:
            for job_id, (path, started_at) in list(pending.items()):
                if time.monotonic() - started_at > self.timeout_seconds:
                    poll_download_failed.append(
                        (path, job_id,
                         {"code": "TIMEOUT", "message": f"OCR job timed out after {self.timeout_seconds}s"})
                    )
                    del pending[job_id]
                    continue

                document = self._poll_job(path, job_id)

                if document is None:
                    continue  # still pending/running

                # 检查是否为 JOB_FAILED，需要业务层重试（重新提交 Job）
                if (
                    not document.ok
                    and document.error.get("code") == "JOB_FAILED"
                    and job_retry_counts[path] < self.retry_max_attempts
                ):
                    job_retry_counts[path] += 1
                    delay = self.retry_base_delay_seconds * (2 ** (job_retry_counts[path] - 1))
                    logger.info("Job %s for %s failed (attempt %d/%d), retrying in %.1fs",
                                job_id, path.name, job_retry_counts[path],
                                self.retry_max_attempts, delay)
                    time.sleep(delay)

                    _, new_job_id, new_error = self._submit_job(path)
                    if new_job_id:
                        pending[new_job_id] = (path, time.monotonic())
                    else:
                        submit_failed.append((path, new_error))
                    del pending[job_id]
                elif document.ok:
                    results[path] = document
                    del pending[job_id]
                else:
                    # POLL_ERROR / RESULT_ERROR / JOB_STATE_ERROR / JOB_FAILED 重试用尽
                    # 收集到 poll_download_failed，循环后尝试重新轮询 + fallback
                    poll_download_failed.append(
                        (path, job_id, document.error or {"code": "UNKNOWN", "message": "Unknown error"})
                    )
                    del pending[job_id]

            if pending:
                time.sleep(self.poll_interval_seconds)

        # 对轮询/下载失败的文件：先尝试重新轮询一次，再走 fallback
        for path, job_id, error in poll_download_failed:
            if path in results:
                continue  # 已通过其他路径成功

            # 尝试重新轮询一次（job 可能仍在服务端运行，或瞬时网络故障已恢复）
            logger.info("Retrying poll for %s (job %s) after %s",
                        path.name, job_id, error.get("code"))
            time.sleep(self.retry_base_delay_seconds)
            document = self._poll_job(path, job_id)
            if document is not None and document.ok:
                results[path] = document
                logger.info("Retry poll succeeded for %s", path.name)
                continue

            # 重新轮询也失败，走 fallback（重试异步提交 → 降级同步 API）
            updated_error = error
            if document is not None and not document.ok:
                updated_error = document.error or error
            results[path] = self._retry_submit_with_fallback(path, updated_error)

        # 对提交失败的文件，尝试应用层重试
        for path, error in submit_failed:
            if path in results:
                continue  # 已通过其他路径成功
            results[path] = self._retry_submit_with_fallback(path, error)

        return [results[path] for path in paths]

    def _retry_submit_with_fallback(
        self, path: Path, initial_error: Dict[str, str]
    ) -> ParsedDocument:
        """对提交失败的文件进行应用层重试，最终回退到同步 API。"""
        last_error = initial_error

        # 应用层重试：手动带退避的文件上传重试
        for attempt in range(1, self.retry_max_attempts + 1):
            delay = self.retry_base_delay_seconds * (2 ** (attempt - 1))
            logger.info("Submit retry %d/%d for %s in %.1fs",
                        attempt, self.retry_max_attempts, path.name, delay)
            time.sleep(delay)

            _, job_id, error = self._submit_job(path)
            if error:
                last_error = error
                continue

            # 提交成功，轮询等待结果
            document = self._poll_until_done(path, job_id)
            if document is not None and document.ok:
                return document
            if document is not None:
                last_error = document.error or {"code": "JOB_ERROR", "message": "Unknown"}
            break

        # 所有重试都失败，尝试同步 API 回退
        if self.fallback_api_url:
            logger.info("Async API failed for %s, falling back to sync API: %s",
                        path.name, self.fallback_api_url)
            fallback = PaddleOcrProvider(
                api_url=self.fallback_api_url,
                access_token=self.access_token,
                timeout_seconds=self.timeout_seconds,
            )
            result = fallback.parse(path)
            if result.ok:
                return result
            # 回退也失败，返回原始错误（附带回退失败信息）
            return _error_document(
                path,
                {
                    "code": "ASYNC_AND_FALLBACK_FAILED",
                    "message": f"Async: {last_error.get('message', '')}; "
                               f"Fallback: {result.error.get('message', '') if result.error else ''}",
                },
            )

        return _error_document(path, last_error)

    def _poll_until_done(
        self, path: Path, job_id: str
    ) -> Optional[ParsedDocument]:
        """轮询 Job 直到完成或超时，返回最终结果。"""
        started_at = time.monotonic()
        while True:
            if time.monotonic() - started_at > self.timeout_seconds:
                return _error_document(
                    path,
                    {"code": "TIMEOUT", "message": f"OCR job timed out after {self.timeout_seconds}s"},
                )
            document = self._poll_job(path, job_id)
            if document is not None:
                return document
            time.sleep(self.poll_interval_seconds)

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
                return path, "", {"code": "SUBMIT_ERROR", "message": f"HTTP {response.status_code}: {response.text[:200]}"}
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
                return _error_document(path, {"code": "POLL_ERROR", "message": f"HTTP {response.status_code}: {response.text[:200]}"})
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
