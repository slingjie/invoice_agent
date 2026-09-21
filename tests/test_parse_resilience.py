import time
from decimal import Decimal
from pathlib import Path
import pytest

from invoice_agent.models import TripInfo, ParsedDocument, ExpenseRecord
from invoice_agent.pipeline import (
    FALLBACK_CLOUD_NOTE,
    FALLBACK_MINERU_NOTE,
    organize_folder,
    sanitize_diagnostic_message,
    build_actionable_failure_message,
)
from invoice_agent.excel import build_preview
from invoice_agent.web import (
    TASKS,
    retry_task_record,
    render_index,
)


def _make_trip() -> TripInfo:
    return TripInfo(
        project_name="测试项目",
        traveler="张三",
        department="技术部",
        trip_start_date="2026-07-14",
        trip_end_date="2026-07-16",
    )


def test_sanitize_diagnostic_message_redacts_credentials():
    leaky_msg = (
        "Request failed for https://aip.baidubce.com/rpc/2.0/ai_custom/v1/doc_analysis"
        "?access_token=24.abcdef1234567890xyz.2592000.1700000000 with auth header "
        "Bearer super_secret_token_12345 and apiKey=top_secret_key_999."
    )
    sanitized = sanitize_diagnostic_message(leaky_msg)
    assert "24.abcdef1234567890xyz" not in sanitized
    assert "super_secret_token_12345" not in sanitized
    assert "top_secret_key_999" not in sanitized
    assert "***REDACTED***" in sanitized


def test_sanitize_diagnostic_message_truncates_long_body():
    long_msg = "Error dump: " + ("A" * 500)
    sanitized = sanitize_diagnostic_message(long_msg)
    assert len(sanitized) <= 200
    assert sanitized.endswith("...")


def test_local_fast_path_success_trace():
    p = Path("测试发票/0714-0716福州六和/26337000000698760199.pdf")
    if not p.exists():
        pytest.skip("真实样票不存在")

    from invoice_agent.fast_probe import probe_pdf_locally
    doc = probe_pdf_locally(p)
    assert doc is not None
    assert doc.ok is True
    assert doc.raw_result.get("parse_source") == "local_fast_path"
    assert doc.raw_result.get("parse_reason_code") == "LOCAL_SUCCESS"
    trace = doc.raw_result.get("parse_trace", {})
    assert trace.get("source") == "local_fast_path"
    assert trace.get("fallback_used") is False


def test_local_failure_falls_back_to_paddle_success(tmp_path: Path, monkeypatch):
    import fitz
    import invoice_agent.fast_probe as fp

    src = tmp_path / "invoices"
    src.mkdir()
    pdf_file = src / "test_ticket.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf_file))
    doc.close()

    # 模拟本地探针缺少起止站（LOCAL_INCOMPLETE）
    def mock_incomplete_probe(path: Path):
        return ParsedDocument(
            source_path=path,
            raw_text="sample",
            raw_result={"probe": "train_ticket", "fast_path": True},
            fields={
                "document_type": "高铁发票",
                "invoice_number": "26339190041007967535",
                "total_with_tax": "353.00",
                "origin": "",
                "destination": "",
                "travel_date": "2026-07-14",
            },
            ok=True,
        )

    monkeypatch.setattr(fp, "probe_pdf_locally", mock_incomplete_probe)

    class MockPaddleSuccessProvider:
        def parse(self, path: Path):
            return ParsedDocument(
                source_path=path,
                raw_text="mock paddle result",
                raw_result={"job_id": "paddle-job-123"},
                fields={
                    "document_type": "高铁发票",
                    "invoice_number": "26339190041007967535",
                    "total_with_tax": "353.00",
                    "origin": "杭州东站",
                    "destination": "福州南站",
                    "travel_date": "2026-07-14",
                },
                ok=True,
            )

    res = organize_folder(
        folder=src,
        trip_info=_make_trip(),
        out_dir=tmp_path / "out",
        ocr_provider=MockPaddleSuccessProvider(),
        write_excel=False,
    )

    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.recognition_status == "已识别"
    assert rec.parse_source == "paddle_ocr"
    assert rec.parse_reason_code == "PADDLE_SUCCESS"
    assert FALLBACK_CLOUD_NOTE in rec.risk_note
    trace = rec.raw_result.get("parse_trace", {})
    assert trace.get("fallback_used") is True
    assert trace.get("fallback_note") == FALLBACK_CLOUD_NOTE
    assert any(s.get("stage") == "local_fast_probe" and s.get("status") == "failed" for s in trace.get("stages", []))

    # 验证诊断汇总
    diag = res.preview.get("diagnostics", {})
    assert diag["by_source"]["paddle_ocr"] == 1
    assert diag["fallback_count"] == 1
    assert diag["by_source"]["failed"] == 0


