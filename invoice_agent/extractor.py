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
    "公交地铁票",
    "停车费发票",
    "快递发票",
    "办公发票",
    "材料发票",
    "会议培训发票",
    "租赁发票",
    "专用发票",
    "普票",
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
    if re.search(r"滴滴|网约车|客运服务费", merged):
        return "网约车发票"
    if re.search(r"住宿|酒店|宾馆|旅店", merged):
        return "住宿发票"
    train_no = re.search(r"(?<![A-Za-z0-9])[GD]\d{1,4}(?![A-Za-z0-9])", merged, re.I)
    if re.search(r"火车票|高铁|动车|铁路电子客票|铁路", merged, re.I) or train_no:
        return "高铁发票"
    if re.search(r"出租车|出租汽车", merged):
        return "出租车票"
    if re.search(r"地铁|公交|公共汽车|巴士", merged):
        return "公交地铁票"
    if re.search(r"停车费|停车服务", merged):
        return "停车费发票"
    if re.search(r"通行费|高速|路网", merged):
        return "通行费发票"
    if re.search(r"餐饮|饭店|餐厅", merged):
        return "餐饮发票"
    if re.search(r"快递|配送", merged):
        return "快递发票"
    if re.search(r"打印|文印|复印|办公用品|文具", merged):
        return "办公发票"
    if re.search(r"材料|配件|设备", merged):
        return "材料发票"
    if re.search(r"会议|培训", merged):
        return "会议培训发票"
    if re.search(r"租赁", merged):
        return "租赁发票"
    if re.search(r"专用发票", merged):
        return "专用发票"
    if re.search(r"增值税|发票号码|发票代码|电子发票", merged):
        return "普票"
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}:
        return "纸质拍照发票"
    return "其他/无法识别"


def extract_fields_from_text(text: str, path: Path) -> Dict[str, Any]:
    raw = text or ""
    cleaned = clean_text(raw)
    fields: Dict[str, Any] = {
        "document_type": detect_document_type(raw, path),
        "issue_date": "",
        "travel_date": "",
        "travel_dates": [],
        "sub_trips": [],
        "invoice_number": "",
        "invoice_code": "",
        "seller_name": "",
        "buyer_name": "",
        "total_amount": "",
        "total_tax": "",
        "total_with_tax": "",
        "origin": "",
        "destination": "",
        "train_departure_time": "",
        "refund_fee": "",
        "change_fee": "",
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
            r"乘车日期[：:]\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
            cleaned,
        ) or re.search(
            r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)\s+(?:\d{1,2}:\d{2}\s*)?(?:开|乘车)",
            cleaned,
        )
        if travel_date:
            fields["travel_date"] = travel_date.group(1)
            fields["travel_dates"].append(normalize_date(travel_date.group(1)))
        departure_time = re.search(
            r"(?:20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?\s*)?(\d{1,2}:\d{2})\s*开",
            cleaned,
        )
        if departure_time:
            fields["train_departure_time"] = departure_time.group(1)

    # 提取网约车/出租车发票与行程单中的出行日期与子行程
    didi_dates, didi_orig, didi_dest, didi_subs = extract_didi_table_info(raw)
    if didi_dates:
        fields["travel_dates"].extend(didi_dates)
        fields["sub_trips"].extend(didi_subs)
        if not fields["travel_date"]:
            fields["travel_date"] = didi_dates[0]
        if didi_orig and not fields["origin"]:
            fields["origin"] = didi_orig
        if didi_dest and not fields["destination"]:
            fields["destination"] = didi_dest

    # 提取机票/航空/旅行社发票备注与正文中的航班出行日期与航程
    flight_dates, flight_orig, flight_dest, flight_subs = extract_flight_info_from_text(raw, cleaned)
    if flight_dates:
        fields["travel_dates"].extend(flight_dates)
        fields["sub_trips"].extend(flight_subs)
        if not fields["travel_date"]:
            fields["travel_date"] = flight_dates[0]
        if flight_orig and not fields["origin"]:
            fields["origin"] = flight_orig
        if flight_dest and not fields["destination"]:
            fields["destination"] = flight_dest

    # 提取行程单起止日期
    itinerary_range = re.search(
        r"行程起止日期[：:]\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2})\s*至\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2})",
        cleaned,
    )
    if itinerary_range:
        d_start = normalize_date(itinerary_range.group(1))
        d_end = normalize_date(itinerary_range.group(2))
        if d_start not in fields["travel_dates"]:
            fields["travel_dates"].append(d_start)
        if d_end not in fields["travel_dates"]:
            fields["travel_dates"].append(d_end)
        if not fields["travel_date"]:
            fields["travel_date"] = d_start

    # 提取住宿发票入离日期
    lodging_match = re.search(
        r"(?:入住[：:]?\s*|入离[：:]?\s*|入离日期[：:]?\s*)(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2})",
        cleaned,
    )
    if lodging_match:
        lodging_d = normalize_date(lodging_match.group(1))
        if lodging_d not in fields["travel_dates"]:
            fields["travel_dates"].append(lodging_d)
        if not fields["travel_date"]:
            fields["travel_date"] = lodging_d

    # 去重并排序出行日期
    fields["travel_dates"] = sorted(list(dict.fromkeys(fields["travel_dates"])))

    if not fields["issue_date"]:
        date_match = re.search(r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}|20\d{6})", cleaned)
        if date_match:
            fields["issue_date"] = date_match.group(1)
    fields["issue_date"] = normalize_date(fields["issue_date"])
    fields["travel_date"] = normalize_date(fields["travel_date"])

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
        ticket_fee = extract_labeled_amount(cleaned, "票价")
        refund_fee = extract_labeled_amount(cleaned, "退票费")
        change_fee = extract_labeled_amount(cleaned, "改签费")
        fields["refund_fee"] = refund_fee
        fields["change_fee"] = change_fee
        if refund_fee and "退票" in cleaned:
            fields["total_with_tax"] = refund_fee
        elif change_fee and "改签" in cleaned:
            fields["total_with_tax"] = change_fee
        elif ticket_fee:
            fields["total_with_tax"] = ticket_fee
        elif refund_fee:
            fields["total_with_tax"] = refund_fee
        elif change_fee:
            fields["total_with_tax"] = change_fee

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


