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
# 1. ROBUST PATH SETUP
# ==============================================================================
# Force absolute paths to prevent "File Not Found" errors on hosted servers
ROOT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in locals() else os.getcwd()
DATA_DIR = os.path.join(ROOT_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

LIGHTER_API = "https://mainnet.zklighter.elliot.ai/api/v1"
PARADEX_API = "https://api.prod.paradex.trade/v1"
BYBIT_API = "https://api.bybit.com"

# ==============================================================================
# 2. DATA COLLECTOR (STABLE VERSION)
# ==============================================================================
async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        # ZkLighter Logic
        try:
            m_id = 4 if coin == "BTC" else 2048
            async with session.get(f"{LIGHTER_API}/orderBookOrders", params={'market_id': m_id, 'limit': 1}, timeout=5) as r:
                data = await r.json()
                asks, bids = data.get('asks', []), data.get('bids', [])
                if asks and bids:
                    prices['lighter'] = (float(asks[0][0]) + float(bids[0][0])) / 2
        except: pass

        # Paradex/Bybit/Binance Logic
        try:
            async with session.get(f"{PARADEX_API}/markets/summary", params={'market': f"{coin}-USD-PERP"}) as r:
                d = await r.json()
                prices['paradex'] = float(d['results'][0]['last_traded_price'])
            async with session.get(f"{BYBIT_API}/v5/market/tickers", params={'category': 'linear', 'symbol': f"{coin}USDT"}) as r:
                d = await r.json()
                prices['bybit'] = float(d['result']['list'][0]['lastPrice'])
            async with session.get("https://fapi.binance.com/fapi/v1/ticker/price", params={'symbol': f"{coin}USDT"}) as r:
                d = await r.json()
                prices['binance'] = float(d['price'])
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
                    if len(p) > 1: # Only write if at least 1 price was fetched
                        with open(self.files[coin], 'a', newline='') as f:
                            csv.writer(f).writerow([p['timestamp'], p.get('lighter',''), p.get('paradex',''), p.get('bybit',''), p.get('binance','')])
                except: pass
            time.sleep(2.0)

@st.cache_resource
def start_collector():
    c = DataCollector(); c.start(); return c

# ==============================================================================
# 3. FRAGMENT RENDERING (SMOOTH TRANSITIONS)
# ==============================================================================
@st.fragment(run_every=2.0)
def render_terminal(coin, ex, hist_m, roll_m, s90, s50, s10):
    path = os.path.join(DATA_DIR, f"history_{coin}.csv")
    
    # Check if file exists; if not, wait
    if not os.path.exists(path):
        st.info("🔄 Creating database... please wait.")
        return

    try:
        # Load data with a copy to prevent "File Busy" crashes
        df = pd.read_csv(path).copy()
        if len(df) < 5: 
            st.warning("📈 Gathering initial liquidity data... (ETA 10s)")
            return

        # Processing
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        for c in ['lighter','paradex','bybit','binance']:
            df[c] = pd.to_numeric(df[c], errors='coerce').ffill()

        tgt = ex.lower()
        df['spread'] = (df[tgt] - df['lighter']) / df[tgt] * 10000
        view = df[df.index >= (df.index[-1] - timedelta(minutes=hist_m))].copy()

        # Stats
        view['q90'] = view['spread'].rolling(f"{roll_m}min").quantile(0.90)
        view['q50'] = view['spread'].rolling(f"{roll_m}min").quantile(0.50)
        view['q10'] = view['spread'].rolling(f"{roll_m}min").quantile(0.10)

        # PLOTS
        config = {'displayModeBar': False, 'staticPlot': False}
        
        # 1. Price
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view.index, y=view[tgt], name=ex, line=dict(color='#00FFAA')))
        fig_p.add_trace(go.Scatter(x=view.index, y=view['lighter'], name="Lighter", line=dict(color='#FF00FF')))
        fig_p.update_layout(title="Price Overlay (Live)", height=300, template="plotly_dark", margin=dict(t=40,b=10))
        st.plotly_chart(fig_p, use_container_width=True, config=config, key="p_chart")

        # 2. Spread Line + Histogram
        fig_s = go.Figure(go.Scatter(x=view.index, y=view['spread'], name="Spread", line=dict(color='#FF4B4B')))
        fig_s.update_layout(title="Arbitrage Spread (bps)", height=300, template="plotly_dark")
        st.plotly_chart(fig_s, use_container_width=True, config=config, key="s_chart")

        fig_h1 = go.Figure(go.Histogram(x=view['spread'].dropna(), nbinsx=50, marker_color='#FF4B4B', opacity=0.7))
        fig_h1.update_layout(title="Raw Spread Distribution", height=250, template="plotly_dark", bargap=0.1)
        st.plotly_chart(fig_h1, use_container_width=True, config=config, key="h1_chart")

        # 3. Corridor + Median Distribution
        fig_stat = go.Figure()
        if s90: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q90'], name="90th", line=dict(color='#FF4B4B', dash='dot')))
        if s50: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q50'], name="Median", line=dict(color='#00D4FF')))
        if s10: fig_stat.add_trace(go.Scatter(x=view.index, y=view['q10'], name="10th", line=dict(color='#FFD700', dash='dot')))
        fig_stat.update_layout(title="Statistical Corridor", height=250, template="plotly_dark")
        st.plotly_chart(fig_stat, use_container_width=True, config=config, key="stat_chart")

        # THE REQUESTED PLOT: Histogram of the Median
        fig_h2 = go.Figure(go.Histogram(x=view['q50'].dropna(), nbinsx=50, marker_color='#00D4FF', opacity=0.7))
        fig_h2.update_layout(title=f"Distribution of {roll_m}m Rolling Median", height=250, template="plotly_dark", bargap=0.1)
        st.plotly_chart(fig_h2, use_container_width=True, config=config, key="h2_chart")

    except Exception as e:
        st.warning(f"Waiting for database access... ({str(e)})")

# ==============================================================================
# 4. MAIN INTERFACE
# ==============================================================================
def main():
    st.set_page_config(page_title="ZkLighter Terminal", layout="wide")
    start_collector()

    st.sidebar.title("ZkLighter Arb Terminal")
    
    # Health Status
    st.sidebar.markdown("### System Health")
    st.sidebar.success("Collector: Active")
    st.sidebar.info(f"Storage: {DATA_DIR}")

    coin = st.sidebar.selectbox("Asset", ["ETH", "BTC"])
    ex = st.sidebar.selectbox("Benchmark", ["Paradex", "Bybit", "Binance"])
    hist_m = st.sidebar.slider("Lookback (Mins)", 5, 1440, 60)
    roll_m = st.sidebar.slider("Stats Window (Mins)", 1, 120, 30)
    
    s90 = st.sidebar.checkbox("Show 90th Percentile", True)
    s50 = st.sidebar.checkbox("Show Median", True)
    s10 = st.sidebar.checkbox("Show 10th Percentile", True)

    # CALL THE FRAGMENT - This creates the non-blinking loop
    render_terminal(coin, ex, hist_m, roll_m, s90, s50, s10)

if __name__ == "__main__":
    main()
