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
# 3. FRAGMENT (The non-blinking logic)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_live_content(coin, ex, hist_m, roll_m, show_90, show_50, show_10):
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(path):
        st.info("Initializing historical data...")
        return

    df = pd.read_csv(path)
    if df.empty: return
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.set_index('timestamp').sort_index()
    df = df[~df.index.duplicated(keep='last')]

    for col in ['lighter','paradex','bybit','binance']:
        df[col] = pd.to_numeric(df[col], errors='coerce').ffill()
    
    tgt = ex.lower()
    df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
    
    last_entry = df.index[-1]
    view = df[df.index >= (last_entry - timedelta(minutes=hist_m))].copy()
    if view.empty: return

    # Calculate percentiles
    view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
    view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
    view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

    # UI Settings to reduce "Blink": Disable Plotly modebar and use manual margins
    chart_config = {'displayModeBar': False, 'staticPlot': False}

    # 1. Price Overlay
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
    fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
    fig_p.update_layout(title=f"{coin} Price Overlay", height=300, template="plotly_dark", margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_p, use_container_width=True, config=chart_config, key="p_live")

    # 2. Spread Plot
    fig_s = go.Figure()
    fig_s.add_trace(go.Scatter(x=view.index, y=view['spread'], name="Spread (bps)", line=dict(color='#FF4B4B', width=2)))
    fig_s.update_layout(title="Arbitrage Spread (Basis Points)", height=350, template="plotly_dark", margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_s, use_container_width=True, config=chart_config, key="s_live")

    # 2.5 Histogram of Raw Spread
    fig_h1 = go.Figure()
    fig_h1.add_trace(go.Histogram(x=view['spread'].dropna(), nbinsx=60, marker_color='#FF4B4B', opacity=0.7))
    fig_h1.update_layout(title="Current Spread Distribution", height=250, template="plotly_dark", bargap=0.1, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_h1, use_container_width=True, config=chart_config, key="h1_live")

    # 3. Statistical Corridor
    fig_stat = go.Figure()
    if show_90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
    if show_50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
    if show_10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
    fig_stat.update_layout(title="Historical Corridor", height=250, template="plotly_dark", margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_stat, use_container_width=True, config=chart_config, key="stat_live")

    # 4. NEW: Histogram of Median (Q50 Distribution)
    fig_h2 = go.Figure()
    fig_h2.add_trace(go.Histogram(x=view['q50'].dropna(), nbinsx=60, marker_color='#00D4FF', opacity=0.7))
    fig_h2.update_layout(title=f"Rolling Median ({roll_m}m) Distribution", height=250, template="plotly_dark", bargap=0.1, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_h2, use_container_width=True, config=chart_config, key="h2_live")

# ==============================================================================
# 4. MAIN ENTRY
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Terminal", layout="wide")
    start_collector()

    # SIDEBAR: Changes here trigger a full script rerun to update the Fragment's arguments
    st.sidebar.title("Configuration")
    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    hist_m = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_m = st.sidebar.slider("Stats Period (Mins)", 1, 1440, 30)
    
    st.sidebar.subheader("Percentiles")
    s90 = st.sidebar.checkbox("90th", True)
    s50 = st.sidebar.checkbox("50th", True)
    s10 = st.sidebar.checkbox("10th", True)

    # Everything inside this function will refresh every 2s without rerunning the sidebar
    render_live_content(coin, ex, hist_m, roll_m, s90, s50, s10)

if __name__ == "__main__":
    main()
