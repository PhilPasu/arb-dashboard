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
# 1. ROBUST PATH & CONFIG
# ==============================================================================
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# Shared state to track API health across threads
if 'api_health' not in st.session_state:
    st.session_state.api_health = {}

# ==============================================================================
# 2. THE RESTORED DATA COLLECTOR
# ==============================================================================
async def fetch_all_prices(coin="ETH"):
    """Fetches from all 4 sources with strict parsing."""
    results = {'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    health = {}
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. PARADEX (The one that works)
        try:
            url = f"https://api.prod.paradex.trade/v1/markets/summary?market={coin}-USD-PERP"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    results['paradex'] = float(data['results'][0]['last_traded_price'])
                    health['Paradex'] = "🟢"
                else: health['Paradex'] = f"🔴 {r.status}"
        except Exception as e: health['Paradex'] = f"❌ Error"

        # 2. BINANCE (FUTURES)
        try:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    results['binance'] = float(data['price'])
                    health['Binance'] = "🟢"
                else: health['Binance'] = f"🔴 {r.status}"
        except Exception: health['Binance'] = "❌ Error"

        # 3. BYBIT (V5)
        try:
            url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    results['bybit'] = float(data['result']['list'][0]['lastPrice'])
                    health['Bybit'] = "🟢"
                else: health['Bybit'] = f"🔴 {r.status}"
        except Exception: health['Bybit'] = "❌ Error"

        # 4. ZKLIGHTER
        try:
            m_id = 4 if coin == "BTC" else 2048
            url = f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={m_id}&limit=1"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    # Check structure: data['asks'] is list of [price, size]
                    if data.get('asks') and data.get('bids'):
                        ask = float(data['asks'][0][0])
                        bid = float(data['bids'][0][0])
                        results['lighter'] = (ask + bid) / 2
                        health['Lighter'] = "🟢"
                    else: health['Lighter'] = "🟡 Empty Book"
                else: health['Lighter'] = f"🔴 {r.status}"
        except Exception: health['Lighter'] = "❌ Error"

    return results, health

class MasterCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        
    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    res, health = loop.run_until_complete(fetch_all_prices(coin))
                    path = os.path.join(DATA_DIR, f"db_{coin}.csv")
                    
                    # Ensure CSV exists
                    if not os.path.exists(path):
                        with open(path, 'w', newline='') as f:
                            csv.writer(f).writerow(['timestamp','lighter','paradex','bybit','binance'])
                    
                    # Write only if we have new data
                    if len(res) > 1:
                        with open(path, 'a', newline='') as f:
                            csv.writer(f).writerow([res['timestamp'], res.get('lighter',''), res.get('paradex',''), res.get('bybit',''), res.get('binance','')])
                    
                    # Update status for UI
                    st.session_state.api_health = health
                except: pass
            time.sleep(2)

@st.cache_resource
def init_worker():
    worker = MasterCollector()
    worker.start()
    return worker

# ==============================================================================
# 3. STATIC-FEEL UI (FRAGMENT)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_live_ui(coin, benchmark, lookback, roll_m, s90, s50, s10):
    path = os.path.join(DATA_DIR, f"db_{coin}.csv")
    if not os.path.exists(path):
        st.info("Searching for data...")
        return

    try:
        df = pd.read_csv(path).tail(2000) # Keep memory light
        if df.empty: return

        # Transform
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        for c in ['lighter','paradex','bybit','binance']:
            df[c] = pd.to_numeric(df[c], errors='coerce').ffill()

        # Calculate Spread
        tgt = benchmark.lower()
        df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
        
        # Windowing
        view = df[df.index >= (df.index[-1] - timedelta(minutes=lookback))].copy()
        view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
        view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
        view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

        # PLOTS
        config = {'displayModeBar': False}

        # Plot 1: Price
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(x=view.index, y=view[tgt], name=benchmark, line=dict(color='#00FFAA')))
        fig1.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
        fig1.update_layout(title="Live Price Overlay", height=300, template="plotly_dark", margin=dict(t=30,b=10))
        st.plotly_chart(fig1, use_container_width=True, config=config, key="f1")

        # Plot 2: Spread
        fig2 = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
        fig2.update_layout(title="Arbitrage Spread (Basis Points)", height=300, template="plotly_dark")
        st.plotly_chart(fig2, use_container_width=True, config=config, key="f2")

        # Plot 3: Stats Corridor
        fig3 = go.Figure()
        if s90: fig3.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
        if s50: fig3.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        if s10: fig3.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
        fig3.update_layout(title="Volatility Corridor", height=250, template="plotly_dark")
        st.plotly_chart(fig3, use_container_width=True, config=config, key="f3")

        # Plot 4: Median Distribution (REQUESTED)
        fig4 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=50, marker_color='#00D4FF', opacity=0.7))
        fig4.update_layout(title="Median Spread Distribution (Frequency)", height=250, template="plotly_dark", bargap=0.1)
        st.plotly_chart(fig4, use_container_width=True, config=config, key="f4")

    except Exception as e:
        st.error(f"UI Error: {e}")

# ==============================================================================
# 4. MAIN TERMINAL
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb", layout="wide")
    init_worker()

    # SIDEBAR DIAGNOSTICS
    st.sidebar.title("Terminal Config")
    
    with st.sidebar.expander("API Health Monitor", expanded=True):
        if st.session_state.api_health:
            for api, status in st.session_state.api_health.items():
                st.write(f"{status} {api}")
        else:
            st.write("⌛ Starting collector...")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    benchmark = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.divider()
    lookback = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_m = st.sidebar.slider("Stats Window (Mins)", 1, 120, 30)
    
    s90 = st.sidebar.checkbox("90th Percentile", True)
    s50 = st.sidebar.checkbox("Median", True)
    s10 = st.sidebar.checkbox("10th Percentile", True)

    # RE-RENDER THE FRAGMENT
    render_live_ui(coin, benchmark, lookback, roll_m, s90, s50, s10)

if __name__ == "__main__":
    main()
