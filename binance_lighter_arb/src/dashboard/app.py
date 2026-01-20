
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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        async with session.get(url, params=params, headers=headers, timeout=5) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None

async def fetch_prices(coin="ETH"):
    """
    Fetches prices from all relevant exchanges for the given coin.
    Returns a dict: {'timestamp': ts, 'lighter': price, 'paradex': price, 'bybit': price, 'binance': price}
    """
    prices = {'timestamp': datetime.now()}
    headers = {"User-Agent": "ArbDashboard/1.0"}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Lighter Mainnet (OrderBook)
        try:
            # Resolving Market ID (Simplified hardcoded for common pairs to save requests)
            market_id = 2048 if coin == "ETH" else (1 if coin == "USDC" else None) 
            if coin == "WBTC" or coin == "BTC": market_id = 10 # Example ID, needs verification for BTC
            
            if market_id:
                url = f"{LIGHTER_MAINNET_API}{LIGHTER_API_VERSION}/orderBookOrders"
                params = {'market_id': market_id, 'limit': 1}
                data = await fetch_json(session, url, params)
                if data and 'bids' in data and 'asks' in data:
                     if data['bids'] and data['asks']:
                        bid = float(data['bids'][0]['price']) if isinstance(data['bids'][0], dict) else float(data['bids'][0][0])
                        ask = float(data['asks'][0]['price']) if isinstance(data['asks'][0], dict) else float(data['asks'][0][0])
                        prices['lighter'] = (bid + ask) / 2
        except Exception as e:
            # print(f"Lighter Fetch Error: {e}")
            pass

        # 2. Paradex (Market Summary)
        try:
            symbol = f"{coin}-USD-PERP"
            url = f"{PARADEX_MAINNET_API}/markets/summary"
            params = {'market': symbol}
            data = await fetch_json(session, url, params)
            if data and 'results' in data and data['results']:
                prices['paradex'] = float(data['results'][0]['last_traded_price'])
        except Exception:
            pass

        # 3. Bybit (V5 Linear)
        try:
            symbol = f"{coin}USDT"
            url = f"{BYBIT_MAINNET_API}/v5/market/tickers"
            params = {'category': 'linear', 'symbol': symbol}
            data = await fetch_json(session, url, params)
            if data and data['retCode'] == 0 and data['result']['list']:
                prices['bybit'] = float(data['result']['list'][0]['lastPrice'])
        except Exception:
            pass
            
        # 4. Binance (Futures Public Ticker)
        try:
             symbol = f"{coin}USDT"
             url = "https://fapi.binance.com/fapi/v1/ticker/price"
             params = {'symbol': symbol}
             data = await fetch_json(session, url, params)
             if data and 'price' in data:
                 prices['binance'] = float(data['price'])
        except Exception:
            pass

    return prices

# ==============================================================================
# 2. Background Data Collector
# ==============================================================================
class DataCollector(threading.Thread):
    def __init__(self, interval=2.0):
        super().__init__()
        self.interval = interval
        self.running = True
        self.coins = ["ETH", "BTC"] # Coins to track
        self.filenames = {c: os.path.join(DATA_DIR, f"history_{c}.csv") for c in self.coins}
        
        # Initialize Files
        for c, fname in self.filenames.items():
            if not os.path.exists(fname):
                with open(fname, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'lighter', 'paradex', 'bybit', 'binance'])

    def run(self):
        # Create new event loop for this thread to handle async fetch
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while self.running:
            for coin in self.coins:
                try:
                    prices = loop.run_until_complete(fetch_prices(coin))
                    
                    # Write to CSV
                    with open(self.filenames[coin], 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            prices['timestamp'], 
                            prices.get('lighter', ''), 
                            prices.get('paradex', ''), 
                            prices.get('bybit', ''),
                            prices.get('binance', '')
                        ])
                except Exception as e:
                    print(f"Collector Error ({coin}): {e}")
            
            time.sleep(self.interval)
            
    def stop(self):
        self.running = False


# ==============================================================================
# 3. Streamlit App Logic
# ==============================================================================
@st.cache_resource
def start_data_collector():
    collector = DataCollector(interval=1.0) # Aggressive fetch
    collector.start()
    return collector

def load_data(coin, lookback_minutes=60):
    fname = os.path.join(DATA_DIR, f"history_{coin}.csv")
    if not os.path.exists(fname):
        return pd.DataFrame()
    
    try:
        # Read last N lines likely to cover lookback (approx 12 lines/min * 60 = 720)
        # For simplicity reading all, optimization would contain reading only tail
        df = pd.read_csv(fname)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True) # Ensure monotonic index for rolling
        
        # Filter lookback
        cutoff = datetime.now() - pd.Timedelta(minutes=lookback_minutes)
        df = df[df.index >= cutoff]
        
        # Convert cols to numeric
        cols = ['lighter', 'paradex', 'bybit', 'binance']
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors='coerce')
            
        return df
    except Exception as e:
        print(f"Read Error: {e}")
        return pd.DataFrame()

