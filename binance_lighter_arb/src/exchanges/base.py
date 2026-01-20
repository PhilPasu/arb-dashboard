from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class ExchangeClient(ABC):
    """
    Abstract base class for all exchange clients.
    """
    
    @abstractmethod
    async def connect(self):
        """Initialize connection to the exchange."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection to the exchange."""
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch the current orderbook."""
        pass

    @abstractmethod
    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        """Place a new order."""
        pass

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel an existing order."""
        pass

    @abstractmethod
    async def get_balance(self, asset: str) -> float:
        """Get account balance for a specific asset."""
        pass
