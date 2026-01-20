import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class ArbStrategy:
    def __init__(self, min_profit_pct: float, binance_fee_pct: float, lighter_fee_pct: float):
        self.min_profit_pct = min_profit_pct
        self.binance_fee_pct = binance_fee_pct
        self.lighter_fee_pct = lighter_fee_pct

    def calculate_binance_maker_prices(self, lighter_bid: float, lighter_ask: float) -> Dict[str, float]:
        """
        Calculate the prices we should quote on Binance to ensure profit after fees.
        
        If we sell on Binance (Maker), we must be able to buy on Lighter (Taker) cheaper.
        Binance Sell Price * (1 - BinanceFee) > Lighter Ask * (1 + LighterFee) + Profit
        
        If we buy on Binance (Maker), we must be able to sell on Lighter (Taker) dearer.
        Binance Buy Price * (1 + BinanceFee) < Lighter Bid * (1 - LighterFee) - Profit
        """
        
        # Target Binance Sell Price (Maker Ask)
        # SellPrice > (LighterAsk * (1 + LighterFee + MinProfit)) / (1 - BinanceFee)
        min_sell_price = (lighter_ask * (1 + self.lighter_fee_pct + self.min_profit_pct)) / (1 - self.binance_fee_pct)
        
        # Target Binance Buy Price (Maker Bid)
        # BuyPrice < (LighterBid * (1 - LighterFee - MinProfit)) / (1 + self.binance_fee_pct)
        max_buy_price = (lighter_bid * (1 - self.lighter_fee_pct - self.min_profit_pct)) / (1 + self.binance_fee_pct)
        
        return {
            "bid": max_buy_price,
            "ask": min_sell_price
        }

    def get_hedge_order_details(self, binance_fill: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determine the order to send to Lighter based on a Binance fill.
        """
        side = binance_fill.get('S') # 'BUY' or 'SELL' from Binance execution report
        quantity = float(binance_fill.get('l', 0)) # Last filled quantity
        
        # If we BOUGHT on Binance, we must SELL on Lighter
        # If we SOLD on Binance, we must BUY on Lighter
        hedge_side = 'SELL' if side == 'BUY' else 'BUY'
        
        return {
            "side": hedge_side,
            "quantity": quantity
        }
