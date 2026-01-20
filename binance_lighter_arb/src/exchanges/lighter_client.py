import logging
from typing import Dict, Any, Optional
from lighter.lighter_client import Client
from lighter.modules.blockchain import OrderSide
from exchanges.base import ExchangeClient

logger = logging.getLogger(__name__)

class LighterClientWrapper(ExchangeClient):
    def __init__(self, private_key: str, api_url: str, web3_url: str):
        self.private_key = private_key
        self.api_url = api_url
        self.web3_url = web3_url
        self.client: Optional[Client] = None

    async def connect(self):
        # Lighter SDK initialization
        self.client = Client(
            api_auth=self.private_key, # Usually private key is used for auth in Lighter
            web3_provider_url=self.web3_url,
            private_key=self.private_key,
            host=self.api_url
        )
        logger.info(f"Connected to Lighter API at {self.api_url}")

    async def disconnect(self):
        if self.client and self.client.async_api:
            await self.client.async_api.close_connection()
        logger.info("Disconnected from Lighter")

    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch the current orderbook for a given symbol."""
        raw_ob = await self.client.async_api.get_orderbook(symbol)
        
        # Format for consistency with Binance: {'bids': [[price, qty], ...], 'asks': [[price, qty], ...]}
        # Elliot.ai response has 'price' and 'remaining_base_amount' keys in list items
        formatted_ob = {
            'bids': [[float(b['price']), float(b.get('remaining_base_amount', 0))] for b in raw_ob.get('bids', [])],
            'asks': [[float(a['price']), float(a.get('remaining_base_amount', 0))] for a in raw_ob.get('asks', [])]
        }
        return formatted_ob

    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        """
        For Taker orders, we use market orders or aggressive limit orders.
        """
        side_enum = OrderSide.BUY if side.upper() == 'BUY' else OrderSide.SELL
        
        # Lighter SDK expects strings for numbers in transaction methods
        qty_str = str(quantity)
        price_str = str(price) if price else "0"

        if order_type.upper() == 'MARKET':
            # Note: create_market_order is async in Lighter SDK
            return await self.client.async_blockchain.create_market_order(
                orderbook_symbol=symbol,
                human_readable_size=qty_str,
                human_readable_price=price_str, # Slippage protected price
                side=side_enum
            )
        else:
            # For limit orders, we use the batch method for now (single order batch)
            return await self.client.async_blockchain.create_limit_order_batch(
                orderbook_symbol=symbol,
                human_readable_sizes=[qty_str],
                human_readable_prices=[price_str],
                sides=[side_enum]
            )

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        # Batch cancel for single order
        return await self.client.async_blockchain.cancel_limit_order_batch(
            orderbook_symbol=symbol, 
            order_ids=[int(order_id)]
        )

    async def get_balance(self, asset: str) -> float:
        # Placeholder: Filtering balances by asset symbol
        balances = self.client.api.get_account_balances() # This is usually for the authenticated account
        # Logic to extract specific asset balance depends on the API response structure
        return 0.0 # Implementation detail
