"""Motor de consenso multi-estrategia."""

from app.engine.consensus.algorithms import (
    DynamicWeighting,
    MajorityVoting,
    RegimeWeighting,
    WeightedAverage,
    WeightedVoting,
)
from app.engine.consensus.engine import ConsensusEngine

__all__ = [
    "ConsensusEngine",
    "DynamicWeighting",
    "MajorityVoting",
    "RegimeWeighting",
    "WeightedAverage",
    "WeightedVoting",
]
