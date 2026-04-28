from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import AppConfig
from ..universe import UniverseResolver
from .base import Strategy

StrategyBuilder = Callable[[AppConfig, UniverseResolver], "StrategyRuntime | None"]


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    display_name: str
    description: str
    signal_group: str
    builder: StrategyBuilder


@dataclass(frozen=True, slots=True)
class StrategyRuntime:
    definition: StrategyDefinition
    strategy: Strategy
    symbol_scope: tuple[str, ...]
    notes: tuple[str, ...] = ()


_REGISTRY: dict[str, StrategyDefinition] = {}


def register_strategy(definition: StrategyDefinition) -> None:
    _REGISTRY[definition.strategy_id] = definition


def iter_strategy_definitions() -> tuple[StrategyDefinition, ...]:
    return tuple(_REGISTRY.values())


def get_strategy_definition(strategy_id: str) -> StrategyDefinition | None:
    return _REGISTRY.get(strategy_id)


def build_strategy_runtimes(
    config: AppConfig,
    universe: UniverseResolver,
) -> list[StrategyRuntime]:
    runtimes: list[StrategyRuntime] = []
    for definition in iter_strategy_definitions():
        runtime = definition.builder(config, universe)
        if runtime is not None:
            runtimes.append(runtime)
    return runtimes
