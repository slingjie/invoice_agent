from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, List, Tuple

from .extractor import parse_amount
from .models import ExpenseRecord


MEAL_ALLOWANCE_CATEGORY = "出差餐补"
REIMBURSEMENT_CATEGORIES = [
    "行程交通费",
    "住宿费",
    "市区交通费",
    "通行费",
    "过路费",
    "油费",
    "退改费",
    "其他费用",
    MEAL_ALLOWANCE_CATEGORY,
]

HIGH_LEVEL_CATEGORY_MAP = {
    "住宿费": "差旅费",
    "行程交通费": "交通费",
    "通行费": "交通费",
    "过路费": "交通费",
    "油费": "交通费",
    "退改费": "交通费",
    "市区交通费": "交通费",
}

HIGH_LEVEL_CATEGORY_KEYWORDS: list[tuple[list[str], str]] = [
    (["餐饮", "吃饭", "餐厅", "美食", "饮品", "食品", "饭店", "娱乐", "KTV", "休闲", "咖啡"], "招待费"),
    (["打印", "配送", "文印", "晒图", "复印", "印刷", "设备", "材料", "文具", "办公用品"], "材料费"),
]


def assign_reimbursement_category(record: ExpenseRecord) -> str:
    text = " ".join(
        [
            record.document_type,
            record.seller_name,
            record.description,
            record.original_name,
            record.raw_text,
        ]
    )
    if record.document_type == "行程单":
        if _has_any(text, ["滴滴", "网约车", "出租车", "出租汽车", "地铁"]):
            return "通行费"
        if _has_any(text, ["高铁", "铁路", "客票", "机票", "航班"]):
            return "行程交通费"
        return "通行费"
    if _has_any(text, ["退票", "改签", "退改", "退改签"]) and _has_any(text, ["高铁", "铁路", "客票", "火车", "机票", "航班"]):
        return "退改费"
    if _has_any(text, ["滴滴", "网约车", "出租车", "出租汽车", "地铁"]):
        return "通行费"
    if _has_any(text, ["住宿", "酒店", "宾馆", "旅店"]):
        return "住宿费"
    if _has_any(text, ["油费", "加油", "石油", "石化", "中石油", "中石化"]):
        return "油费"
    if _has_any(text, ["高速", "过路", "路桥", "路网", "车辆通行费", "通行费发票"]):
        return "过路费"
    if _has_any(text, ["市区交通"]):
        return "市区交通费"
    if _has_any(text, ["高铁", "铁路", "客票", "火车", "机票", "航班"]):
        return "行程交通费"
    return "其他费用"


def compute_high_level_category(record: ExpenseRecord) -> str:
    fine = record.reimbursement_category
    if fine in HIGH_LEVEL_CATEGORY_MAP:
        return HIGH_LEVEL_CATEGORY_MAP[fine]
    text = " ".join(
        [record.document_type, record.seller_name, record.description, record.original_name, record.raw_text]
    )
    for keywords, category in HIGH_LEVEL_CATEGORY_KEYWORDS:
        if _has_any(text, keywords):
            return category
    return "其他"


def assign_reimbursement_categories(records: Iterable[ExpenseRecord]) -> None:
    for record in records:
        record.reimbursement_category = assign_reimbursement_category(record)
        record.high_level_category = compute_high_level_category(record)


def reimbursement_summary_rows(records: Iterable[ExpenseRecord]) -> List[Tuple[str, Decimal, int]]:
    records = list(records)
    summary = {category: [Decimal("0.00"), 0] for category in REIMBURSEMENT_CATEGORIES}
    for record in records:
        if not record.include_in_amount:
            continue
        category = record.reimbursement_category or assign_reimbursement_category(record)
        if category not in summary:
            category = "其他费用"
        summary[category][0] += parse_amount(record.total_with_tax)
        summary[category][1] += 1
    meal_total, meal_days = meal_allowance_total(records)
    summary[MEAL_ALLOWANCE_CATEGORY][0] = meal_total
    summary[MEAL_ALLOWANCE_CATEGORY][1] = 0
    return [(category, total, count) for category, (total, count) in summary.items()]


def meal_allowance_total(records: Iterable[ExpenseRecord]) -> Tuple[Decimal, int]:
    for record in records:
        daily_amount = parse_amount(record.daily_meal_allowance)
        days = _inclusive_trip_days(record.trip_start_date, record.trip_end_date)
        if daily_amount > 0 and days > 0:
            return daily_amount * days, days
    return Decimal("0.00"), 0


def _inclusive_trip_days(start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return 0
    days = (end - start).days + 1
    return max(days, 0)


def _has_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)
