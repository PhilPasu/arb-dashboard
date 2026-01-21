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
# 1. SETUP & CONFIGURATION
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1"
PARADEX_API = "https://api.prod.paradex.trade/v1"
BYBIT_API = "https://api.bybit.com"

# ==============================================================================
# 2. DATA FETCHER (Market ID 4 for BTC, 2048 for ETH)
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # Lighter
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}) as r:
                data = await r.json()
                ask = float(data['asks'][0][0] if isinstance(data['asks'][0], list) else data['asks'][0]['price'])
                bid = float(data['bids'][0][0] if isinstance(data['bids'][0], list) else data['bids'][0]['price'])
                prices['lighter'] = (ask + bid) / 2
        except: pass

        # External Benchmark
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
# 3. DASHBOARD UI
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb Terminal", layout="wide")
    start_collector()

    # --- Sidebar ---
    st.sidebar.header("Monitor Settings")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    
    # 7-day max = 10080 minutes
    hist_m = st.sidebar.slider("Lookback (Minutes)", 5, 10080, 1440)
    roll_m = st.sidebar.slider("Rolling Period (Minutes)", 1, 1440, 30)

    # --- Data Loading ---
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path): return st.info("Collecting data...")
    
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    # Forward-fill prices to prevent NaNs in calculations
    for col in ['lighter','paradex','bybit','binance']:
        df[col] = pd.to_numeric(df[col], errors='coerce').ffill()
    
    # Spread Logic
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    df['q90'] = df['spread'].rolling(f"{roll_m}min").quantile(0.90)
    df['q50'] = df['spread'].rolling(f"{roll_m}min").quantile(0.50)
    df['q10'] = df['spread'].rolling(f"{roll_m}min").quantile(0.10)
    
    # Filter the view
    view = df[df.index >= (datetime.now() - timedelta(minutes=hist_m))].copy()

    if not view.empty:
        # 1. Price Plot
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
        fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
        fig_p.update_layout(title=f"{coin} Prices", height=300, template="plotly_dark")
        st.plotly_chart(fig_p, use_container_width=True)

        # 2. Spread Plot (FIXED: Solid White + Connect Gaps)
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=view.index, 
            y=view['spread'].ffill(), 
            mode='lines',
            name="Spread (bps)", 
            line=dict(color='#FFFFFF', width=2.5), 
            connectgaps=True # This ensures the line doesn't disappear if there's a gap
        ))
        fig_s.update_layout(title="Arb Spread (BPS)", height=350, template="plotly_dark")
        st.plotly_chart(fig_s, use_container_width=True)

        # 3. Statistical Corridor
        fig_stat = go.Figure()
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B')))
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700')))
        fig_stat.update_layout(title="Percentile Bands", height=250, template="plotly_dark")
        st.plotly_chart(fig_stat, use_container_width=True)

    time.sleep(2); st.rerun()

if __name__ == "__main__": main()
