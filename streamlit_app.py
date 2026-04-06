"""
WhatsApp Delivery Tracker — Production v3
Upload → Parse → Analyse → Filter → Store Search → Download Excel
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd
from datetime import datetime, date

from chat_parser import parse_chat
from engine import (process_messages, build_delivery_summary,
                    build_delivery_details, build_route_summary,
                    build_exceptions, build_store_search)
from reporter import generate_excel

# ════════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ════════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="WhatsApp Delivery Tracker",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background:#0f1623; }
[data-testid="stHeader"]           { background:transparent; }
[data-testid="stSidebar"]          { background:#111827; }
h1,h2,h3,h4                        { color:#e2e8f0 !important; }
p,li,label,.stMarkdown p           { color:#94a3b8 !important; }
.kpi {
  background:#111827; border:1px solid #1e2d45; border-radius:14px;
  padding:16px 18px; display:flex; flex-direction:column; gap:4px;
}
.kpi-icon  { font-size:20px; }
.kpi-num   { font-size:32px; font-weight:700; color:#3b82f6; line-height:1; }
.kpi-label { font-size:11px; color:#64748b; }
.sec-hdr {
  display:flex; align-items:center; gap:10px; margin:18px 0 10px;
  border-left:4px solid #3b82f6; padding-left:12px;
}
.sec-hdr h3   { margin:0 !important; font-size:15px !important; }
.sec-badge    { background:#1e2d45; color:#60a5fa; font-size:11px;
                padding:2px 10px; border-radius:20px; font-weight:600; }
.stDownloadButton > button {
  background:linear-gradient(135deg,#10b981,#059669) !important;
  color:#fff !important; border:none !important; font-weight:700 !important;
  border-radius:10px !important; font-size:13px !important;
}
div[data-testid="stTabs"] > div:first-child {
  background:#111827; border-radius:12px; padding:4px; border:1px solid #1e2d45;
}
div[data-testid="stTabs"] button {
  color:#64748b !important; border-radius:8px !important; font-size:13px !important;
}
div[data-testid="stTabs"] button[aria-selected="true"] {
  background:#3b82f6 !important; color:#fff !important;
}
hr { border-color:#1e2d45 !important; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════════
def kpi(icon, num, label, color="#3b82f6"):
    return (f'<div class="kpi"><span class="kpi-icon">{icon}</span>'
            f'<span class="kpi-num" style="color:{color}">{num}</span>'
            f'<span class="kpi-label">{label}</span></div>')

def sec(icon, title, n=None):
    badge = f'<span class="sec-badge">{n} rows</span>' if n is not None else ''
    st.markdown(f'<div class="sec-hdr"><h3>{icon} {title}</h3>{badge}</div>',
                unsafe_allow_html=True)

def _val(row, key, default="—"):
    """Safe row accessor — never raises KeyError."""
    v = row.get(key) if hasattr(row, 'get') else (
        row[key] if key in row.index else None)
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == '':
        return default
    return v

def _metric(col, label, row, key, suffix=""):
    v = _val(row, key)
    col.metric(label, f"{v}{suffix}" if v != "—" else "—")


# ── Stylers ───────────────────────────────────────────────────────────────────
STATUS_STYLE = {
    'OK':          'background:#d4edda;color:#155724;font-weight:700',
    'Delayed':     'background:#fff3cd;color:#856404;font-weight:700',
    'Missing POD': 'background:#f8d7da;color:#721c24;font-weight:700',
    'High Travel': 'background:#fff3cd;color:#856404;font-weight:700',
    'Not Ended':   'background:#f8d7da;color:#721c24;font-weight:700',
    'Complete':    'background:#d4edda;color:#155724;font-weight:700',
}

def style_details(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    if 'Status' in df.columns:
        for i, v in enumerate(df['Status']):
            s.at[df.index[i], 'Status'] = STATUS_STYLE.get(v, '')
    for col, threshold, hi, lo in [
        ('Travel Time (mins)', 60, 'background:#f8d7da;color:#721c24;font-weight:700',
                                   'background:#fff3cd;color:#856404'),
        ('Store Time (mins)',  30, '', 'background:#fff3cd;color:#856404'),
    ]:
        if col not in df.columns:
            continue
        for i, v in enumerate(df[col]):
            try:
                fv = float(v)
                if hi and fv > threshold:
                    s.at[df.index[i], col] = hi
                elif fv > (30 if col == 'Travel Time (mins)' else threshold):
                    s.at[df.index[i], col] = lo
            except Exception:
                pass
    return df.style.apply(lambda _: s, axis=None)

def style_routes(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    if 'Status' not in df.columns:
        return df.style
    for i, row in df.iterrows():
        s.at[i, 'Status'] = STATUS_STYLE.get(row.get('Status', ''), '')
        try:
            if float(row.get('Avg Travel Time (mins)') or 0) > 45:
                if 'Avg Travel Time (mins)' in df.columns:
                    s.at[i, 'Avg Travel Time (mins)'] = 'background:#fff3cd;color:#856404'
        except Exception:
            pass
    return df.style.apply(lambda _: s, axis=None)

def style_summary(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    for i, row in df.iterrows():
        v = row.get('Net Working Time (mins)')
        if pd.notna(v) and isinstance(v, (int, float)):
            s.at[i, 'Net Working Time (mins)'] = (
                'color:#f59e0b;font-weight:600' if v < 300 else 'color:#10b981')
        v2 = row.get('Avg Time per Delivery (mins)')
        if pd.notna(v2) and isinstance(v2, (int, float)) and v2 > 30:
            s.at[i, 'Avg Time per Delivery (mins)'] = 'color:#f59e0b;font-weight:600'
    return df.style.apply(lambda _: s, axis=None)

def style_exc(df):
    warm = {'Long Delay', 'No Activity Gap', 'Break End Without Start', 'High Travel Time'}
    def row_fn(row):
        v = row.get('Exception Type', '')
        st_style = ('background:#fff3cd;color:#856404;font-weight:700'
                    if v in warm else 'background:#f8d7da;color:#721c24;font-weight:700')
        return [st_style] * len(row)
    return df.style.apply(row_fn, axis=1)


# ════════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("# 📦 WhatsApp Delivery Tracker")
st.markdown("Upload → Parse → Analyse → Filter → Search → Download Excel")
st.markdown("---")

# ════════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ════════════════════════════════════════════════════════════════════════════════
uploaded = st.file_uploader(
    "📂 Upload WhatsApp Chat Export (.txt)", type=['txt'],
    help="WhatsApp → Group → ⋮ → Export Chat → Without Media"
)

if not uploaded:
    st.info("👆 Upload a `.txt` WhatsApp chat export to get started.")
    with st.expander("ℹ️ Expected message format"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""**Steps to export:**
1. Open the WhatsApp group
2. Tap ⋮ → More → **Export Chat**
3. Choose **Without Media**
4. Upload the `.txt` file above""")
        with c2:
            st.code("""\
[12/03/2024, 08:30:00] Ravi Kumar: Route 1 Start
[12/03/2024, 08:55:00] Ravi Kumar: Sharma General Store
[12/03/2024, 09:10:00] Ravi Kumar: POD
[12/03/2024, 09:25:00] Ravi Kumar: Krishna Medical
[12/03/2024, 09:40:00] Ravi Kumar: Closed
[12/03/2024, 09:55:00] Ravi Kumar: Break Start
[12/03/2024, 10:15:00] Ravi Kumar: Break End
[12/03/2024, 10:20:00] Ravi Kumar: City Pharmacy
[12/03/2024, 10:45:00] Ravi Kumar: POD Submitted
[12/03/2024, 11:30:00] Ravi Kumar: Route 1 End""", language="text")
    st.stop()