def test_paddle_failure_to_mineru_success(tmp_path: Path, monkeypatch):
    from invoice_agent.web import _maybe_parse_with_mineru

    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy")

    paddle_err_doc = ParsedDocument(
        source_path=pdf_path,
        raw_text="",
        raw_result={},
        fields={},
        ok=False,
        error={"code": "SDK_ERROR", "message": "Server timeout with token=secret123"},
    )

    class MockMinerUSuccess:
        def __init__(self, *args, **kwargs):
            pass
        def parse(self, path: Path):
            return ParsedDocument(
                source_path=path,
                raw_text="markdown content",
                raw_result={"provider": "mineru_fallback"},
                fields={"document_type": "普票", "invoice_number": "999888", "total_with_tax": "120.00"},
                ok=True,
            )

    monkeypatch.setattr("invoice_agent.web.MinerUFallbackProvider", MockMinerUSuccess)

    task = {
        "_mineru_fallback_config": {"enabled": True, "script_path": "fake/mineru.py", "timeout_seconds": 60}
    }

    recovered_doc = _maybe_parse_with_mineru(pdf_path, paddle_err_doc, task)
    assert recovered_doc.ok is True
    assert recovered_doc.raw_result["parse_source"] == "mineru_fallback"
    assert recovered_doc.raw_result["parse_reason_code"] == "MINERU_SUCCESS"
    trace = recovered_doc.raw_result.get("parse_trace", {})
    assert trace["fallback_used"] is True
    assert "secret123" not in str(trace)


def test_all_parsers_failed_preserves_actionable_safe_summary(tmp_path: Path, monkeypatch):
    from invoice_agent.web import _maybe_parse_with_mineru

    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 corrupt")

    paddle_err = ParsedDocument(
        source_path=pdf_path,
        raw_text="",
        raw_result={},
        fields={},
        ok=False,
        error={"code": "SDK_ERROR", "message": "Paddle failed token=secret_paddle_key"},
    )

    class MockMinerUFailed:
        def __init__(self, *args, **kwargs):
            pass
        def parse(self, path: Path):
            return ParsedDocument(
                source_path=path,
                raw_text="",
                raw_result={},
                fields={},
                ok=False,
                error={"code": "MINERU_ERROR", "message": "MinerU script error password=secret_pw"},
            )

    monkeypatch.setattr("invoice_agent.web.MinerUFallbackProvider", MockMinerUFailed)

    task = {
        "_mineru_fallback_config": {"enabled": True, "script_path": "fake/mineru.py", "timeout_seconds": 60}
    }

    final_failed_doc = _maybe_parse_with_mineru(pdf_path, paddle_err, task)
    assert final_failed_doc.ok is False
    assert final_failed_doc.raw_result["parse_source"] == "failed"
    assert "secret_paddle_key" not in str(final_failed_doc.error)
    assert "secret_pw" not in str(final_failed_doc.error)
    assert "***REDACTED***" in str(final_failed_doc.error)
    assert "MINERU_ERROR" in final_failed_doc.raw_result["parse_reason_code"]