def main():
    st.set_page_config(page_title="Arbitrage Dashboard", layout="wide")
    st.title("⚡ Real-Time Arbitrage Dashboard")
    
    # Start Background Collector (Singleton)
    start_data_collector()

    # sidebar
    st.sidebar.header("Configuration")
    selected_coin = st.sidebar.selectbox("Select Coin", ["ETH", "BTC"])
    target_exchange = st.sidebar.selectbox("Target Exchange", ["Paradex", "Bybit", "Binance"])
    base_exchange = "Lighter" # Always compare against Lighter
    
    lookback = st.sidebar.slider("History Window (Minutes)", 5, 10080, 60)
    auto_refresh = st.sidebar.checkbox("Auto-Refresh (Fast)", value=True)

    # Main Content
    data = load_data(selected_coin, lookback)
    
    if data.empty:
        st.warning("Waiting for data collection... (This may take up to 5 seconds)")
    else:
        # Layout metrics
        latest = data.iloc[-1]
        
        col1, col2, col3, col4 = st.columns(4)
        
        p_target = latest.get(target_exchange.lower())
        p_base = latest.get(base_exchange.lower())
        
        col1.metric(f"{target_exchange}", f"${p_target:,.2f}" if pd.notnull(p_target) else "N/A")
        col2.metric(f"{base_exchange}", f"${p_base:,.2f}" if pd.notnull(p_base) else "N/A")
        
        if pd.notnull(p_target) and pd.notnull(p_base):
            spread_abs = p_target - p_base
            spread_bps = (spread_abs / p_target) * 10000
            col3.metric("Spread ($)", f"{spread_abs:.2f}")
            col4.metric("Spread (bps)", f"{spread_bps:.2f}")
            
        # Charts
        # 1. Price
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(x=data.index, y=data[target_exchange.lower()], name=target_exchange, line=dict(color='purple')))
        fig_price.add_trace(go.Scatter(x=data.index, y=data[base_exchange.lower()], name=base_exchange, line=dict(color='blue')))
        fig_price.update_layout(title=f"{selected_coin} Price Comparison", height=300, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_price, use_container_width=True)
        
        # 2. Spread
        # Calculate Spread Series
        s_series = (data[target_exchange.lower()] - data[base_exchange.lower()]) / data[target_exchange.lower()] * 10000
        
        # Calculate Rolling Percentiles (Window = History Window)
        # Using min_periods=1 to show expanding bands from start of data
        window_str = f"{lookback}min"
        qt_90 = s_series.rolling(window_str, min_periods=1).quantile(0.90)
        qt_50 = s_series.rolling(window_str, min_periods=1).quantile(0.50)
        qt_10 = s_series.rolling(window_str, min_periods=1).quantile(0.10)

        # Chart 2: Relative Spread with Rolling Bands
        fig_spread = go.Figure()
        fig_spread.add_trace(go.Scatter(x=data.index, y=s_series, name="Spread (bps)", line=dict(color='green', width=1)))
        
        # Overlay Rolling Bands
        fig_spread.add_trace(go.Scatter(x=data.index, y=qt_90, name="90% Band", line=dict(color='orange', width=1, dash='dot')))
        fig_spread.add_trace(go.Scatter(x=data.index, y=qt_50, name="Median Band", line=dict(color='blue', width=1, dash='dash')))
        fig_spread.add_trace(go.Scatter(x=data.index, y=qt_10, name="10% Band", line=dict(color='orange', width=1, dash='dot')))
        
        fig_spread.add_hline(y=0, line_dash="solid", line_color="black", opacity=0.3)
        fig_spread.update_layout(title="Relative Spread (bps) vs Rolling Bands", height=250, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_spread, use_container_width=True)
        
        # Chart 3: Rolling Percentiles (Standalone)
        fig_avg = go.Figure()
        fig_avg.add_trace(go.Scatter(x=data.index, y=qt_90, name="90% Quantile", line=dict(color='orange', width=2)))
        fig_avg.add_trace(go.Scatter(x=data.index, y=qt_50, name="50% Quantile", line=dict(color='blue', width=2)))
        fig_avg.add_trace(go.Scatter(x=data.index, y=qt_10, name="10% Quantile", line=dict(color='orange', width=2)))
        
        fig_avg.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.5)
        fig_avg.update_layout(title=f"Rolling {lookback}m Percentiles (bps)", height=250, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig_avg, use_container_width=True)

    # Auto-rerun
    if auto_refresh:
        time.sleep(0.5)
        st.rerun()

if __name__ == "__main__":
    main()
