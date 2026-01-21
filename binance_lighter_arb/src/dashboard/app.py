import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import aiohttp
import asyncio
import threading
import time
import os
import csv
from datetime import datetime

# ==============================================================================
# Configuration & Constants
# ==============================================================================
DATA_DIR = os.path.join(os.getcwd(), "data", "dashboard")
os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_MAINNET_API = "https://mainnet.zklighter.elliot.ai"
LIGHTER_API_VERSION = "/api/v1"
PARADEX_MAINNET_API = "https://api.prod.paradex.trade/v1"
BYBIT_MAINNET_API = "https://api.bybit.com"

# ==============================================================================
# 1. Robust Data Fetcher
# ==============================================================================
async def fetch_json(session, url, params=None):
    try:
        headers = {"User-Agent": "ArbDashboard/1.3"}
        async with session.get(url, params=params, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                return await resp.json()
    except: pass
    return None

async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # Lighter (BTC: 4, ETH: 2048)
        try:
            m_id = 4 if coin == "BTC" else 2048
            url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
            data = await fetch_json(session, url, {'market_id': m_id, 'limit': 1})
            if data and data.get('asks') and data.get('bids'):
                def parse(x): return float(x[0]) if isinstance(x, list) else float(x.get('price', 0))
                ask, bid = parse(data['asks'][0]), parse(data['bids'][0])
                if ask > 0 and bid > 0: prices['lighter'] = (ask + bid) / 2
        except: pass

        # Others
        try:
            p_data = await fetch_json(session, f"{PARADEX_MAINNET_API}/markets/summary", {'market': f"{coin}-USD-PERP"})
            if p_data: prices['paradex'] = float(p_data['results'][0]['last_traded_price'])
            
            b_data = await fetch_json(session, f"{BYBIT_MAINNET_API}/v5/market/tickers", {'category': 'linear', 'symbol': f"{coin}USDT"})
            if b_data: prices['bybit'] = float(b_data['result']['list'][0]['lastPrice'])
            
            bin_data = await fetch_json(session, "https://fapi.binance.com/fapi/v1/ticker/price", {'symbol': f"{coin}USDT"})
            if bin_data: prices['binance'] = float(bin_data['price'])
        except: pass
    return prices

# ==============================================================================
# 2. Collector Thread
# ==============================================================================
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
            time.sleep(1.5)

@st.cache_resource
def start_collector():
    c = DataCollector(); c.start(); return c

# ==============================================================================
# 3. Streamlit Dashboard
# ==============================================================================
def main():
    st.set_page_config(page_title="Arb Pro", layout="wide")
    start_collector()

    # --- SIDEBAR ---
    st.sidebar.header("Settings")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Exchange", ["Paradex", "Bybit", "Binance"])
    hist_m = st.sidebar.slider("View (Min)", 5, 120, 30)
    roll_m = st.sidebar.slider("Stats (Min)", 1, 120, 15)
    
    st.sidebar.subheader("Percentile Toggles")
    s90 = st.sidebar.checkbox("Show 90th", True)
    s50 = st.sidebar.checkbox("Show 50th", True)
    s10 = st.sidebar.checkbox("Show 10th", True)

    # --- DATA ---
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path): return st.info("Loading...")
    
    df = pd.read_csv(path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    for col in ['lighter','paradex','bybit','binance']: df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # Calc Stats
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    df['q90'] = df['spread'].rolling(f"{roll_m}min").quantile(0.90)
    df['q50'] = df['spread'].rolling(f"{roll_m}min").quantile(0.50)
    df['q10'] = df['spread'].rolling(f"{roll_m}min").quantile(0.10)
    
    view = df[df.index >= (datetime.now() - pd.Timedelta(minutes=hist_m))].copy()

    if not view.empty:
        # Metrics
        curr = view.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric(f"Lighter {coin}", f"${curr['lighter']:,.2f}")
        c2.metric(f"{ex} {coin}", f"${curr[tgt]:,.2f}")
        c3.metric("Live Spread", f"{curr['spread']:.2f} bps")

        # 1. RESTORED PRICE PLOT
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#A020F0')))
        fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#00BFFF')))
        fig_p.update_layout(title=f"{coin} Price Action", height=300, template="plotly_dark")
        st.plotly_chart(fig_p, use_container_width=True)

        # 2. SPREAD PLOT
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='white')))
        if s90: fig_s.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(dash='dot', color='red')))
        if s50: fig_s.add_trace(go.Scatter(x=view_df.index if 'view_df' in locals() else view.index, y=view['q50'], name="Median", line=dict(dash='dash', color='cyan')))
        if s10: fig_s.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(dash='dot', color='orange')))
        fig_s.update_layout(title="Spread with Overlay Bands", height=300, template="plotly_dark")
        st.plotly_chart(fig_s, use_container_width=True)

        # 3. PURE STATS PLOT
        fig_stat = go.Figure()
        if s90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90%", line=dict(color='red')))
        if s50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="50%", line=dict(color='cyan')))
        if s10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10%", line=dict(color='orange')))
        fig_stat.update_layout(title=f"Rolling {roll_m}m Statistical Distribution", height=250, template="plotly_dark")
        st.plotly_chart(fig_stat, use_container_width=True)

    time.sleep(1); st.rerun()

if __name__ == "__main__": main()
