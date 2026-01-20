import yaml
import os

def load_config(config_path: str = "config/settings.yaml"):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

# Sample config structure
config_template = {
    "exchanges": {
        "binance": {
            "api_key": "YOUR_BINANCE_KEY",
            "api_secret": "YOUR_BINANCE_SECRET",
            "testnet": True
        },
        "lighter": {
            "private_key": "YOUR_PRIVATE_KEY",
            "api_url": "https://api.testnet.lighter.xyz",
            "web3_url": "https://rpc.testnet.lighter.xyz"
        }
    },
    "strategy": {
        "symbol_binance": "BTCUSDT",
        "symbol_id_lighter": 1,
        "min_profit_pct": 0.001,  # 0.1%
        "binance_fee_pct": 0.001, # 0.1%
        "lighter_fee_pct": 0.0
    }
}
