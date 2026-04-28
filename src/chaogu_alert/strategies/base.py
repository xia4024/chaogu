from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import SignalIdea
from ..scan_context import ScanContext


class Strategy(ABC):
    strategy_id: str

    @abstractmethod
    def generate(self, context: ScanContext) -> list[SignalIdea]:
        raise NotImplementedError
