"""Entity resolution: merge overlapping/duplicate detections into canonical entities."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Iterable

from idp_anonymiser.agent.state import Detection, ResolvedEntity


def canonicalise(text: str, entity_type: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).strip()
    if entity_type in {
        "EMAIL", "URL", "IBAN", "SWIFT_BIC", "BANK_ACCOUNT", "SORT_CODE",
        "COMPANY_REG_NO", "TAX_ID", "LEI", "PASSPORT", "NATIONAL_ID",
        "CLIENT_ID", "CASE_ID", "POSTCODE",
    }:
        return re.sub(r"[\s\-./]+", "", t).lower()
    if entity_type in {"PERSON", "ORG", "ADDRESS"}:
        return re.sub(r"\s+", " ", t).strip().lower()
    if entity_type == "PHONE":
        return re.sub(r"[^\d+]", "", t)
    if entity_type in {"DATE_OF_BIRTH", "GENERIC_DATE"}:
        return re.sub(r"[\s./\-]+", "-", t.lower())
    return re.sub(r"\s+", " ", t).lower()


def _overlaps(a: Detection, b: Detection) -> bool:
    sa, ea = a.span.start, a.span.end
    sb, eb = b.span.start, b.span.end
    if sa is None or ea is None or sb is None or eb is None:
        return False
    return sa < eb and sb < ea


_TYPE_PRIORITY: dict = {
    "LEI": 100, "IBAN": 99, "SWIFT_BIC": 95, "EMAIL": 95,
    "PASSPORT": 90, "NATIONAL_ID": 90, "VAT": 90, "TAX_ID": 88,
    "COMPANY_REG_NO": 85, "BANK_ACCOUNT": 80, "SORT_CODE": 78,
    "POSTCODE": 75, "PHONE": 70, "DATE_OF_BIRTH": 65,
    "GENERIC_DATE": 60, "URL": 55, "PERSON": 50, "ORG": 50,
    "ADDRESS": 45, "CLIENT_ID": 40, "CASE_ID": 40,
}


def _detection_score(d: Detection):
    return (
        d.confidence,
        _TYPE_PRIORITY.get(d.entity_type, 0),
        (d.span.end or 0) - (d.span.start or 0),
    )


def _strictly_contains(outer: Detection, inner: Detection) -> bool:
    if outer.span.start is None or outer.span.end is None:
        return False
    if inner.span.start is None or inner.span.end is None:
        return False
    if (outer.span.end - outer.span.start) <= (inner.span.end - inner.span.start):
        return False
    return outer.span.start <= inner.span.start and inner.span.end <= outer.span.end


def deduplicate_overlapping(detections: Iterable[Detection]):
    indexed = sorted(
        (d for d in detections if d.span.start is not None and d.span.end is not None),
        key=lambda d: (d.span.start or 0, -(d.span.end or 0)),
    )
    no_offsets = [d for d in detections if d.span.start is None or d.span.end is None]
    kept = []
    for d in indexed:
        replaced = False
        skip = False
        for i, existing in enumerate(kept):
            if _strictly_contains(existing, d):
                skip = True
                break
            if _strictly_contains(d, existing):
                kept[i] = d
                replaced = True
                break
            if _overlaps(d, existing):
                if _detection_score(d) > _detection_score(existing):
                    kept[i] = d
                replaced = True
                break
        if skip:
            continue
        if not replaced:
            kept.append(d)
    return kept + no_offsets


def _make_entity_id(canonical_value: str, entity_type: str) -> str:
    h = hashlib.sha1((entity_type + chr(1) + canonical_value).encode("utf-8")).hexdigest()
    return "ent_" + h[:16]


def _try_fuzzy_group(canonical_value, entity_type, existing, threshold=92):
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return None
    if entity_type not in {"PERSON", "ORG", "ADDRESS"}:
        return None
    best = None
    for (k_val, k_type), _ in existing.items():
        if k_type != entity_type:
            continue
        score = fuzz.token_set_ratio(canonical_value, k_val)
        if score >= threshold and (best is None or score > best[1]):
            best = ((k_val, k_type), score)
    return best[0] if best else None


def resolve_entities(
    detections,
    *,
    use_registry: bool = False,
    replace_contextual_aliases: bool = True,
    fuzzy_threshold: int = 88,
):
    if use_registry:
        from idp_anonymiser.detection.canonical_registry import (
            CanonicalEntityRegistry,
            collect_mentions,
        )
        deduped_for_registry = deduplicate_overlapping(detections)
        mentions = collect_mentions(deduped_for_registry)
        reg = CanonicalEntityRegistry(
            replace_contextual_aliases=replace_contextual_aliases,
            fuzzy_threshold=fuzzy_threshold,
        )
        reg.ingest(mentions)
        out = []
        from idp_anonymiser.agent.state import Detection as _Detection
        for ce in reg.canonical_entities():
            dets = [
                _Detection(
                    text=m.text,
                    entity_type=m.entity_type,
                    confidence=m.confidence,
                    detector=m.detector,
                    span=m.span,
                    metadata=m.metadata,
                )
                for m in ce.mentions
            ]
            out.append(
                ResolvedEntity(
                    entity_id=ce.entity_id,
                    canonical_value=ce.canonical_original,
                    entity_type=ce.entity_type,
                    confidence=ce.confidence,
                    detections=dets,
                )
            )
        return out

    deduped = deduplicate_overlapping(detections)
    groups = defaultdict(list)
    for d in deduped:
        canonical = canonicalise(d.text, d.entity_type)
        if not canonical:
            continue
        match = _try_fuzzy_group(canonical, d.entity_type, groups)
        key = match if match else (canonical, d.entity_type)
        groups[key].append(d)

    resolved = []
    for (canonical_value, entity_type), group_detections in groups.items():
        best = max(group_detections, key=lambda d: (d.confidence, len(d.text)))
        resolved.append(
            ResolvedEntity(
                entity_id=_make_entity_id(canonical_value, entity_type),
                canonical_value=best.text,
                entity_type=entity_type,
                confidence=max(d.confidence for d in group_detections),
                detections=group_detections,
            )
        )
    return resolved


__all__ = ["resolve_entities", "deduplicate_overlapping", "canonicalise"]
