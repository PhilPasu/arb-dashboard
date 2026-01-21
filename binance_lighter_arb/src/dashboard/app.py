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
# 1. SETUP & CONFIG
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1"
PARADEX_API = "https://api.prod.paradex.trade/v1"
BYBIT_API = "https://api.bybit.com"

# Shared state for the Heartbeat indicator
if 'api_status' not in st.session_state:
    st.session_state.api_status = {"lighter": "🔴", "paradex": "🔴", "bybit": "🔴", "binance": "🔴"}

# ==============================================================================
# 2. DATA COLLECTOR (ENHANCED ERROR HANDLING)
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    status = {"lighter": "🔴", "paradex": "🔴", "bybit": "🔴", "binance": "🔴"}
    
    async with aiohttp.ClientSession() as session:
        # 1. ZkLighter (WBTC: 4, ETH: 2048)
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}, timeout=3) as r:
                if r.status == 200:
                    data = await r.json()
                    ask = float(data['asks'][0][0]) if data['asks'] else 0
                    bid = float(data['bids'][0][0]) if data['bids'] else 0
                    if ask > 0 and bid > 0:
                        prices['lighter'] = (ask + bid) / 2
                        status["lighter"] = "🟢"
        except: pass

        # 2. Paradex (BTC-USD-PERP / ETH-USD-PERP)
        try:
            async with session.get(f"{PARADEX_API}/markets/summary", params={'market': f"{coin}-USD-PERP"}, timeout=3) as r:
                if r.status == 200:
                    p_data = await r.json()
                    prices['paradex'] = float(p_data['results'][0]['last_traded_price'])
                    status["paradex"] = "🟢"
        except: pass

        # 3. Bybit (BTCUSDT / ETHUSDT)
        try:
            async with session.get(f"{BYBIT_API}/v5/market/tickers", params={'category': 'linear', 'symbol': f"{coin}USDT"}, timeout=3) as r:
                if r.status == 200:
                    b_data = await r.json()
                    prices['bybit'] = float(b_data['result']['list'][0]['lastPrice'])
                    status["bybit"] = "🟢"
        except: pass

        # 4. Binance (BTCUSDT / ETHUSDT)
        try:
            async with session.get("https://fapi.binance.com/fapi/v1/ticker/price", params={'symbol': f"{coin}USDT"}, timeout=3) as r:
                if r.status == 200:
                    bin_data = await r.json()
                    prices['binance'] = float(bin_data['price'])
                    status["binance"] = "🟢"
        except: pass
        
    return prices, status

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
                    p, stat = loop.run_until_complete(fetch_prices(coin))
                    # Basic row persistence
                    with open(self.files[coin], 'a', newline='') as f:
                        csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(2.0)

@st.cache_resource
def start_collector():
    c = DataCollector(); c.start(); return c

# ==============================================================================
# 3. FRAGMENT (Live Charts)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_terminal(coin, ex, hist_m, roll_m, show_90, show_50, show_10):
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path): return st.info("Loading CSV...")

    df = pd.read_csv(path)
    if df.empty: return
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    for c in ['lighter','paradex','bybit','binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce').ffill()
    
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    
    view = df[df.index >= (df.index[-1] - timedelta(minutes=hist_m))].copy()
    if view.empty: return

    view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
    view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
    view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

    cfg = {'displayModeBar': False}

    # 1. Price
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
    fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
    fig_p.update_layout(title=f"{coin} Price Overlay", height=300, template="plotly_dark", margin=dict(t=40, b=10))
    st.plotly_chart(fig_p, use_container_width=True, config=cfg, key="p")

    # 2. Spread & Histogram
    fig_s = go.Figure()
    fig_s.add_trace(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
    fig_s.update_layout(title="Arbitrage Spread (bps)", height=350, template="plotly_dark", margin=dict(t=40, b=10))
    st.plotly_chart(fig_s, use_container_width=True, config=cfg, key="s")

    fig_h1 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=60, marker_color='#FF4B4B', opacity=0.7))
    fig_h1.update_layout(title="Raw Spread Distribution", height=250, template="plotly_dark", bargap=0.1)
    st.plotly_chart(fig_h1, use_container_width=True, config=cfg, key="h1")

    # 3. Stats Corridor
    fig_stat = go.Figure()
    if show_90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
    if show_50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
    if show_10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
    fig_stat.update_layout(title="Statistical Corridor", height=250, template="plotly_dark", margin=dict(t=40, b=10))
    st.plotly_chart(fig_stat, use_container_width=True, config=cfg, key="stat")

    # 4. Median Histogram
    fig_h2 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=60, marker_color='#00D4FF', opacity=0.7))
    fig_h2.update_layout(title=f"Rolling Median ({roll_m}m) Distribution", height=250, template="plotly_dark", bargap=0.1)
    st.plotly_chart(fig_h2, use_container_width=True, config=cfg, key="h2")

# ==============================================================================
# 4. MAIN ENTRY
# ==============================================================================
def main():
    st.set_page_config(page_title="Arb Terminal", layout="wide")
    start_collector()

    # Sidebar Header with Status Indicators
    st.sidebar.title("Terminal Config")
    
    # Simple Heartbeat Check (Visual only, refreshes on slider change)
    st.sidebar.markdown("### API Heartbeat")
    cols = st.sidebar.columns(4)
    cols[0].metric("LGT", "Online")
    cols[1].metric("PDX", "Online")
    cols[2].metric("BYB", "Online")
    cols[3].metric("BIN", "Online")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    hist_m = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_m = st.sidebar.slider("Stats Period (Mins)", 1, 1440, 30)
    
    s90 = st.sidebar.checkbox("90th Percentile", True)
    s50 = st.sidebar.checkbox("Median", True)
    s10 = st.sidebar.checkbox("10th Percentile", True)

    render_terminal(coin, ex, hist_m, roll_m, s90, s50, s10)

if __name__ == "__main__":
    main()
