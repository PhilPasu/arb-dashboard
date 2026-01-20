import asyncio
import logging
from typing import Optional
from core.strategy import ArbStrategy
from exchanges.binance_client import BinanceClientWrapper
from exchanges.lighter_client import LighterClientWrapper

logger = logging.getLogger(__name__)

class TradeEngine:
    def __init__(self, 
                 binance: BinanceClientWrapper, 
                 lighter: LighterClientWrapper, 
                 strategy: ArbStrategy,
                 symbol_binance: str,
                 symbol_lighter: str):
        self.binance = binance
        self.lighter = lighter
        self.strategy = strategy
        self.symbol_binance = symbol_binance
        self.symbol_lighter = symbol_lighter
        
        self.active_orders = {"bid": None, "ask": None}
        self.is_running = False

    async def start(self):
        self.is_running = True
        logger.info("Starting Trade Engine...")
        
        # Connect to exchanges
        await self.binance.connect()
        await self.lighter.connect()
        
        # Start Binance execution listener in the background
        asyncio.create_task(self.binance.start_execution_listener(self.on_binance_fill))
        
        # Main Loop: Monitor Lighter Orderbook and update Binance Quotes
        while self.is_running:
            try:
                await self.update_quotes()
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(1) # Frequency of quote updates

    async def update_quotes(self):
        """
        Fetches Lighter prices and updates Binance Limit Orders.
        """
        # 1. Get Lighter Taker Prices
        ob = await self.lighter.get_orderbook(self.symbol_lighter)
        if not ob or not ob.get('asks') or not ob.get('bids'):
            return

        best_ask = float(ob['asks'][0][0])
        best_bid = float(ob['bids'][0][0])
        
        # 2. Calculate target Binance prices
        targets = self.strategy.calculate_binance_maker_prices(best_bid, best_ask)
        
        # 3. Update Binance Orders (Logic to cancel/replace if price moved significantly)
        # Simplified: Just replace if targets changed much
        await self.manage_binance_order("BUY", targets['bid'], "bid")
        await self.manage_binance_order("SELL", targets['ask'], "ask")

    async def manage_binance_order(self, side: str, price: float, order_key: str):
        # Implementation of order management logic (cancellation, rate limiting)
        # Placeholder: This would check if an order exists and needs updating
        pass

    async def on_binance_fill(self, fill_msg: dict):
        """
        Callback triggered when a Binance order is filled.
        Immediately sends a hedge order to Lighter.
        """
        logger.info(f"Binance Fill Received: {fill_msg}")
        
        hedge_params = self.strategy.get_hedge_order_details(fill_msg)
        
        try:
            logger.info(f"Sending Hedge Order to Lighter: {hedge_params}")
            res = await self.lighter.create_order(
                symbol=self.symbol_lighter,
                side=hedge_params['side'],
                order_type='MARKET', # Or aggressive limit
                quantity=hedge_params['quantity']
            )
            logger.info(f"Lighter Hedge Result: {res}")
        except Exception as e:
            logger.error(f"FATAL: Failed to hedge on Lighter! {e}")
            # Raise alert system here
