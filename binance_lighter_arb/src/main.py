import asyncio
import logging
import sys
import os

# Ensure 'src' is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from utils.config_loader import load_config
from exchanges.binance_client import BinanceClientWrapper
from exchanges.lighter_client import LighterClientWrapper
from core.strategy import ArbStrategy
from core.engine import TradeEngine

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('arb_bot.log')
    ]
)
logger = logging.getLogger(__name__)

async def main():
    try:
        # 1. Load Configuration
        config = load_config("config/settings.yaml")
        
        # 2. Initialize Exchange Clients
        binance = BinanceClientWrapper(
            api_key=config['exchanges']['binance']['api_key'],
            api_secret=config['exchanges']['binance']['api_secret'],
            testnet=config['exchanges']['binance']['testnet']
        )
        
        lighter = LighterClientWrapper(
            private_key=config['exchanges']['lighter']['private_key'],
            api_url=config['exchanges']['lighter']['api_url'],
            web3_url=config['exchanges']['lighter']['web3_url']
        )
        
        # 3. Initialize Strategy
        strategy = ArbStrategy(
            min_profit_pct=config['strategy']['min_profit_pct'],
            binance_fee_pct=config['strategy']['binance_fee_pct'],
            lighter_fee_pct=config['strategy']['lighter_fee_pct']
        )
        
        # 4. Initialize Trade Engine
        engine = TradeEngine(
            binance=binance,
            lighter=lighter,
            strategy=strategy,
            symbol_binance=config['strategy']['symbol_binance'],
            symbol_lighter=config['strategy']['symbol_lighter']
        )
        
        # 5. Run Engine
        await engine.start()
        
    except FileNotFoundError:
        logger.error("Configuration file not found. Please create config/settings.yaml")
    except Exception as e:
        logger.exception(f"Unhandled exception in main: {e}")

if __name__ == "__main__":
    asyncio.run(main())
