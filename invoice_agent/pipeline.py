from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

from .analysis import assign_reimbursement_categories
from .company_reimbursement import build_company_reimbursement_data, write_company_workbook
from .excel import build_preview, write_workbook
from .extractor import is_invoice_type, parse_amount
from .models import (
    BatchOrganizeItem,
    BatchOrganizeResult,
    ExpenseRecord,
    ExportArtifact,
    ExportResult,
    OrganizeResult,
    ParsedDocument,
    TripInfo,
)
from .ocr import PaddleOcrProvider
from .pdf_export import export_company_pdf
from .scanner import SUPPORTED_EXTENSIONS, scan_documents, sha256_file
from .trip_audit import TripAuditLlmClient, TripAuditPolicy, run_trip_audit


class OcrProvider(Protocol):
    def parse(self, path: Path) -> ParsedDocument:
        ...


CATEGORY_ORDER = {
    "高铁发票": 10,
    "网约车发票": 20,
    "出租车票": 30,
    "通行费发票": 40,
    "住宿发票": 50,
    "餐饮发票": 60,
    "专用发票": 65,
    "普票": 70,
    "纸质拍照发票": 80,
    "其他/无法识别": 90,
    "行程单": 99,
}

ORGANIZE_MODE_SINGLE = "single"
ORGANIZE_MODE_BATCH_SUBFOLDERS = "batch_subfolders"


