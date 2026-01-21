import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import aiohttp
import asyncio
import threading
import time
import os
import csv
from datetime import datetime, timedelta

# ==============================================================================
# 1. SETUP & PATHING
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1"
PARADEX_API = "https://api.prod.paradex.trade/v1"
BYBIT_API = "https://api.bybit.com"

# ==============================================================================
# 2. DATA COLLECTOR (WITH LOGGING)
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    
    async with aiohttp.ClientSession() as session:
        # ZkLighter (ID 4=WBTC, 2048=ETH)
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    # Defensive check for nested list structure
                    asks, bids = data.get('asks', []), data.get('bids', [])
                    if asks and bids:
                        prices['lighter'] = (float(asks[0][0]) + float(bids[0][0])) / 2
        except Exception: pass

        # Benchmarks
        try:
            # Paradex
            async with session.get(f"{PARADEX_API}/markets/summary", params={'market': f"{coin}-USD-PERP"}, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['paradex'] = float(d['results'][0]['last_traded_price'])
            
            # Bybit
            async with session.get(f"{BYBIT_API}/v5/market/tickers", params={'category': 'linear', 'symbol': f"{coin}USDT"}, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['bybit'] = float(d['result']['list'][0]['lastPrice'])

            # Binance
            async with session.get("https://fapi.binance.com/fapi/v1/ticker/price", params={'symbol': f"{coin}USDT"}, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['binance'] = float(d['price'])
        except Exception: pass
        
    return prices

class DataCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        self.files = {c: os.path.join(DATA_DIR, f"history_{c}.csv") for c in self.coins}
        for f in self.files.values():
            if not os.path.exists(f):
                with open(f, 'w', newline='') as csvf:
                    csv.writer(csvf).writerow(['timestamp','lighter','paradex','bybit','binance'])

    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_prices(coin))
                    # Only write if we have at least one valid price to save space
                    if len(p) > 1:
                        with open(self.files[coin], 'a', newline='') as f:
                            csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except Exception: pass
            time.sleep(2.0)

@st.cache_resource
def start_collector():
    c = DataCollector()
    c.start()
    return c

# ==============================================================================
# 3. FRAGMENT (UI RENDERING)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_terminal(coin, ex, hist_m, roll_m, show_90, show_50, show_10):
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    
    if not os.path.exists(path):
        st.error("CSV file not found. Check permissions.")
        return

    df = pd.read_csv(path)
    
    # DEBUG INFO IN SIDEBAR
    with st.sidebar.expander("Diagnostic Data", expanded=False):
        st.write(f"Rows in CSV: {len(df)}")
        if not df.empty:
            st.write(f"Last update: {df['timestamp'].iloc[-1]}")
            st.dataframe(df.tail(3))

    if len(df) < 2:
        st.warning("Collecting initial data points... please wait ~10 seconds.")
        return
    
    # Data Cleaning
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    
    for c in ['lighter','paradex','bybit','binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce').ffill()
    
    tgt = ex.lower()
    if tgt not in df.columns or df[tgt].isnull().all():
        st.error(f"No data available for {ex}. API may be rate-limiting.")
        return

    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    
    # Filter Window
    lookback_cutoff = df.index[-1] - timedelta(minutes=hist_m)
    view = df[df.index >= lookback_cutoff].copy()
    
    if view.empty:
        st.info("No data in this time window.")
        return

    # Stats
    view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
    view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
    view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

    # PLOTS
    cfg = {'displayModeBar': False}
    
    # 1. Price
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
    fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
    fig_p.update_layout(title="Price Overlay", height=300, template="plotly_dark", margin=dict(t=40, b=10))
    st.plotly_chart(fig_p, use_container_width=True, config=cfg, key="p_chart")

    # 2. Spread & Histogram
    fig_s = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
    fig_s.update_layout(title="Arbitrage Spread (bps)", height=350, template="plotly_dark")
    st.plotly_chart(fig_s, use_container_width=True, config=cfg, key="s_chart")

    fig_h1 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=50, marker_color='#FF4B4B', opacity=0.7))
    fig_h1.update_layout(title="Spread Distribution", height=250, template="plotly_dark", bargap=0.1)
    st.plotly_chart(fig_h1, use_container_width=True, config=cfg, key="h1_chart")

    # 3. Corridor
    fig_stat = go.Figure()
    if show_90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
    if show_50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
    if show_10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
    fig_stat.update_layout(title="Historical Corridor", height=250, template="plotly_dark")
    st.plotly_chart(fig_stat, use_container_width=True, config=cfg, key="stat_chart")

    # 4. Median Histogram
    fig_h2 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=50, marker_color='#00D4FF', opacity=0.7))
    fig_h2.update_layout(title="Rolling Median Distribution", height=250, template="plotly_dark", bargap=0.1)
    st.plotly_chart(fig_h2, use_container_width=True, config=cfg, key="h2_chart")

# ==============================================================================
# 4. MAIN
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb", layout="wide")
    start_collector()

    st.sidebar.title("Configuration")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    hist_m = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_m = st.sidebar.slider("Stats Period (Mins)", 1, 1440, 30)
    
    s90 = st.sidebar.checkbox("Show 90th", True)
    s50 = st.sidebar.checkbox("Show Median", True)
    s10 = st.sidebar.checkbox("Show 10th", True)

    render_terminal(coin, ex, hist_m, roll_m, s90, s50, s10)

if __name__ == "__main__":
    main()