# ════════════════════════════════════════════════════════════════════════════════
# PROCESS
# ════════════════════════════════════════════════════════════════════════════════
raw  = uploaded.read()
text = None
for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        pass

if not text:
    st.error("❌ Could not decode file.")
    st.stop()

with st.spinner("⚙️ Parsing & analysing…"):
    messages    = parse_chat(text)
    if not messages:
        st.error("❌ No valid messages found.")
        st.stop()
    states      = process_messages(messages)
    summary     = build_delivery_summary(states)
    details     = build_delivery_details(states)
    routes      = build_route_summary(states)
    exceptions  = build_exceptions(states)
    store_index = build_store_search(states)
    excel_bytes = generate_excel(summary, details, routes, exceptions)

st.success(f"✅ Processed **{uploaded.name}** — {len(messages)} messages parsed")

# ════════════════════════════════════════════════════════════════════════════════
# KPIs
# ════════════════════════════════════════════════════════════════════════════════
total_ok      = sum(1 for d in details if d.get('Status') == 'OK')
total_delayed = sum(1 for d in details if d.get('Status') == 'Delayed')
total_missing = sum(1 for d in details if d.get('Status') == 'Missing POD')
exc_color     = "#ef4444" if exceptions else "#10b981"

k1, k2, k3, k4, k5, k6 = st.columns(6)
with k1: st.markdown(kpi("💬", len(messages),  "Messages Parsed"),            unsafe_allow_html=True)
with k2: st.markdown(kpi("🧑‍💼", len(states), "Delivery Personnel"),          unsafe_allow_html=True)
with k3: st.markdown(kpi("✅", len(details),   "Total Deliveries", "#10b981"), unsafe_allow_html=True)
with k4: st.markdown(kpi("🟢", total_ok,       "On Time",          "#10b981"), unsafe_allow_html=True)
with k5: st.markdown(kpi("🟡", total_delayed,  "Delayed",          "#f59e0b"), unsafe_allow_html=True)
with k6: st.markdown(kpi("🔴", total_missing,  "Missing POD",      "#ef4444"), unsafe_allow_html=True)

