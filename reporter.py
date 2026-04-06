"""
Excel Report Generator — Business-Ready v3
- Removed decorative title/subtitle rows; data starts at row 1
- Clean headers only, no merged cells, no unnamed columns
- Colour-coded Status, Travel Time, Store Time
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference
import io

# ── Palette ───────────────────────────────────────────────────────────────────
C_HDR_BG = '1A3C5E'
C_HDR_FG = 'FFFFFF'
C_ALT    = 'EBF3FB'
C_WARN   = 'FFF3CD'
C_ERR    = 'F8D7DA'
C_OK     = 'D4EDDA'

_thin  = Side(style='thin', color='C0C0C0')
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

STATUS_COLORS = {
    'OK':          C_OK,
    'Complete':    C_OK,
    'Delayed':     C_WARN,
    'High Travel': C_WARN,
    'Missing POD': C_ERR,
    'Not Ended':   C_ERR,
}


# ── Primitives ────────────────────────────────────────────────────────────────

def _hdr(ws, headers: list):
    """Write header row at row 1. No title row above it."""
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font      = Font(bold=True, color=C_HDR_FG, name='Arial', size=10)
        c.fill      = PatternFill('solid', fgColor=C_HDR_BG)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border    = BORDER
    ws.row_dimensions[1].height = 28


def _row(ws, row_num: int, values: list, alt: bool = False):
    bg = C_ALT if alt else 'FFFFFF'
    for col, v in enumerate(values, 1):
        c = ws.cell(row=row_num, column=col, value=v)
        c.font      = Font(name='Arial', size=9)
        c.fill      = PatternFill('solid', fgColor=bg)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = BORDER


def _autowidth(ws, mn: int = 9, mx: int = 38):
    for cc in ws.columns:
        w = max(len(str(c.value or '')) for c in cc)
        ws.column_dimensions[get_column_letter(cc[0].column)].width = min(max(w + 2, mn), mx)


def _freeze(ws):
    ws.freeze_panes = 'A2'   # freeze header row


def _write_sheet(wb, sheet_name: str, rows: list,
                 status_col: str = 'Status',
                 extra_color_fn=None) -> None:
    ws = wb.create_sheet(sheet_name)
    if not rows:
        ws.cell(1, 1, 'No data.')
        return
    headers = list(rows[0].keys())
    _hdr(ws, headers)
    _freeze(ws)

    for i, r in enumerate(rows):
        rn = i + 2   # data starts at row 2
        _row(ws, rn, list(r.values()), alt=(i % 2 == 0))

        # Status colour
        if status_col in headers:
            sv = r.get(status_col, '')
            if sv in STATUS_COLORS:
                c = ws.cell(rn, headers.index(status_col) + 1)
                c.fill = PatternFill('solid', fgColor=STATUS_COLORS[sv])
                c.font = Font(name='Arial', size=9, bold=True)

        if extra_color_fn:
            extra_color_fn(ws, rn, headers, r)

    _autowidth(ws)


# ── Per-sheet colour callbacks ────────────────────────────────────────────────

def _color_details(ws, rn, headers, r):
    if 'Travel Time (mins)' in headers:
        ci = headers.index('Travel Time (mins)') + 1
        try:
            v = float(r['Travel Time (mins)'])
            bg = C_ERR if v > 60 else (C_WARN if v > 30 else None)
            if bg:
                ws.cell(rn, ci).fill = PatternFill('solid', fgColor=bg)
        except (ValueError, TypeError):
            pass

    if 'Store Time (mins)' in headers:
        ci2 = headers.index('Store Time (mins)') + 1
        try:
            v2 = float(r['Store Time (mins)'])
            if v2 > 30:
                ws.cell(rn, ci2).fill = PatternFill('solid', fgColor=C_WARN)
        except (ValueError, TypeError):
            pass


def _color_routes(ws, rn, headers, r):
    if 'Avg Travel Time (mins)' in headers:
        ci = headers.index('Avg Travel Time (mins)') + 1
        try:
            v = float(r.get('Avg Travel Time (mins)') or 0)
            if v > 45:
                ws.cell(rn, ci).fill = PatternFill('solid', fgColor=C_WARN)
        except (ValueError, TypeError):
            pass


def _color_summary(ws, rn, headers, r):
    if 'Net Working Time (mins)' in headers:
        ci = headers.index('Net Working Time (mins)') + 1
        try:
            v = float(r.get('Net Working Time (mins)') or 0)
            if v < 300:
                ws.cell(rn, ci).fill = PatternFill('solid', fgColor=C_WARN)
        except (ValueError, TypeError):
            pass


def _color_exc(ws, rn, headers, r):
    KIND = {
        'Missing POD': C_ERR, 'Route Not Ended': C_ERR,
        'POD Without Store': C_ERR, 'Route End Without Start': C_ERR,
        'Long Delay': C_WARN, 'No Activity Gap': C_WARN,
        'High Travel Time': C_WARN, 'Break End Without Start': C_WARN,
    }
    bg = KIND.get(r.get('Exception Type', ''), C_WARN)
    for col in range(1, len(headers) + 1):
        ws.cell(rn, col).fill = PatternFill('solid', fgColor=bg)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_excel(summary, details, routes, exceptions) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    _write_sheet(wb, 'Daily Summary',    summary,    status_col='__none__',
                 extra_color_fn=_color_summary)
    _write_sheet(wb, 'Delivery Details', details,    status_col='Status',
                 extra_color_fn=_color_details)
    _write_sheet(wb, 'Route Summary',    routes,     status_col='Status',
                 extra_color_fn=_color_routes)
    _write_sheet(wb, 'Exceptions',       exceptions, status_col='__none__',
                 extra_color_fn=_color_exc)

    # ── Chart data (hidden) + Charts sheet ────────────────────────────────────
    cdata = wb.create_sheet('_data')
    cdata.sheet_state = 'hidden'

    people = {}
    for r in summary:
        n = r.get('Name', '')
        people[n] = people.get(n, 0) + (r.get('Total Deliveries (POD)') or 0)
    cdata['A1'], cdata['B1'] = 'Person', 'Deliveries'
    for i, (n, v) in enumerate(people.items(), 2):
        cdata[f'A{i}'] = n
        cdata[f'B{i}'] = v

    status_cnt: dict = {}
    for r in details:
        st = r.get('Status', 'OK')
        status_cnt[st] = status_cnt.get(st, 0) + 1
    cdata['D1'], cdata['E1'] = 'Status', 'Count'
    for i, (k, v) in enumerate(status_cnt.items(), 2):
        cdata[f'D{i}'] = k
        cdata[f'E{i}'] = v

    if people:
        cws = wb.create_sheet('Charts')
        c1 = BarChart()
        c1.type = 'col'
        c1.title = 'Deliveries per Person'
        c1.add_data(
            Reference(cdata, min_col=2, min_row=1, max_row=len(people) + 1),
            titles_from_data=True
        )
        c1.set_categories(
            Reference(cdata, min_col=1, min_row=2, max_row=len(people) + 1)
        )
        c1.width, c1.height = 18, 12
        cws.add_chart(c1, 'A1')

        c2 = BarChart()
        c2.type = 'col'
        c2.title = 'Delivery Status Breakdown'
        c2.add_data(
            Reference(cdata, min_col=5, min_row=1, max_row=len(status_cnt) + 1),
            titles_from_data=True
        )
        c2.set_categories(
            Reference(cdata, min_col=4, min_row=2, max_row=len(status_cnt) + 1)
        )
        c2.width, c2.height = 18, 12
        cws.add_chart(c2, 'A22')

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
