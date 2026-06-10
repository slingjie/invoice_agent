from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


INVOICE_TYPES = {
    "住宿发票",
    "高铁发票",
    "网约车发票",
    "出租车票",
    "通行费发票",
    "餐饮发票",
    "普通/专用发票",
    "纸质拍照发票",
}


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def clean_text(text: str) -> str:
    cleaned = html.unescape(strip_html(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def clean_multiline_text(text: str) -> str:
    cleaned = html.unescape(strip_html(text or ""))
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_amount(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).replace(",", "").replace("¥", "").replace("￥", "").strip()
    if not text:
        return Decimal("0")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0")


def normalize_date(text: str) -> str:
    text = (text or "").strip()
    match = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return text


def split_name_taxid(cell_text: str) -> Tuple[str, str]:
    text = clean_text(cell_text)
    text = re.sub(r"^.*?名称[：:]\s*", "", text)
    match = re.search(r"([\dA-Za-z]{10,})\s*$", text)
    if not match:
        return text.strip(), ""
    tax_id = match.group(1)
    name = re.sub(
        r"(?:统一社会信用代码|纳税人识别号)[/：:]*\s*[\dA-Za-z]{10,}\s*$",
        "",
        text,
    ).strip()
    name = re.sub(r"(?:统一社会信用代码|纳税人识别号)[/：:]*\s*$", "", name).strip()
    return name, tax_id


def detect_document_type(text: str, path: Path) -> str:
    merged = f"{path.name} {clean_text(text)}"
    if "行程报销单" in merged or "行程单" in merged:
        return "行程单"
    if re.search(r"住宿|酒店|宾馆|旅店", merged):
        return "住宿发票"
    train_no = re.search(r"(?<![A-Za-z0-9])[GD]\d{1,4}(?![A-Za-z0-9])", merged, re.I)
    if re.search(r"火车票|高铁|动车|铁路电子客票|铁路", merged, re.I) or train_no:
        return "高铁发票"
    if re.search(r"滴滴|网约车|客运服务费", merged):
        return "网约车发票"
    if re.search(r"出租车|出租汽车", merged):
        return "出租车票"
    if re.search(r"通行费|高速|路网", merged):
        return "通行费发票"
    if re.search(r"餐饮|饮品|食品|饭店|餐厅", merged):
        return "餐饮发票"
    if re.search(r"增值税|发票号码|发票代码|电子发票", merged):
        return "普通/专用发票"
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}:
        return "纸质拍照发票"
    return "其他/无法识别"


def extract_fields_from_text(text: str, path: Path) -> Dict[str, Any]:
    raw = text or ""
    cleaned = clean_text(raw)
    fields: Dict[str, Any] = {
        "document_type": detect_document_type(raw, path),
        "issue_date": "",
        "invoice_number": "",
        "invoice_code": "",
        "seller_name": "",
        "buyer_name": "",
        "total_amount": "",
        "total_tax": "",
        "total_with_tax": "",
        "origin": "",
        "destination": "",
        "description": "",
    }

    simple_patterns = {
        "invoice_number": r"发票号码[：:]\s*([A-Za-z0-9]+)",
        "invoice_code": r"发票代码[：:]\s*([A-Za-z0-9]+)",
        "issue_date": r"(?:开票日期|乘车日期|日期)[：:]\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2})",
    }
    for key, pattern in simple_patterns.items():
        match = re.search(pattern, cleaned)
        if match:
            fields[key] = match.group(1).strip()

    if fields["document_type"] == "高铁发票":
        travel_date = re.search(
            r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)\s+\d{1,2}:\d{2}\s*开",
            cleaned,
        )
        if travel_date:
            fields["issue_date"] = travel_date.group(1)

    if not fields["issue_date"]:
        date_match = re.search(r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}|20\d{6})", cleaned)
        if date_match:
            fields["issue_date"] = date_match.group(1)
    fields["issue_date"] = normalize_date(fields["issue_date"])

    buyer_cell = re.search(r"购买方.*?</td>\s*<td[^>]*>(.*?)</td>", raw, re.DOTALL)
    seller_cell = re.search(r"销售方.*?</td>\s*<td[^>]*>(.*?)</td>", raw, re.DOTALL)
    if buyer_cell:
        fields["buyer_name"], _ = split_name_taxid(buyer_cell.group(1))
    if seller_cell:
        fields["seller_name"], _ = split_name_taxid(seller_cell.group(1))
    if not fields["buyer_name"]:
        fields["buyer_name"] = extract_party_name(cleaned, "购买方")
    if not fields["seller_name"]:
        fields["seller_name"] = extract_party_name(cleaned, "销售方")
    if fields["document_type"] == "高铁发票" and not fields["seller_name"]:
        fields["seller_name"] = "铁路电子客票"

    total_with_tax = re.search(r"价税合计.*?(?:小写)?\s*[¥￥]?\s*([\d,]+\.\d{2})", cleaned)
    if not total_with_tax:
        total_with_tax = re.search(r"(?:总金额|合计金额|金额)[：:]?\s*[¥￥]?\s*([\d,]+\.\d{2})", cleaned)
    if total_with_tax:
        fields["total_with_tax"] = total_with_tax.group(1).replace(",", "")

    if fields["document_type"] == "高铁发票":
        train_amount = re.search(r"(?:票价|退票费)[：:]?\s*[¥￥]\s*([\d,]+\.\d{2})", cleaned)
        if train_amount:
            fields["total_with_tax"] = train_amount.group(1).replace(",", "")

    if fields["document_type"] == "行程单":
        itinerary_amount = re.search(
            r"(?:共\d+笔行程，?合计|累计金额（元）|累计金额\(元\)|总金额)[：:]?\s*([¥￥]?\s*[\d,]+\.\d{2})\s*元?",
            cleaned,
        )
        if itinerary_amount:
            fields["total_with_tax"] = itinerary_amount.group(1).replace("¥", "").replace("￥", "").replace(",", "").strip()
        origin, destination = extract_itinerary_route(raw)
        if origin and destination:
            fields["origin"] = origin
            fields["destination"] = destination

    total_line = re.search(r"合\s*计.*?[¥￥]\s*([\d,]+\.\d{2}).*?[¥￥]\s*([\d,]+\.\d{2})", cleaned)
    if total_line:
        fields["total_amount"] = total_line.group(1).replace(",", "")
        fields["total_tax"] = total_line.group(2).replace(",", "")

    origin, destination = extract_route_from_filename(path.stem)
    if origin and destination:
        fields["origin"] = origin
        fields["destination"] = destination
    elif fields["document_type"] == "高铁发票":
        origin, destination = extract_train_route(raw, cleaned)
        if origin and destination:
            fields["origin"] = origin
            fields["destination"] = destination

    fields["description"] = infer_description(fields, path)
    return fields


