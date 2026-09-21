from __future__ import annotations

import logging
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from .extractor import (
    clean_text,
    detect_document_type,
    extract_route_from_filename,
    is_invoice_type,
    normalize_date,
    parse_amount,
)
from .models import ParsedDocument

logger = logging.getLogger(__name__)

OCR_COMPAT_TRANSLATION = str.maketrans({
    "⻔": "门",
    "⻝": "食",
    "⻨": "麦",
    "⻆": "角",
})


def normalize_pdf_text(text: str) -> str:
    """标准 Unicode NFKC 归一化，修复康熙部首与变体字编码（如电⼦发票 -> 电子发票）。"""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text).translate(OCR_COMPAT_TRANSLATION)
    return norm.replace("\xa0", " ").replace("\u3000", " ")


def extract_pdf_embedded_text(path: Path, max_pages: int = 2) -> str:
    """使用 PyMuPDF (fitz) 高速读取 PDF 的嵌入式文本层，失败时自动退避到 pypdf。"""
    if not path or not path.exists():
        return ""
    try:
        # 预先校验文件大小与 PDF 魔数头，避免非标准测试 mock 文件引发底层 C 库异常
        if path.stat().st_size < 100:
            return ""
        with open(path, "rb") as f:
            header = f.read(5)
            if not header.startswith(b"%PDF"):
                return ""

        with fitz.open(path) as doc:
            if doc.is_closed or doc.is_encrypted:
                return ""
            parts = []
            for i in range(min(len(doc), max_pages)):
                parts.append(doc.load_page(i).get_text("text", sort=True) or "")
            text = "\n".join(part for part in parts if part).strip()
            if text:
                return normalize_pdf_text(text)
    except Exception as exc:
        logger.debug("PyMuPDF failed to extract text from %s: %s", path.name, exc)

    # pypdf 备用降级
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for i in range(min(len(reader.pages), max_pages)):
            parts.append(reader.pages[i].extract_text() or "")
        return normalize_pdf_text("\n".join(parts).strip())
    except Exception as exc:
        logger.debug("pypdf failed to extract text from %s: %s", path.name, exc)

    return ""


def extract_parties_from_text(text: str) -> Tuple[str, str]:
    """从文本行中提取购买方和销售方单位名称。"""
    # 优先使用带“名称/户名”标签的精准提取（适配双栏排序文本）
    labeled_matches = re.findall(
        r"(?:名称|户名)[：:\s]*([^\s\n\r]+(?:公司|酒店|店|局|网|厂|部|中心|行|站|社))",
        text,
    )
    companies: List[str] = []
    for c in labeled_matches:
        c_clean = re.sub(r"^[^\u4e00-\u9fa5A-Za-z0-9]+", "", c).strip()
        if len(c_clean) >= 4 and c_clean not in companies and not any(k in c_clean for k in ["税务局", "发票", "代码"]):
            companies.append(c_clean)

    if not companies:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for l in lines:
            if any(w in l for w in ["公司", "酒店", "店", "局", "网", "厂", "部", "中心", "行", "站", "社"]):
                if not any(
                    nc in l
                    for nc in [
                        "统一社会信用代码",
                        "纳税人识别号",
                        "发票",
                        "国家税务总局",
                        "中国铁路",
                        "购买方",
                        "销售方",
                        "开票人",
                        "收款人",
                        "复核",
                        "下载次数",
                        "地址",
                        "电话",
                        "开户行",
                    ]
                ):
                    l_clean = re.sub(r"^(?:名称[：:\s]*|户名[：:\s]*)", "", l).strip()
                    if len(l_clean) >= 4 and l_clean not in companies and not re.fullmatch(r"[0-9A-Za-z]+", l_clean):
                        companies.append(l_clean)

    buyer = next((c for c in companies if "勤合" in c), companies[0] if companies else "")
    seller = next((c for c in companies if c != buyer), "")
    return buyer, seller


