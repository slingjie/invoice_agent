from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Protocol

from .extractor import parse_amount
from .models import ExpenseRecord


DEFAULT_CITY_TRANSPORT_DAILY_LIMIT = "100"


@dataclass
class TripAuditPolicy:
    city_transport_daily_limit: str = DEFAULT_CITY_TRANSPORT_DAILY_LIMIT
    lodging_daily_limit: str = ""
    enable_llm_review: bool = False
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key_env: str = ""


@dataclass
class TripAuditItem:
    category: str
    severity: str
    conclusion: str
    evidence: List[str] = field(default_factory=list)
    related_sequences: List[int] = field(default_factory=list)
    suggested_action: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TripAuditResult:
    items: List[TripAuditItem] = field(default_factory=list)
    llm_review: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "items": [item.to_json() for item in self.items],
            "llm_review": self.llm_review,
        }


class TripAuditLlmClient(Protocol):
    def review(self, payload: Dict[str, Any]) -> str:
        ...


class OpenAICompatibleTripAuditClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def review(self, payload: Dict[str, Any]) -> str:
        if not self.base_url or not self.model or not self.api_key:
            raise ValueError("LLM config is incomplete")
        endpoint = f"{self.base_url}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是差旅报销行程校对助手。只基于用户提供的结构化证据给出简短中文复核意见。",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "invoice-agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        return str(data["choices"][0]["message"]["content"]).strip()


def run_trip_audit(
    records: Iterable[ExpenseRecord],
    policy: Optional[TripAuditPolicy] = None,
    llm_client: Optional[TripAuditLlmClient] = None,
) -> TripAuditResult:
    records = [record for record in records if record.include_in_amount]
    policy = policy or TripAuditPolicy()
    items: List[TripAuditItem] = []
    items.extend(_audit_date_coverage(records))
    items.extend(_audit_route_loop(records))
    items.extend(_audit_lodging(records, policy))
    items.extend(_audit_city_transport(records, policy))
    result = TripAuditResult(items=items)
    if policy.enable_llm_review:
        _apply_llm_review(result, records, policy, llm_client)
    return result


def build_trip_audit_payload(
    records: Iterable[ExpenseRecord],
    policy: TripAuditPolicy,
    items: Iterable[TripAuditItem],
) -> Dict[str, Any]:
    return {
        "policy": {
            "city_transport_daily_limit": policy.city_transport_daily_limit,
            "lodging_daily_limit": policy.lodging_daily_limit,
        },
        "records": [_safe_record_payload(record) for record in records if record.include_in_amount],
        "rule_findings": [item.to_json() for item in items],
    }


def client_from_policy(policy: TripAuditPolicy) -> Optional[OpenAICompatibleTripAuditClient]:
    if not policy.enable_llm_review:
        return None
    api_key = os.environ.get(policy.llm_api_key_env or "", "")
    if not policy.llm_base_url or not policy.llm_model or not api_key:
        return None
    return OpenAICompatibleTripAuditClient(policy.llm_base_url, policy.llm_model, api_key)


def _audit_date_coverage(records: List[ExpenseRecord]) -> List[TripAuditItem]:
    intercity = _intercity_records(records)
    if not records:
        return []
    start_date = records[0].trip_start_date
    end_date = records[0].trip_end_date
    dates = {record.document_date for record in intercity}
    findings = []
    if start_date and start_date not in dates:
        findings.append(
            TripAuditItem(
                category="日期覆盖",
                severity="warning",
                conclusion=f"未发现出差开始日期 {start_date} 对应的出发城际交通票",
                evidence=[_record_brief(record) for record in intercity],
                related_sequences=[record.sequence for record in intercity],
                suggested_action="检查是否遗漏出发高铁/机票等城际交通票据。",
            )
        )
    if end_date and end_date not in dates:
        findings.append(
            TripAuditItem(
                category="日期覆盖",
                severity="warning",
                conclusion=f"未发现出差结束日期对应的返程城际交通票：{end_date}",
                evidence=[_record_brief(record) for record in intercity],
                related_sequences=[record.sequence for record in intercity],
                suggested_action="检查是否遗漏返程高铁/机票等城际交通票据。",
            )
        )
    return findings