def extract_party_name(cleaned: str, party_label: str) -> str:
    patterns = [
        rf"{party_label}名称[：:]\s*(.+?)(?:统一社会信用代码|纳税人识别号|销售方信息|购买方信息|项目名称|$)",
        rf"{party_label}信息\s*名称[：:]\s*(.+?)(?:统一社会信用代码|纳税人识别号|销售方信息|购买方信息|项目名称|$)",
        rf"{party_label}信息\s+名称[：:]\s*(.+?)(?:统一社会信用代码|纳税人识别号|销售方信息|购买方信息|项目名称|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            return clean_party_name(match.group(1))
    return ""


def clean_party_name(value: str) -> str:
    value = re.sub(r"(?:统一社会信用代码|纳税人识别号).*", "", value)
    return value.strip(" ：:/")


def extract_train_route(raw: str, cleaned: str) -> Tuple[str, str]:
    station = r"([\u4e00-\u9fa5A-Za-z0-9（）()·-]{2,24}站)"
    match = re.search(
        rf"开票日期[：:].*?{station}.*?\b[GDCKZT]\d{{1,5}}\b.*?{station}\s+20\d{{2}}[年./-]\d{{1,2}}[月./-]\d{{1,2}}",
        cleaned,
        re.I,
    )
    if match:
        return match.group(1), match.group(2)
    lines = clean_multiline_text(raw).splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(r"[GDCKZT]\d{1,5}", line, flags=re.I):
            before = _nearest_station(lines[:index], reverse=True)
            after = _nearest_station(lines[index + 1 :])
            if before and after:
                return before, after
    return "", ""


def _nearest_station(lines, reverse: bool = False) -> str:
    iterable = reversed(lines) if reverse else lines
    for line in iterable:
        if re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9（）()·-]{2,24}站", line):
            return line
    return ""


def extract_itinerary_route(raw: str) -> Tuple[str, str]:
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw or "", re.DOTALL):
        cells = [
            clean_text(cell)
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        if len(cells) >= 6 and cells[0].isdigit():
            return cells[4], cells[5]
    return "", ""


def extract_route_from_filename(stem: str) -> Tuple[str, str]:
    if "→" not in stem:
        return "", ""
    left, right = stem.split("→", 1)
    left = re.split(r"[\s_-]+", left.strip())[-1]
    right = re.split(r"[\s_¥￥]+", right.strip())[0]
    left = re.sub(r"^(?:火车票|退票费|高铁票|行程单)", "", left)
    right = re.sub(r"(?:火车票|退票费|高铁票|行程单)$", "", right)
    return left.strip(), right.strip()


def infer_description(fields: Dict[str, Any], path: Path) -> str:
    route = "-".join([x for x in [fields.get("origin"), fields.get("destination")] if x])
    if route:
        return route
    seller = str(fields.get("seller_name") or "").strip()
    if seller:
        return seller
    return path.stem[:40]


def is_invoice_type(document_type: str) -> bool:
    return document_type in INVOICE_TYPES
