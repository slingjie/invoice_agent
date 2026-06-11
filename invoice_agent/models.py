from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TripInfo:
    project_name: str
    traveler: str
    department: str
    trip_start_date: str
    trip_end_date: str
    daily_meal_allowance: str = "50"


@dataclass
class ParsedDocument:
    source_path: Path
    raw_text: str
    raw_result: Dict[str, Any]
    fields: Dict[str, Any]
    ok: bool
    error: Optional[Dict[str, Any]] = None


@dataclass
class ExpenseRecord:
    sequence: int
    source_path: Path
    original_name: str
    file_hash: str
    project_name: str
    traveler: str
    department: str
    trip_start_date: str
    trip_end_date: str
    daily_meal_allowance: str = "50"
    document_date: str = ""
    document_type: str = "其他/无法识别"
    reimbursement_category: str = ""
    high_level_category: str = ""
    include_in_amount: bool = False
    invoice_number: str = ""
    invoice_code: str = ""
    seller_name: str = ""
    buyer_name: str = ""
    total_amount: str = ""
    total_tax: str = ""
    total_with_tax: str = ""
    origin: str = ""
    destination: str = ""
    description: str = ""
    new_name: str = ""
    copied_path: str = ""
    duplicate_mark: str = ""
    recognition_status: str = "待人工确认"
    risk_note: str = ""
    manual_note: str = ""
    linked_group: str = ""
    raw_text: str = ""
    raw_result: Dict[str, Any] = field(default_factory=dict)

    @property
    def business_duplicate_key(self) -> str:
        if not self.invoice_number or not self.total_with_tax or not self.document_date:
            return ""
        return "|".join([self.invoice_number, self.total_with_tax, self.document_date])

    def to_json(self) -> Dict[str, Any]:
        data = asdict(self)
        data["source_path"] = str(self.source_path)
        return data


@dataclass
class OrganizeResult:
    output_dir: Path
    records: List[ExpenseRecord]
    preview: Dict[str, Any] = field(default_factory=dict)
    trip_audit: Any = None


@dataclass
class BatchOrganizeItem:
    package_id: str
    name: str
    folder: Path
    output_dir: Path
    state: str
    error: str = ""
    result: Optional[OrganizeResult] = None


@dataclass
class BatchOrganizeResult:
    root_folder: Path
    output_dir: Path
    items: List[BatchOrganizeItem]
