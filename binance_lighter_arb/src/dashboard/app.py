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
# 1. SETUP & DIRECTORIES
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1"
PARADEX_API = "https://api.prod.paradex.trade/v1"
BYBIT_API = "https://api.bybit.com"

# ==============================================================================
# 2. DATA COLLECTOR (MARKET ID 4: BTC, 2048: ETH)
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # Lighter Orderbook Price
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}, timeout=5) as r:
                data = await r.json()
                # Parse asks/bids safely
                def get_p(x): return float(x[0]) if isinstance(x, list) else float(x.get('price', 0))
                ask, bid = get_p(data['asks'][0]), get_p(data['bids'][0])
                if ask > 0 and bid > 0: prices['lighter'] = (ask + bid) / 2
        except: pass

        # External Markets
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
# 3. DASHBOARD MAIN APP
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter 7-Day Terminal", layout="wide")
    start_collector()

    # --- Sidebar Settings ---
    st.sidebar.title("Terminal Config")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    # Time Sliders (7 Days = 10,080 minutes)
    hist_m = st.sidebar.slider("Lookback Window (Minutes)", 5, 10080, 1440)
    roll_m = st.sidebar.slider("Percentile Smoothing (Minutes)", 1, 1440, 30)

    # --- Data Processing ---
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path): return st.info("Starting Data Collector...")
    
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    # CRITICAL FIX: Ensure index is monotonic for rolling calculations
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='last')] # Remove accidental duplicates

    # Fill Price Gaps
    for col in ['lighter','paradex','bybit','binance']:
        df[col] = pd.to_numeric(df[col], errors='coerce').ffill()
    
    # Math Calculations
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    
    # Rolling calculations (Monotonic index required here)
    df['q90'] = df['spread'].rolling(f"{roll_m}min").quantile(0.90)
    df['q50'] = df['spread'].rolling(f"{roll_m}min").quantile(0.50)
    df['q10'] = df['spread'].rolling(f"{roll_m}min").quantile(0.10)
    
    # Filter for the view window
    view = df[df.index >= (datetime.now() - timedelta(minutes=hist_m))].copy()

    if not view.empty:
        curr = view.iloc[-1]
        
        # Header Metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Lighter Price", f"${curr['lighter']:,.2f}")
        col2.metric(f"{ex} Price", f"${curr[tgt]:,.2f}")
        col3.metric("Live Spread", f"{curr['spread']:.2f} bps")

        # 1. Price Plot
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
        fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
        fig_p.update_layout(title=f"{coin} Price Overlay", height=300, template="plotly_dark")
        st.plotly_chart(fig_p, use_container_width=True)

        # 2. Spread Plot (Clean White Line ONLY)
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=view.index, 
            y=view['spread'].ffill(), # Fill NaNs to prevent line breaking
            name="Spread (bps)", 
            mode='lines',
            line=dict(color='#FFFFFF', width=2.5), # SOLID WHITE
            connectgaps=True # Bridge any missing API data
        ))
        fig_s.update_layout(title="Arbitrage Spread (Basis Points)", height=350, template="plotly_dark")
        st.plotly_chart(fig_s, use_container_width=True)

        # 3. Percentiles Plot (Separated)
        fig_stat = go.Figure()
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th Pctl", line=dict(color='#FF4B4B', dash='dot')))
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th Pctl", line=dict(color='#FFD700', dash='dot')))
        fig_stat.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.3)
        fig_stat.update_layout(title="Historical Statistical Bands", height=250, template="plotly_dark")
        st.plotly_chart(fig_stat, use_container_width=True)

    # 2-second refresh
    time.sleep(2)
    st.rerun()

if __name__ == "__main__":
    main()
