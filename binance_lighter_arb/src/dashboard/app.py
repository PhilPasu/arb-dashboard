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

# ==============================================================================
# 2. DATA COLLECTOR
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}, timeout=5) as r:
                data = await r.json()
                def get_p(x): return float(x[0]) if isinstance(x, list) else float(x.get('price', 0))
                ask, bid = get_p(data['asks'][0]), get_p(data['bids'][0])
                if ask > 0 and bid > 0: prices['lighter'] = (ask + bid) / 2
        except: pass

        try:
            async with session.get(f"{PARADEX_API}/markets/summary", params={'market': f"{coin}-USD-PERP"}) as r:
                p_data = await r.json()
                prices['paradex'] = float(p_data['results'][0]['last_traded_price'])
            
            async with session.get(f"{BYBIT_API}/v5/market/tickers", params={'category': 'linear', 'symbol': f"{coin}USDT"}) as r:
                b_data = await r.json()
                prices['bybit'] = float(b_data['result']['list'][0]['lastPrice'])

            async with session.get("https://fapi.binance.com/fapi/v1/ticker/price", params={'symbol': f"{coin}USDT"}) as r:
                bin_data = await r.json()
                prices['binance'] = float(bin_data['price'])
        except: pass
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
                    with open(self.files[coin], 'a', newline='') as f:
                        csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(2.0)

@st.cache_resource
def start_collector():
    c = DataCollector(); c.start(); return c

# ==============================================================================
# 3. MAIN DASHBOARD
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb Terminal", layout="wide")
    start_collector()

    # --- SIDEBAR CONFIG (Triggers script rerun when changed) ---
    st.sidebar.title("Terminal Config")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.subheader("Time Window")
    hist_m = st.sidebar.slider("Lookback (Minutes)", 5, 10080, 1440)
    roll_m = st.sidebar.slider("Stats Period (Minutes)", 1, 1440, 30)

    st.sidebar.subheader("Display Options")
    show_90 = st.sidebar.checkbox("Show 90th Percentile", value=True)
    show_50 = st.sidebar.checkbox("Show Median (50th)", value=True)
    show_10 = st.sidebar.checkbox("Show 10th Percentile", value=True)

    # --- UI CONTAINERS (Created once, updated repeatedly) ---
    price_container = st.empty()
    spread_container = st.empty()
    hist_container = st.empty()
    stat_container = st.empty()

    path = os.path.join(DATA_DIR, f"history_{coin}.csv")

    # --- LIVE SMOOTH UPDATE LOOP ---
    while True:
        try:
            if not os.path.exists(path):
                st.info("Waiting for data collection...")
                time.sleep(2)
                continue

            # 1. LOAD DATA
            df = pd.read_csv(path)
            if df.empty or len(df) < 2:
                time.sleep(2)
                continue

            # 2. PROCESS DATA
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # FIXED: Removed 'inplace=True' chaining that caused the AttributeError
            df = df.set_index('timestamp').sort_index()
            df = df[~df.index.duplicated(keep='last')]

            for col in ['lighter','paradex','bybit','binance']:
                df[col] = pd.to_numeric(df[col], errors='coerce').ffill()
            
            tgt = ex.lower()
            # Basis points calculation
            df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
            
            # Apply Lookback Window
            last_ts = df.index[-1]
            view = df[df.index >= (last_ts - timedelta(minutes=hist_m))].copy()
            
            if view.empty:
                time.sleep(1)
                continue

            # 3. STATS CALCULATION
            view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
            view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
            view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

            # --- PLOTTING ---

            # Chart 1: Price Overlay
            fig_p = go.Figure()
            fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA', width=1.5)))
            fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF', width=1.5)))
            fig_p.update_layout(title=f"{coin} Price Overlay (Live)", height=300, margin=dict(t=40, b=10), template="plotly_dark")
            price_container.plotly_chart(fig_p, use_container_width=True)

            # Chart 2: Spread Line
            fig_s = go.Figure()
            fig_s.add_trace(go.Scatter(x=view.index, y=view['spread'].ffill(), name="Spread", line=dict(color='#FF4B4B', width=2)))
            fig_s.update_layout(title="Arbitrage Spread (BPS)", height=350, margin=dict(t=40, b=10), template="plotly_dark")
            spread_container.plotly_chart(fig_s, use_container_width=True)

            # Chart 3: Histogram (Distribution)
            fig_h = go.Figure()
            fig_h.add_trace(go.Histogram(
                x=view['spread'].dropna(), 
                nbinsx=100, 
                marker_color='#FF4B4B', 
                opacity=0.6,
                name="Frequency"
            ))
            # Current value marker in histogram
            cur_val = view['spread'].iloc[-1]
            fig_h.add_vline(x=cur_val, line_width=3, line_dash="dash", line_color="white", 
                             annotation_text=f"Now: {cur_val:.2f}", annotation_position="top right")
            fig_h.update_layout(title=f"Spread Distribution ({hist_m}m window)", height=300, bargap=0.1, template="plotly_dark")
            hist_container.plotly_chart(fig_h, use_container_width=True)

            # Chart 4: Statistical Corridor
            fig_stat = go.Figure()
            if show_90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
            if show_50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
            if show_10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
            fig_stat.update_layout(title="Historical corridor", height=250, margin=dict(t=40, b=10), template="plotly_dark")
            stat_container.plotly_chart(fig_stat, use_container_width=True)

            # Sleep for update frequency (2 seconds)
            time.sleep(2)

        except Exception as e:
            # Prevent loop from dying on temporary file access errors
            st.error(f"Loop Update Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
