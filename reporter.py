"""
Excel Report Generator — multi-sheet styled workbook.
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference
import io

# ── Colour palette ────────────────────────────────────────────────────────────
C_HEADER_BG = '1A3C5E'
C_HEADER_FG = 'FFFFFF'
C_ALT_ROW   = 'EBF3FB'
C_WARN_BG   = 'FFF3CD'
C_ERR_BG    = 'F8D7DA'
C_OK_BG     = 'D4EDDA'
C_TITLE_BG  = '0D6EFD'

_thin  = Side(style='thin', color='C0C0C0')
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _header_row(ws, row_num, headers, bg=C_HEADER_BG, fg=C_HEADER_FG):
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=row_num, column=col, value=h)
        c.font      = Font(bold=True, color=fg, name='Arial', size=10)
        c.fill      = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border    = BORDER


def _data_row(ws, row_num, values, alt=False):
    bg = C_ALT_ROW if alt else 'FFFFFF'
    for col, v in enumerate(values, 1):
        c = ws.cell(row=row_num, column=col, value=v)
        c.font      = Font(name='Arial', size=9)
        c.fill      = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = BORDER


def _auto_width(ws, min_w=8, max_w=40):
    for col_cells in ws.columns:
        length = max(len(str(c.value or '')) for c in col_cells)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(length + 2, min_w), max_w)


def _title_block(ws, title: str, subtitle: str = ''):
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


# ── Sheets ────────────────────────────────────────────────────────────────────

def _sheet_summary(wb, rows):
    ws    = wb.create_sheet('Delivery Summary')
    start = _title_block(ws, '📦 Delivery Summary', 'Per delivery boy per day')
    if not rows:
        ws.cell(start, 1, 'No data found.'); return
    headers = list(rows[0].keys())
    _header_row(ws, start, headers)
    for i, row in enumerate(rows):
        _data_row(ws, start + 1 + i, list(row.values()), alt=(i % 2 == 0))
    _auto_width(ws)


def _sheet_details(wb, rows):
    ws    = wb.create_sheet('Delivery Details')
    start = _title_block(ws, '🗺️ Delivery Details', 'Each store visit with timing')
    if not rows:
        ws.cell(start, 1, 'No data found.'); return
    headers = list(rows[0].keys())
    _header_row(ws, start, headers)
    STATUS_COLORS = {'OK': C_OK_BG, 'Delayed': C_WARN_BG, 'Missing POD': C_ERR_BG}
    for i, row in enumerate(rows):
        _data_row(ws, start + 1 + i, list(row.values()), alt=(i % 2 == 0))
        status = row.get('Status', '')
        if status in STATUS_COLORS:
            c = ws.cell(start + 1 + i, headers.index('Status') + 1)
            c.fill = PatternFill('solid', fgColor=STATUS_COLORS[status])
            c.font = Font(name='Arial', size=9, bold=True)
        inter_val = row.get('Time Between Deliveries (mins)')
        if isinstance(inter_val, (int, float)) and inter_val > 60:
            col_idx = headers.index('Time Between Deliveries (mins)') + 1
            c2 = ws.cell(start + 1 + i, col_idx)
            c2.fill = PatternFill('solid', fgColor=C_ERR_BG)
            c2.font = Font(name='Arial', size=9, bold=True, color='721C24')
    _auto_width(ws)


def _sheet_routes(wb, rows):
    ws    = wb.create_sheet('Route Summary')
    start = _title_block(ws, '🚚 Route Summary')
    if not rows:
        ws.cell(start, 1, 'No data found.'); return
    headers = list(rows[0].keys())
    _header_row(ws, start, headers)
    for i, row in enumerate(rows):
        _data_row(ws, start + 1 + i, list(row.values()), alt=(i % 2 == 0))
        if row.get('Status') == 'Not Ended':
            c = ws.cell(start + 1 + i, headers.index('Status') + 1)
            c.fill = PatternFill('solid', fgColor=C_ERR_BG)
    _auto_width(ws)


def _sheet_exceptions(wb, rows):
    ws    = wb.create_sheet('Exceptions & Flags')
    start = _title_block(ws, '⚠️ Exceptions & Flags', 'Anomalies detected in delivery data')
    if not rows:
        ws.cell(start, 1, '✅ No exceptions detected.'); return
    headers = list(rows[0].keys())
    _header_row(ws, start, headers, bg='C0392B')
    KIND_COLORS = {
        'Missing POD':             C_ERR_BG,
        'Route Not Ended':         C_ERR_BG,
        'Long Delay':              C_WARN_BG,
        'No Activity Gap':         C_WARN_BG,
        'High Travel Time':        C_WARN_BG,
        'POD Without Store':       C_ERR_BG,
        'Route End Without Start': C_ERR_BG,
        'Break End Without Start': C_WARN_BG,
    }
    for i, row in enumerate(rows):
        _data_row(ws, start + 1 + i, list(row.values()))
        bg = KIND_COLORS.get(row.get('Exception Type', ''), C_WARN_BG)
        for col in range(1, len(headers) + 1):
            ws.cell(start + 1 + i, col).fill = PatternFill('solid', fgColor=bg)
    _auto_width(ws)


def _sheet_charts(wb, summary_rows, details_rows):
    ws = wb.create_sheet('Charts Data')
    ws.sheet_state = 'hidden'

    people = {}
    for r in summary_rows:
        n = r['Name']
        people[n] = people.get(n, 0) + r['Total Deliveries (POD)']
    ws['A1'], ws['B1'] = 'Person', 'Total Deliveries'
    for i, (n, v) in enumerate(people.items(), 2):
        ws[f'A{i}'] = n
        ws[f'B{i}'] = v

    delay_data = {}
    for r in details_rows:
        n = r['Delivery Boy']
        if n not in delay_data:
            delay_data[n] = {'ok': 0, 'delayed': 0, 'missing': 0}
        s = r['Status']
        if s == 'OK':        delay_data[n]['ok']      += 1
        elif s == 'Delayed': delay_data[n]['delayed'] += 1
        else:                delay_data[n]['missing'] += 1

    ws['D1'], ws['E1'], ws['F1'], ws['G1'] = 'Person', 'On Time', 'Delayed', 'Missing POD'
    for i, (n, v) in enumerate(delay_data.items(), 2):
        ws[f'D{i}'] = n
        ws[f'E{i}'] = v['ok']
        ws[f'F{i}'] = v['delayed']
        ws[f'G{i}'] = v['missing']

    n_people = len(people)
    if n_people == 0:
        return

    cws = wb.create_sheet('📊 Charts')

    chart1 = BarChart()
    chart1.type  = 'col'
    chart1.title = 'Total Deliveries per Person'
    chart1.y_axis.title = 'Deliveries'
    chart1.x_axis.title = 'Person'
    data1 = Reference(ws, min_col=2, min_row=1, max_row=n_people + 1)
    cats1 = Reference(ws, min_col=1, min_row=2, max_row=n_people + 1)
    chart1.add_data(data1, titles_from_data=True)
    chart1.set_categories(cats1)
    chart1.width, chart1.height = 18, 12
    cws.add_chart(chart1, 'A1')

    if delay_data:
        chart2 = BarChart()
        chart2.type     = 'bar'
        chart2.grouping = 'stacked'
        chart2.title    = 'On-Time vs Delayed vs Missing POD'
        data2 = Reference(ws, min_col=5, max_col=7, min_row=1, max_row=len(delay_data) + 1)
        cats2 = Reference(ws, min_col=4, min_row=2, max_row=len(delay_data) + 1)
        chart2.add_data(data2, titles_from_data=True)
        chart2.set_categories(cats2)
        chart2.width, chart2.height = 18, 12
        cws.add_chart(chart2, 'A22')


def generate_excel(summary, details, routes, exceptions) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet_summary(wb, summary)
    _sheet_details(wb, details)
    _sheet_routes(wb, routes)
    _sheet_exceptions(wb, exceptions)
    _sheet_charts(wb, summary, details)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
