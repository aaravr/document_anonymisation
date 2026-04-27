"""Detection sub-package: regex, NER, label-value, table, and entity resolution.

The submodules are imported on demand to avoid pulling in heavy NER stacks
(spaCy / Presidio) when only the regex layer is needed.
"""
from __future__ import annotations


def __getattr__(name: str):
    if name == "CompositeDetector":
        from idp_anonymiser.detection.detector import CompositeDetector

        return CompositeDetector
    if name == "resolve_entities":
        from idp_anonymiser.detection.entity_resolution import resolve_entities

        return resolve_entities
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["CompositeDetector", "resolve_entities"]
