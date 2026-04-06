"""
WhatsApp Delivery Tracker — Streamlit Cloud Entry Point
Upload → Parse → Analyse → View → Download Excel
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd

from chat_parser import parse_chat
from engine     import (process_messages, build_delivery_summary,
                         build_delivery_details, build_route_summary, build_exceptions)
from reporter   import generate_excel

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
    return (
        f'<div class="kpi">'
        f'<span class="kpi-icon">{icon}</span>'
        f'<span class="kpi-num" style="color:{color}">{num}</span>'
        f'<span class="kpi-label">{label}</span>'
        f'</div>'
    )

def sec(icon, title, n=None):
    badge = f'<span class="sec-badge">{n} rows</span>' if n is not None else ''
    st.markdown(
        f'<div class="sec-hdr"><h3>{icon} {title}</h3>{badge}</div>',
        unsafe_allow_html=True
    )

def style_details(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    status_map = {
        'OK':          'background:#d4edda;color:#155724;font-weight:700',
        'Delayed':     'background:#fff3cd;color:#856404;font-weight:700',
        'Missing POD': 'background:#f8d7da;color:#721c24;font-weight:700',
    }
    for i, v in enumerate(df['Status']):
        s.at[df.index[i], 'Status'] = status_map.get(v, '')
    for i, v in enumerate(df['Time Spent (mins)']):
        try:
            if float(v) > 30:
                s.at[df.index[i], 'Time Spent (mins)'] = 'background:#fff3cd;color:#856404'
        except Exception:
            pass
    for i, v in enumerate(df['Time Between Deliveries (mins)']):
        try:
            fv = float(v)
            if fv > 60:
                s.at[df.index[i], 'Time Between Deliveries (mins)'] = 'background:#f8d7da;color:#721c24;font-weight:700'
            elif fv > 30:
                s.at[df.index[i], 'Time Between Deliveries (mins)'] = 'background:#fff3cd;color:#856404'
        except Exception:
            pass
    return df.style.apply(lambda _: s, axis=None)

def style_exc_table(df):
    warm = {'Long Delay', 'No Activity Gap', 'Break End Without Start', 'High Travel Time'}
    def row_style(row):
        v = row.get('Exception Type', '')
        style = (
            'background:#fff3cd;color:#856404;font-weight:700'
            if v in warm else
            'background:#f8d7da;color:#721c24;font-weight:700'
        )
        return [style] * len(row)
    return df.style.apply(row_style, axis=1)

def style_routes(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    for i, row in df.iterrows():
        sv = row['Status']
        sc = (
            'background:#d4edda;color:#155724;font-weight:700' if sv == 'Complete'
            else 'background:#f8d7da;color:#721c24;font-weight:700'
        )
        s.at[i, 'Status'] = sc
        try:
            if float(row['Avg Inter-Delivery (mins)']) > 45:
                s.at[i, 'Avg Inter-Delivery (mins)'] = 'background:#fff3cd;color:#856404'
        except Exception:
            pass
    return df.style.apply(lambda _: s, axis=None)

def style_summary(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    for i, row in df.iterrows():
        v = row.get('Net Working Time (mins)')
        if pd.notna(v) and isinstance(v, (int, float)):
            s.at[i, 'Net Working Time (mins)'] = (
                'color:#f59e0b;font-weight:600' if v < 300 else 'color:#10b981'
            )
        v2 = row.get('Avg Time per Delivery (mins)')
        if pd.notna(v2) and isinstance(v2, (int, float)) and v2 > 30:
            s.at[i, 'Avg Time per Delivery (mins)'] = 'color:#f59e0b;font-weight:600'
    return df.style.apply(lambda _: s, axis=None)


# ════════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("# 📦 WhatsApp Delivery Tracker")
st.markdown("Upload a WhatsApp chat export → auto-process → view analytics → download Excel.")
st.markdown("---")


# ════════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ════════════════════════════════════════════════════════════════════════════════
uploaded = st.file_uploader(
    "📂 Upload WhatsApp Chat Export (.txt)",
    type=['txt'],
    help="WhatsApp → Group → ⋮ → Export Chat → Without Media"
)

if not uploaded:
    st.info("👆 Upload a `.txt` WhatsApp chat export to get started.")
    with st.expander("ℹ️ How to export & expected message format"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("""**Steps to export from WhatsApp:**
