"""
WhatsApp Delivery Tracker — Streamlit App
Deploy at: https://streamlit.io/cloud
"""
import sys, os, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd

from parser   import parse_chat
from engine   import (process_messages, build_delivery_summary,
                      build_delivery_details, build_route_summary, build_exceptions)
from reporter import generate_excel

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="WhatsApp Delivery Tracker",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0b0f1a; }
  [data-testid="stHeader"] { background: transparent; }
  h1, h2, h3 { color: #e2e8f0 !important; }
  p, li, span { color: #94a3b8 !important; }
  .metric-card {
    background: #111827; border: 1px solid #1e2d45;
    border-radius: 14px; padding: 20px 24px; text-align: center;
  }
  .metric-num { font-size: 40px; font-weight: 700; color: #3b82f6 !important; }
  .metric-label { font-size: 13px; color: #64748b !important; margin-top: 4px; }
  .stDataFrame { border-radius: 12px; overflow: hidden; }
  div[data-testid="stTabs"] button { color: #94a3b8 !important; }
  div[data-testid="stTabs"] button[aria-selected="true"] { color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 📦 WhatsApp Delivery Tracker")
st.markdown("Upload a WhatsApp group chat export to generate a structured delivery report.")
st.markdown("---")

# ── File Upload ───────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Upload WhatsApp Chat Export (.txt)",
    type=['txt'],
    help="Export: WhatsApp → Group → ⋮ → Export Chat → Without Media"
)

if not uploaded:
    st.info("👆 Upload a `.txt` WhatsApp chat export to get started.")
    with st.expander("ℹ️ Expected Message Format"):
        st.code("""Route 1 Start
Sharma General Store
POD
Krishna Medical
POD
Break Start
Break End
Route 1 End""", language="text")
    st.stop()

# ── Process ───────────────────────────────────────────────────────────────────
raw = uploaded.read()
text = None
for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue

if not text:
    st.error("Could not decode the file. Please ensure it is a valid WhatsApp .txt export.")
    st.stop()

with st.spinner("Parsing & analysing chat data…"):
    messages = parse_chat(text)
    if not messages:
        st.error("No valid messages found. Please check the file format.")
        st.stop()
    states     = process_messages(messages)
    summary    = build_delivery_summary(states)
    details    = build_delivery_details(states)
    routes     = build_route_summary(states)
    exceptions = build_exceptions(states)
    excel_bytes = generate_excel(summary, details, routes, exceptions)

# ── Stats bar ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><div class="metric-num">{len(messages)}</div><div class="metric-label">💬 Messages Parsed</div></div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><div class="metric-num">{len(states)}</div><div class="metric-label">🧑‍💼 Delivery Personnel</div></div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-card"><div class="metric-num">{len(details)}</div><div class="metric-label">✅ Total Deliveries</div></div>', unsafe_allow_html=True)
with c4:
    exc_color = "#ef4444" if exceptions else "#10b981"
    st.markdown(f'<div class="metric-card"><div class="metric-num" style="color:{exc_color}!important">{len(exceptions)}</div><div class="metric-label">⚠️ Exceptions Found</div></div>', unsafe_allow_html=True)

st.markdown("")

# ── Download button ───────────────────────────────────────────────────────────
st.download_button(
    label="⬇️  Download Full Excel Report",
    data=excel_bytes,
    file_name="delivery_report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=False,
)

st.markdown("---")

# ── Helper: style dataframes ──────────────────────────────────────────────────
def style_status(val):
    if val == 'OK':          return 'background-color:#d4edda; color:#155724'
    if val == 'Delayed':     return 'background-color:#fff3cd; color:#856404'
    if val == 'Missing POD': return 'background-color:#f8d7da; color:#721c24'
    if val == 'Not Ended':   return 'background-color:#f8d7da; color:#721c24'
    if val == 'Complete':    return 'background-color:#d4edda; color:#155724'
    return ''

def style_exc_type(val):
    warm = {'Long Delay','No Activity Gap','Break End Without Start','High Travel Time'}
    if val in warm: return 'background-color:#fff3cd; color:#856404; font-weight:600'
    return 'background-color:#f8d7da; color:#721c24; font-weight:600'

def style_inter(val):
    try:
        if isinstance(val, (int, float)) and val > 60:
            return 'background-color:#f8d7da; color:#721c24; font-weight:600'
    except: pass
    return ''

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Charts", "📋 Delivery Summary", "🗺️ Delivery Details",
    "🚚 Route Summary", "⚠️ Exceptions"
])

# ── Tab: Charts ───────────────────────────────────────────────────────────────
with tab1:
    if not summary:
        st.info("No data to chart.")
    else:
        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader("Total Deliveries per Person")
            df_del = pd.DataFrame(summary)[['Name','Total Deliveries (POD)']].groupby('Name').sum()
            st.bar_chart(df_del)

        with col_b:
            st.subheader("Avg Time per Delivery (mins)")
            df_avg = pd.DataFrame(summary)[['Name','Avg Time per Delivery (mins)']].dropna()
            if not df_avg.empty:
                df_avg = df_avg.groupby('Name').mean()
                st.bar_chart(df_avg)
            else:
                st.info("No timing data available.")

        col_c, col_d = st.columns(2)

        with col_c:
            st.subheader("Status Breakdown")
            if details:
                status_counts = pd.DataFrame(details)['Status'].value_counts()
                st.bar_chart(status_counts)

        with col_d:
            st.subheader("Avg Inter-Delivery Time by Route")
            if routes:
                df_r = pd.DataFrame(routes)
                df_plot = df_r[['Delivery Boy','Route No.','Avg Inter-Delivery (mins)']].dropna()
                if not df_plot.empty:
                    df_plot['Label'] = df_r['Delivery Boy'] + ' R' + df_r['Route No.'].astype(str)
                    df_plot = df_plot.set_index('Label')[['Avg Inter-Delivery (mins)']]
                    st.bar_chart(df_plot)
                else:
                    st.info("No inter-delivery data available.")

# ── Tab: Delivery Summary ─────────────────────────────────────────────────────
with tab2:
    st.subheader(f"📋 Delivery Summary — {len(summary)} records")
    if summary:
        st.dataframe(pd.DataFrame(summary), use_container_width=True)
    else:
        st.info("No summary data.")

# ── Tab: Delivery Details ─────────────────────────────────────────────────────
with tab3:
    st.subheader(f"🗺️ Delivery Details — {len(details)} records")
    if details:
        df_d = pd.DataFrame(details)
        styled = df_d.style \
            .applymap(style_status, subset=['Status']) \
            .applymap(style_inter,  subset=['Time Between Deliveries (mins)'])
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("No detail records.")

# ── Tab: Route Summary ────────────────────────────────────────────────────────
with tab4:
    st.subheader(f"🚚 Route Summary — {len(routes)} routes")
    if routes:
        df_r = pd.DataFrame(routes)
        styled_r = df_r.style.applymap(style_status, subset=['Status'])
        st.dataframe(styled_r, use_container_width=True)
    else:
        st.info("No route data.")

# ── Tab: Exceptions ───────────────────────────────────────────────────────────
with tab5:
    st.subheader(f"⚠️ Exceptions & Flags — {len(exceptions)} found")
    if exceptions:
        df_e = pd.DataFrame(exceptions)
        styled_e = df_e.style.applymap(style_exc_type, subset=['Exception Type'])
        st.dataframe(styled_e, use_container_width=True)
    else:
        st.success("✅ No exceptions detected!")