def extract_max_invoice_amount(text: str) -> str:
    """
    提取中国发票中的价税合计金额。
    收集所有合法金额候选（关键字附近与全文候选），并使用 Decimal 精度比较，
    防止局部正则提前命中明细金额或浮点精度损失。
    """
    keyword_candidates: List[Tuple[Decimal, str]] = []

    # 1. 优先收集在（小写）、小写、价税合计附近的候选金额
    for m in re.finditer(
        r"(?:[（(]小写[）)]|小写|价\s*税\s*合\s*计)[^\d¥￥]{0,30}[¥￥]?\s*([0-9]+(?:\.[0-9]{2})?)\b",
        text,
    ):
        raw = m.group(1)
        try:
            val = Decimal(raw)
            if Decimal("0.01") <= val <= Decimal("5000000.00"):
                keyword_candidates.append((val, f"{val:.2f}"))
        except (InvalidOperation, TypeError):
            continue

    if keyword_candidates:
        return max(keyword_candidates, key=lambda x: x[0])[1]

    # 2. 全文金额候选 fallback
    matches = re.findall(r"[¥￥]?\s*([0-9]+\.[0-9]{2})\b", text)
    valid: List[Tuple[Decimal, str]] = []
    year_numbers = {Decimal(f"{y}.00") for y in range(2020, 2035)}
    for m in matches:
        try:
            val = Decimal(m)
            if Decimal("0.01") <= val <= Decimal("5000000.00") and val not in year_numbers:
                valid.append((val, f"{val:.2f}"))
        except (InvalidOperation, TypeError):
            continue
    if valid:
        return max(valid, key=lambda x: x[0])[1]
    return ""


def probe_train_ticket(text: str, path: Path) -> Optional[Dict[str, Any]]:
    """12306 铁路电子客票（高铁/动车/普速客票）极速探针。"""
    if "铁路电子客票" not in text and "电子客票号" not in text:
        return None

    inv_match = re.search(r"发票号码[：:\s]*([0-9]{10,24})", text)
    if not inv_match:
        return None
    invoice_number = inv_match.group(1).strip()

    date_match = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日)\s*\d{1,2}:\d{2}开", text)
    if not date_match:
        date_match = re.search(r"乘车日期[：:\s]*(20\d{2}年\d{1,2}月\d{1,2}日)", text)
    travel_date = normalize_date(date_match.group(1)) if date_match else ""

    issue_match = re.search(r"开票日期[：:\s]*(20\d{2}年\d{1,2}月\d{1,2}日)", text)
    issue_date = normalize_date(issue_match.group(1)) if issue_match else travel_date

    fare_match = re.search(r"[¥￥]\s*([0-9]+(?:\.[0-9]{2})?)", text)
    amount = fare_match.group(1) if fare_match else ""

    route_match = re.search(r"([^\s]+站)\s+([GDCKZT]\d{1,5})\s+([^\s]+站)", text)
    if route_match:
        origin = route_match.group(1).strip()
        train_no = route_match.group(2).strip()
        destination = route_match.group(3).strip()
    else:
        stations = re.findall(r"([\u4e00-\u9fa5A-Za-z0-9·-]{2,12}站)", text)
        origin = stations[0] if len(stations) >= 1 else ""
        destination = stations[1] if len(stations) >= 2 else ""
        train_match = re.search(r"\b([GDCKZT]\d{1,5})\b", text)
        train_no = train_match.group(1) if train_match else ""

    buyer_match = re.search(r"购买方名称[：:\s]*([^\n\r]+)", text)
    buyer = buyer_match.group(1).strip() if buyer_match else ""
    if buyer:
        buyer = re.split(r"(?:统一社会信用代码|纳税人识别号|地址|电话|开户行)", buyer)[0].strip()
        buyer = buyer.strip(":： ")

    time_match = re.search(r"(\d{1,2}:\d{2})\s*开", text)
    departure_time = time_match.group(1) if time_match else ""

    desc = f"{origin}-{destination}" if origin and destination else "铁路电子客票"
    if train_no:
        desc += f" {train_no}"

    return {
        "document_type": "高铁发票",
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "travel_date": travel_date,
        "travel_dates": [travel_date] if travel_date else [],
        "sub_trips": [
            {
                "date": travel_date,
                "origin": origin,
                "destination": destination,
                "flight": train_no,
                "amount": amount,
                "transport": "火车",
            }
        ] if travel_date else [],
        "total_with_tax": amount,
        "origin": origin,
        "destination": destination,
        "train_departure_time": departure_time,
        "buyer_name": buyer,
        "seller_name": "中国国家铁路集团有限公司",
        "description": desc,
    }


