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
# 1. Improved Async Data Fetcher
# ==============================================================================
async def fetch_json(session, url, params=None):
    try:
        headers = {"User-Agent": "ArbDashboard/1.2"}
        async with session.get(url, params=params, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                return await resp.json()
    except: pass
    return None

async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # 1. Lighter Mainnet
        try:
            # BTC-USDC = 4, ETH-USDC = 2048
            m_id = 4 if coin == "BTC" else 2048
            url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
            data = await fetch_json(session, url, {'market_id': m_id, 'limit': 1})
            
            if data and data.get('asks') and data.get('bids'):
                # Extracting price safely from either list or dict format
                def get_p(item):
                    if isinstance(item, list): return float(item[0])
                    return float(item.get('price', 0))
                
                ask = get_p(data['asks'][0])
                bid = get_p(data['bids'][0])
                if ask > 0 and bid > 0:
                    prices['lighter'] = (ask + bid) / 2
        except: pass

        # 2. Paradex
        try:
            data = await fetch_json(session, f"{PARADEX_MAINNET_API}/markets/summary", {'market': f"{coin}-USD-PERP"})
            if data and 'results' in data:
                prices['paradex'] = float(data['results'][0]['last_traded_price'])
        except: pass

        # 3. Bybit (Linear USDT)
        try:
            data = await fetch_json(session, f"{BYBIT_MAINNET_API}/v5/market/tickers", {'category': 'linear', 'symbol': f"{coin}USDT"})
            if data and data['retCode'] == 0:
                prices['bybit'] = float(data['result']['list'][0]['lastPrice'])
        except: pass
            
        # 4. Binance (Futures)
        try:
             data = await fetch_json(session, "https://fapi.binance.com/fapi/v1/ticker/price", {'symbol': f"{coin}USDT"})
             if data and 'price' in data:
                 prices['binance'] = float(data['price'])
        except: pass

    return prices

# ==============================================================================
# 2. Background Data Collector
# ==============================================================================
class DataCollector(threading.Thread):
    def __init__(self, interval=1.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.coins = ["ETH", "BTC"]
        self.filenames = {c: os.path.join(DATA_DIR, f"history_{c}.csv") for c in self.coins}
        
        for c, fname in self.filenames.items():
            if not os.path.exists(fname):
                with open(fname, 'w', newline='') as f:
                    csv.writer(f).writerow(['timestamp', 'lighter', 'paradex', 'bybit', 'binance'])

    def run(self):
        loop = asyncio.new_event_loop()
        while True:
            for coin in self.coins:
                try:
                    p = loop.run_until_complete(fetch_prices(coin))
                    with open(self.filenames[coin], 'a', newline='') as f:
                        csv.writer(f).writerow([
                            p['timestamp'], 
                            p.get('lighter', ''), 
                            p.get('paradex', ''), 
                            p.get('bybit', ''), 
                            p.get('binance', '')
                        ])
                except: pass
            time.sleep(self.interval)

@st.cache_resource
def start_collector():
    collector = DataCollector()
    collector.start()
    return collector

# ==============================================================================
# 3. Streamlit UI
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Arb Dashboard", layout="wide")
    start_collector()

    # --- SIDEBAR CONFIG ---
    st.sidebar.header("Control Panel")
    selected_coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    target_ex = st.sidebar.selectbox("Comparison Exchange", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.divider()
    hist_win = st.sidebar.slider("Chart View (Min)", 5, 240, 30)
    roll_win = st.sidebar.slider("Rolling Window (Min)", 1, 240, 15)
    
    st.sidebar.divider()
    st.sidebar.write("Toggle Percentiles:")
    show_90 = st.sidebar.checkbox("90th (Upper Bound)", value=True)
    show_50 = st.sidebar.checkbox("50th (Median)", value=True)
    show_10 = st.sidebar.checkbox("10th (Lower Bound)", value=True)

    # --- DATA LOADING & PROCESSING ---
    fname = os.path.join(DATA_DIR, f"history_{selected_coin}.csv")
    if not os.path.exists(fname):
        st.info("No data yet. Waiting for collector...")
        return

    df = pd.read_csv(fname)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # FIX: Avoid chaining to prevent AttributeError
    df.set_index('timestamp', inplace=True)
    df.sort_index(inplace=True)
    
    for c in ['lighter', 'paradex', 'bybit', 'binance']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    
    # Calculate Spread
    target_col = target_ex.lower()
    df['spread_bps'] = (df[target_col] - df['lighter']) / df[target_col] * 10000
    
    # Rolling Statistics
    roll_str = f"{roll_win}min"
    df['q90'] = df['spread_bps'].rolling(roll_str).quantile(0.90)
    df['q50'] = df['spread_bps'].rolling(roll_str).quantile(0.50)
    df['q10'] = df['spread_bps'].rolling(roll_str).quantile(0.10)

    # Slicing for Chart Display
    cutoff = datetime.now() - pd.Timedelta(minutes=hist_win)
    view_df = df[df.index >= cutoff].copy()

    if view_df.empty or len(view_df) < 2:
        st.warning("Insufficient data for the selected window. Please wait...")
    else:
        # --- TOP LEVEL METRICS ---
        curr = view_df.iloc[-1]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Lighter", f"${curr['lighter']:,.2f}")
        m2.metric(target_ex, f"${curr[target_col]:,.2f}")
        m3.metric("Spread (bps)", f"{curr['spread_bps']:.2f}")
        m4.metric("Rolling Median", f"{curr['q50']:.2f}" if pd.notnull(curr['q50']) else "N/A")

        # --- PLOT 1: LIVE SPREAD & BANDS ---
        st.subheader(f"Live Spread vs {roll_win}m Bands")
        fig1 = go.Figure()
        fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['spread_bps'], name="Spread", line=dict(color='#00ff00', width=1.5)))
        
        if show_90: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q90'], name="90th", line=dict(dash='dot', color='rgba(255, 100, 100, 0.7)')))
        if show_50: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q50'], name="Median", line=dict(dash='dash', color='rgba(100, 200, 255, 0.7)')))
        if show_10: fig1.add_trace(go.Scatter(x=view_df.index, y=view_df['q10'], name="10th", line=dict(dash='dot', color='rgba(255, 200, 100, 0.7)')))
        
        fig1.update_layout(height=400, template="plotly_dark", margin=dict(t=20, b=20), hovermode="x unified")
        st.plotly_chart(fig1, use_container_width=True)

        # --- PLOT 2: PERCENTILES ONLY ---
        st.subheader("Statistical Analysis (Percentiles Only)")
        fig2 = go.Figure()
        if show_90: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q90'], name="Upper Band", fill=None, line=dict(color='orange')))
        if show_50: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q50'], name="Median / Fair Value", line=dict(color='deepskyblue', width=2)))
        if show_10: fig2.add_trace(go.Scatter(x=view_df.index, y=view_df['q10'], name="Lower Band", fill='tonexty', line=dict(color='gold')))
        
        fig2.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)
        fig2.update_layout(height=300, template="plotly_dark", margin=dict(t=20, b=20), yaxis_title="Basis Points (bps)")
        st.plotly_chart(fig2, use_container_width=True)

    # --- AUTO REFRESH ---
    time.sleep(1)
    st.rerun()

if __name__ == "__main__":
    main()
