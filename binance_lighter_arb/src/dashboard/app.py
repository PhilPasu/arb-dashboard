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
# 1. Async Data Fetchers
# ==============================================================================
async def fetch_json(session, url, params=None):
    try:
        headers = {"User-Agent": "ArbDashboard/1.0"}
        async with session.get(url, params=params, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None

async def fetch_prices(coin="ETH"):
    prices = {'timestamp': datetime.now()}
    async with aiohttp.ClientSession() as session:
        # 1. Lighter Mainnet (Updated Market IDs)
        try:
            # BTC is usually 4, ETH is 2048
            market_id = 2048 if coin == "ETH" else (4 if coin == "BTC" else None) 
            
            if market_id:
                url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
                params = {'market_id': market_id, 'limit': 1}
                data = await fetch_json(session, url, params)
                if data and data.get('bids') and data.get('asks'):
                    bid = float(data['bids'][0][0]) if isinstance(data['bids'][0], list) else float(data['bids'][0]['price'])
                    ask = float(data['asks'][0][0]) if isinstance(data['asks'][0], list) else float(data['asks'][0]['price'])
                    prices['lighter'] = (bid + ask) / 2
        except Exception: pass

        # 2. Paradex
        try:
            symbol = f"{coin}-USD-PERP"
            url = f"{PARADEX_MAINNET_API}/markets/summary"
            data = await fetch_json(session, url, {'market': symbol})
            if data and 'results' in data:
                prices['paradex'] = float(data['results'][0]['last_traded_price'])
        except Exception: pass

        # 3. Bybit
        try:
            symbol = f"{coin}USDT"
            url = f"{BYBIT_MAINNET_API}/v5/market/tickers"
            data = await fetch_json(session, url, {'category': 'linear', 'symbol': symbol})
            if data and data['retCode'] == 0:
                prices['bybit'] = float(data['result']['list'][0]['lastPrice'])
        except Exception: pass
            
        # 4. Binance
        try:
             symbol = f"{coin}USDT"
             url = "https://fapi.binance.com/fapi/v1/ticker/price"
             data = await fetch_json(session, url, {'symbol': symbol})
             if data and 'price' in data:
                 prices['binance'] = float(data['price'])
        except Exception: pass

    return prices

# ==============================================================================
# 2. Background Data Collector (Logic remains same, ensures IDs update)
# ==============================================================================
class DataCollector(threading.Thread):
    def __init__(self, interval=2.0):
        super().__init__()
        self.interval = interval
        self.running = True
        self.coins = ["ETH", "BTC"]
        self.filenames = {c: os.path.join(DATA_DIR, f"history_{c}.csv") for c in self.coins}
        
        for c, fname in self.filenames.items():
            if not os.path.exists(fname):
                with open(fname, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'lighter', 'paradex', 'bybit', 'binance'])

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self.running:
            for coin in self.coins:
                try:
                    prices = loop.run_until_complete(fetch_prices(coin))
                    with open(self.filenames[coin], 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([prices['timestamp'], prices.get('lighter', ''), 
                                         prices.get('paradex', ''), prices.get('bybit', ''),
                                         prices.get('binance', '')])
                except Exception: pass
            time.sleep(self.interval)

@st.cache_resource
def start_data_collector():
    collector = DataCollector(interval=1.5)
    collector.setDaemon(True)
    collector.start()
    return collector

# ==============================================================================
# 3. Streamlit App Logic
# ==============================================================================
def load_data(coin, history_minutes, rolling_minutes):
    fname = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(fname): return pd.DataFrame()
    
    try:
        df = pd.read_csv(fname)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        # We need to load enough data to cover BOTH the history view 
        # AND the lookback needed for the first visible rolling calc.
        max_lookback = max(history_minutes, rolling_minutes)
        cutoff = datetime.now() - pd.Timedelta(minutes=max_lookback + 1)
        df = df[df.index >= cutoff]
        
        cols = ['lighter', 'paradex', 'bybit', 'binance']
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors='coerce')
            
        return df
    except Exception: return pd.DataFrame()

def main():
    st.set_page_config(page_title="Arb Dashboard", layout="wide")
    start_data_collector()

    # Sidebar
    st.sidebar.header("Settings")
    selected_coin = st.sidebar.selectbox("Coin", ["ETH", "BTC"])
    target_ex = st.sidebar.selectbox("Target Exchange", ["Paradex", "Bybit", "Binance"])
    
    st.sidebar.subheader("Time Windows")
    history_win = st.sidebar.slider("Chart Display (Min)", 5, 1440, 60)
    rolling_win = st.sidebar.slider("Rolling Stats Period (Min)", 1, 1440, 30)
    
    auto_refresh = st.sidebar.checkbox("Live Update", value=True)

    data = load_data(selected_coin, history_win, rolling_win)
    
    if data.empty or len(data) < 2:
        st.info("Gathering data points...")
    else:
        # Calculate full series first
        s_series = (data[target_ex.lower()] - data['lighter']) / data[target_ex.lower()] * 10000
        
        # Compute Rolling Stats based on rolling_win
        roll_str = f"{rolling_win}min"
        qt_90 = s_series.rolling(roll_str).quantile(0.90)
        qt_50 = s_series.rolling(roll_str).quantile(0.50)
        qt_10 = s_series.rolling(roll_str).quantile(0.10)

        # Slice data for DISPLAY based on history_win
        display_cutoff = datetime.now() - pd.Timedelta(minutes=history_win)
        view_df = data[data.index >= display_cutoff]
        view_s = s_series[s_series.index >= display_cutoff]
        view_q90 = qt_90[qt_90.index >= display_cutoff]
        view_q50 = qt_50[qt_50.index >= display_cutoff]
        view_q10 = qt_10[qt_10.index >= display_cutoff]

        # Metrics
        l = view_df.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric(f"Lighter {selected_coin}", f"${l['lighter']:,.2f}")
        c2.metric(f"{target_ex} {selected_coin}", f"${l[target_ex.lower()]:,.2f}")
        c3.metric("Current Spread (bps)", f"{(view_s.iloc[-1]):.2f}")

        # Chart 1: Prices
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=view_df.index, y=view_df[target_ex.lower()], name=target_ex))
        fig_p.add_trace(go.Scatter(x=view_df.index, y=view_df['lighter'], name="Lighter"))
        fig_p.update_layout(title="Price Action", height=300)
        st.plotly_chart(fig_p, use_container_width=True)

        # Chart 2: Spread & Rolling Bands
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(x=view_s.index, y=view_s, name="Spread", line=dict(color='green')))
        fig_s.add_trace(go.Scatter(x=view_q90.index, y=view_q90, name="90th", line=dict(dash='dot', color='orange')))
        fig_s.add_trace(go.Scatter(x=view_q50.index, y=view_q50, name="Median", line=dict(dash='dash', color='blue')))
        fig_s.add_trace(go.Scatter(x=view_q10.index, y=view_q10, name="10th", line=dict(dash='dot', color='orange')))
        fig_s.update_layout(title=f"Spread (bps) with Rolling {rolling_win}m Percentiles", height=350)
        st.plotly_chart(fig_s, use_container_width=True)

    if auto_refresh:
        time.sleep(1)
        st.rerun()

if __name__ == "__main__":
    main()
