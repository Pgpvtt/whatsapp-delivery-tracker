"""
Excel Report Generator — multi-sheet styled workbook.
Column names kept in sync with engine.py build_* output.
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference
import io

C_HEADER_BG = '1A3C5E'
C_HEADER_FG = 'FFFFFF'
C_ALT_ROW   = 'EBF3FB'
C_WARN_BG   = 'FFF3CD'
C_ERR_BG    = 'F8D7DA'
C_OK_BG     = 'D4EDDA'
C_TITLE_BG  = '0D6EFD'

_thin  = Side(style='thin', color='C0C0C0')
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _hdr(ws, row, headers, bg=C_HEADER_BG, fg=C_HEADER_FG):
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=h)
        c.font      = Font(bold=True, color=fg, name='Arial', size=10)
        c.fill      = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border    = BORDER


def _row(ws, row, values, alt=False):
    bg = C_ALT_ROW if alt else 'FFFFFF'
    for col, v in enumerate(values, 1):
        c = ws.cell(row=row, column=col, value=v)
        c.font      = Font(name='Arial', size=9)
        c.fill      = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = BORDER


def _autowidth(ws, mn=8, mx=40):
    for cc in ws.columns:
        w = max(len(str(c.value or '')) for c in cc)
        ws.column_dimensions[get_column_letter(cc[0].column)].width = min(max(w + 2, mn), mx)


def _title(ws, title, subtitle=''):
    ws.merge_cells('A1:H1')
    t = ws['A1']
    t.value     = title
    t.font      = Font(bold=True, color='FFFFFF', name='Arial', size=14)
    t.fill      = PatternFill('solid', fgColor=C_TITLE_BG)
    t.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30
    if subtitle:
        ws.merge_cells('A2:H2')
        s = ws['A2']
        s.value     = subtitle
        s.font      = Font(italic=True, color='555555', name='Arial', size=9)
        s.alignment = Alignment(horizontal='center')
        return 3
    return 2


def _sheet(wb, name, rows, title, subtitle='', status_col='Status',
           status_colors=None, extra_color_fn=None):
    ws    = wb.create_sheet(name)
    start = _title(ws, title, subtitle)
    if not rows:
        ws.cell(start, 1, 'No data found.'); return
    headers = list(rows[0].keys())
    _hdr(ws, start, headers)
    sc = status_colors or {'OK': C_OK_BG, 'Delayed': C_WARN_BG,
                           'Missing POD': C_ERR_BG, 'High Travel': C_WARN_BG,
                           'Not Ended': C_ERR_BG, 'Complete': C_OK_BG}
    for i, r in enumerate(rows):
        _row(ws, start + 1 + i, list(r.values()), alt=(i % 2 == 0))
        sv = r.get(status_col, '')
        if sv in sc and status_col in headers:
            c = ws.cell(start + 1 + i, headers.index(status_col) + 1)
            c.fill = PatternFill('solid', fgColor=sc[sv])
            c.font = Font(name='Arial', size=9, bold=True)
        if extra_color_fn:
            extra_color_fn(ws, start + 1 + i, headers, r)
    _autowidth(ws)


def generate_excel(summary, details, routes, exceptions) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    # ── Summary ───────────────────────────────────────────────────────────────
    _sheet(wb, 'Daily Summary', summary,
           '📋 Daily Summary', 'Per delivery boy per day', status_col='__none__')

    # ── Delivery Details ──────────────────────────────────────────────────────
    def _det_color(ws, row_num, headers, r):
        # Travel Time > 60 → red; > 30 → amber
        if 'Travel Time (mins)' in headers:
            ci = headers.index('Travel Time (mins)') + 1
            try:
                v = float(r['Travel Time (mins)'])
                bg = C_ERR_BG if v > 60 else (C_WARN_BG if v > 30 else None)
                if bg:
                    ws.cell(row_num, ci).fill = PatternFill('solid', fgColor=bg)
            except (ValueError, TypeError):
                pass
        # Store Time > 30 → amber
        if 'Store Time (mins)' in headers:
            ci2 = headers.index('Store Time (mins)') + 1
            try:
                v2 = float(r['Store Time (mins)'])
                if v2 > 30:
                    ws.cell(row_num, ci2).fill = PatternFill('solid', fgColor=C_WARN_BG)
            except (ValueError, TypeError):
                pass

    _sheet(wb, 'Delivery Details', details,
           '🗺️ Delivery Details', 'Each store visit with timing',
           status_col='Status', extra_color_fn=_det_color)

    # ── Route Summary ─────────────────────────────────────────────────────────
    _sheet(wb, 'Route Summary', routes, '🚚 Route Summary', status_col='Status')

    # ── Exceptions ────────────────────────────────────────────────────────────
    ws_exc    = wb.create_sheet('Exceptions & Flags')
    exc_start = _title(ws_exc, '⚠️ Exceptions & Flags', 'Anomalies detected')
    if not exceptions:
        ws_exc.cell(exc_start, 1, '✅ No exceptions detected.')
    else:
        hdrs = list(exceptions[0].keys())
        _hdr(ws_exc, exc_start, hdrs, bg='C0392B')
        KIND = {
            'Missing POD': C_ERR_BG, 'Route Not Ended': C_ERR_BG,
            'Long Delay': C_WARN_BG, 'No Activity Gap': C_WARN_BG,
            'High Travel Time': C_WARN_BG, 'POD Without Store': C_ERR_BG,
            'Route End Without Start': C_ERR_BG, 'Break End Without Start': C_WARN_BG,
        }
        for i, r in enumerate(exceptions):
            _row(ws_exc, exc_start + 1 + i, list(r.values()))
            bg = KIND.get(r.get('Exception Type', ''), C_WARN_BG)
            for col in range(1, len(hdrs) + 1):
                ws_exc.cell(exc_start + 1 + i, col).fill = PatternFill('solid', fgColor=bg)
        _autowidth(ws_exc)

    # ── Charts data (hidden) + chart sheet ────────────────────────────────────
    cdata = wb.create_sheet('_ChartData')
    cdata.sheet_state = 'hidden'
    people = {}
    for r in summary:
        n = r['Name']
        people[n] = people.get(n, 0) + r['Total Deliveries (POD)']
    cdata['A1'], cdata['B1'] = 'Person', 'Deliveries'
    for i, (n, v) in enumerate(people.items(), 2):
        cdata[f'A{i}'] = n; cdata[f'B{i}'] = v

    status_cnt = {}
    for r in details:
        st = r.get('Status', 'OK')
        status_cnt[st] = status_cnt.get(st, 0) + 1
    cdata['D1'], cdata['E1'] = 'Status', 'Count'
    for i, (k, v) in enumerate(status_cnt.items(), 2):
        cdata[f'D{i}'] = k; cdata[f'E{i}'] = v

    if people:
        cws = wb.create_sheet('📊 Charts')
        c1 = BarChart()
        c1.type = 'col'; c1.title = 'Deliveries per Person'
        c1.add_data(Reference(cdata, min_col=2, min_row=1, max_row=len(people)+1), titles_from_data=True)
        c1.set_categories(Reference(cdata, min_col=1, min_row=2, max_row=len(people)+1))
        c1.width, c1.height = 18, 12
        cws.add_chart(c1, 'A1')

        c2 = BarChart()
        c2.type = 'col'; c2.title = 'Delivery Status Breakdown'
        c2.add_data(Reference(cdata, min_col=5, min_row=1, max_row=len(status_cnt)+1), titles_from_data=True)
        c2.set_categories(Reference(cdata, min_col=4, min_row=2, max_row=len(status_cnt)+1))
        c2.width, c2.height = 18, 12
        cws.add_chart(c2, 'A22')

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
