from decimal import Decimal
from pathlib import Path

from invoice_agent.company_reimbursement import build_company_reimbursement_data
from invoice_agent.html_report import (
    export_company_html,
    format_exact_amount,
    render_reimbursement_html,
    to_chinese_upper_currency_exact,
)
from invoice_agent.models import ExpenseRecord


def test_format_exact_amount():
    assert format_exact_amount(Decimal("353")) == "353.00"
    assert format_exact_amount(Decimal("91.6")) == "91.60"
    assert format_exact_amount(Decimal("356.889")) == "356.889"
    assert format_exact_amount(Decimal("0.05")) == "0.05"
    assert format_exact_amount(Decimal("0")) == ""
    assert format_exact_amount(None) == ""


def test_to_chinese_upper_currency_exact():
    assert to_chinese_upper_currency_exact(Decimal("353")) == "人民币叁佰伍拾叁元整"
    assert to_chinese_upper_currency_exact(Decimal("1005.08")) == "人民币壹仟零伍元零捌分"
    assert to_chinese_upper_currency_exact(Decimal("0.58")) == "人民币零元伍角捌分"
    assert to_chinese_upper_currency_exact(Decimal("356.889")) == "人民币叁佰伍拾陆元捌角捌分玖厘"
    assert to_chinese_upper_currency_exact(Decimal("123.4567")) == "人民币壹佰贰拾叁元肆角伍分陆厘柒毫"


def test_render_reimbursement_html_structure(tmp_path: Path):
    rec = ExpenseRecord(
        sequence=1,
        source_path=tmp_path / "test.pdf",
        original_name="test.pdf",
        file_hash="hash123",
        project_name="测试出差项目",
        traveler="石凌杰",
        department="研发部",
        trip_start_date="2026-07-14",
        trip_end_date="2026-07-16",
        document_date="2026-07-14",
        document_type="高铁发票",
        reimbursement_category="行程交通费",
        seller_name="中国铁路",
        total_with_tax="353.00",
        origin="杭州东",
        destination="福州南",
        include_in_amount=True,
    )
    data = build_company_reimbursement_data([rec])
    html = render_reimbursement_html(data)

    # 1. 验证 A5 打印样式约束
    assert "@page" in html
    assert "size: A5 landscape" in html
    # 2. 验证就地微调 contenteditable 支持
    assert "contenteditable" in html
    # 3. 验证业务内容包含
    assert "测试出差项目" in html
    assert "石凌杰" in html
    assert "353.00" in html
    assert "人民币" in html

    # 4. 验证导出到物理文件
    out_file = tmp_path / "01_公司报销单_A5可编辑打印台.html"
    export_company_html(data, out_file)
    assert out_file.exists()
    assert out_file.stat().st_size > 1000