1. Open the WhatsApp group
2. Tap ⋮ (Android) or group name (iOS)
3. More → **Export Chat**
4. Choose **Without Media**
5. Upload the `.txt` file above""")
        with c2:
            st.code("""\
[12/03/2024, 08:30:00] Ravi Kumar: Route 1 Start
[12/03/2024, 08:55:00] Ravi Kumar: Sharma General Store
[12/03/2024, 09:10:00] Ravi Kumar: POD
[12/03/2024, 09:25:00] Ravi Kumar: Krishna Medical
[12/03/2024, 09:40:00] Ravi Kumar: POD
[12/03/2024, 09:55:00] Ravi Kumar: Break Start
[12/03/2024, 10:15:00] Ravi Kumar: Break End
[12/03/2024, 11:30:00] Ravi Kumar: Route 1 End""", language="text")
    st.stop()


# ════════════════════════════════════════════════════════════════════════════════
# PARSE & PROCESS
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
    st.error("❌ Could not decode the file. Please ensure it is a valid WhatsApp .txt export.")
    st.stop()

with st.spinner("⚙️ Parsing & analysing chat data…"):
    messages = parse_chat(text)
    if not messages:
        st.error("❌ No valid messages found. Please check the file format.")
        st.stop()
    states     = process_messages(messages)
    summary    = build_delivery_summary(states)
    details    = build_delivery_details(states)
    routes     = build_route_summary(states)
    exceptions = build_exceptions(states)
    excel_bytes = generate_excel(summary, details, routes, exceptions)

st.success(f"✅ Processed **{uploaded.name}** — {len(messages)} messages parsed")


# ════════════════════════════════════════════════════════════════════════════════
# KPI BAR
# ════════════════════════════════════════════════════════════════════════════════
total_ok      = sum(1 for d in details if d['Status'] == 'OK')
total_delayed = sum(1 for d in details if d['Status'] == 'Delayed')
total_missing = sum(1 for d in details if d['Status'] == 'Missing POD')
exc_color     = "#ef4444" if exceptions else "#10b981"

k1, k2, k3, k4, k5, k6 = st.columns(6)
with k1: st.markdown(kpi("💬", len(messages),    "Messages Parsed"),            unsafe_allow_html=True)
with k2: st.markdown(kpi("🧑‍💼", len(states),    "Delivery Personnel"),          unsafe_allow_html=True)
with k3: st.markdown(kpi("✅", len(details),     "Total Deliveries", "#10b981"), unsafe_allow_html=True)
with k4: st.markdown(kpi("🟢", total_ok,         "On Time",          "#10b981"), unsafe_allow_html=True)
with k5: st.markdown(kpi("🟡", total_delayed,    "Delayed",          "#f59e0b"), unsafe_allow_html=True)
with k6: st.markdown(kpi("🔴", total_missing,    "Missing POD",      "#ef4444"), unsafe_allow_html=True)

st.markdown("")

# ── Primary download button ───────────────────────────────────────────────────
dl_col, _ = st.columns([2, 6])
with dl_col:
    st.download_button(
        label="⬇️  Download Full Excel Report",
        data=excel_bytes,
        file_name=f"delivery_report_{uploaded.name.replace('.txt', '')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.markdown("---")


# ════════════════════════════════════════════════════════════════════════════════
# BUILD DATAFRAMES
# ════════════════════════════════════════════════════════════════════════════════
df_summary = pd.DataFrame(summary)  if summary    else pd.DataFrame()
df_details = pd.DataFrame(details)  if details    else pd.DataFrame()
df_routes  = pd.DataFrame(routes)   if routes     else pd.DataFrame()
df_exc     = pd.DataFrame(exceptions) if exceptions else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════════
# SIDEBAR FILTERS
# ════════════════════════════════════════════════════════════════════════════════
all_people = sorted(df_details['Delivery Boy'].unique()) if not df_details.empty else []
all_dates  = sorted(df_details['Date'].unique())         if not df_details.empty else []

with st.sidebar:
    st.markdown("### 🔍 Filters")
    sel_people = st.multiselect("Delivery Personnel", all_people, default=all_people)
    sel_dates  = st.multiselect("Date", all_dates, default=all_dates)
    if not sel_people: sel_people = all_people
    if not sel_dates:  sel_dates  = all_dates

    st.markdown("---")
    st.markdown("### 📊 Quick Stats")
    if not df_details.empty:
        nt = pd.to_numeric(df_details['Time Spent (mins)'],              errors='coerce').dropna()
        ni = pd.to_numeric(df_details['Time Between Deliveries (mins)'], errors='coerce').dropna()
        st.metric("Avg Store Time",   f"{nt.mean():.1f} min" if not nt.empty else "—")
        st.metric("Avg Travel Time",  f"{ni.mean():.1f} min" if not ni.empty else "—")
        st.metric("Total Exceptions", len(exceptions))

    st.markdown("---")
    st.markdown("### ℹ️ About")
    st.caption(
        "Parses WhatsApp group chat exports.\n\n"
        "Supports: Route/Store/POD/Break sequences, "
        "multiple delivery boys, Android & iOS formats."
    )


# ── Apply filters ─────────────────────────────────────────────────────────────
def _f(df, name_col='Delivery Boy'):
    if df.empty: return df
    return df[df[name_col].isin(sel_people) & df['Date'].isin(sel_dates)]

det_f = _f(df_details)
rte_f = _f(df_routes)
exc_f = _f(df_exc)
sum_f = _f(df_summary, name_col='Name')


# ════════════════════════════════════════════════════════════════════════════════
# TABS
# ════════════════════════════════════════════════════════════════════════════════
t1, t2, t3, t4, t5 = st.tabs([
    "📊  Charts",
    "📋  Daily Summary",
    "🗺️  Delivery Details",
    "🚚  Route Summary",
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
            tmp['ts'] = pd.to_numeric(tmp['Time Spent (mins)'], errors='coerce')
            avg = tmp.groupby('Delivery Boy')['ts'].mean().dropna().round(1)
            if not avg.empty: st.bar_chart(avg.rename("Avg mins"))
            else: st.info("No timing data.")

        st.markdown("")
        r2c1, r2c2, r2c3 = st.columns(3)

        with r2c1:
            st.markdown("##### 🚗 Avg Travel Time (mins)")
            tmp2 = det_f.copy()
            tmp2['it'] = pd.to_numeric(tmp2['Time Between Deliveries (mins)'], errors='coerce')
            avg_i = tmp2.groupby('Delivery Boy')['it'].mean().dropna().round(1)
            if not avg_i.empty: st.bar_chart(avg_i.rename("Avg mins"))
            else: st.info("No inter-delivery data.")

        with r2c2:
            st.markdown("##### 🚚 Deliveries per Route")
            if not rte_f.empty:
                tmp3 = rte_f.copy()
                tmp3['Label'] = (
                    tmp3['Delivery Boy'].str.split().str[0] + ' R' +
                    tmp3['Route No.'].astype(str)
                )
                st.bar_chart(tmp3.set_index('Label')['Total Deliveries'])
            else:
                st.info("No route data.")

        with r2c3:
            st.markdown("##### ⚠️ Exceptions by Type")
            if not exc_f.empty: st.bar_chart(exc_f['Exception Type'].value_counts())
            else: st.success("✅ No exceptions!")

        if not sum_f.empty:
            st.markdown("")
            st.markdown("##### 🕐 Working Time Breakdown (mins)")
            wt = sum_f.copy()
            wt['Label'] = wt['Name'].str.split().str[0] + ' ' + wt['Date']
            st.bar_chart(wt.set_index('Label')[[
                'Total Working Time (mins)',
                'Total Break Time (mins)',
                'Net Working Time (mins)',
            ]])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — DAILY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
with t2:
    sec("📋", "Daily Summary per Delivery Boy", len(sum_f))
    if sum_f.empty:
        st.info("No data for selected filters.")
    else:
        st.dataframe(
            style_summary(sum_f),
            use_container_width=True,
            height=min(420, 60 + len(sum_f) * 38),
        )
        st.caption("🟡 Net Working Time < 5 hrs  |  🟡 Avg Delivery > 30 min")

        st.markdown("#### 🧑 Person Cards")
        for _, row in sum_f.iterrows():
            with st.expander(f"👤 {row['Name']}  —  {row['Date']}"):
                a, b, c, d = st.columns(4)
                a.metric("Deliveries",    row['Total Deliveries (POD)'])
                b.metric("Routes",        row['Total Routes'])
                c.metric("Net Work Time", f"{row['Net Working Time (mins)']} min")
                d.metric("Avg Delivery",
                         f"{row['Avg Time per Delivery (mins)']} min"
                         if row['Avg Time per Delivery (mins)'] else "—")
                e, f_, g, h = st.columns(4)
                e.metric("First Start",  row['First Start'])
                f_.metric("Last End",    row['Last End'])
                g.metric("Break Time",   f"{row['Total Break Time (mins)']} min")
                h.metric("Stores",       row['Total Stores Covered'])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — DELIVERY DETAILS
# ─────────────────────────────────────────────────────────────────────────────
with t3:
    sec("🗺️", "Delivery Details", len(det_f))
    if det_f.empty:
        st.info("No records for selected filters.")
    else:
        st.dataframe(
            style_details(det_f),
            use_container_width=True,
            height=min(620, 60 + len(det_f) * 38),
        )
        st.caption("🟢 OK  |  🟡 Time >30 min  |  🔴 Missing POD / Travel >60 min")

        st.markdown("")
        qs1, qs2, qs3, qs4 = st.columns(4)
        nt = pd.to_numeric(det_f['Time Spent (mins)'],              errors='coerce').dropna()
        ni = pd.to_numeric(det_f['Time Between Deliveries (mins)'], errors='coerce').dropna()
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
        st.dataframe(
            style_routes(rte_f),
            use_container_width=True,
            height=min(500, 60 + len(rte_f) * 38),
        )
        st.caption("🟢 Complete  |  🔴 Not Ended  |  🟡 Avg Travel > 45 min")

        st.markdown("#### 🚚 Route Cards")
        for _, row in rte_f.iterrows():
            icon = "✅" if row['Status'] == 'Complete' else "❌"
            with st.expander(
                f"{icon}  {row['Delivery Boy']}  —  Route {row['Route No.']}  ({row['Date']})"
            ):
                a, b, c, d = st.columns(4)
                a.metric("Start",      row['Start Time'])
                b.metric("End",        row['End Time'])
                c.metric("Deliveries", row['Total Deliveries'])
                d.metric("Total Time",
                         f"{row['Total Time (mins)']} min"
                         if row['Total Time (mins)'] else "—")
                e, f_, g, h = st.columns(4)
                e.metric("Avg Delivery",
                         f"{row['Avg per Delivery (mins)']} min"
                         if row['Avg per Delivery (mins)'] else "—")
                f_.metric("Avg Travel",
                          f"{row['Avg Inter-Delivery (mins)']} min"
                          if row['Avg Inter-Delivery (mins)'] else "—")
                g.metric("Max Travel",
                         f"{row['Max Inter-Delivery (mins)']} min"
                         if row['Max Inter-Delivery (mins)'] else "—")
                h.metric("Min Travel",
                         f"{row['Min Inter-Delivery (mins)']} min"
                         if row['Min Inter-Delivery (mins)'] else "—")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — FLAGS & EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────────────
with t5:
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
        st.dataframe(
            style_exc_table(exc_f),
            use_container_width=True,
            height=min(600, 60 + len(exc_f) * 38),
        )

        st.markdown("#### 👤 Exceptions per Person")
        pivot = (
            exc_f
            .groupby(['Delivery Boy', 'Exception Type'])
            .size()
            .unstack(fill_value=0)
        )
        st.dataframe(
            pivot.style.highlight_max(axis=0, color='#f8d7da'),
            use_container_width=True,
        )


# ════════════════════════════════════════════════════════════════════════════════
# FOOTER
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("---")
fc1, fc2 = st.columns([4, 1])
with fc1:
    st.caption(
        "WhatsApp Delivery Tracker · "
        "Route / Store / POD / Break parsing · "
        "Multi-person · Android & iOS formats"
    )
with fc2:
    st.download_button(
        "⬇️ Excel",
        data=excel_bytes,
        file_name=f"delivery_report_{uploaded.name.replace('.txt', '')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="dl_footer",
    )
