from .registry import (
    StrategyDefinition,
    StrategyRuntime,
    build_strategy_runtimes,
    get_strategy_definition,
    iter_strategy_definitions,
    register_strategy,
)

# Import strategy modules so they register themselves.
from . import etf_trend as _etf_trend  # noqa: F401
from . import etf_rotation as _etf_rotation  # noqa: F401
from . import etf_t_trade as _etf_t_trade  # noqa: F401

__all__ = [
    "StrategyDefinition",
    "StrategyRuntime",
    "build_strategy_runtimes",
    "get_strategy_definition",
    "iter_strategy_definitions",
    "register_strategy",
]
