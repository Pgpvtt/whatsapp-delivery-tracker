import streamlit as st
import pandas as pd
from datetime import datetime

st.title("📦 WhatsApp Delivery Tracker")

uploaded_file = st.file_uploader("Upload WhatsApp Chat (.txt)", type=["txt"])

def parse_chat(file):
    data = []
    lines = file.read().decode("utf-8").split("\n")

    for line in lines:
        try:
            if "]" in line:
                date_time, rest = line.split("] ", 1)
                name, message = rest.split(": ", 1)
                date_time = date_time.replace("[", "")
                dt = datetime.strptime(date_time, "%d/%m/%Y, %H:%M:%S")

                data.append({
                    "datetime": dt,
                    "name": name,
                    "message": message
                })
        except:
            continue

    return pd.DataFrame(data)

if uploaded_file:
    df = parse_chat(uploaded_file)

    st.subheader("Raw Data")
    st.dataframe(df)

    st.subheader("Summary")
    st.write("Total Messages:", len(df))

    pod_count = df[df["message"].str.upper() == "POD"].shape[0]
    st.write("Total Deliveries (POD):", pod_count)