def _audit_route_loop(records: List[ExpenseRecord]) -> List[TripAuditItem]:
    intercity = [record for record in _intercity_records(records) if record.origin and record.destination]
    if not intercity:
        return [
            TripAuditItem(
                category="行程闭环",
                severity="warning",
                conclusion="未发现可用于闭环判断的城际交通起止城市",
                suggested_action="检查高铁/机票是否缺失起点或终点。",
            )
        ]
    intercity.sort(key=lambda record: (record.document_date or "9999-99-99", record.sequence))
    home_city = _city_key(intercity[0].origin)
    final_city = _city_key(intercity[-1].destination)
    if home_city and final_city and home_city != final_city:
        return [
            TripAuditItem(
                category="行程闭环",
                severity="warning",
                conclusion=f"最后一段城际交通未返回{home_city}",
                evidence=[
                    f"推断出发城市：{home_city}",
                    f"首段：{_record_brief(intercity[0])}",
                    f"末段：{_record_brief(intercity[-1])}",
                ],
                related_sequences=[intercity[0].sequence, intercity[-1].sequence],
                suggested_action="检查返程票据是否遗漏，或人工修正首尾行程城市。",
            )
        ]
    return []


def _audit_lodging(records: List[ExpenseRecord], policy: TripAuditPolicy) -> List[TripAuditItem]:
    lodging_records = [record for record in records if _is_lodging(record)]
    if not records:
        return []
    expected_nights = _trip_nights(records[0].trip_start_date, records[0].trip_end_date)
    findings = []
    if expected_nights > 0 and not lodging_records:
        findings.append(
            TripAuditItem(
                category="住宿晚数",
                severity="warning",
                conclusion=f"未发现住宿发票，预计需要 {expected_nights} 晚住宿记录",
                suggested_action="检查是否遗漏酒店住宿发票，或确认无需住宿。",
            )
        )
    parsed_nights = sum(_lodging_nights(record) for record in lodging_records)
    if expected_nights > 0 and parsed_nights > 0 and parsed_nights != expected_nights:
        direction = "少于" if parsed_nights < expected_nights else "多于"
        findings.append(
            TripAuditItem(
                category="住宿晚数",
                severity="warning",
                conclusion=f"住宿发票晚数 {parsed_nights} 晚，{direction}预计 {expected_nights} 晚",
                evidence=[_record_brief(record) for record in lodging_records],
                related_sequences=[record.sequence for record in lodging_records],
                suggested_action="检查是否存在遗漏住宿发票、重复住宿发票或住宿天数识别错误。",
            )
        )
    lodging_limit = parse_amount(policy.lodging_daily_limit)
    lodging_total = sum((parse_amount(record.total_with_tax) for record in lodging_records), Decimal("0.00"))
    if lodging_limit > 0 and expected_nights > 0 and lodging_total > lodging_limit * expected_nights:
        allowed = lodging_limit * expected_nights
        findings.append(
            TripAuditItem(
                category="住宿标准",
                severity="warning",
                conclusion=f"住宿费 {lodging_total:.2f} 元，超过 {expected_nights} 晚标准 {allowed:.2f} 元",
                evidence=[_record_brief(record) for record in lodging_records],
                related_sequences=[record.sequence for record in lodging_records],
                suggested_action="核对住宿标准、城市等级或是否包含非住宿费用。",
            )
        )
    return findings


def _audit_city_transport(records: List[ExpenseRecord], policy: TripAuditPolicy) -> List[TripAuditItem]:
    daily_limit = parse_amount(policy.city_transport_daily_limit)
    if daily_limit <= 0:
        return []
    by_date: Dict[str, List[ExpenseRecord]] = {}
    for record in records:
        if not _is_city_transport(record) or not record.document_date:
            continue
        by_date.setdefault(record.document_date, []).append(record)
    findings = []
    for day, day_records in sorted(by_date.items()):
        total = sum((parse_amount(record.total_with_tax) for record in day_records), Decimal("0.00"))
        if total <= daily_limit:
            continue
        findings.append(
            TripAuditItem(
                category="市内交通",
                severity="warning",
                conclusion=f"{day} 市内交通 {total:.2f} 元，超过每日标准 {daily_limit:.2f} 元",
                evidence=[_record_brief(record) for record in day_records],
                related_sequences=[record.sequence for record in day_records],
                suggested_action="检查是否有重复报销、跨日行程或需补充超标说明。",
            )
        )
    return findings