def probe_didi_and_ride(text: str, path: Path) -> Optional[Dict[str, Any]]:
    """滴滴电子发票、网约车行程报销单、美团跑腿行程单探针。"""
    is_didi_invoice = "滴滴" in text and ("电子发票" in text or "旅客运输服务" in text)
    is_itinerary = "行程单" in text or "行程报销单" in text or "DIDI TRAVEL" in text.upper() or "高德地图" in text or "美团跑腿" in text

    if not is_didi_invoice and not is_itinerary:
        return None

    if is_itinerary:
        # 行程单解析
        amt_match = (
            re.search(r"(?:合计|订单总金额|金额)[:：\s]*([0-9]+(?:\.[0-9]{1,2})?)\s*元", text)
            or re.search(r"合计\s*[¥￥]?\s*([0-9]+\.[0-9]{2})", text)
        )
        amount = f"{float(amt_match.group(1)):.2f}" if amt_match else ""

        range_match = re.search(r"行程(?:起止)?(?:时间|日期)[:：]?\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})\s*至\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})", text)
        travel_dates = []
        if range_match:
            d1 = normalize_date(range_match.group(1))
            d2 = normalize_date(range_match.group(2))
            travel_dates.extend([d1, d2])

        # 提取行程单中的所有单笔金额（排除合计金额）
        total_m = re.search(r"(?:合计|订单总金额)[:：\s]*([0-9]+\.[0-9]{2})", text)
        total_amt = total_m.group(1) if total_m else ""
        item_amounts = [m for m in re.findall(r"\b([0-9]+\.[0-9]{2})\b", text) if m != total_amt]

        # 逐项建立 sub_trips 并提取起点终点与金额
        sub_trips = []
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        header_idx = -1
        for i, l in enumerate(lines):
            if ("起点" in l and "终点" in l) or "里程" in l:
                header_idx = i
                break

        y_m = re.search(r"(20\d{2})", text)
        year = y_m.group(1) if y_m else "2026"

        if header_idx != -1:
            cur_seq = 1
            for i in range(header_idx + 1, len(lines)):
                if lines[i] == str(cur_seq):
                    next_idx = -1
                    for j in range(i + 1, len(lines)):
                        if lines[j] == str(cur_seq + 1) or "页码" in lines[j] or "合计" in lines[j]:
                            next_idx = j
                            break
                    block = lines[i:next_idx] if next_idx != -1 else lines[i:]
                    cur_seq += 1

                    # 日期
                    t_date = ""
                    for item in block:
                        dm = re.search(r"(\d{2}-\d{2})", item)
                        if dm:
                            t_date = f"{year}-{dm.group(1)}"
                            break
                    if not t_date and travel_dates:
                        t_date = travel_dates[min(cur_seq - 2, len(travel_dates) - 1)]

                    # 金额
                    t_amt = ""
                    for item in reversed(block):
                        if re.fullmatch(r"[0-9]+\.[0-9]{2}", item):
                            t_amt = item
                            break

                    # 地址提取
                    addr_candidates = []
                    for item in block:
                        if any(k in item for k in ["|", "路", "街", "站", "门", "号", "区", "机场", "中心", "园", "寓", "大厦"]):
                            if not re.search(r"^\d+$", item) and "周" not in item and "车" not in item and len(item) >= 3:
                                addr_candidates.append(item)
                    orig = addr_candidates[0] if len(addr_candidates) >= 1 else ""
                    dest = addr_candidates[-1] if len(addr_candidates) >= 2 else ""

                    sub_trips.append({
                        "date": t_date,
                        "origin": orig,
                        "destination": dest,
                        "amount": t_amt,
                        "transport": "出租车",
                    })

        if not sub_trips:
            for idx, td in enumerate(travel_dates):
                amt = item_amounts[idx] if idx < len(item_amounts) else ""
                sub_trips.append({
                    "date": td,
                    "origin": "",
                    "destination": "",
                    "amount": amt,
                    "transport": "出租车",
                })

        travel_dates = sorted(list(dict.fromkeys(travel_dates)))
        d_found = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}", text)
        primary_date = travel_dates[0] if travel_dates else (normalize_date(d_found.group(0)) if d_found else "")

        seller_name = "滴滴出行"
        if "高德" in text:
            seller_name = "高德地图"
        elif "美团" in text:
            seller_name = "美团跑腿"

        return {
            "document_type": "行程单",
            "invoice_number": "",
            "issue_date": primary_date,
            "travel_date": primary_date,
            "travel_dates": travel_dates,
            "sub_trips": sub_trips,
            "total_with_tax": amount,
            "buyer_name": "",
            "seller_name": seller_name,
            "description": path.stem[:40],
        }

    # 滴滴电子发票解析
    inv_match = re.search(r"发票号码[:：\s]*([0-9]{8,})", text)
    date_match = re.search(r"开票日期[:：\s]*(20\d{2}年\d{1,2}月\d{1,2}日)", text)
    amt_match = (
        re.search(r"（小写）\s*¥?\s*([0-9]+\.[0-9]{2})", text)
        or re.search(r"价\s*税\s*合\s*计.*?([0-9]+\.[0-9]{2})\s*¥?", text)
        or re.search(r"([0-9]+\.[0-9]{2})¥?壹", text)
    )

    if not inv_match or not date_match:
        return None

    invoice_number = inv_match.group(1).strip()
    issue_date = normalize_date(date_match.group(1))
    amount = amt_match.group(1) if amt_match else extract_max_invoice_amount(text)

    buyer, seller = extract_parties_from_text(text)
    if not seller:
        seller = "杭州滴滴出行科技有限公司"

    # 提取出行日期与子行程列表
    travel_dates = []
    sub_trips = []
    trip_date_matches = re.findall(r"(20\d{2}-\d{2}-\d{2})", text)
    for td in trip_date_matches:
        if td != issue_date and td not in travel_dates:
            travel_dates.append(td)
            sub_trips.append({
                "date": td,
                "origin": "",
                "destination": "",
                "transport": "出租车",
            })

    travel_dates = sorted(list(dict.fromkeys(travel_dates)))
    primary_date = travel_dates[0] if travel_dates else issue_date

    return {
        "document_type": "网约车发票",
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "travel_date": primary_date,
        "travel_dates": travel_dates,
        "sub_trips": sub_trips,
        "total_with_tax": amount,
        "buyer_name": buyer,
        "seller_name": seller,
        "description": "滴滴出行",
    }