def extract_labeled_amount(cleaned: str, label: str) -> str:
    match = re.search(rf"{label}[：:]?\s*[¥￥]?\s*([\d,]+\.\d{{2}})", cleaned)
    return match.group(1).replace(",", "") if match else ""


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
    clean_stem = re.sub(r"【.*?】", "", stem).strip()
    clean_stem = re.sub(r"^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}\s*", "", clean_stem)

    if "→" in clean_stem:
        left, right = clean_stem.split("→", 1)
        left = re.split(r"[\s_-]+", left.strip())[-1]
        right = re.split(r"[\s_¥￥]+", right.strip())[0]
        left = re.sub(r"^(?:火车票|退票费|高铁票|行程单)", "", left)
        right = re.sub(r"(?:火车票|退票费|高铁票|行程单)$", "", right)
        return left.strip(), right.strip()

    # 仅当文件名包含交通相关关键词（如机票、飞猪、携程、航班、航线等）时支持连字符路线
    if any(k in stem for k in ["机票", "飞猪", "携程", "航班", "航线", "客票", "车票"]):
        segments = re.findall(r"([\u4e00-\u9fa5]{2,6})-([\u4e00-\u9fa5]{2,6})", clean_stem)
        non_cities = {"餐饮", "服务", "发票", "凭证", "订单", "机票", "行程", "报销", "普通", "专票", "普票"}
        valid = [
            (s1, s2)
            for s1, s2 in segments
            if not any(nc in s1 or nc in s2 for nc in non_cities)
        ]
        if len(valid) >= 2 and valid[0][1] == valid[1][0]:
            return valid[0][0], valid[1][1]
        elif len(valid) == 1:
            return valid[0][0], valid[0][1]
    return "", ""


def extract_flight_info_from_text(raw: str, cleaned: str) -> Tuple[List[str], str, str, List[Dict[str, Any]]]:
    travel_dates = []
    sub_trips = []
    flight_matches = re.findall(
        r"(\d{4}[/.-]\d{1,2}[/.-]\d{1,2})\s+([\u4e00-\u9fa5A-Za-z0-9·-]+)-([\u4e00-\u9fa5A-Za-z0-9·-]+)\s+([A-Za-z0-9]{2}\d{3,4})",
        raw,
    )
    if flight_matches:
        for d, o, dest, f_no in flight_matches:
            norm_d = normalize_date(d)
            travel_dates.append(norm_d)
            sub_trips.append({
                "date": norm_d,
                "origin": o,
                "destination": dest,
                "flight": f_no,
                "transport": "机票",
            })
        if len(flight_matches) >= 2 and flight_matches[0][2] == flight_matches[1][1]:
            origin = flight_matches[0][1]
            destination = flight_matches[1][2]
        else:
            origin = flight_matches[0][1]
            destination = flight_matches[0][2]
        return travel_dates, origin, destination, sub_trips
    return [], "", "", []


def extract_didi_table_info(raw: str) -> Tuple[List[str], str, str, List[Dict[str, Any]]]:
    travel_dates = []
    sub_trips = []
    table_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.DOTALL)
    for r in table_rows:
        cells = [clean_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
        for i, c in enumerate(cells):
            d_m = re.fullmatch(r"20\d{2}-\d{2}-\d{2}", c)
            if d_m:
                d = d_m.group(0)
                travel_dates.append(d)
                orig = cells[i + 1].replace("\\n", " ").replace("\n", " ").strip() if i + 1 < len(cells) else ""
                dest = cells[i + 2].replace("\\n", " ").replace("\n", " ").strip() if i + 2 < len(cells) else ""
                trans = cells[i + 3].strip() if i + 3 < len(cells) and cells[i + 3].strip() else "出租车"
                sub_trips.append({
                    "date": d,
                    "origin": orig,
                    "destination": dest,
                    "transport": trans,
                })
                break
        if len(cells) >= 8 and re.match(r"^\d+$", cells[0]):
            time_str = cells[2]
            tm = re.search(r"(\d{2}-\d{2})", time_str)
            if tm:
                y_m = re.search(r"(20\d{2})", raw)
                year = y_m.group(1) if y_m else "2026"
                norm_d = f"{year}-{tm.group(1)}"
                travel_dates.append(norm_d)
                sub_trips.append({
                    "date": norm_d,
                    "origin": cells[4].replace("\\n", " ").replace("\n", " ").strip(),
                    "destination": cells[5].replace("\\n", " ").replace("\n", " ").strip(),
                    "amount": cells[7].strip(),
                    "transport": cells[1] or "出租车",
                })
    origin = sub_trips[0]["origin"] if sub_trips else ""
    destination = sub_trips[-1]["destination"] if sub_trips else ""
    return travel_dates, origin, destination, sub_trips


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
