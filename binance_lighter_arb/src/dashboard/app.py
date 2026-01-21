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
# 1. PATH SETUP (Safe for Cloud & Local)
# ==============================================================================
# Use a local folder but don't force absolute paths which can fail on some servers
DATA_FILE_DIR = "data"
if not os.path.exists(DATA_FILE_DIR):
    os.makedirs(DATA_FILE_DIR, exist_ok=True)

# ==============================================================================
# 2. DATA COLLECTOR (Optimized for Speed)
# ==============================================================================
async def fetch_api_prices(coin="ETH"):
    prices = {'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        # Lighter
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id={m_id}&limit=1", timeout=3) as r:
                data = await r.json()
                if data.get('asks') and data.get('bids'):
                    prices['lighter'] = (float(data['asks'][0][0]) + float(data['bids'][0][0])) / 2
        except: pass

        # Paradex/Bybit/Binance
        try:
            async with session.get(f"https://api.prod.paradex.trade/v1/markets/summary?market={coin}-USD-PERP") as r:
                prices['paradex'] = float((await r.json())['results'][0]['last_traded_price'])
            async with session.get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={coin}USDT") as r:
                prices['bybit'] = float((await r.json())['result']['list'][0]['lastPrice'])
            async with session.get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={coin}USDT") as r:
                prices['binance'] = float((await r.json())['price'])
        except: pass
    return prices

class BackgroundCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.coins = ["ETH", "BTC"]
        
    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_api_prices(coin))
                    path = os.path.join(DATA_FILE_DIR, f"live_{coin}.csv")
                    # Create file if missing
                    if not os.path.exists(path):
                        with open(path, 'w', newline='') as f:
                            csv.writer(f).writerow(['timestamp','lighter','paradex','bybit','binance'])
                    # Append data
                    if len(p) > 1:
                        with open(path, 'a', newline='') as f:
                            csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(1.5)

@st.cache_resource
def start_worker():
    worker = BackgroundCollector()
    worker.start()
    return worker

# ==============================================================================
# 3. FRAGMENT (The non-blinking UI)
# ==============================================================================
@st.fragment(run_every=2.0)
def live_dashboard_fragment(coin, ex, lookback, roll_window, s90, s50, s10):
    path = os.path.join(DATA_FILE_DIR, f"live_{coin}.csv")
    
    if not os.path.exists(path):
        st.warning("Waiting for first data point from API...")
        return

    try:
        df = pd.read_csv(path)
        if len(df) < 2:
            st.info("Collecting data... please wait 5 seconds.")
            return

        # CLEANING
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        for col in ['lighter','paradex','bybit','binance']:
            df[col] = pd.to_numeric(df[col], errors='coerce').ffill()

        # CALC SPREAD (BPS)
        tgt = ex.lower()
        df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
        
        # WINDOWING
        view = df[df.index >= (df.index[-1] - timedelta(minutes=lookback))].copy()
        view['q90'] = view['spread'].rolling(f"{roll_window}min").quantile(0.90)
        view['q50'] = view['spread'].rolling(f"{roll_window}min").quantile(0.50)
        view['q10'] = view['spread'].rolling(f"{roll_window}min").quantile(0.10)

        # ---------------------------------------------------------
        # CHARTS (No-Blink Config)
        # ---------------------------------------------------------
        p_cfg = {'displayModeBar': False}

        # 1. Price
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
        fig_price.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
        fig_price.update_layout(title="Price Overlay", height=300, template="plotly_dark", margin=dict(t=30,b=10))
        st.plotly_chart(fig_price, use_container_width=True, config=p_cfg, key="p_c")

        # 2. Spread Line
        fig_spread = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B', width=2)))
        fig_spread.update_layout(title="Arb Spread (Basis Points)", height=300, template="plotly_dark")
        st.plotly_chart(fig_spread, use_container_width=True, config=p_cfg, key="s_c")

        # 3. Spread Histogram
        fig_h1 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=50, marker_color='#FF4B4B', opacity=0.7))
        fig_h1.update_layout(title="Spread Probability Density", height=250, template="plotly_dark", bargap=0.05)
        st.plotly_chart(fig_h1, use_container_width=True, config=p_cfg, key="h1_c")

        # 4. Corridor
        fig_corr = go.Figure()
        if s90: fig_corr.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
        if s50: fig_corr.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        if s10: fig_corr.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
        fig_corr.update_layout(title="Volatility Corridor", height=250, template="plotly_dark")
        st.plotly_chart(fig_corr, use_container_width=True, config=p_cfg, key="c_c")

        # 5. MEDIAN HISTOGRAM (REQUESTED)
        fig_h2 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=50, marker_color='#00D4FF', opacity=0.7))
        fig_h2.update_layout(title=f"Distribution of Rolling Median ({roll_window}m)", height=250, template="plotly_dark", bargap=0.05)
        st.plotly_chart(fig_h2, use_container_width=True, config=p_cfg, key="h2_c")

    except Exception:
        st.info("Syncing data file...")

# ==============================================================================
# 4. MAIN LAYOUT
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Terminal", layout="wide", initial_sidebar_state="expanded")
    start_worker()

    # SIDEBAR
    st.sidebar.title("⚡ Arb Terminal")
    
    with st.sidebar.expander("System Status", expanded=True):
        st.write("Collector: 🟢 Running")
        st.write(f"Last UI Sync: {datetime.now().strftime('%H:%M:%S')}")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.divider()
    lookback = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_window = st.sidebar.slider("Stats Window (Mins)", 1, 120, 30)
    
    st.sidebar.subheader("Visibility")
    s90 = st.sidebar.checkbox("90th Percentile", True)
    s50 = st.sidebar.checkbox("Median", True)
    s10 = st.sidebar.checkbox("10th Percentile", True)

    # RENDER FRAGMENT
    live_dashboard_fragment(coin, ex, lookback, roll_window, s90, s50, s10)

if __name__ == "__main__":
    main()
