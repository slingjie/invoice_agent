import time
from pathlib import Path
from invoice_agent.fast_probe import (
    extract_pdf_embedded_text,
    normalize_pdf_text,
    probe_pdf_locally,
    probe_train_ticket,
    probe_didi_and_ride,
    probe_flight_booking,
    probe_standard_china_einvoice,
)


def test_normalize_pdf_text_kangxi_radicals():
    # 康熙部首子 \u2f26 -> \u5b50
    raw = "电\u2f26发票（增值税专用发票）\xa0¥\u3000100.00"
    norm = normalize_pdf_text(raw)
    assert "电子发票" in norm
    assert "\xa0" not in norm


def test_fast_probe_vietnam_invoices_all_pass_locally():
    folder = Path("测试发票/0809-0816越南原始发票")
    pdf_files = list(folder.glob("*.pdf"))
    assert len(pdf_files) == 4

    t0 = time.time()
    for p in pdf_files:
        doc = probe_pdf_locally(p)
        assert doc is not None, f"Failed to probe {p.name} locally"
        assert doc.ok is True
        assert doc.fields.get("total_with_tax") != ""
        assert doc.raw_result.get("fast_path") is True

    elapsed = time.time() - t0
    # 4 份发票本地极速探针应在 0.1 秒内全部完成
    assert elapsed < 0.2, f"Expected <0.2s, took {elapsed:.3f}s"


def test_fast_probe_fuzhou_invoices_all_pass_locally():
    folder = Path("测试发票/0714-0716福州六和")
    pdf_files = list(folder.glob("*.pdf"))
    assert len(pdf_files) >= 5

    for p in pdf_files:
        doc = probe_pdf_locally(p)
        assert doc is not None, f"Failed to probe {p.name} locally"
        assert doc.ok is True
        assert doc.fields.get("total_with_tax") != ""


def test_fast_probe_train_tickets_offline():
    folder = Path("测试发票/0615发票")
    train_pdfs = [p for p in folder.glob("*.pdf") if p.name.startswith("263") and not p.name.startswith("26337")]
    assert len(train_pdfs) >= 5

    for p in train_pdfs:
        doc = probe_pdf_locally(p)
        assert doc is not None
        assert doc.ok is True
        assert doc.fields.get("document_type") == "高铁发票"
        assert doc.fields.get("origin") != ""
        assert doc.fields.get("destination") != ""


def test_fast_probe_sample_subway_ticket_exact_fields():
    import pytest
    p = Path("测试发票/0714-0716福州六和/26337000000698760199.pdf")
    if not p.exists():
        pytest.skip("真实样票 26337000000698760199.pdf 不存在，跳过离线回归断言")

    doc = probe_pdf_locally(p)
    assert doc is not None
    assert doc.ok is True
    # 验证修复后的金额必须为 6.00（价税合计），不得错误返回明细金额 5.83
    assert doc.fields.get("total_with_tax") == "6.00"
    assert doc.fields.get("invoice_number") == "26337000000698760199"


def test_fast_probe_sample_train_ticket_exact_fields():
    import pytest
    p = Path("测试发票/0714-0716福州六和/26339190041007967535.pdf")
    if not p.exists():
        pytest.skip("真实样票 26339190041007967535.pdf 不存在，跳过离线回归断言")

    doc = probe_pdf_locally(p)
    assert doc is not None
    assert doc.ok is True
    # 验证修复后的路线：杭州东站 -> 福州南站，出行日期 2026-07-14，购方清洗无税号标签
    assert doc.fields.get("origin") == "杭州东站"
    assert doc.fields.get("destination") == "福州南站"
    assert doc.fields.get("travel_date") == "2026-07-14"
    assert doc.fields.get("buyer_name") == "杭州勤合能源科技有限公司"
    assert doc.fields.get("total_with_tax") == "353.00"


def test_is_fast_probe_result_complete_threshold():
    from invoice_agent.fast_probe import is_fast_probe_result_complete
    from invoice_agent.models import ParsedDocument

    # 1. 空或失败结果
    assert not is_fast_probe_result_complete(None)
    assert not is_fast_probe_result_complete(ParsedDocument(Path("x.pdf"), "", {}, {}, False))

    # 2. 金额缺失或为0
    doc_no_amt = ParsedDocument(Path("x.pdf"), "", {}, {"document_type": "普票", "invoice_number": "123", "total_with_tax": ""}, True)
    assert not is_fast_probe_result_complete(doc_no_amt)
    doc_zero_amt = ParsedDocument(Path("x.pdf"), "", {}, {"document_type": "普票", "invoice_number": "123", "total_with_tax": "0.00"}, True)
    assert not is_fast_probe_result_complete(doc_zero_amt)

    # 3. 高铁发票缺少起点/终点/乘车日期/发票号 -> 不完整
    doc_train_incomplete = ParsedDocument(
        Path("x.pdf"), "", {},
        {"document_type": "高铁发票", "invoice_number": "123", "total_with_tax": "100.00", "origin": "", "destination": "福州站", "travel_date": "2026-07-14"},
        True
    )
    assert not is_fast_probe_result_complete(doc_train_incomplete)

    # 4. 高铁发票缺少 buyer_name（非致命字段） -> 依然完整
    doc_train_no_buyer = ParsedDocument(
        Path("x.pdf"), "", {},
        {"document_type": "高铁发票", "invoice_number": "123", "total_with_tax": "100.00", "origin": "杭州东站", "destination": "福州站", "travel_date": "2026-07-14", "buyer_name": ""},
        True
    )
    assert is_fast_probe_result_complete(doc_train_no_buyer)

    # 5. 普通发票缺少发票号码 -> 不完整
    doc_inv_no_num = ParsedDocument(
        Path("x.pdf"), "", {},
        {"document_type": "普票", "invoice_number": "", "total_with_tax": "100.00"},
        True
    )
    assert not is_fast_probe_result_complete(doc_inv_no_num)

    # 6. 行程单无发票号但有金额 -> 完整
    doc_itinerary = ParsedDocument(
        Path("x.pdf"), "", {},
        {"document_type": "行程单", "invoice_number": "", "total_with_tax": "100.00"},
        True
    )
    assert is_fast_probe_result_complete(doc_itinerary)


def test_pipeline_falls_back_to_ocr_when_fast_probe_incomplete(tmp_path: Path, monkeypatch):
    import fitz
    import invoice_agent.fast_probe as fp
    from invoice_agent.pipeline import organize_folder
    from invoice_agent.models import TripInfo, ParsedDocument

    src = tmp_path / "test_folder"
    src.mkdir()
    pdf_file = src / "test_invoice.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf_file))
    doc.close()

    # 模拟本地探针解析出不完整的高铁票（缺少起点和终点）
    def mock_incomplete_probe(path: Path):
        return ParsedDocument(
            source_path=path,
            raw_text="mock",
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

    class FallbackTrackingProvider:
        def __init__(self):
            self.called_paths = []

        def parse(self, path: Path):
            self.called_paths.append(path)
            return ParsedDocument(
                source_path=path,
                raw_text="ocr fallback text",
                raw_result={"ocr": "fallback"},
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

    tracking_provider = FallbackTrackingProvider()

    res = organize_folder(
        folder=src,
        trip_info=TripInfo("测试项目", "张三", "技术部", "2026-07-14", "2026-07-16"),
        out_dir=tmp_path / "out",
        ocr_provider=tracking_provider,
        write_excel=False,
    )

    assert pdf_file in tracking_provider.called_paths
    assert len(res.records) == 1
    assert res.records[0].origin == "杭州东站"
    assert res.records[0].destination == "福州南站"
