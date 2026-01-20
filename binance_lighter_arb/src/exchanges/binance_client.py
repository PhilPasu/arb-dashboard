import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from binance import AsyncClient, BinanceSocketManager
from exchanges.base import ExchangeClient

logger = logging.getLogger(__name__)

class BinanceClientWrapper(ExchangeClient):
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.client: Optional[AsyncClient] = None
        self.bsm: Optional[BinanceSocketManager] = None
        self._execution_callback: Optional[Callable] = None

    async def connect(self):
        self.client = await AsyncClient.create(self.api_key, self.api_secret, testnet=self.testnet)
        self.bsm = BinanceSocketManager(self.client)
        logger.info(f"Connected to Binance {'Testnet' if self.testnet else 'Mainnet'}")

    async def disconnect(self):
        if self.client:
            await self.client.close_connection()
        logger.info("Disconnected from Binance")

    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        return await self.client.get_order_book(symbol=symbol)

    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity
        }
        if price:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
            
        return await self.client.create_order(**params)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return await self.client.cancel_order(symbol=symbol, orderId=order_id)

    async def get_balance(self, asset: str) -> float:
        res = await self.client.get_asset_balance(asset=asset.upper())
        return float(res['free'])

    async def start_execution_listener(self, callback: Callable):
        """
        Listens to user execution reports (fills).
        """
        self._execution_callback = callback
        ts = self.bsm.user_socket()
        async with ts as tscm:
            while True:
                msg = await tscm.recv()
                if msg.get('e') == 'executionReport':
                    # Filter for fills
                    if msg.get('x') == 'TRADE':
                        await self._execution_callback(msg)
                await asyncio.sleep(0.01)
