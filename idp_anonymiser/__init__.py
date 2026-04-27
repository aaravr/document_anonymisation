"""IDP Anonymiser: CPU-first PII / client-data anonymisation for Intelligent Document Processing.

Public surface kept intentionally small. Most callers should use:

    from idp_anonymiser.agent import AnonymisationAgent
    from idp_anonymiser.agent.state import AnonymisationRequest

The package is structured as a deterministic pipeline (detect -> resolve -> replace ->
rewrite -> validate -> audit) with agent-style orchestration, not a free-form LLM agent.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