def organize_folder(
    folder: Path,
    trip_info_path: Optional[Path] = None,
    trip_info: Optional[TripInfo] = None,
    out_dir: Optional[Path] = None,
    apply: bool = False,
    ocr_provider: Optional[OcrProvider] = None,
    max_workers: int = 3,
    progress_callback: Optional[Callable[[ExpenseRecord], None]] = None,
    write_excel: bool = True,
    trip_audit_policy: Optional[TripAuditPolicy] = None,
    trip_audit_llm_client: Optional[TripAuditLlmClient] = None,
) -> OrganizeResult:
    folder = folder.expanduser().resolve()
    trip = resolve_trip_info(folder, trip_info_path, trip_info)
    output_dir = resolve_output_dir(folder, trip.project_name, out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    provider = ocr_provider or PaddleOcrProvider()
    paths = [path for path in scan_documents(folder) if not _is_inside(path, output_dir)]
    records = _parse_records(paths, trip, provider, max_workers=max_workers, progress_callback=progress_callback)
    _mark_duplicates(records)
    _link_itineraries(records)
    records.sort(key=_sort_key)
    analyze_records(records)
    _assign_names(records)
    trip_audit = run_trip_audit(records, trip_audit_policy or TripAuditPolicy(), llm_client=trip_audit_llm_client)

    if write_excel:
        export_records(output_dir, records, apply=apply, write_excel=True, trip_audit=trip_audit)
    else:
        write_result_files(output_dir, records, write_excel=False, trip_audit=trip_audit)
    return OrganizeResult(output_dir=output_dir, records=records, preview=build_preview(records, trip_audit), trip_audit=trip_audit)


def organize_batch_subfolders(
    folder: Path,
    trip_info: Optional[TripInfo] = None,
    out_dir: Optional[Path] = None,
    apply: bool = False,
    ocr_provider: Optional[OcrProvider] = None,
    max_workers: int = 3,
    write_excel: bool = True,
    trip_audit_policy: Optional[TripAuditPolicy] = None,
    trip_audit_llm_client: Optional[TripAuditLlmClient] = None,
    item_callback: Optional[Callable[[BatchOrganizeItem], None]] = None,
) -> BatchOrganizeResult:
    root = folder.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    output_root = (out_dir.expanduser().resolve() if out_dir else root / "整理结果")
    _raise_if_root_has_direct_documents(root)
    output_root.mkdir(parents=True, exist_ok=True)

    items: List[BatchOrganizeItem] = []
    for package_folder in discover_batch_package_folders(root, output_root):
        output_dir = _unique_target(output_root / (sanitize_filename(package_folder.name) or "报销包"))
        package_id = sanitize_filename(package_folder.name).replace(" ", "-") or f"package-{len(items) + 1}"
        documents = scan_documents(package_folder)
        if not documents:
            item = BatchOrganizeItem(
                package_id=package_id,
                name=package_folder.name,
                folder=package_folder,
                output_dir=output_dir,
                state="skipped",
                error="未发现支持的票据文件",
            )
            items.append(item)
            if item_callback:
                item_callback(item)
            continue
        try:
            package_trip_info = _resolve_batch_trip_info(package_folder, root, trip_info)
            result = organize_folder(
                folder=package_folder,
                trip_info=package_trip_info,
                out_dir=output_dir,
                apply=apply if write_excel else False,
                ocr_provider=ocr_provider,
                max_workers=max_workers,
                write_excel=write_excel,
                trip_audit_policy=trip_audit_policy,
                trip_audit_llm_client=trip_audit_llm_client,
            )
            item = BatchOrganizeItem(
                package_id=package_id,
                name=package_folder.name,
                folder=package_folder,
                output_dir=output_dir,
                state="done" if write_excel else "review",
                result=result,
            )
            items.append(item)
            if item_callback:
                item_callback(item)
        except Exception as exc:
            item = BatchOrganizeItem(
                package_id=package_id,
                name=package_folder.name,
                folder=package_folder,
                output_dir=output_dir,
                state="failed",
                error=str(exc),
            )
            items.append(item)
            if item_callback:
                item_callback(item)
    return BatchOrganizeResult(root_folder=root, output_dir=output_root, items=items)


def discover_batch_package_folders(root: Path, output_root: Optional[Path] = None) -> List[Path]:
    root = root.expanduser().resolve()
    output_root_resolved = output_root.expanduser().resolve() if output_root else None
    folders = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if output_root_resolved and child.resolve() == output_root_resolved:
            continue
        folders.append(child.resolve())
    return folders


def analyze_records(records: List[ExpenseRecord]) -> None:
    assign_reimbursement_categories(records)
    _apply_duplicate_amount_policy(records)


def export_records(
    output_dir: Path,
    records: List[ExpenseRecord],
    apply: bool = False,
    write_excel: bool = True,
    trip_audit=None,
) -> ExportResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    if apply:
        _copy_records(records, output_dir)
    return write_result_files(output_dir, records, write_excel=write_excel, trip_audit=trip_audit)


def write_result_files(
    output_dir: Path,
    records: List[ExpenseRecord],
    write_excel: bool,
    trip_audit=None,
) -> ExportResult:
    (output_dir / "raw_results.json").write_text(
        json.dumps([record.to_json() for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "rename_plan.json").write_text(
        json.dumps(
            [
                {
                    "sequence": record.sequence,
                    "source_path": str(record.source_path),
                    "new_name": record.new_name,
                    "copied_path": record.copied_path,
                    "status": record.recognition_status,
                }
                for record in records
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if trip_audit is not None:
        (output_dir / "trip_audit.json").write_text(
            json.dumps(trip_audit.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not write_excel:
        return ExportResult(status="success")
    artifacts: List[ExportArtifact] = []
    warnings: List[str] = []
    summary_path = output_dir / "00_报销清单.xlsx"
    write_workbook(summary_path, records, trip_audit)
    artifacts.append(ExportArtifact("summary_excel", summary_path, "success"))
    company_path = output_dir / "01_公司报销单.xlsx"
    pdf_path = output_dir / "01_公司报销单.pdf"
    try:
        data = build_company_reimbursement_data(records)
        write_company_workbook(company_path, data)
        artifacts.append(ExportArtifact("company_excel", company_path, "success"))
        warnings.extend(data.warnings)
    except Exception as exc:
        message = f"公司报销单生成失败：{exc}"
        artifacts.append(ExportArtifact("company_excel", company_path, "failed", message))
        warnings.append(message)
        return ExportResult(status="partial_success", artifacts=artifacts, warnings=warnings)
    pdf_result = export_company_pdf(company_path, pdf_path)
    artifacts.append(ExportArtifact("company_pdf", pdf_path, pdf_result.status, pdf_result.message))
    if pdf_result.status == "failed":
        warnings.append(pdf_result.message)
        status = "partial_success"
    else:
        if pdf_result.status == "skipped" and pdf_result.message:
            warnings.append(pdf_result.message)
        status = "success"
    return ExportResult(status=status, artifacts=artifacts, warnings=warnings)


def _parse_records(
    paths: List[Path],
    trip: TripInfo,
    provider: OcrProvider,
    max_workers: int,
    progress_callback: Optional[Callable[[ExpenseRecord], None]] = None,
) -> List[ExpenseRecord]:
    if hasattr(provider, "parse_many") and progress_callback is None:
        try:
            parsed_docs = provider.parse_many(paths, max_workers=max_workers)  # type: ignore[attr-defined]
        except TypeError:
            parsed_docs = provider.parse_many(paths)  # type: ignore[attr-defined]
        return [_record_from_parsed(path, trip, parsed) for path, parsed in zip(paths, parsed_docs)]
    workers = max(1, min(max_workers, len(paths) or 1))
    if workers == 1:
        records = []
        for path in paths:
            record = _record_from_path(path, trip, provider)
            if progress_callback:
                progress_callback(record)
            records.append(record)
        return records
    records_by_path: Dict[Path, ExpenseRecord] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_record_from_path, path, trip, provider): path for path in paths}
        for future in as_completed(futures):
            path = futures[future]
            record = future.result()
            records_by_path[path] = record
            if progress_callback:
                progress_callback(record)
    return [records_by_path[path] for path in paths]


def resolve_trip_info(
    folder: Path,
    trip_info_path: Optional[Path] = None,
    trip_info: Optional[TripInfo] = None,
) -> TripInfo:
    config: Dict[str, str] = {}
    candidate = trip_info_path or folder / "trip_info.json"
    if candidate.exists():
        config = json.loads(candidate.read_text(encoding="utf-8"))
    if trip_info:
        config.update(
            {
                "project_name": trip_info.project_name,
                "traveler": trip_info.traveler,
                "department": trip_info.department,
                "trip_start_date": trip_info.trip_start_date,
                "trip_end_date": trip_info.trip_end_date,
                "daily_meal_allowance": trip_info.daily_meal_allowance,
            }
        )
    config.setdefault("project_name", folder.name)
    missing = [
        key
        for key in ["traveler", "department", "trip_start_date", "trip_end_date"]
        if not str(config.get(key, "")).strip()
    ]
    if missing:
        raise ValueError("Missing trip info: " + ", ".join(missing))
    return TripInfo(
        project_name=str(config["project_name"]),
        traveler=str(config["traveler"]),
        department=str(config["department"]),
        trip_start_date=str(config["trip_start_date"]),
        trip_end_date=str(config["trip_end_date"]),
        daily_meal_allowance=str(config.get("daily_meal_allowance") or "50"),
    )


def _resolve_batch_trip_info(folder: Path, root: Path, default_trip_info: Optional[TripInfo]) -> TripInfo:
    config: Dict[str, str] = {}
    candidate = folder / "trip_info.json"
    if candidate.exists():
        config = json.loads(candidate.read_text(encoding="utf-8"))
    if default_trip_info:
        defaults = {
            "project_name": default_trip_info.project_name,
            "traveler": default_trip_info.traveler,
            "department": default_trip_info.department,
            "trip_start_date": default_trip_info.trip_start_date,
            "trip_end_date": default_trip_info.trip_end_date,
            "daily_meal_allowance": default_trip_info.daily_meal_allowance,
        }
        if defaults.get("project_name") == root.name:
            defaults["project_name"] = folder.name
        for key, value in defaults.items():
            if value and not str(config.get(key, "")).strip():
                config[key] = value
    config.setdefault("project_name", folder.name)
    missing = [
        key
        for key in ["traveler", "department", "trip_start_date", "trip_end_date"]
        if not str(config.get(key, "")).strip()
    ]
    if missing:
        raise ValueError("Missing trip info: " + ", ".join(missing))
    return TripInfo(
        project_name=str(config["project_name"]),
        traveler=str(config["traveler"]),
        department=str(config["department"]),
        trip_start_date=str(config["trip_start_date"]),
        trip_end_date=str(config["trip_end_date"]),
        daily_meal_allowance=str(config.get("daily_meal_allowance") or "50"),
    )


def _raise_if_root_has_direct_documents(root: Path) -> None:
    direct_documents = [
        path.name
        for path in sorted(root.iterdir(), key=lambda candidate: candidate.name.lower())
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if direct_documents:
        sample = "、".join(direct_documents[:3])
        raise ValueError(
            f"批量报销包模式发现根目录直属票据：{sample}。请将直属票据移动到某个一级子文件夹，或切换为单报销包模式。"
        )


def resolve_output_dir(folder: Path, project_name: str, out_dir: Optional[Path]) -> Path:
    if out_dir:
        return out_dir.expanduser().resolve()
    safe_project = sanitize_filename(project_name) or "项目"
    stamp = date.today().strftime("%Y%m%d")
    return folder / f"整理结果_{safe_project}_{stamp}"


def _record_from_path(path: Path, trip: TripInfo, provider: OcrProvider) -> ExpenseRecord:
    file_hash = sha256_file(path)
    parsed = provider.parse(path)
    return _record_from_parsed(path, trip, parsed, file_hash=file_hash)


def _record_from_parsed(
    path: Path,
    trip: TripInfo,
    parsed: ParsedDocument,
    file_hash: Optional[str] = None,
) -> ExpenseRecord:
    file_hash = file_hash or sha256_file(path)
    fields = parsed.fields if parsed.ok else {}
    document_type = str(fields.get("document_type") or "其他/无法识别")
    include = bool(parsed.ok and is_invoice_type(document_type))
    total_with_tax = str(fields.get("total_with_tax") or "")
    if document_type == "行程单" and not total_with_tax:
        total_with_tax = str(fields.get("total_amount") or "")
    risk_note = ""
    status = "已识别" if parsed.ok and document_type != "其他/无法识别" else "无法识别"
    if parsed.ok and include and (not fields.get("invoice_number") or not fields.get("issue_date") or not total_with_tax):
        status = "待人工确认"
        risk_note = "缺少发票号码/日期/金额"
    if not parsed.ok:
        risk_note = (parsed.error or {}).get("message", "")
    return ExpenseRecord(
        sequence=0,
        source_path=path,
        original_name=path.name,
        file_hash=file_hash,
        project_name=trip.project_name,
        traveler=trip.traveler,
        department=trip.department,
        trip_start_date=trip.trip_start_date,
        trip_end_date=trip.trip_end_date,
        daily_meal_allowance=trip.daily_meal_allowance,
        document_date=str(fields.get("issue_date") or ""),
        document_type=document_type,
        include_in_amount=include,
        invoice_number=str(fields.get("invoice_number") or ""),
        invoice_code=str(fields.get("invoice_code") or ""),
        seller_name=str(fields.get("seller_name") or ""),
        buyer_name=str(fields.get("buyer_name") or ""),
        total_amount=str(fields.get("total_amount") or ""),
        total_tax=str(fields.get("total_tax") or ""),
        total_with_tax=total_with_tax,
        origin=str(fields.get("origin") or ""),
        destination=str(fields.get("destination") or ""),
        description=str(fields.get("description") or ""),
        recognition_status=status,
        risk_note=risk_note,
        raw_text=parsed.raw_text,
        raw_result=parsed.raw_result,
    )


def _mark_duplicates(records: List[ExpenseRecord]) -> None:
    by_hash = defaultdict(list)
    by_business = defaultdict(list)
    for record in records:
        by_hash[record.file_hash].append(record)
        if record.business_duplicate_key:
            by_business[record.business_duplicate_key].append(record)
    for group in by_hash.values():
        if len(group) > 1:
            for record in group:
                record.duplicate_mark = _append_mark(record.duplicate_mark, "Hash重复")
    for group in by_business.values():
        if len(group) > 1:
            for record in group:
                record.duplicate_mark = _append_mark(record.duplicate_mark, "业务键重复")


def _apply_duplicate_amount_policy(records: List[ExpenseRecord]) -> None:
    for group in _duplicate_groups(records):
        included = [record for record in group if record.include_in_amount]
        if len(included) <= 1:
            continue
        keeper = sorted(included, key=_sort_key)[0]
        for record in included:
            if record is keeper:
                continue
            record.include_in_amount = False
            record.risk_note = _append_mark(record.risk_note, "重复发票不计入汇总")


def _duplicate_groups(records: List[ExpenseRecord]) -> List[List[ExpenseRecord]]:
    parent = {id(record): id(record) for record in records}
    by_key: Dict[str, List[ExpenseRecord]] = defaultdict(list)
    for record in records:
        if record.file_hash:
            by_key[f"hash:{record.file_hash}"].append(record)
        if record.business_duplicate_key:
            by_key[f"business:{record.business_duplicate_key}"].append(record)

    def find(record_id: int) -> int:
        while parent[record_id] != record_id:
            parent[record_id] = parent[parent[record_id]]
            record_id = parent[record_id]
        return record_id

    def union(left: ExpenseRecord, right: ExpenseRecord) -> None:
        root_left = find(id(left))
        root_right = find(id(right))
        if root_left != root_right:
            parent[root_right] = root_left

    for group in by_key.values():
        if len(group) < 2:
            continue
        first = group[0]
        for record in group[1:]:
            union(first, record)

    groups: Dict[int, List[ExpenseRecord]] = defaultdict(list)
    for record in records:
        groups[find(id(record))].append(record)
    return [group for group in groups.values() if len(group) > 1]


def _link_itineraries(records: List[ExpenseRecord]) -> None:
    invoices = [record for record in records if record.include_in_amount]
    for invoice in invoices:
        invoice.linked_group = invoice.invoice_number or f"invoice-{invoice.file_hash[:8]}"
    for itinerary in [record for record in records if record.document_type == "行程单"]:
        matches = [
            invoice
            for invoice in invoices
            if parse_amount(invoice.total_with_tax) == parse_amount(itinerary.total_with_tax)
        ]
        if len(matches) == 1:
            itinerary.linked_group = matches[0].linked_group
            _copy_route_from_itinerary(matches[0], itinerary)
        elif len(matches) > 1 and len({match.file_hash for match in matches}) == 1:
            match = sorted(matches, key=_sort_key)[0]
            itinerary.linked_group = match.linked_group
            _copy_route_from_itinerary(match, itinerary)
        elif len(matches) > 1:
            itinerary.risk_note = _append_mark(itinerary.risk_note, "行程单匹配多个发票候选")
        else:
            itinerary.risk_note = _append_mark(itinerary.risk_note, "行程单未匹配发票")


def _copy_route_from_itinerary(invoice: ExpenseRecord, itinerary: ExpenseRecord) -> None:
    if itinerary.origin and not invoice.origin:
        invoice.origin = itinerary.origin
    if itinerary.destination and not invoice.destination:
        invoice.destination = itinerary.destination
    if invoice.origin and invoice.destination and (
        not invoice.description or invoice.description == invoice.seller_name
    ):
        invoice.description = f"{invoice.origin}-{invoice.destination}"


def _sort_key(record: ExpenseRecord):
    is_itinerary = 1 if record.document_type == "行程单" else 0
    return (
        record.document_date or "9999-99-99",
        CATEGORY_ORDER.get(record.document_type, 98),
        record.invoice_number or "发票号待确认",
        parse_amount(record.total_with_tax),
        record.original_name.lower(),
        is_itinerary,
    )


def _assign_names(records: List[ExpenseRecord]) -> None:
    for index, record in enumerate(records, start=1):
        record.sequence = index
        date_part = record.document_date or "日期待确认"
        amount_part = "不计金额" if not record.include_in_amount else _amount_text(record.total_with_tax)
        invoice_part = (
            f"发票号{record.invoice_number}"
            if record.include_in_amount and record.invoice_number
            else ("发票号待确认" if record.include_in_amount else "")
        )
        route_or_desc = _route_or_description(record)
        parts = [
            f"{index:03d}",
            date_part,
            record.document_type,
            route_or_desc,
            amount_part,
            invoice_part,
        ]
        record.new_name = sanitize_filename("_".join([part for part in parts if part])) + record.source_path.suffix.lower()


def _copy_records(records: List[ExpenseRecord], output_dir: Path) -> None:
    buckets = {
        "identified": output_dir / "01_已识别_重命名",
        "review": output_dir / "02_待人工确认",
        "duplicate": output_dir / "03_重复疑似",
        "failed": output_dir / "04_无法识别",
    }
    for bucket in buckets.values():
        bucket.mkdir(parents=True, exist_ok=True)
    for record in records:
        if record.duplicate_mark:
            target_dir = buckets["duplicate"]
        elif record.recognition_status == "无法识别":
            target_dir = buckets["failed"]
        elif record.recognition_status == "待人工确认":
            target_dir = buckets["review"]
        else:
            target_dir = buckets["identified"]
        target = _unique_target(target_dir / record.new_name)
        shutil.copy2(record.source_path, target)
        record.copied_path = str(target)


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(2, 1000):
        candidate = target.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Too many duplicate targets for {target}")


def _amount_text(value: str) -> str:
    amount = parse_amount(value)
    return "金额待确认" if amount == Decimal("0") and not value else f"{amount:.2f}"


def _route_or_description(record: ExpenseRecord) -> str:
    route = "-".join([part for part in [record.origin, record.destination] if part])
    if route:
        return route
    if record.description:
        return record.description
    if record.seller_name:
        return record.seller_name
    return Path(record.original_name).stem


def _append_mark(existing: str, mark: str) -> str:
    if not existing:
        return mark
    if mark in existing:
        return existing
    return f"{existing};{mark}"


def sanitize_filename(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]", "_", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def _is_inside(path: Path, maybe_parent: Path) -> bool:
    try:
        path.resolve().relative_to(maybe_parent.resolve())
        return True
    except ValueError:
        return False