st.markdown("")
dl_col, _ = st.columns([2, 6])
with dl_col:
    st.download_button(
        "⬇️  Download Full Excel Report", data=excel_bytes,
        file_name=f"delivery_report_{uploaded.name.replace('.txt', '')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
st.markdown("---")

# ════════════════════════════════════════════════════════════════════════════════
# DATAFRAMES
# ════════════════════════════════════════════════════════════════════════════════
df_summary = pd.DataFrame(summary)     if summary     else pd.DataFrame()
df_details = pd.DataFrame(details)     if details     else pd.DataFrame()
df_routes  = pd.DataFrame(routes)      if routes      else pd.DataFrame()
df_exc     = pd.DataFrame(exceptions)  if exceptions  else pd.DataFrame()
df_search  = pd.DataFrame(store_index) if store_index else pd.DataFrame()

# ════════════════════════════════════════════════════════════════════════════════
# SIDEBAR — FILTERS
# ════════════════════════════════════════════════════════════════════════════════
all_people = sorted(df_details['Delivery Boy'].unique()) if not df_details.empty else []
all_routes = sorted(df_details['Route No.'].unique())    if not df_details.empty else []
all_dates  = sorted(df_details['Date'].unique())         if not df_details.empty else []

with st.sidebar:
    st.markdown("### 🔍 Filters")

    sel_people = st.multiselect("Delivery Personnel", all_people, default=all_people)
    if not sel_people:
        sel_people = all_people

    sel_routes = st.multiselect("Route Number", all_routes, default=all_routes)
    if not sel_routes:
        sel_routes = all_routes

    st.markdown("**Date Range**")
    if all_dates:
        try:
            min_date = datetime.strptime(min(all_dates), '%Y-%m-%d').date()
            max_date = datetime.strptime(max(all_dates), '%Y-%m-%d').date()
        except Exception:
            min_date = max_date = date.today()
        date_from = st.date_input("From", value=min_date, min_value=min_date, max_value=max_date)
        date_to   = st.date_input("To",   value=max_date, min_value=min_date, max_value=max_date)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        sel_dates = [
            d for d in all_dates
            if date_from <= datetime.strptime(d, '%Y-%m-%d').date() <= date_to
        ]
    else:
        sel_dates = []

    st.markdown("---")
    st.markdown("### 📊 Quick Stats")
    if not df_details.empty:
        nt = pd.to_numeric(df_details.get('Store Time (mins)',  pd.Series(dtype=float)), errors='coerce').dropna()
        ni = pd.to_numeric(df_details.get('Travel Time (mins)', pd.Series(dtype=float)), errors='coerce').dropna()
        st.metric("Avg Store Time",   f"{nt.mean():.1f} min" if not nt.empty else "—")
        st.metric("Avg Travel Time",  f"{ni.mean():.1f} min" if not ni.empty else "—")
        st.metric("Total Exceptions", len(exceptions))

    st.markdown("---")
    st.caption("Route 1 Start → Store → POD/Closed → Route 1 End")


# ── Apply filters ─────────────────────────────────────────────────────────────
def _f(df, name_col='Delivery Boy', route_col='Route No.'):
    if df.empty:
        return df
    mask = df[name_col].isin(sel_people) & df['Date'].isin(sel_dates)
    if route_col in df.columns:
        mask &= df[route_col].isin(sel_routes)
    return df[mask]

def _fs(df, name_col='Name'):
    if df.empty:
        return df
    return df[df[name_col].isin(sel_people) & df['Date'].isin(sel_dates)]

det_f  = _f(df_details)
rte_f  = _f(df_routes)
exc_f  = _f(df_exc, route_col='__none__')
sum_f  = _fs(df_summary)
srch_f = _f(df_search)

# ════════════════════════════════════════════════════════════════════════════════
# TABS
# ════════════════════════════════════════════════════════════════════════════════
t1, t2, t3, t4, t5, t6 = st.tabs([
    "📊  Charts",
    "📋  Daily Summary",
    "🗺️  Delivery Details",
    "🚚  Route Summary",
    "🔍  Store Search",
    "⚠️  Flags & Exceptions",
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — CHARTS
# ─────────────────────────────────────────────────────────────────────────────
with t1:
    if det_f.empty:
        st.info("No data for selected filters.")
    else:
        r1c1, r1c2, r1c3 = st.columns(3)
        with r1c1:
            st.markdown("##### 📦 Deliveries per Person")
            st.bar_chart(det_f.groupby('Delivery Boy').size().rename("Deliveries"))
        with r1c2:
            st.markdown("##### 🎯 Delivery Status")
            st.bar_chart(det_f['Status'].value_counts())
        with r1c3:
            st.markdown("##### ⏱ Avg Store Time (mins)")
            tmp = det_f.copy()
            tmp['_st'] = pd.to_numeric(tmp.get('Store Time (mins)', pd.Series(dtype=float)), errors='coerce')
            avg = tmp.groupby('Delivery Boy')['_st'].mean().dropna().round(1)
            if not avg.empty:
                st.bar_chart(avg.rename("Avg mins"))
            else:
                st.info("No store time data.")

        st.markdown("")
        r2c1, r2c2, r2c3 = st.columns(3)
        with r2c1:
            st.markdown("##### 🚗 Avg Travel Time (mins)")
            tmp2 = det_f.copy()
            tmp2['_tt'] = pd.to_numeric(tmp2.get('Travel Time (mins)', pd.Series(dtype=float)), errors='coerce')
            avg2 = tmp2.groupby('Delivery Boy')['_tt'].mean().dropna().round(1)
            if not avg2.empty:
                st.bar_chart(avg2.rename("Avg mins"))
            else:
                st.info("No travel time data.")
        with r2c2:
            st.markdown("##### 🚚 Deliveries per Route")
            if not rte_f.empty:
                tmp3 = rte_f.copy()
                tmp3['Label'] = (tmp3['Delivery Boy'].str.split().str[0]
                                 + ' R' + tmp3['Route No.'].astype(str))
                st.bar_chart(tmp3.set_index('Label')['Total Deliveries'])
            else:
                st.info("No route data.")
        with r2c3:
            st.markdown("##### ⚠️ Exceptions by Type")
            if not exc_f.empty:
                st.bar_chart(exc_f['Exception Type'].value_counts())
            else:
                st.success("✅ No exceptions!")

        # Working time breakdown — only if columns exist
        if not sum_f.empty:
            wt_cols = [c for c in ['Total Working Time (mins)', 'Total Break Time (mins)',
                                   'Net Working Time (mins)'] if c in sum_f.columns]
            if wt_cols:
                st.markdown("")
                st.markdown("##### 🕐 Working Time Breakdown (mins)")
                wt = sum_f.copy()
                wt['Label'] = wt['Name'].str.split().str[0] + ' ' + wt['Date']
                st.bar_chart(wt.set_index('Label')[wt_cols])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — DAILY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
with t2:
    sec("📋", "Daily Summary per Delivery Boy", len(sum_f))
    if sum_f.empty:
        st.info("No data for selected filters.")
    else:
        st.dataframe(style_summary(sum_f), use_container_width=True,
                     height=min(420, 60 + len(sum_f) * 38))
        st.caption("🟡 Net Working Time < 5 hrs  |  🟡 Avg Delivery > 30 min")

        st.markdown("#### 🧑 Person Cards")
        for _, row in sum_f.iterrows():
            with st.expander(f"👤 {_val(row, 'Name')}  —  {_val(row, 'Date')}"):
                a, b, c, d = st.columns(4)
                a.metric("Deliveries",   _val(row, 'Total Deliveries (POD)'))
                b.metric("Routes",       _val(row, 'Total Routes'))
                c.metric("Net Work Time",
                         f"{_val(row, 'Net Working Time (mins)')} min"
                         if _val(row, 'Net Working Time (mins)') != "—" else "—")
                d.metric("Avg Delivery",
                         f"{_val(row, 'Avg Time per Delivery (mins)')} min"
                         if _val(row, 'Avg Time per Delivery (mins)') != "—" else "—")
                e, f_, g, h = st.columns(4)
                # Use new column names from engine v3
                e.metric("First Activity",  _val(row, 'First Activity'))
                f_.metric("Last Activity",  _val(row, 'Last Activity'))
                g.metric("Break Time",
                         f"{_val(row, 'Total Break Time (mins)')} min"
                         if _val(row, 'Total Break Time (mins)') != "—" else "—")
                h.metric("Stores",          _val(row, 'Total Stores Covered'))

                # Second row — route office times (new columns)
                if 'Route Start (First)' in sum_f.columns or 'Route End (Last)' in sum_f.columns:
                    r1, r2 = st.columns(2)
                    r1.metric("Route Start (Office Departure)", _val(row, 'Route Start (First)'))
                    r2.metric("Route End (Office Return)",      _val(row, 'Route End (Last)'))

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — DELIVERY DETAILS
# ─────────────────────────────────────────────────────────────────────────────
with t3:
    sec("🗺️", "Delivery Details", len(det_f))
    if det_f.empty:
        st.info("No records for selected filters.")
    else:
        st.dataframe(style_details(det_f), use_container_width=True,
                     height=min(640, 60 + len(det_f) * 38))
        st.caption(
            "🟢 OK  |  🟡 Store Time >30 min or Travel >30 min  |  "
            "🔴 Missing POD / Travel >60 min"
        )
        st.markdown("")
        qs1, qs2, qs3, qs4 = st.columns(4)
        nt = pd.to_numeric(det_f.get('Store Time (mins)',  pd.Series(dtype=float)), errors='coerce').dropna()
        ni = pd.to_numeric(det_f.get('Travel Time (mins)', pd.Series(dtype=float)), errors='coerce').dropna()
        qs1.metric("Avg Store Time",  f"{nt.mean():.1f} min" if not nt.empty else "—")
        qs2.metric("Max Store Time",  f"{nt.max():.1f} min"  if not nt.empty else "—")
        qs3.metric("Avg Travel Time", f"{ni.mean():.1f} min" if not ni.empty else "—")
        qs4.metric("Max Travel Time", f"{ni.max():.1f} min"  if not ni.empty else "—")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — ROUTE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
with t4:
    sec("🚚", "Route Summary", len(rte_f))
    if rte_f.empty:
        st.info("No route data for selected filters.")
    else:
        st.dataframe(style_routes(rte_f), use_container_width=True,
                     height=min(500, 60 + len(rte_f) * 38))
        st.caption("🟢 Complete  |  🔴 Not Ended  |  🟡 Avg Travel > 45 min")

        st.markdown("#### 🚚 Route Cards")
        for _, row in rte_f.iterrows():
            status = _val(row, 'Status', 'Unknown')
            icon   = "✅" if status == 'Complete' else "❌"
            with st.expander(
                f"{icon}  {_val(row, 'Delivery Boy')}  —  "
                f"Route {_val(row, 'Route No.')}  ({_val(row, 'Date')})"
            ):
                a, b, c, d = st.columns(4)
                # Engine v3 uses 'Route Start Time' / 'Route End Time'
                a.metric("Route Start",  _val(row, 'Route Start Time'))
                b.metric("Route End",    _val(row, 'Route End Time'))
                c.metric("Deliveries",   _val(row, 'Total Deliveries'))
                dur = _val(row, 'Route Duration (mins)')
                d.metric("Duration", f"{dur} min" if dur != "—" else "—")

                e, f_, g, h = st.columns(4)
                avg_s  = _val(row, 'Avg Store Time (mins)')
                avg_t  = _val(row, 'Avg Travel Time (mins)')
                max_t  = _val(row, 'Max Travel Time (mins)')
                min_t  = _val(row, 'Min Travel Time (mins)')
                stores = _val(row, 'Total Stores Covered')
                e.metric("Avg Store",    f"{avg_s} min"  if avg_s  != "—" else "—")
                f_.metric("Avg Travel",  f"{avg_t} min"  if avg_t  != "—" else "—")
                g.metric("Max Travel",   f"{max_t} min"  if max_t  != "—" else "—")
                h.metric("Total Stores", stores)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — STORE SEARCH
# ─────────────────────────────────────────────────────────────────────────────
with t5:
    sec("🔍", "Store Search")
    st.markdown(
        "Type a store name (or part of it) to find all deliveries to that store "
        "across all delivery boys and dates."
    )

    query = st.text_input(
        "Search store name",
        placeholder="e.g. Sharma, Medical, Pharmacy…",
        key="store_search_input"
    )

    if not srch_f.empty:
        if query and query.strip():
            result = srch_f[
                srch_f['Store Name'].str.contains(query.strip(), case=False, na=False)
            ]
        else:
            result = srch_f

        if query and query.strip():
            matched_stores = result['Store Name'].nunique()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Matched Stores",  matched_stores)
            c2.metric("Total Deliveries", len(result))
            c3.metric("Delivery Boys",
                      result['Delivery Boy'].nunique() if not result.empty else 0)
            ok_count = len(result[result['Status'] == 'OK']) if not result.empty else 0
            c4.metric("Successful PODs", ok_count)
            st.markdown("")

        if not result.empty:
            st.dataframe(
                style_details(result.reset_index(drop=True)),
                use_container_width=True,
                height=min(640, 60 + len(result) * 38),
            )
            st.caption(f"Showing {len(result)} record(s)")

            if result['Store Name'].nunique() > 1:
                st.markdown("#### 📊 Breakdown by Store")
                breakdown = (
                    result.groupby('Store Name')
                    .agg(
                        Deliveries=('Store Name', 'count'),
                        Delivery_Boys=('Delivery Boy', 'nunique'),
                        Avg_Store_Time=('Store Time (mins)',
                            lambda x: round(
                                pd.to_numeric(x, errors='coerce').dropna().mean(), 1
                            ) if pd.to_numeric(x, errors='coerce').dropna().size else None),
                    )
                    .reset_index()
                    .rename(columns={
                        'Delivery_Boys':  'Delivery Boys',
                        'Avg_Store_Time': 'Avg Store Time (mins)',
                    })
                )
                st.dataframe(breakdown, use_container_width=True)

        elif query and query.strip():
            st.info(f'No deliveries found matching "{query}"')
    else:
        st.info("No delivery data available.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — FLAGS & EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────
with t6:
    sec("⚠️", "Flags & Exceptions", len(exc_f))
    if exc_f.empty:
        st.success("✅ No exceptions found! All deliveries look clean.")
    else:
        exc_icons = {
            'Missing POD':             ('🔴', '#ef4444'),
            'Long Delay':              ('🟡', '#f59e0b'),
            'No Activity Gap':         ('🟡', '#f59e0b'),
            'High Travel Time':        ('🟡', '#f59e0b'),
            'Route Not Ended':         ('🔴', '#ef4444'),
            'POD Without Store':       ('🔴', '#ef4444'),
            'Route End Without Start': ('🔴', '#ef4444'),
            'Break End Without Start': ('🟠', '#f97316'),
        }
        counts = exc_f['Exception Type'].value_counts()
        cols   = st.columns(min(len(counts), 4))
        for i, (etype, cnt) in enumerate(counts.items()):
            icon, color = exc_icons.get(etype, ('⚠️', '#94a3b8'))
            with cols[i % len(cols)]:
                st.markdown(kpi(icon, cnt, etype, color), unsafe_allow_html=True)

        st.markdown("")
        st.dataframe(style_exc(exc_f), use_container_width=True,
                     height=min(600, 60 + len(exc_f) * 38))

        st.markdown("#### 👤 Exceptions per Person")
        pivot = (exc_f.groupby(['Delivery Boy', 'Exception Type'])
                 .size().unstack(fill_value=0))
        st.dataframe(
            pivot.style.highlight_max(axis=0, color='#f8d7da'),
            use_container_width=True
        )

# ════════════════════════════════════════════════════════════════════════════════
# FOOTER
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("---")
fc1, fc2 = st.columns([4, 1])
with fc1:
    st.caption(
        "WhatsApp Delivery Tracker v3 · "
        "Route/Store/POD/Closed/Break · "
        "Multi-person · Android & iOS formats"
    )
with fc2:
    st.download_button(
        "⬇️ Excel", data=excel_bytes,
        file_name=f"delivery_report_{uploaded.name.replace('.txt', '')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True, key="dl_footer",
    )
