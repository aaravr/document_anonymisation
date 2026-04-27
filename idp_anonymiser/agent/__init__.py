"""Agent-style orchestration for the anonymisation pipeline."""
from __future__ import annotations

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    AnonymisationRequest,
    AnonymisationResult,
    CanonicalEntity,
    Detection,
    DocumentSpan,
    Mention,
    Replacement,
    ResolvedEntity,
    ValidationReport,
)

__all__ = [
    "AnonymisationAgent",
    "AnonymisationRequest",
    "AnonymisationResult",
    "AnonymisationPlan",
    "CanonicalEntity",
    "Detection",
    "DocumentSpan",
    "Mention",
    "Replacement",
    "ResolvedEntity",
    "ValidationReport",
]


def __getattr__(name: str):
    if name == "AnonymisationAgent":
        from idp_anonymiser.agent.anonymisation_agent import AnonymisationAgent
        return AnonymisationAgent
    raise AttributeError("module 'idp_anonymiser.agent' has no attribute " + repr(name))