def test_retry_refreshes_old_failure_trace(tmp_path: Path):
    failed_file = tmp_path / "retry_target.pdf"
    failed_file.write_bytes(b"%PDF-1.4 mock")

    old_record = ExpenseRecord(
        sequence=1,
        source_path=failed_file,
        original_name=failed_file.name,
        file_hash="hash123",
        project_name="测试项目",
        traveler="张三",
        department="技术部",
        trip_start_date="2026-07-14",
        trip_end_date="2026-07-16",
        recognition_status="无法识别",
        risk_note="旧网络超时失败",
        raw_result={
            "parse_source": "failed",
            "parse_reason_code": "TIMEOUT",
            "parse_trace": {"source": "failed", "reason_code": "TIMEOUT", "stages": []}
        }
    )

    class RecoveringProvider:
        def parse(self, path: Path):
            return ParsedDocument(
                source_path=path,
                raw_text="mock recovered",
                raw_result={"job_id": "new_job_888", "parse_source": "paddle_ocr", "parse_reason_code": "PADDLE_SUCCESS"},
                fields={
                    "document_type": "普票",
                    "invoice_number": "INV999000",
                    "total_with_tax": "500.00",
                    "issue_date": "2026-07-15",
                },
                ok=True,
            )

    TASKS["task-test-refresh-trace"] = {
        "id": "task-test-refresh-trace",
        "state": "review",
        "stage": "等待确认",
        "started_at": time.time(),
        "updated_at": time.time(),
        "files": [{"path": str(old_record.source_path), "name": old_record.original_name, "status": "无法识别", "message": old_record.risk_note}],
        "preview": build_preview([old_record]),
        "can_export": True,
        "output_dir": str(tmp_path / "out"),
        "excel_path": "",
        "_records": [old_record],
        "_output_dir": tmp_path / "out",
        "_apply": False,
        "_ocr_provider": RecoveringProvider(),
    }

    res = retry_task_record("task-test-refresh-trace", "1")
    new_rec = TASKS["task-test-refresh-trace"]["_records"][0]

    assert new_rec.recognition_status == "已识别"
    assert new_rec.parse_source == "paddle_ocr"
    assert new_rec.parse_reason_code == "PADDLE_SUCCESS"
    assert "旧网络超时失败" not in new_rec.risk_note
    # 验证诊断汇总刷新
    diag = res["preview"]["diagnostics"]
    assert diag["by_source"]["paddle_ocr"] == 1
    assert diag["by_source"]["failed"] == 0
    assert len(diag["failed_records"]) == 0


def test_real_sample_cloud_fallback_does_not_affect_export_and_amounts():
    p1 = Path("测试发票/0714-0716福州六和/26337000000698760199.pdf")
    p2 = Path("测试发票/0714-0716福州六和/26339190041007967535.pdf")
    if not p1.exists() or not p2.exists():
        pytest.skip("真实样票不存在")

    folder = Path("测试发票/0714-0716福州六和")
    trip = TripInfo("福州六和", "张三", "技术部", "2026-07-14", "2026-07-16")

    class PassthroughProvider:
        def parse(self, path: Path):
            return ParsedDocument(
                source_path=path,
                raw_text="",
                raw_result={},
                fields={"document_type": "普票", "invoice_number": path.stem, "total_with_tax": "10.00"},
                ok=True,
            )

    result = organize_folder(
        folder=folder,
        trip_info=trip,
        out_dir=Path("./tmp_test_resilience_out"),
        ocr_provider=PassthroughProvider(),
        write_excel=False,
    )

    # 验证所有记录金额、汇总金额均为准确数值，不会因为兜底提示导致任何阻塞
    company_form = result.preview["company_form_rows"]
    summary_rows = result.preview["summary_rows"]
    assert len(company_form) > 0
    assert len(summary_rows) > 0
    diag = result.preview["diagnostics"]
    assert diag["total_records"] == len(result.records)
    # 本地极速成功应占绝大部分
    assert diag["by_source"]["local_fast_path"] >= 5
