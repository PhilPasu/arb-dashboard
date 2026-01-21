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
# 1. PATH & CONFIG (Resilient Setup)
# ==============================================================================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# We use a global dict for health because background threads can't write to st.session_state
API_STATUS = {"Lighter": "⏳", "Paradex": "⏳", "Bybit": "⏳", "Binance": "⏳"}

# ==============================================================================
# 2. DATA COLLECTOR (Enhanced Robustness)
# ==============================================================================
async def fetch_prices_robust(coin="ETH"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prices = {'timestamp': now}
    
    # Headers to look like a browser (helps with Bybit/Binance blocks)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. PARADEX (Market Summary)
        try:
            url = f"https://api.prod.paradex.trade/v1/markets/summary?market={coin}-USD-PERP"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['paradex'] = float(d['results'][0]['last_traded_price'])
                    API_STATUS['Paradex'] = "🟢"
                else: API_STATUS['Paradex'] = f"🔴 {r.status}"
        except: API_STATUS['Paradex'] = "❌ Err"

        # 2. BINANCE FUTURES (Price Ticker)
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['binance'] = float(d['price'])
                    API_STATUS['Binance'] = "🟢"
                else: API_STATUS['Binance'] = f"🔴 {r.status}"
        except: API_STATUS['Binance'] = "❌ Err"

        # 3. BYBIT V5 (Tickers)
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['bybit'] = float(d['result']['list'][0]['lastPrice'])
                    API_STATUS['Bybit'] = "🟢"
                else: API_STATUS['Bybit'] = f"🔴 {r.status}"
        except: API_STATUS['Bybit'] = "❌ Err"

        # 4. ZKLIGHTER (Orderbook)
        try:
            m_id = 4 if coin == "BTC" else 2048
            url = f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={m_id}&limit=1"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    if d.get('asks') and d.get('bids'):
                        # ZkLighter returns price in first element of each level list
                        prices['lighter'] = (float(d['asks'][0][0]) + float(d['bids'][0][0])) / 2
                        API_STATUS['Lighter'] = "🟢"
                    else: API_STATUS['Lighter'] = "🟡 Empty"
                else: API_STATUS['Lighter'] = f"🔴 {r.status}"
        except: API_STATUS['Lighter'] = "❌ Err"

    return prices

class ArbWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        
    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_prices_robust(coin))
                    path = os.path.join(DATA_DIR, f"arb_{coin}.csv")
                    # Init file
                    if not os.path.exists(path):
                        with open(path, 'w', newline='') as f:
                            csv.writer(f).writerow(['timestamp','lighter','paradex','bybit','binance'])
                    # Append row
                    if len(p) > 1:
                        with open(path, 'a', newline='') as f:
                            csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(2)

@st.cache_resource
def start_worker():
    w = ArbWorker(); w.start(); return w

# ==============================================================================
# 3. FRAGMENT UI (No-Blink Updates)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_plots(coin, bench, lookback, stats_period, s90, s50, s10):
    path = os.path.join(DATA_DIR, f"arb_{coin}.csv")
    if not os.path.exists(path):
        st.info("⌛ Gathering market data...")
        return

    df = pd.read_csv(path)
    if df.empty: return

    # Cleaning & Processing
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()
    df = df[~df.index.duplicated(keep='last')]
    for c in ['lighter','paradex','bybit','binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce').ffill()

    # Arb Calculation
    target_col = bench.lower()
    df['spread'] = (df[target_col] - df['lighter']) / df[target_col] * 10000
    
    # Filtering for Window
    view = df[df.index >= (df.index[-1] - timedelta(minutes=lookback))].copy()
    if view.empty: return

    # Percentiles
    view['q90'] = view['spread'].rolling(f"{stats_period}min").quantile(0.90)
    view['q50'] = view['spread'].rolling(f"{stats_period}min").quantile(0.50)
    view['q10'] = view['spread'].rolling(f"{stats_period}min").quantile(0.10)

    # UI Configuration
    p_cfg = {'displayModeBar': False}

    # 1. Price Overlay
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=view.index, y=view[target_col], name=bench, line=dict(color='#00FFAA')))
    fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
    fig_p.update_layout(title="Live Price Feed", height=300, template="plotly_dark", margin=dict(t=30,b=10))
    st.plotly_chart(fig_p, use_container_width=True, config=p_cfg, key="p_v")

    # 2. Spread Line
    fig_s = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
    fig_s.update_layout(title="Arb Spread (Basis Points)", height=300, template="plotly_dark")
    st.plotly_chart(fig_s, use_container_width=True, config=p_cfg, key="s_v")

    # 3. Spread Probability Histogram
    fig_h1 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=60, marker_color='#FF4B4B', opacity=0.7))
    fig_h1.update_layout(title="Spread Occurrence Density", height=250, template="plotly_dark", bargap=0.05)
    st.plotly_chart(fig_h1, use_container_width=True, config=p_cfg, key="h1_v")

    # 4. Volatility Corridor
    fig_stat = go.Figure()
    if s90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
    if s50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
    if s10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
    fig_stat.update_layout(title="Market Percentile Corridor", height=250, template="plotly_dark")
    st.plotly_chart(fig_stat, use_container_width=True, config=p_cfg, key="c_v")

    # 5. MEDIAN DISTRIBUTION (REQUESTED)
    fig_h2 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=60, marker_color='#00D4FF', opacity=0.7))
    fig_h2.update_layout(title=f"Distribution of {stats_period}m Rolling Median", height=250, template="plotly_dark", bargap=0.05)
    st.plotly_chart(fig_h2, use_container_width=True, config=p_cfg, key="h2_v")

# ==============================================================================
# 4. MAIN PAGE
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb Terminal", layout="wide")
    start_worker()

    st.sidebar.title("⚡ Settings")
    
    # LIVE API STATUS MONITOR
    st.sidebar.markdown("### API Health")
    status_cols = st.sidebar.columns(4)
    status_cols[0].write(f"LGT:{API_STATUS['Lighter']}")
    status_cols[1].write(f"PDX:{API_STATUS['Paradex']}")
    status_cols[2].write(f"BYB:{API_STATUS['Bybit']}")
    status_cols[3].write(f"BIN:{API_STATUS['Binance']}")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    bench = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.divider()
    lookback = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    stats_period = st.sidebar.slider("Stats Window (Mins)", 1, 120, 30)
    
    st.sidebar.subheader("Percentiles")
    s90 = st.sidebar.checkbox("90th", True)
    s50 = st.sidebar.checkbox("Median", True)
    s10 = st.sidebar.checkbox("10th", True)

    render_plots(coin, bench, lookback, stats_period, s90, s50, s10)

if __name__ == "__main__":
    main()
