# -*- coding: utf-8 -*-
"""
HTML interactive A5/A4 reimbursement voucher generator.
Provides 1:1 pixel-perfect visual match with company Excel/PDF templates.
Features:
- Exact decimal preservation (NO rounding, displays all fractional digits)
- Anti-overflow layout (generous column width, overflow:visible, never truncated or obscured)
- In-place editing and real-time formula recalculation
- A5 landscape physical print locking
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .company_reimbursement import CompanyReimbursementData


def format_exact_amount(amount: Decimal | float | int | str | None) -> str:
    """
    精确格式化金额：
    1. 绝不四舍五入，保留所有原始小数位；
    2. 若只有1位小数或无小数，补足到至少2位（标准财务习惯：353 -> 353.00，91.6 -> 91.60）；
    3. 若有3位、4位或更多小数，原样全量输出（如 356.889 -> 356.889）。
    """
    if amount is None or amount == "":
        return ""
    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except Exception:
            return str(amount)
    if amount == 0:
        return ""

    s = f"{amount:f}"
    if "." in s:
        s_int, s_frac = s.split(".", 1)
        if len(s_frac) < 2:
            s_frac = s_frac.ljust(2, "0")
        return f"{s_int}.{s_frac}"
    else:
        return f"{s}.00"


def to_chinese_upper_currency_exact(amount: Decimal | float | int | str | None) -> str:
    """
    将金额转换为标准财务中文大写：
    支持全量小数位（角、分、厘、毫），绝不截断丢弃分以下小数。
    """
    if amount is None or amount == "":
        return "人民币零元整"
    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except Exception:
            amount = Decimal("0")

    if amount == 0:
        return "人民币零元整"

    digits = "零壹贰叁肆伍陆柒捌玖"
    radices = ["", "拾", "佰", "仟"]
    big_radices = ["", "万", "亿"]

    is_negative = amount < 0
    amount = abs(amount)

    s = f"{amount:f}"
    if "." in s:
        s_int, s_frac = s.split(".", 1)
    else:
        s_int, s_frac = s, ""

    integer_part = int(s_int) if s_int else 0
    res = []

    if integer_part > 0:
        length = len(s_int)
        zero_flag = False
        for idx, ch in enumerate(s_int):
            pos = length - idx - 1
            digit = int(ch)
            radix_idx = pos % 4
            big_radix_idx = pos // 4

            if digit != 0:
                if zero_flag:
                    res.append("零")
                    zero_flag = False
                res.append(digits[digit])
                res.append(radices[radix_idx])
            else:
                if radix_idx != 0:
                    zero_flag = True

            if radix_idx == 0 and big_radix_idx > 0:
                if res and res[-1] in big_radices:
                    pass
                else:
                    res.append(big_radices[big_radix_idx])
                zero_flag = False
        res.append("元")
    else:
        res.append("零元")

    # 处理小数部分（角、分、厘、毫）
    frac_units = ["角", "分", "厘", "毫"]
    has_frac = False
    for i, ch in enumerate(s_frac[:4]):
        d = int(ch)
        if d != 0:
            res.append(digits[d] + frac_units[i])
            has_frac = True
        elif i == 0 and integer_part > 0 and any(int(x) > 0 for x in s_frac[1:4]):
            res.append("零")

    if not has_frac:
        res.append("整")

    prefix = "负人民币" if is_negative else "人民币"
    return prefix + "".join(res)


def render_reimbursement_html(data: CompanyReimbursementData) -> str:
    # 拆分大交通与市内交通
    trip_rows = [r for r in data.travel_rows if getattr(r, "kind", "") in ("intercity", "trip")]
    city_rows = [r for r in data.travel_rows if getattr(r, "kind", "") == "city"]

    trip_total = sum((r.amount for r in trip_rows), Decimal("0.00"))
    city_total = sum((r.amount for r in city_rows), Decimal("0.00"))
    transport_total = trip_total + city_total

    meal_days = data.meal_allowance.count or data.trip_days
    meal_total = data.meal_allowance.amount

    other_total = data.lodging_total + data.toll_total + data.fuel_total + data.refund_total
    grand_total = transport_total + meal_total + other_total

    upper_total = to_chinese_upper_currency_exact(grand_total)
    export_date_str = data.export_date.strftime("%Y年%m月%d日") if data.export_date else date.today().strftime("%Y年%m月%d日")

    # 填充表格行（行程交通固定6行，市区交通固定6行）
    def pad_list(rows: list, length: int):
        padded = list(rows)
        while len(padded) < length:
            padded.append(None)
        return padded

    padded_trip = pad_list(trip_rows, 6)
    padded_city = pad_list(city_rows, 6)

    # 其它费用固定项目（住宿费、过路费、油费、退改费）
    other_items = [
        ("住宿费", data.lodging_total),
        ("过路费", data.toll_total),
        ("油  费", data.fuel_total),
        ("退改费", data.refund_total),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
        ("", Decimal("0.00")),
    ]

    # 日常费用行
    daily_items = pad_list(data.daily_rows, 8)
    daily_total = sum((r.amount for r in data.daily_rows), Decimal("0.00"))
    daily_upper = to_chinese_upper_currency_exact(daily_total)

    # HTML 模板输出
    html = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>公司报销单 - {data.project_name} - {data.traveler}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #cfd8dc;
      font-family: "SimSun", "Songti SC", "Microsoft YaHei", serif;
      color: #000;
      padding-bottom: 50px;
    }}

    /* 顶部控制栏（打印时隐藏） */
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 1000;
      background: #263238;
      color: #eceff1;
      padding: 10px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: 0 3px 10px rgba(0,0,0,0.2);
      font-family: -apple-system, BlinkMacSystemFont, "Microsoft YaHei", sans-serif;
    }}
    .tabs {{ display: flex; gap: 8px; }}
    .tab-btn {{
      background: #37474f;
      color: #b0bec5;
      border: 1px solid #546e7a;
      padding: 6px 14px;
      border-radius: 4px;
      font-size: 13px;
      cursor: pointer;
      font-weight: 500;
      transition: all 0.2s;
    }}
    .tab-btn:hover {{ background: #546e7a; color: #fff; }}
    .tab-btn.active {{
      background: #1976d2;
      color: #fff;
      border-color: #2196f3;
    }}
    .btn-group {{ display: flex; gap: 10px; align-items: center; }}
    .btn-action {{
      background: #2e7d32;
      color: #fff;
      border: none;
      padding: 7px 16px;
      border-radius: 4px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn-action:hover {{ background: #1b5e20; }}
    .btn-action.blue {{ background: #0288d1; }}
    .btn-action.blue:hover {{ background: #01579b; }}

    /* 纸张容器 */
    .paper-container {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
      margin-top: 20px;
    }}

    /* A5 横向纸张规格：210mm x 148mm */
    .sheet-a5 {{
      width: 210mm;
      height: 148mm;
      background: #fff;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      padding: 7mm 10mm 5mm 10mm;
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}

    /* A4 纵向纸张规格：210mm x 297mm */
    .sheet-a4 {{
      width: 210mm;
      min-height: 297mm;
      background: #fff;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      padding: 12mm 14mm;
      position: relative;
    }}

    /* 标题与填报日期 */
    .header-box {{
      text-align: center;
      margin-bottom: 2px;
    }}
    .sheet-title {{
      font-size: 20pt;
      font-weight: bold;
      letter-spacing: 4px;
      font-family: "SimSun", "Songti SC", serif;
    }}
    .date-row {{
      text-align: right;
      font-size: 10pt;
      margin-top: -2px;
      padding-right: 2px;
    }}

    /* 核心表格样式：无遮挡、防截断 */
    table.table-voucher {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 9pt;
      border: 1px solid #000;
    }}
    table.table-voucher td, table.table-voucher th {{
      border: 1px solid #000;
      padding: 1px 2px;
      text-align: center;
      vertical-align: middle;
      height: 15pt;
      line-height: 1.25;
    }}

    /* 专门为金额单元格设计的抗遮挡无损样式 */
    .amt-cell, .traffic-amt, .other-amt, .subsidy-amt, .daily-amt, .sub-item-amt {{
      overflow: visible !important;
      text-overflow: clip !important;
      white-space: nowrap !important;
      text-align: right !important;
      padding-right: 4px !important;
      font-variant-numeric: tabular-nums;
    }}

    .editable {{
      cursor: text;
      outline: none;
    }}
    .editable:focus {{
      background: #e3f2fd !important;
      box-shadow: inset 0 0 0 1px #1976d2;
    }}

    /* 签字栏 */
    .signature-area {{
      font-size: 9.5pt;
      margin-top: 3px;
    }}
    .sig-row {{
      display: flex;
      justify-content: space-between;
      padding: 2px 4px;
    }}
    .sig-col {{
      flex: 1;
      display: flex;
      align-items: center;
    }}
    .sig-line {{
      flex: 1;
      border-bottom: 1px solid #000;
      margin: 0 10px 0 2px;
      height: 14px;
    }}

    /* 粘贴单 */
    .paste-box {{
      height: 100%;
      position: relative;
    }}
    .paste-left-line {{
      position: absolute;
      top: 0;
      bottom: 0;
      left: 32mm;
      border-left: 1.5px dashed #444;
    }}
    .paste-title {{
      text-align: center;
      font-size: 18pt;
      font-weight: bold;
      letter-spacing: 3px;
      margin-top: 4mm;
    }}
    .paste-rules {{
      position: absolute;
      bottom: 12mm;
      left: 36mm;
      right: 56mm;
      font-size: 8.5pt;
      line-height: 1.6;
      color: #222;
    }}
    .paste-table {{
      position: absolute;
      top: 6mm;
      right: 2mm;
      width: 48mm;
      border-collapse: collapse;
      border: 1px solid #000;
      font-size: 8.5pt;
    }}
    .paste-table td {{
      border: 1px solid #000;
      padding: 3px 2px;
      text-align: center;
      height: 18px;
    }}

    .tab-pane {{ display: none; width: 100%; }}
    .tab-pane.active {{ display: flex; flex-direction: column; align-items: center; gap: 20px; }}

    /* 打印样式：锁定 A5 横向 / A4 纵向，零边距，绝不溢出 */
    @media print {{
      body {{ background: #fff !important; padding: 0 !important; }}
      .toolbar {{ display: none !important; }}
      .paper-container {{ margin: 0 !important; gap: 0 !important; }}
      .tab-pane {{ display: none !important; }}
      .tab-pane.active {{ display: block !important; }}
      .sheet-a5 {{
        box-shadow: none !important;
        margin: 0 !important;
        padding: 5mm 10mm 5mm 10mm !important;
        page-break-after: always;
        break-after: page;
      }}
      .sheet-a4 {{
        box-shadow: none !important;
        margin: 0 !important;
        padding: 10mm !important;
        page-break-after: always;
        break-after: page;
      }}
      @page {{
        size: A5 landscape;
        margin: 0;
      }}
    }}
  </style>
</head>
<body>

  <div class="toolbar">
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('tab-travel')">差旅费报销单 (A5)</button>
      <button class="tab-btn" onclick="switchTab('tab-daily')">日常费用报销单 (A5)</button>
      <button class="tab-btn" onclick="switchTab('tab-detail')">报销明细表 (A4)</button>
    </div>
    <div class="btn-group">
      <select id="printSelect" style="background:#37474f; color:#fff; padding:6px 10px; border-radius:4px; border:1px solid #546e7a; font-size:13px;" onchange="filterPrintScope()">
        <option value="all">打印整份 (主表 + 粘贴单)</option>
        <option value="main">仅打印主表 (Page 1)</option>
        <option value="paste">仅打印粘贴单 (Page 2)</option>
      </select>
      <button class="btn-action" onclick="window.print()">🖨️ 打印当前单据</button>
      <button class="btn-action blue" onclick="downloadHtml()">💾 保存修改到本地</button>
    </div>
  </div>

  <div class="paper-container">

    <!-- TAB 1: 差旅费报销单 (A5 横向) -->
    <div id="tab-travel" class="tab-pane active">
      
      <!-- Page 1: 差旅费报销单主表 -->
      <div class="sheet-a5 print-main">
        <div>
          <div class="header-box">
            <div class="sheet-title">差旅费报销单</div>
            <div class="date-row">填报日期：<span class="editable" contenteditable="true">{export_date_str}</span></div>
          </div>

          <table class="table-voucher" style="margin-top: 1px;">
            <!-- 列宽优化分配：大幅拓宽金额列，防遮挡 -->
            <colgroup>
              <col style="width: 3.0%;"> <!-- 竖排类别 -->
              <col style="width: 9.0%;"> <!-- 日期 -->
              <col style="width: 9.5%;"> <!-- 出发地 -->
              <col style="width: 10.5%;"> <!-- 目的地 -->
              <col style="width: 5.5%;"> <!-- 交通工具 -->
              <col style="width: 4.5%;"> <!-- 单据张数 -->
              <col style="width: 11.5%;"> <!-- 大交通金额（拓宽） -->
              <col style="width: 5.5%;"> <!-- 补贴项目 -->
              <col style="width: 4.5%;"> <!-- 补贴天数 -->
              <col style="width: 10.5%;"> <!-- 补贴金额（拓宽） -->
              <col style="width: 8.0%;"> <!-- 其他项目 -->
              <col style="width: 13.0%;"> <!-- 其他金额（拓宽） -->
            </colgroup>

            <!-- R03: 部门 / 出差人 / 出差天数 / 出差事由 -->
            <tr style="height: 19pt;">
              <td colspan="2" style="font-weight:bold;">部 门</td>
              <td class="editable" contenteditable="true">{data.department or "项目部"}</td>
              <td style="font-weight:bold;">出差人</td>
              <td colspan="2" class="editable" contenteditable="true">{data.traveler}</td>
              <td style="font-weight:bold;">出差天数</td>
              <td class="editable" contenteditable="true" id="meal-days" oninput="recalc()">{meal_days}</td>
              <td style="font-weight:bold;">出差事由</td>
              <td colspan="3" style="text-align:left; padding-left:4px;" class="editable" contenteditable="true">{data.project_name}</td>
            </tr>

            <!-- R04~R05: 表头 -->
            <tr style="height: 14pt; font-weight:bold;">
              <td rowspan="8" style="letter-spacing:1px; line-height:1.2; font-weight:bold; width:3.0%;">行<br>程<br>交<br>通<br>费</td>
              <td rowspan="2">日 期</td>
              <td rowspan="2">出发地</td>
              <td rowspan="2">目的地</td>
              <td rowspan="2">交通<br>工具</td>
              <td rowspan="2">单据<br>张数</td>
              <td rowspan="2">金 额</td>
              <td colspan="3">其他补贴</td>
              <td colspan="2">其他费用</td>
            </tr>
            <tr style="height: 14pt; font-weight:bold;">
              <td>项 目</td>
              <td>天数</td>
              <td>金 额</td>
              <td>项 目</td>
              <td>金 额</td>
            </tr>
"""]

    # 填充 12 行明细行（前6行大交通，后6行市区交通）
    for i in range(12):
        is_trip = i < 6
        sub_idx = i if is_trip else i - 6
        row_obj = padded_trip[sub_idx] if is_trip else padded_city[sub_idx]

        d_str = row_obj.date_text if row_obj else ""
        orig_str = row_obj.origin if row_obj else ""
        dest_str = row_obj.destination if row_obj else ""
        trans_str = row_obj.transport if row_obj else ""
        cnt_str = str(row_obj.count) if row_obj and row_obj.count else ""
        amt_str = format_exact_amount(row_obj.amount) if row_obj else ""

        # 右侧其他费用
        other_lbl, other_amt_dec = other_items[i]
        other_amt_str = format_exact_amount(other_amt_dec) if other_amt_dec > 0 else ""

        html.append('            <tr style="height: 13.5pt;">')
        if i == 6:
            html.append('              <td rowspan="6" style="letter-spacing:1px; line-height:1.2; font-weight:bold; width:3.0%;">市<br>区<br>交<br>通<br>费</td>')

        html.append(f'              <td class="editable" contenteditable="true">{d_str}</td>')
        html.append(f'              <td class="editable" contenteditable="true">{orig_str}</td>')
        html.append(f'              <td class="editable" contenteditable="true">{dest_str}</td>')
        html.append(f'              <td class="editable" contenteditable="true">{trans_str}</td>')
        html.append(f'              <td class="editable" contenteditable="true">{cnt_str}</td>')
        html.append(f'              <td class="editable traffic-amt amt-cell" contenteditable="true" oninput="recalc()">{amt_str}</td>')

        if i == 0:
            meal_amt_str = format_exact_amount(meal_total)
            html.append('              <td>餐补</td>')
            html.append(f'              <td class="editable" id="meal-days-sub" contenteditable="true" oninput="recalc()">{meal_days}</td>')
            html.append(f'              <td class="editable subsidy-amt amt-cell" id="meal-amt-cell" contenteditable="true" oninput="recalc()">{meal_amt_str}</td>')
        else:
            html.append('              <td></td><td></td><td></td>')

        html.append(f'              <td>{other_lbl}</td>')
        html.append(f'              <td class="editable other-amt amt-cell" contenteditable="true" oninput="recalc()">{other_amt_str}</td>')
        html.append('            </tr>')

    formatted_traffic_total = format_exact_amount(transport_total)
    formatted_meal_total = format_exact_amount(meal_total)
    formatted_other_total = format_exact_amount(other_total)
    formatted_grand_total = format_exact_amount(grand_total)

    html.append(f"""            <!-- R18: 合计行 -->
            <tr style="height: 19pt; font-weight:bold;">
              <td colspan="6" style="text-align:right; padding-right:12px;">交通费合计</td>
              <td id="sum-traffic" class="amt-cell" style="font-weight:bold;">{formatted_traffic_total}</td>
              <td colspan="2">补贴合计</td>
              <td id="sum-subsidy" class="amt-cell" style="font-weight:bold;">{formatted_meal_total}</td>
              <td>其他费用<br>合计</td>
              <td id="sum-other" class="amt-cell" style="font-weight:bold;">{formatted_other_total}</td>
            </tr>

            <!-- R19: 总额行 -->
            <tr style="height: 20pt; font-weight:bold;">
              <td colspan="2">报销总额（大写）</td>
              <td colspan="7" style="text-align:left; padding-left:10px; font-size:10pt;" id="grand-upper">{upper_total}</td>
              <td colspan="3" style="font-size:11pt; text-align:right; padding-right:8px; overflow:visible;" id="grand-num">¥ {formatted_grand_total}</td>
            </tr>
          </table>

          <!-- R21~R22: 审批签字栏 -->
          <div class="signature-area">
            <div class="sig-row">
              <div class="sig-col">报销人： <span class="editable" contenteditable="true">{data.traveler}</span><div class="sig-line"></div></div>
              <div class="sig-col">部门主管： <div class="sig-line"></div></div>
              <div class="sig-col">财务审核： <div class="sig-line"></div></div>
            </div>
            <div class="sig-row" style="margin-top: 4px;">
              <div class="sig-col">总经理： <div class="sig-line"></div></div>
              <div class="sig-col">董事长： <div class="sig-line"></div></div>
              <div class="sig-col">出  纳： <div class="sig-line"></div></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Page 2: 原始单据粘贴单 -->
      <div class="sheet-a5 print-paste">
        <div class="paste-box">
          <div class="paste-title">原始单据粘贴单</div>
          <div class="paste-left-line"></div>
          <div class="paste-rules">
            <p>1、虚线为凭证装订位置，请不要将发票贴过虚线外位置。</p>
            <p>2、如果发票超过原始单据粘贴单边缘，请折叠整齐，大小同粘贴单。</p>
            <p>3、注意，粘贴单与报销单仅用胶水粘贴虚线左上角部分便可，虚线以内粘贴原始凭据用。</p>
          </div>
          <table class="paste-table">
            <tr style="font-weight:bold; background:#fafafa;">
              <td style="width:45%;">单据类别</td>
              <td style="width:25%;">单据张数</td>
              <td style="width:30%;">单据金额</td>
            </tr>
            <tr><td>城际大交通</td><td class="editable" contenteditable="true">{len(trip_rows)}</td><td class="editable amt-cell" contenteditable="true">{format_exact_amount(trip_total)}</td></tr>
            <tr><td>市区交通</td><td class="editable" contenteditable="true">{len(city_rows)}</td><td class="editable amt-cell" contenteditable="true">{format_exact_amount(city_total)}</td></tr>
            <tr><td>住宿费</td><td class="editable" contenteditable="true">{"1" if data.lodging_total > 0 else ""}</td><td class="editable amt-cell" contenteditable="true">{format_exact_amount(data.lodging_total)}</td></tr>
            <tr><td>其他补贴</td><td class="editable" contenteditable="true">0</td><td class="editable amt-cell" contenteditable="true">{format_exact_amount(meal_total)}</td></tr>
            <tr style="font-weight:bold;"><td>合 计</td><td class="editable" contenteditable="true">{len(trip_rows)+len(city_rows)+(1 if data.lodging_total > 0 else 0)}</td><td class="editable amt-cell" contenteditable="true">{formatted_grand_total}</td></tr>
          </table>
        </div>
      </div>

    </div>

    <!-- TAB 2: 日常费用报销单 (A5 横向) -->
    <div id="tab-daily" class="tab-pane">
      <div class="sheet-a5 print-main">
        <div>
          <div class="header-box">
            <div class="sheet-title" style="margin-top: 2px;">日常费用报销单</div>
            <div style="display:flex; justify-content:space-between; font-size:9.5pt; margin-top:4px; padding:0 2px;">
              <div>所属部门： <span class="editable" contenteditable="true">{data.department or "项目部"}</span></div>
              <div>报销人： <span class="editable" contenteditable="true">{data.traveler}</span></div>
              <div>填报日期： <span class="editable" contenteditable="true">{export_date_str}</span></div>
            </div>
          </div>

          <table class="table-voucher" style="margin-top: 4px;">
            <tr style="height: 18pt; font-weight:bold; background:#fafafa;">
              <td style="width: 22%;">发生日期</td>
              <td style="width: 58%;">报销内容</td>
              <td style="width: 20%;">金  额</td>
            </tr>
""")

    for d_row in daily_items:
        d_date = d_row.date_text if d_row else ""
        d_content = d_row.content if d_row else ""
        d_amt = format_exact_amount(d_row.amount) if d_row and d_row.amount > 0 else ""
        html.append(f"""            <tr style="height: 15pt;">
              <td class="editable" contenteditable="true">{d_date}</td>
              <td class="editable" style="text-align:left; padding-left:8px;" contenteditable="true">{d_content}</td>
              <td class="editable daily-amt amt-cell" contenteditable="true" oninput="recalcDaily()">{d_amt}</td>
            </tr>""")

    formatted_daily_total = format_exact_amount(daily_total)

    html.append(f"""            <tr style="height: 20pt; font-weight:bold;">
              <td>合  计</td>
              <td style="text-align:left; padding-left:8px;" id="daily-upper">{daily_upper}</td>
              <td id="daily-num" class="amt-cell" style="font-size:10.5pt; font-weight:bold;">¥ {formatted_daily_total}</td>
            </tr>
          </table>

          <div class="signature-area" style="margin-top: 10px;">
            <div class="sig-row">
              <div class="sig-col">报销人： <span class="editable" contenteditable="true">{data.traveler}</span><div class="sig-line"></div></div>
              <div class="sig-col">部门主管： <div class="sig-line"></div></div>
              <div class="sig-col">财务审核： <div class="sig-line"></div></div>
            </div>
            <div class="sig-row" style="margin-top: 6px;">
              <div class="sig-col">总经理： <div class="sig-line"></div></div>
              <div class="sig-col">董事长： <div class="sig-line"></div></div>
              <div class="sig-col">出  纳： <div class="sig-line"></div></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Page 2: 日常粘贴单 -->
      <div class="sheet-a5 print-paste">
        <div class="paste-box">
          <div class="paste-title">原始单据粘贴单</div>
          <div class="paste-left-line"></div>
          <div class="paste-rules">
            <p>1、虚线为凭证装订位置，请不要将发票贴过虚线外位置。</p>
            <p>2、如果发票超过原始单据粘贴单边缘，请折叠整齐，大小同粘贴单。</p>
            <p>3、注意，粘贴单与报销单仅用胶水粘贴虚线左上角部分便可，虚线以内粘贴原始凭据用。</p>
          </div>
          <table class="paste-table">
            <tr style="font-weight:bold; background:#fafafa;">
              <td style="width:45%;">单据类别</td>
              <td style="width:25%;">单据张数</td>
              <td style="width:30%;">单据金额</td>
            </tr>
            <tr><td>日常费用</td><td class="editable" contenteditable="true">{len(data.daily_rows)}</td><td class="editable amt-cell" contenteditable="true">{formatted_daily_total}</td></tr>
            <tr style="font-weight:bold;"><td>合 计</td><td class="editable" contenteditable="true">{len(data.daily_rows)}</td><td class="editable amt-cell" contenteditable="true">{formatted_daily_total}</td></tr>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 3: 报销明细表 (A4 纵向) -->
    <div id="tab-detail" class="tab-pane">
      <div class="sheet-a4">
        <div style="text-align:center; margin-bottom:8px;">
          <div style="font-size:20pt; font-weight:bold; letter-spacing:4px; font-family:'SimSun','Songti SC',serif;">报销明细表</div>
          <div style="display:flex; justify-content:space-between; font-size:10pt; margin-top:8px; padding:0 2px;">
            <div>项目名称： <span class="editable" contenteditable="true">{data.project_name}</span></div>
            <div>报销日期： <span class="editable" contenteditable="true">{export_date_str}</span></div>
          </div>
        </div>

        <table class="table-voucher" style="font-size:9pt; margin-top:4px;">
          <colgroup>
            <col style="width: 17%;">
            <col style="width: 12%;">
            <col style="width: 24%;">
            <col style="width: 25%;">
            <col style="width: 14%;"> <!-- 拓宽明细金额列 -->
            <col style="width: 8%;">
          </colgroup>
          <tr style="height:18pt; font-weight:bold; background:#fafafa;">
            <th>日 期</th>
            <th>人 物</th>
            <th>地 点</th>
            <th>目 的</th>
            <th>金 额</th>
            <th>单 据</th>
          </tr>
""")

    # 规范科目顺序
    standard_categories = [
        ("招待费（餐饮、娱乐）", ["招待费", "餐饮费", "业务招待"]),
        ("差旅费", ["差旅费", "住宿费", "餐补", "差旅补贴"]),
        ("交通费", ["交通费", "行程交通费", "市区交通费", "通行费", "过路费", "油费", "退改费"]),
        ("办公费", ["办公费", "办公用品", "耗材"]),
        ("礼品、礼卡", ["礼品、礼卡", "礼品", "礼卡"]),
        ("材料", ["材料", "材料费", "五金配件"]),
        ("其他费用", ["其他费用", "其他", "杂费"]),
    ]

    cat_buckets: dict[str, list] = {c[0]: [] for c in standard_categories}
    for sec_name, lines in data.detail_sections.items():
        matched = False
        for std_name, aliases in standard_categories:
            if sec_name == std_name or any(a in sec_name for a in aliases):
                cat_buckets[std_name].extend(lines)
                matched = True
                break
        if not matched:
            cat_buckets["其他费用"].extend(lines)

    grand_detail_sum = Decimal("0.00")
    total_bill_count = 0

    for std_name, _ in standard_categories:
        lines = cat_buckets[std_name]
        html.append(f"""          <tr style="height:16pt; background:#f4f4f4; font-weight:bold; text-align:left;">
            <td colspan="6" style="text-align:left; padding-left:8px; font-size:9.5pt;">{std_name}</td>
          </tr>""")

        sec_subtotal = Decimal("0.00")
        sec_count = 0

        if lines:
            for l in lines:
                sec_subtotal += l.amount
                sec_count += l.count
                amt_str = format_exact_amount(l.amount)
                cnt_str = str(l.count) if l.count else "0"
                html.append(f"""          <tr style="height:15pt;">
            <td class="editable" contenteditable="true">{l.date_text}</td>
            <td class="editable" contenteditable="true">{l.person}</td>
            <td class="editable" style="text-align:left; padding-left:4px;" contenteditable="true">{l.location}</td>
            <td class="editable" style="text-align:left; padding-left:4px;" contenteditable="true">{l.purpose}</td>
            <td class="editable sub-item-amt amt-cell" contenteditable="true" oninput="recalcDetail()">{amt_str}</td>
            <td class="editable sub-item-cnt" contenteditable="true" oninput="recalcDetail()">{cnt_str}</td>
          </tr>""")
        else:
            html.append("""          <tr style="height:14pt;">
            <td class="editable" contenteditable="true"></td>
            <td class="editable" contenteditable="true"></td>
            <td class="editable" contenteditable="true"></td>
            <td class="editable" contenteditable="true"></td>
            <td class="editable sub-item-amt amt-cell" contenteditable="true" oninput="recalcDetail()"></td>
            <td class="editable sub-item-cnt" contenteditable="true" oninput="recalcDetail()"></td>
          </tr>""")

        grand_detail_sum += sec_subtotal
        total_bill_count += sec_count

        sub_amt_str = format_exact_amount(sec_subtotal) if sec_subtotal > 0 else "0.00"
        html.append(f"""          <tr style="height:16pt; font-weight:bold; background:#fafafa;">
            <td colspan="4" style="text-align:right; padding-right:12px;">小     计</td>
            <td style="text-align:right; padding-right:4px;" class="sec-subtotal amt-cell">{sub_amt_str}</td>
            <td class="sec-subcount">{sec_count}</td>
          </tr>""")

    formatted_detail_grand = format_exact_amount(grand_detail_sum)

    html.append(f"""          <!-- 总计行 -->
          <tr style="height:20pt; font-weight:bold; background:#eeeeee; font-size:10pt;">
            <td colspan="4" style="text-align:right; padding-right:12px;">总  计</td>
            <td style="text-align:right; padding-right:4px; color:#b71c1c; font-size:10.5pt;" id="detail-grand-amt" class="amt-cell">¥ {formatted_detail_grand}</td>
            <td id="detail-grand-cnt">{total_bill_count}</td>
          </tr>

          <!-- 费用超支情况说明 -->
          <tr>
            <td style="font-weight:bold; height:45pt; vertical-align:middle;">费用超支<br>情况说明</td>
            <td colspan="5" style="text-align:left; vertical-align:top; padding:6px 8px; line-height:1.5;" class="editable" contenteditable="true"></td>
          </tr>
        </table>

        <!-- 审批签字栏 -->
        <div class="signature-area" style="margin-top:10px; font-size:10pt;">
          <div class="sig-row">
            <div class="sig-col">报销人： <span class="editable" contenteditable="true">{data.traveler}</span><div class="sig-line"></div></div>
            <div class="sig-col">项目经理： <div class="sig-line"></div></div>
            <div class="sig-col">财务审核： <div class="sig-line"></div></div>
            <div class="sig-col">总经理： <div class="sig-line"></div></div>
          </div>
        </div>
      </div>
    </div>

  </div>

  <script>
    function switchTab(id) {{
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      event.target.classList.add('active');
    }}

    function filterPrintScope() {{
      const v = document.getElementById('printSelect').value;
      document.querySelectorAll('.print-main').forEach(el => el.style.display = (v === 'paste') ? 'none' : 'flex');
      document.querySelectorAll('.print-paste').forEach(el => el.style.display = (v === 'main') ? 'none' : 'flex');
    }}

    // 高精度金额格式化：绝不四舍五入，保留所有有效小数位
    function formatExactJs(num, minDecimals = 2) {{
      if (isNaN(num) || num === 0) return '0.00';
      const s = num.toString();
      if (s.includes('.')) {{
        const parts = s.split('.');
        let frac = parts[1];
        while (frac.length < minDecimals) {{
          frac += '0';
        }}
        return parts[0] + '.' + frac;
      }}
      return s + '.00';
    }}

    // 高精度中文大写转换（支持角分厘毫）
    function digitToChinese(num) {{
      if (isNaN(num) || num === 0) return '人民币零元整';
      const fractionUnits = ['角', '分', '厘', '毫'];
      const digits = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'];
      const units = [['元', '万', '亿'], ['', '拾', '佰', '仟']];
      
      let prefix = num < 0 ? '负人民币' : '人民币';
      num = Math.abs(num);
      
      const sNum = num.toString();
      const parts = sNum.split('.');
      const sInt = parts[0];
      const sFrac = parts[1] || '';
      
      let res = '';
      let intVal = parseInt(sInt, 10);
      
      if (intVal > 0) {{
        for (let i = 0; i < units[0].length && intVal > 0; i++) {{
          let p = '';
          for (let j = 0; j < units[1].length && intVal > 0; j++) {{
            p = digits[intVal % 10] + units[1][j] + p;
            intVal = Math.floor(intVal / 10);
          }}
          res = p.replace(/(零.)*零$/, '').replace(/^$/, '零') + units[0][i] + res;
        }}
        res = res.replace(/(零.)*零元/, '元').replace(/(零.)+/g, '零');
      }} else {{
        res = '零元';
      }}
      
      let fracStr = '';
      let hasFrac = false;
      for (let i = 0; i < fractionUnits.length && i < sFrac.length; i++) {{
        const d = parseInt(sFrac[i], 10);
        if (d !== 0) {{
          fracStr += digits[d] + fractionUnits[i];
          hasFrac = true;
        }} else if (i === 0 && intVal > 0 && sFrac.slice(1, 4).match(/[1-9]/)) {{
          fracStr += '零';
        }}
      }}
      
      if (!hasFrac) {{
        fracStr = '整';
      }}
      
      return prefix + res + fracStr;
    }}

    function parseCellAmount(td) {{
      const text = td.innerText.replace(/[^0-9.-]+/g, '').trim();
      return parseFloat(text) || 0;
    }}

    function recalc() {{
      let traffic = 0;
      document.querySelectorAll('.traffic-amt').forEach(td => {{
        traffic += parseCellAmount(td);
      }});
      document.getElementById('sum-traffic').innerText = formatExactJs(traffic);

      const days = parseInt(document.getElementById('meal-days').innerText.trim(), 10) || 0;
      const meal = parseCellAmount(document.getElementById('meal-amt-cell'));
      document.getElementById('sum-subsidy').innerText = formatExactJs(meal);

      let other = 0;
      document.querySelectorAll('.other-amt').forEach(td => {{
        other += parseCellAmount(td);
      }});
      document.getElementById('sum-other').innerText = formatExactJs(other);

      const grand = traffic + meal + other;
      document.getElementById('grand-num').innerText = '¥ ' + formatExactJs(grand);
      document.getElementById('grand-upper').innerText = digitToChinese(grand);
    }}

    function recalcDaily() {{
      let daily = 0;
      document.querySelectorAll('.daily-amt').forEach(td => {{
        daily += parseCellAmount(td);
      }});
      document.getElementById('daily-num').innerText = '¥ ' + formatExactJs(daily);
      document.getElementById('daily-upper').innerText = digitToChinese(daily);
    }}

    function recalcDetail() {{
      let grandAmt = 0;
      let grandCnt = 0;
      document.querySelectorAll('.sub-item-amt').forEach(td => {{
        grandAmt += parseCellAmount(td);
      }});
      document.querySelectorAll('.sub-item-cnt').forEach(td => {{
        grandCnt += parseInt(td.innerText.replace(/[^0-9]+/g, ''), 10) || 0;
      }});
      document.getElementById('detail-grand-amt').innerText = '¥ ' + formatExactJs(grandAmt);
      document.getElementById('detail-grand-cnt').innerText = grandCnt;
    }}

    function downloadHtml() {{
      const content = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
      const blob = new Blob([content], {{ type: 'text/html;charset=utf-8' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = document.title + '.html';
      a.click();
      URL.revokeObjectURL(url);
      alert('报销单已成功保存到本地！');
    }}
  </script>
</body>
</html>
""")
    return "\n".join(html)


def export_company_html(data: CompanyReimbursementData, output_path: Path) -> Path:
    """渲染并导出公司报销单 HTML 文件"""
    content = render_reimbursement_html(data)
    output_path.write_text(content, encoding="utf-8")
    return output_path
