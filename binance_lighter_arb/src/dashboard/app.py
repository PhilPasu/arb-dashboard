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
# Setup & Directories
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_MAINNET_API = "https://mainnet.zklighter.elliot.ai"
LIGHTER_API_VERSION = "/api/v1"
PARADEX_MAINNET_API = "https://api.prod.paradex.trade/v1"
BYBIT_MAINNET_API = "https://api.bybit.com"

# ==============================================================================
# 1. Background Data Collector
# ==============================================================================
async def fetch_json(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=5) as resp:
            if resp.status == 200: return await resp.json()
    except: pass
    return None

async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # Lighter (BTC Market ID: 4, ETH Market ID: 2048)
        try:
            m_id = 4 if coin == "BTC" else 2048
            url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
            data = await fetch_json(session, url, {'market_id': m_id, 'limit': 1})
            if data and data.get('asks') and data.get('bids'):
                def parse(x): return float(x[0]) if isinstance(x, list) else float(x.get('price', 0))
                ask, bid = parse(data['asks'][0]), parse(data['bids'][0])
                if ask > 0 and bid > 0: prices['lighter'] = (ask + bid) / 2
        except: pass

        # External Markets
        try:
            p_data = await fetch_json(session, f"{PARADEX_MAINNET_API}/markets/summary", {'market': f"{coin}-USD-PERP"})
            if p_data: prices['paradex'] = float(p_data['results'][0]['last_traded_price'])
            
            b_data = await fetch_json(session, f"{BYBIT_MAINNET_API}/v5/market/tickers", {'category': 'linear', 'symbol': f"{coin}USDT"})
            if b_data: prices['bybit'] = float(b_data['result']['list'][0]['lastPrice'])
            
            bin_data = await fetch_json(session, "https://fapi.binance.com/fapi/v1/ticker/price", {'symbol': f"{coin}USDT"})
            if bin_data: prices['binance'] = float(bin_data['price'])
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
# 2. Main Dashboard App
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter 7-Day Monitor", layout="wide")
    start_collector()

    # --- SIDEBAR ---
    st.sidebar.title("Config")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    # 7-day View Window
    hist_m = st.sidebar.slider("View Window (Mins)", 5, 10080, 1440)
    roll_m = st.sidebar.slider("Stats Window (Mins)", 1, 1440, 30)
    
    st.sidebar.divider()
    st.sidebar.subheader("Percentile Plot Toggles")
    s90 = st.sidebar.checkbox("90th Percentile", True)
    s50 = st.sidebar.checkbox("Median", True)
    s10 = st.sidebar.checkbox("10th Percentile", True)

    # --- DATA PROCESSING ---
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path): return st.info("Initializing...")
    
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    for col in ['lighter','paradex','bybit','binance']:
        df[col] = pd.to_numeric(df[col], errors='coerce').ffill()
    
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    df['q90'] = df['spread'].rolling(f"{roll_m}min").quantile(0.90)
    df['q50'] = df['spread'].rolling(f"{roll_m}min").quantile(0.50)
    df['q10'] = df['spread'].rolling(f"{roll_m}min").quantile(0.10)
    
    view = df[df.index >= (datetime.now() - timedelta(minutes=hist_m))].copy()

    if not view.empty:
        curr = view.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("Lighter", f"${curr['lighter']:,.2f}")
        c2.metric(ex, f"${curr[tgt]:,.2f}")
        c3.metric("Spread", f"{curr['spread']:.2f} bps")

        # 1. Price Overlay
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA', width=2)))
        fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF', width=2)))
        fig_p.update_layout(title=f"{coin} Price Action", height=350, template="plotly_dark")
        st.plotly_chart(fig_p, use_container_width=True)

        # 2. Spread ONLY Plot (Solid White Line)
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=view.index, y=view['spread'], 
            name="Spread (bps)", 
            line=dict(color='#FFFFFF', width=2.5) # Solid Bright White
        ))
        fig_s.update_layout(title="Arbitrage Spread (Basis Points)", height=350, template="plotly_dark")
        st.plotly_chart(fig_s, use_container_width=True)

        # 3. Percentiles ONLY Plot
        fig_stat = go.Figure()
        if s90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B')))
        if s50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        if s10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700')))
        fig_stat.add_hline(y=0, line_width=1, line_color="#FFFFFF", opacity=0.3)
        fig_stat.update_layout(title="Statistical Corridor", height=250, template="plotly_dark")
        st.plotly_chart(fig_stat, use_container_width=True)

    time.sleep(2); st.rerun()

if __name__ == "__main__": main()