def _apply_llm_review(
    result: TripAuditResult,
    records: List[ExpenseRecord],
    policy: TripAuditPolicy,
    llm_client: Optional[TripAuditLlmClient],
) -> None:
    client = llm_client or client_from_policy(policy)
    if not client:
        result.items.append(
            TripAuditItem(
                category="模型复核",
                severity="info",
                conclusion="模型复核未执行：未配置可用的大模型 API",
                suggested_action="如需模型复核，请配置 OpenAI 兼容 base_url、model 和 API key 环境变量。",
            )
        )
        return
    payload = build_trip_audit_payload(records, policy, result.items)
    try:
        result.llm_review = client.review(payload)
    except Exception as exc:
        result.items.append(
            TripAuditItem(
                category="模型复核",
                severity="info",
                conclusion=f"模型复核未执行：{exc}",
                suggested_action="已保留本地规则校对结果，可稍后重试模型复核。",
            )
        )


def _safe_record_payload(record: ExpenseRecord) -> Dict[str, Any]:
    return {
        "sequence": record.sequence,
        "document_date": record.document_date,
        "document_type": record.document_type,
        "reimbursement_category": record.reimbursement_category,
        "total_with_tax": record.total_with_tax,
        "origin": record.origin,
        "destination": record.destination,
        "description": record.description,
        "seller_name": record.seller_name,
        "include_in_amount": record.include_in_amount,
    }


def _intercity_records(records: Iterable[ExpenseRecord]) -> List[ExpenseRecord]:
    return [
        record
        for record in records
        if record.document_type in {"高铁发票"} or _has_any(_record_text(record), ["机票", "航班", "铁路", "高铁", "火车"])
    ]


def _is_lodging(record: ExpenseRecord) -> bool:
    return record.document_type == "住宿发票" or _has_any(_record_text(record), ["住宿", "酒店", "宾馆", "旅店"])


def _is_city_transport(record: ExpenseRecord) -> bool:
    if record.document_type in {"网约车发票", "出租车票"}:
        return True
    return _has_any(_record_text(record), ["滴滴", "网约车", "出租车", "出租汽车", "地铁", "市区交通"])


def _lodging_nights(record: ExpenseRecord) -> int:
    text = _record_text(record)
    match = re.search(r"(\d{1,2})\s*(?:晚|夜)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,2})\s*天", text)
    if match:
        return int(match.group(1))
    return 0


def _trip_nights(start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return 0
    return max((end - start).days, 0)


def _record_text(record: ExpenseRecord) -> str:
    return " ".join(
        [
            record.document_type,
            record.reimbursement_category,
            record.seller_name,
            record.description,
            record.original_name,
        ]
    )


def _record_brief(record: ExpenseRecord) -> str:
    route = "-".join(part for part in [record.origin, record.destination] if part)
    parts = [
        f"#{record.sequence}",
        record.document_date,
        record.document_type,
        route,
        f"{parse_amount(record.total_with_tax):.2f}元",
    ]
    return " ".join(part for part in parts if part)


def _city_key(value: str) -> str:
    city = re.sub(r"\s+", "", value or "")
    city = re.sub(r"站$", "", city)
    city = re.sub(r"(东|南|西|北)$", "", city)
    for province in [
        "北京",
        "上海",
        "天津",
        "重庆",
        "河北",
        "山西",
        "辽宁",
        "吉林",
        "黑龙江",
        "江苏",
        "浙江",
        "安徽",
        "福建",
        "江西",
        "山东",
        "河南",
        "湖北",
        "湖南",
        "广东",
        "海南",
        "四川",
        "贵州",
        "云南",
        "陕西",
        "甘肃",
        "青海",
    ]:
        if city.startswith(province) and len(city) > len(province):
            city = city[len(province) :]
            break
    return city


def _has_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)