def probe_flight_booking(text: str, path: Path) -> Optional[Dict[str, Any]]:
    """机票代订、航旅普票、携程/飞猪凭据极速探针。"""
    is_flight = (
        "代订机票" in text
        or "机票款" in path.name
        or "阿斯兰航空" in text
        or "上海华程西南国际旅行社" in text
        or "航空运输" in text
    )
    if not is_flight or ("发票号码" not in text and "全 国 统 一 发 票" not in text and "电子发票" not in text):
        return None

    compact_lines = [re.sub(r"\s+", "", line or "") for line in text.splitlines() if line.strip()]
    inv_match = re.search(r"发票号码[:：\s]*([0-9]{8,24})", text)
    invoice_candidates = [line for line in compact_lines if re.fullmatch(r"[0-9]{8,24}", line)]
    invoice_number = (
        inv_match.group(1).strip()
        if inv_match
        else next((line for line in invoice_candidates if len(line) >= 20), "")
    )
    if not invoice_number and invoice_candidates:
        invoice_number = invoice_candidates[0]

    date_match = re.search(r"开票日期[：:\s]*(20\d{2}年\d{1,2}月\d{1,2}日)", text) or re.search(
        r"(20\d{2}年\d{1,2}月\d{1,2}日)", text
    )
    amount = extract_max_invoice_amount(text)

    if not invoice_number or not amount:
        return None

    issue_date = normalize_date(date_match.group(1)) if date_match else ""

    buyer, seller = extract_parties_from_text(text)

    # 从备注或文件名抽取航程与出行日期
    travel_dates = []
    sub_trips = []
    origin = ""
    destination = ""

    flight_matches = re.findall(
        r"(\d{4}[/.-]\d{1,2}[/.-]\d{1,2})\s+([\u4e00-\u9fa5A-Za-z0-9·-]+)-([\u4e00-\u9fa5A-Za-z0-9·-]+)\s+([A-Za-z0-9]{2}\d{3,4})",
        text,
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

    # 文件名航线 fallback
    fn_orig, fn_dest = extract_route_from_filename(path.stem)
    if fn_orig and not origin:
        origin = fn_orig
    if fn_dest and not destination:
        destination = fn_dest

    travel_dates = sorted(list(dict.fromkeys(travel_dates)))
    primary_date = travel_dates[0] if travel_dates else ""

    desc = f"{origin}-{destination}" if origin and destination else "代订机票"

    return {
        "document_type": "普票",
        "reimbursement_category": "行程交通费",
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "travel_date": primary_date,
        "travel_dates": travel_dates,
        "sub_trips": sub_trips,
        "total_with_tax": amount,
        "origin": origin,
        "destination": destination,
        "buyer_name": buyer,
        "seller_name": seller,
        "description": desc,
    }


def probe_standard_china_einvoice(text: str, path: Path) -> Optional[Dict[str, Any]]:
    """标准中国统一数电发票 / 增值税电子发票极速探针。"""
    if "发票号码" not in text or "开票日期" not in text:
        return None
    if "电子发票" not in text and "发票监制章" not in text:
        return None

    compact_lines = [re.sub(r"\s+", "", line or "") for line in text.splitlines()]
    compact_lines = [line for line in compact_lines if line]

    inv_match = re.search(r"发票号码[:：\s]*([0-9]{8,24})", text)
    invoice_candidates = [line for line in compact_lines if re.fullmatch(r"[0-9]{8,24}", line)]
    invoice_number = (
        inv_match.group(1).strip()
        if inv_match
        else next((line for line in invoice_candidates if len(line) >= 20), "")
    )
    if not invoice_number and invoice_candidates:
        invoice_number = invoice_candidates[0]
    if not invoice_number:
        return None

    date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if not date_match:
        return None
    issue_date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"

    amount = extract_max_invoice_amount(text)
    if not amount:
        return None

    buyer, seller = extract_parties_from_text(text)
    doc_type = detect_document_type(text, path)

    # 检查地铁/客运特殊出行日期
    travel_dates = []
    t_m = re.search(r"出行日期[^\d]{0,10}(20\d{2}-\d{2}-\d{2})", text)
    if t_m:
        travel_dates.append(t_m.group(1))

    return {
        "document_type": doc_type,
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "travel_date": travel_dates[0] if travel_dates else "",
        "travel_dates": travel_dates,
        "sub_trips": [],
        "total_with_tax": amount,
        "buyer_name": buyer,
        "seller_name": seller,
        "description": path.stem[:40],
    }


def probe_pdf_locally(path: Path) -> Optional[ParsedDocument]:
    """
    顶层快速探测入口。
    以微秒级吞吐提取 PDF 文本层并运行确定性规则探针。
    若成功，返回 ParsedDocument(ok=True)；若为纯图片/扫描件则返回 None 触发云端降级。
    """
    text = extract_pdf_embedded_text(path, max_pages=2)
    if len(text) < 40:
        return None

    # 按精准度排序的探针执行链
    probes = (
        ("train_ticket", probe_train_ticket),
        ("flight_booking", probe_flight_booking),
        ("didi_and_ride", probe_didi_and_ride),
        ("standard_einvoice", probe_standard_china_einvoice),
    )

    for probe_name, probe_fn in probes:
        try:
            fields = probe_fn(text, path)
            if fields and fields.get("total_with_tax") and (fields.get("invoice_number") or fields.get("document_type") == "行程单"):
                full_fields = {
                    "document_type": fields.get("document_type", "普票"),
                    "issue_date": fields.get("issue_date", ""),
                    "travel_date": fields.get("travel_date", ""),
                    "travel_dates": fields.get("travel_dates", []),
                    "sub_trips": fields.get("sub_trips", []),
                    "invoice_number": fields.get("invoice_number", ""),
                    "invoice_code": fields.get("invoice_code", ""),
                    "seller_name": fields.get("seller_name", ""),
                    "buyer_name": fields.get("buyer_name", ""),
                    "total_amount": fields.get("total_amount", ""),
                    "total_tax": fields.get("total_tax", ""),
                    "total_with_tax": fields.get("total_with_tax", ""),
                    "origin": fields.get("origin", ""),
                    "destination": fields.get("destination", ""),
                    "train_departure_time": fields.get("train_departure_time", ""),
                    "refund_fee": fields.get("refund_fee", ""),
                    "change_fee": fields.get("change_fee", ""),
                    "description": fields.get("description", path.stem[:40]),
                }
                raw_res = {
                    "probe": probe_name,
                    "fast_path": True,
                    "parse_source": "local_fast_path",
                    "parse_reason_code": "LOCAL_SUCCESS",
                    "parse_trace": {
                        "source": "local_fast_path",
                        "reason_code": "LOCAL_SUCCESS",
                        "summary": "本地快速探针解析成功",
                        "fallback_used": False,
                        "fallback_note": "",
                        "stages": [
                            {
                                "stage": "local_fast_probe",
                                "status": "success",
                                "reason_code": "LOCAL_SUCCESS",
                                "message": f"命中探针 {probe_name}",
                            }
                        ],
                    },
                }
                return ParsedDocument(
                    source_path=path,
                    raw_text=text,
                    raw_result=raw_res,
                    fields=full_fields,
                    ok=True,
                )
        except Exception as exc:
            logger.debug("Probe %s failed for %s: %s", probe_name, path.name, exc)

    return None


def is_fast_probe_result_complete(doc: Optional[ParsedDocument]) -> bool:
    """
    检查本地探针解析结果是否满足最小关键字段完整性门槛。
    若关键字段不完整，则回退至外部 OCR 提供商。
    非致命字段（如购买方抬头、车次时间等）缺失不会导致回退。
    """
    if not doc or not doc.ok or not doc.fields:
        return False

    fields = doc.fields
    doc_type = fields.get("document_type", "")
    amount_str = str(fields.get("total_with_tax") or "").strip()
    inv_num = str(fields.get("invoice_number") or "").strip()

    # 任何票种都必须有有效金额
    if not amount_str:
        return False
    try:
        amount = Decimal(amount_str)
        if amount <= Decimal("0"):
            return False
    except (InvalidOperation, TypeError):
        return False

    # 高铁发票：必须有发票号码、出行日期、起点站和终点站
    if doc_type == "高铁发票":
        travel_date = str(fields.get("travel_date") or "").strip()
        origin = str(fields.get("origin") or "").strip()
        destination = str(fields.get("destination") or "").strip()
        if not inv_num or not travel_date or not origin or not destination:
            return False
        return True

    # 行程单：不需要发票号码，但必须有金额
    if doc_type == "行程单":
        return True

    # 标准中国发票（普票/专用发票/电子发票/网约车发票/餐饮/住宿/公交地铁等）：必须有发票号码
    if not inv_num:
        return False

    return True


def probe_pdf_locally_detailed(path: Path) -> Tuple[Optional[ParsedDocument], str, str]:
    """
    详细本地探针调用，返回 (doc, reason_code, message)。
    用于在流水线中准确记录本地阶段的状态与回退原因。
    """
    try:
        text = extract_pdf_embedded_text(path, max_pages=2)
        if len(text) < 40:
            return None, "LOCAL_NO_TEXT", "PDF未提取到有效文本层或文本过短"
    except Exception as exc:
        return None, "LOCAL_ERROR", f"PDF文本提取异常: {exc}"

    try:
        doc = probe_pdf_locally(path)
    except Exception as exc:
        return None, "LOCAL_ERROR", f"本地探针执行异常: {exc}"

    if doc is None:
        return None, "LOCAL_NO_MATCH", "本地探针规则未匹配已知票据格式"

    if not is_fast_probe_result_complete(doc):
        missing = []
        fields = doc.fields or {}
        doc_type = fields.get("document_type", "")
        if not fields.get("total_with_tax"):
            missing.append("金额")
        if doc_type == "高铁发票":
            if not fields.get("origin") or not fields.get("destination"):
                missing.append("起终点")
            if not fields.get("travel_date"):
                missing.append("乘车日期")
            if not fields.get("invoice_number"):
                missing.append("发票号码")
        elif doc_type != "行程单":
            if not fields.get("invoice_number"):
                missing.append("发票号码")
        detail = "、".join(missing) if missing else "必要字段"
        return doc, "LOCAL_INCOMPLETE", f"本地解析缺少关键字段（{detail}）"

    return doc, "LOCAL_SUCCESS", "本地快速探针解析成功"
