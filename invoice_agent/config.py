from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentConfig:
    paddleocr_doc_parsing_api_url: str = ""
    paddleocr_job_url: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    paddleocr_access_token: str = ""
    paddleocr_model: str = "PaddleOCR-VL-1.6"
    ocr_provider: str = "async_jobs"
    city_transport_daily_limit: str = "100"
    lodging_daily_limit: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key_env: str = ""
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    fallback_api_url: str = ""
    request_timeout_seconds: int = 60


def load_agent_config(path: Optional[Path]) -> AgentConfig:
    if not path:
        return AgentConfig()
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return AgentConfig(
        paddleocr_doc_parsing_api_url=str(
            data.get("paddleocr_doc_parsing_api_url")
            or data.get("PADDLEOCR_DOC_PARSING_API_URL")
            or ""
        ).strip(),
        paddleocr_job_url=str(
            data.get("paddleocr_job_url")
            or data.get("PADDLEOCR_JOB_URL")
            or "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
        ).strip(),
        paddleocr_access_token=str(
            data.get("paddleocr_access_token") or data.get("PADDLEOCR_ACCESS_TOKEN") or ""
        ).strip(),
        paddleocr_model=str(
            data.get("paddleocr_model") or data.get("PADDLEOCR_MODEL") or "PaddleOCR-VL-1.6"
        ).strip(),
        ocr_provider=str(data.get("ocr_provider") or data.get("OCR_PROVIDER") or "async_jobs").strip(),
        city_transport_daily_limit=str(
            data.get("city_transport_daily_limit") or data.get("CITY_TRANSPORT_DAILY_LIMIT") or "100"
        ).strip(),
        lodging_daily_limit=str(data.get("lodging_daily_limit") or data.get("LODGING_DAILY_LIMIT") or "").strip(),
        llm_base_url=str(data.get("llm_base_url") or data.get("LLM_BASE_URL") or "").strip(),
        llm_model=str(data.get("llm_model") or data.get("LLM_MODEL") or "").strip(),
        llm_api_key_env=str(data.get("llm_api_key_env") or data.get("LLM_API_KEY_ENV") or "").strip(),
        retry_max_attempts=int(
            data.get("retry_max_attempts") or data.get("RETRY_MAX_ATTEMPTS") or 3
        ),
        retry_base_delay_seconds=float(
            data.get("retry_base_delay_seconds") or data.get("RETRY_BASE_DELAY_SECONDS") or 1.0
        ),
        fallback_api_url=str(
            data.get("fallback_api_url") or data.get("FALLBACK_API_URL")
            # 自动从 paddleocr_doc_parsing_api_url 继承
            or data.get("paddleocr_doc_parsing_api_url")
            or data.get("PADDLEOCR_DOC_PARSING_API_URL")
            or ""
        ).strip(),
        request_timeout_seconds=int(
            data.get("request_timeout_seconds") or data.get("REQUEST_TIMEOUT_SECONDS") or 60
        ),
    )
