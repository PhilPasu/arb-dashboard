import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import aiohttp
import asyncio
import threading
import time
import os
import csv
from datetime import datetime

# ==============================================================================
# Configuration & Constants
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_MAINNET_API = "https://mainnet.zklighter.elliot.ai"
LIGHTER_API_VERSION = "/api/v1"
PARADEX_MAINNET_API = "https://api.prod.paradex.trade/v1"
BYBIT_MAINNET_API = "https://api.bybit.com"

# ==============================================================================
# 1. Improved Async Data Fetcher
# ==============================================================================
async def fetch_json(session, url, params=None):
    try:
        headers = {"User-Agent": "ArbDashboard/1.1"}
        async with session.get(url, params=params, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                return await resp.json()
    except: pass
    return None

async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # 1. Lighter Mainnet - BTC ID is 4, ETH is 2048
        try:
            m_id = 2048 if coin == "ETH" else (4 if coin == "BTC" else None) 
            if m_id:
                url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
                data = await fetch_json(session, url, {'market_id': m_id, 'limit': 1})
                if data:
                    # Robust check for different API response structures
                    asks = data.get('asks', [])
                    bids = data.get('bids', [])
                    if asks and bids:
                        # Try to handle both [price, size] and {'price': x} formats
                        ask = float(asks[0][0] if isinstance(asks[0], list) else asks[0].get('price', 0))
                        bid = float(bids[0][0] if isinstance(bids[0], list) else bids[0].get('price', 0))
                        if ask > 0 and bid > 0:
                            prices['lighter'] = (ask + bid) / 2
        except: pass

        # 2. Paradex
        try:
            data = await fetch_json(session, f"{PARADEX_MAINNET_API}/markets/summary", {'market': f"{coin}-USD-PERP"})
            if data and 'results' in data:
                prices['paradex'] = float(data['results'][0]['last_traded_price'])
        except: pass

        # 3. Bybit
        try:
            data = await fetch_json(session, f"{BYBIT_MAINNET_API}/v5/market/tickers", {'category': 'linear', 'symbol': f"{coin}USDT"})
            if data and data['retCode'] == 0:
                prices['bybit'] = float(data['result']['list'][0]['lastPrice'])
        except: pass
            
        # 4. Binance
        try:
             data = await fetch_json(session, "https://fapi.binance.com/fapi/v1/ticker/price", {'symbol': f"{coin}USDT"})
             if data and 'price' in data:
                 prices['binance'] = float(data['price'])
        except: pass

    return prices

# ==============================================================================
# 2. Background Data Collector
# ==============================================================================
class DataCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        self.filenames = {c: os.path.join(DATA_DIR, f"history_{c}.csv") for c in self.coins}
        for c, fname in self.filenames.items():
            if not os.path.exists(fname):
                with open(fname, 'w', newline='') as f:
                    csv.writer(f).writerow(['timestamp', 'lighter', 'paradex', 'bybit', 'binance'])

    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_prices(coin))
                    with open(self.filenames[coin], 'a', newline='') as f:
                        csv.writer(f).writerow([p['timestamp'], p.get('lighter', ''), p.get('paradex', ''), p.get('bybit', ''), p.get('binance', '')])
                except: pass
            time.sleep(1.5)

@st.cache_resource
def start_collector():
    c = DataCollector()
    c.start()
    return c

# ==============================================================================
# 3. Streamlit UI
# ==============================================================================
def main():
    st.set_page_config(page_title="Arb Analysis", layout="wide")
    start_collector()

    # --- SIDEBAR ---
    st.sidebar.header("Data Settings")
    selected_coin = st.sidebar.selectbox("Select Asset", ["ETH", "BTC"])
    target_ex = st.sidebar.selectbox("Target Exchange", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.subheader("Windows")
    hist_win = st.sidebar.slider("Chart View (Minutes)", 5, 120, 30)
    roll_win = st.sidebar.slider("Rolling Calc Period (Minutes)", 1, 120, 15)
    
    st.sidebar.subheader("Toggle Bands")
    show_90 = st.sidebar.checkbox("90th Percentile", value=True)
    show_50 = st.sidebar.checkbox("50th (Median)", value=True)
    show_10 = st.sidebar.checkbox("10th Percentile", value=True)

    # --- DATA PROCESSING ---
    fname = os.path.join(DATA_DIR, f"history_{selected_coin}.csv")
    if not os.path.exists(fname):
        st.info("Initializing CSV...")
        return

    df = pd.read_csv(fname)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True).sort_index()
    for c in ['lighter', 'paradex', 'bybit', 'binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    # Calculate Spread (bps)
    target_col = target_ex.lower()
    df['spread_bps'] = (df[target_col] - df['lighter']) / df[target_col] * 10000
    
    # Calculate Rolling Stats
    r_str = f"{roll_win}min"
    df['q90'] = df['spread_bps'].rolling(r_str).quantile(0.90)
    df['q50'] = df['spread_bps'].rolling(r_str).quantile(0.50)
    df['q10'] = df['spread_bps'].rolling(r_str).quantile(0.10)

    # Filter for Display
    view_df = df[df.index >= (datetime.now() - pd.Timedelta(minutes=hist_win))]

    if view_df.empty:
        st.warning("Collecting data points...")
    else:
        # --- METRICS ---
        curr = view_df.iloc[-1]
        m1, m2, m3 = st.columns(3)
        m1.metric("Lighter Price", f"${curr['lighter']:,.2f}")
        m2.metric(f"{target_ex} Price", f"${curr[target_col]:,.2f}")
        m3.metric("Spread (bps)", f"{curr['spread_bps']:.2f}")

        # --- PLOT 1: SPREAD + BANDS ---
        st.subheader("Spread vs. Dynamic Bands")
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['spread_bps'], name="Live Spread", line=dict(color='white', width=1.5)))
        if show_90: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q90'], name="90th", line=dict(dash='dot', color='red')))
        if show_50: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q50'], name="Median", line=dict(dash='dash', color='cyan')))
        if show_10: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q10'], name="10th", line=dict(dash='dot', color='green')))
        fig1.update_layout(height=400, template="plotly_dark")
        st.plotly_chart(fig1, use_container_width=True)

        # --- PLOT 2: PERCENTILES ONLY ---
        st.subheader(f"Rolling {roll_win}m Percentile Analysis")
        fig2 = go.Figure()
        if show_90: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q90'], name="Upper Bound (90%)", line=dict(color='orange')))
        if show_50: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q50'], name="Fair Value (50%)", line=dict(color='blue')))
        if show_10: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q10'], name="Lower Bound (10%)", line=dict(color='purple')))
        fig2.add_hline(y=0, line_width=1, line_color="gray")
        fig2.update_layout(height=300, template="plotly_dark", yaxis_title="Basis Points (bps)")
        st.plotly_chart(fig2, use_container_width=True)

    time.sleep(1)
    st.rerun()

if __name__ == "__main__":
    main()
