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
# 1. ROBUST SETUP & PATHING
# ==============================================================================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# Shared dictionary for UI status (safe for threads)
API_LOG = {"Lighter": "⏳", "Paradex": "⏳", "Bybit": "⏳", "Binance": "⏳"}

# ==============================================================================
# 2. DATA COLLECTOR (FIXED PARSING)
# ==============================================================================
async def fetch_all_prices(coin="ETH"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prices = {'timestamp': now}
    
    # Critical: Use a real browser User-Agent to avoid exchange blocks
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. BINANCE FUTURES (fapi.binance.com)
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['binance'] = float(d['price'])
                    API_LOG['Binance'] = "🟢"
                else: API_LOG['Binance'] = f"🔴 {r.status}"
        except: API_LOG['Binance'] = "❌ Err"

        # 2. BYBIT V5 (api.bybit.com)
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['bybit'] = float(d['result']['list'][0]['lastPrice'])
                    API_LOG['Bybit'] = "🟢"
                else: API_LOG['Bybit'] = f"🔴 {r.status}"
        except: API_LOG['Bybit'] = "❌ Err"

        # 3. ZKLIGHTER (Nested List Parsing Fix)
        try:
            m_id = 4 if coin == "BTC" else 2048
            url = f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={m_id}&limit=1"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    # Lighter returns [[price, size], ...]
                    if d.get('asks') and d.get('bids'):
                        ask = float(d['asks'][0][0])
                        bid = float(d['bids'][0][0])
                        prices['lighter'] = (ask + bid) / 2
                        API_LOG['Lighter'] = "🟢"
                    else: API_LOG['Lighter'] = "🟡 No Liquidity"
                else: API_LOG['Lighter'] = f"🔴 {r.status}"
        except: API_LOG['Lighter'] = "❌ Err"

        # 4. PARADEX (The fallback that worked)
        try:
            url = f"https://api.prod.paradex.trade/v1/markets/summary?market={coin}-USD-PERP"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    d = await r.json()
                    prices['paradex'] = float(d['results'][0]['last_traded_price'])
                    API_LOG['Paradex'] = "🟢"
        except: API_LOG['Paradex'] = "❌ Err"

    return prices

class MasterCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        
    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_all_prices(coin))
                    path = os.path.join(DATA_DIR, f"db_{coin}.csv")
                    if not os.path.exists(path):
                        with open(path, 'w', newline='') as f:
                            csv.writer(f).writerow(['timestamp','lighter','paradex','bybit','binance'])
                    if len(p) > 1:
                        with open(path, 'a', newline='') as f:
                            csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(2)

@st.cache_resource
def start_worker():
    w = MasterCollector(); w.start(); return w

# ==============================================================================
# 3. FRAGMENT (ANIMATED FEEL, NO-BLINK)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_plots(coin, bench, hist, roll, s90, s50, s10):
    path = os.path.join(DATA_DIR, f"db_{coin}.csv")
    if not os.path.exists(path):
        st.info("⌛ Gathering data...")
        return

    df = pd.read_csv(path)
    if df.empty: return

    # Cleaning
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index() # FIXED: Removed inplace=True
    df = df[~df.index.duplicated(keep='last')]
    
    # Better gap handling for new datasets
    for c in ['lighter','paradex','bybit','binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce').ffill().bfill()

    # Calculations
    target = bench.lower()
    df['spread'] = (df[target] - df['lighter']) / df[target] * 10000
    
    view = df[df.index >= (df.index[-1] - timedelta(minutes=hist))].copy()
    if view.empty: return

    view['q90'] = view['spread'].rolling(f"{roll}min").quantile(0.90)
    view['q50'] = view['spread'].rolling(f"{roll}min").quantile(0.50)
    view['q10'] = view['spread'].rolling(f"{roll}min").quantile(0.10)

    # UI Settings
    cfg = {'displayModeBar': False}

    # Plot 1: Prices
    f1 = go.Figure()
    f1.add_trace(go.Scatter(x=view.index, y=view[target], name=bench, line=dict(color='#00FFAA')))
    f1.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
    f1.update_layout(title="Live Price Feed", height=300, template="plotly_dark", margin=dict(t=30,b=10))
    st.plotly_chart(f1, use_container_width=True, config=cfg, key="p1")

    # Plot 2: Spread Line
    f2 = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
    f2.update_layout(title="Spread (bps)", height=300, template="plotly_dark")
    st.plotly_chart(f2, use_container_width=True, config=cfg, key="p2")

    # Plot 3: Histogram
    f3 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=60, marker_color='#FF4B4B', opacity=0.7))
    f3.update_layout(title="Spread Distribution", height=250, template="plotly_dark", bargap=0.05)
    st.plotly_chart(f3, use_container_width=True, config=cfg, key="p3")

    # Plot 4: Corridor
    f4 = go.Figure()
    if s90: f4.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
    if s50: f4.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
    if s10: f4.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
    f4.update_layout(title="Market Corridor", height=250, template="plotly_dark")
    st.plotly_chart(f4, use_container_width=True, config=cfg, key="p4")

    # Plot 5: Median Histogram (NEW)
    f5 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=60, marker_color='#00D4FF', opacity=0.7))
    f5.update_layout(title=f"Median ({roll}m) Distribution", height=250, template="plotly_dark", bargap=0.05)
    st.plotly_chart(f5, use_container_width=True, config=cfg, key="p5")

# ==============================================================================
# 4. MAIN TERMINAL
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Terminal", layout="wide")
    start_worker()

    st.sidebar.title("⚡ Terminal Settings")
    
    # API Monitor in Sidebar
    st.sidebar.markdown("### API Health")
    c1, c2 = st.sidebar.columns(2)
    c1.write(f"LGT: {API_LOG['Lighter']}")
    c2.write(f"PDX: {API_LOG['Paradex']}")
    c3, c4 = st.sidebar.columns(2)
    c3.write(f"BYB: {API_LOG['Bybit']}")
    c4.write(f"BIN: {API_LOG['Binance']}")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    bench = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.divider()
    hist = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll = st.sidebar.slider("Stats Window (Mins)", 1, 120, 30)
    
    s90 = st.sidebar.checkbox("Show 90th", True)
    s50 = st.sidebar.checkbox("Show Median", True)
    s10 = st.sidebar.checkbox("Show 10th", True)

    # Launch fragment
    render_plots(coin, bench, hist, roll, s90, s50, s10)

if __name__ == "__main__":
    main()
