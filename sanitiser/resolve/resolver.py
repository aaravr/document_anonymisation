"""Group detections into canonical real-world entities.

Three groupings:
1. ORG: full-name + abbreviation linking
   - explicit "Full Name (ABBR)" pattern
   - abbreviation of full-name capital letters
   - seed-list-supplied abbreviations
2. PERSON: variant grouping
   - "Victor Dodig" + "Mr Dodig" + "Dodig" + "V. Dodig" -> same canonical
   - only merge surname-only when unambiguous (one person with that surname)
3. STRUCTURED IDS: identical normalised value -> same canonical
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sanitiser.state import Detection
from sanitiser.resolve.normaliser import (
    normalise, org_stem, person_first_name, person_last_name
)


@dataclass
class _Cluster:
    entity_type: str
    full_name: str = ""
    normalised_keys: set[str] = field(default_factory=set)
    detections: list[Detection] = field(default_factory=list)
    abbreviation: Optional[str] = None
    is_org_full: bool = False  # True when this cluster has a full org name (vs only abbrev)
    confidence: float = 0.0
    pages: set[int] = field(default_factory=set)


def _is_abbrev_of(abbrev: str, full_norm: str) -> bool:
    """Heuristic: do the capital letters of full_norm spell abbrev?"""
    if not abbrev or not full_norm:
        return False
    STOP = {"of", "the", "and", "&", "to", "for"}
    initials = "".join(t[0] for t in full_norm.split() if t and t.lower() not in STOP and t.lower() != t.upper())
    return initials.upper().startswith(abbrev.upper())


def _explicit_abbrev_pairs(text_pages) -> list[tuple[str, str]]:
    """Find inline 'Full Name (ABBR)' patterns across the document.

    Returns list of (full_name, abbreviation).
    """
    pairs: list[tuple[str, str]] = []
    pat = re.compile(r"([A-Z][A-Za-z&.\-' ]{4,80}?)\s*\(([A-Z]{2,8})\)")
    for page_text in text_pages:
        for m in pat.finditer(page_text):
            full = m.group(1).strip()
            abbr = m.group(2).strip()
            # Only keep if the abbreviation is plausibly derived from the full name
            # OR appears separately enough times to be informative.
            full_norm = normalise(full, "ORG")
            if _is_abbrev_of(abbr, full_norm):
                pairs.append((full, abbr))
    return pairs


class EntityResolver:
    def __init__(self, *, fuzzy_threshold: int = 90) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self.clusters: list[_Cluster] = []

    def resolve(self, detections: list[Detection], page_texts: list[str]) -> list[_Cluster]:
        # 1. Process explicit "Full Name (ABBR)" pairs first so the seed graph is rich.
        abbrev_pairs = _explicit_abbrev_pairs(page_texts)
        seeded_org_abbrev: dict[str, str] = {}
        for full, abbr in abbrev_pairs:
            seeded_org_abbrev[normalise(full, "ORG")] = abbr

        # Sort detections so longer / more-specific names are clustered first.
        ordered = sorted(detections, key=lambda d: -len(d.text))

        for d in ordered:
            if d.entity_type == "ORG":
                self._ingest_org(d, seeded_org_abbrev)
            elif d.entity_type == "PERSON":
                self._ingest_person(d)
            elif d.entity_type == "LOCATION":
                self._ingest_simple(d)
            else:
                self._ingest_structured(d)

        # Pass 2: link surname-only person clusters into full-name clusters
        # whenever exactly one full-name cluster has that surname.
        self._link_surname_persons()
        # Pass 3: link bare-abbrev ORG clusters into full-name clusters when
        # the abbreviation matches.
        self._link_bare_abbrev_orgs()
        return self.clusters

    # ------------------------------------------------------------------
    # ORG ingestion: full name ↔ abbreviation linking
    # ------------------------------------------------------------------

    def _ingest_org(self, d: Detection, seeded_pairs: dict[str, str]) -> None:
        norm = normalise(d.text, "ORG")
        # Detection may carry an abbreviation in metadata (from seed list)
        meta_abbrev = d.metadata.get("abbreviation")
        # Decide if d itself is an abbreviation: short, all caps, no spaces
        looks_like_abbrev = (
            d.text.upper() == d.text and 2 <= len(d.text.replace(".", "")) <= 8
            and " " not in d.text
        )

        # Try to merge into an existing cluster
        target: Optional[_Cluster] = None
        for c in self.clusters:
            if c.entity_type != "ORG":
                continue
            if norm in c.normalised_keys:
                target = c
                break
            if c.is_org_full and meta_abbrev and c.abbreviation == meta_abbrev:
                target = c
                break
            if c.is_org_full and looks_like_abbrev and c.abbreviation and c.abbreviation == d.text:
                target = c
                break
            if c.is_org_full and looks_like_abbrev and _is_abbrev_of(d.text, next(iter(c.normalised_keys))):
                target = c
                break
            # Stem-equality for "Acme Holdings" matching "Acme Holdings Limited"
            if c.is_org_full and not looks_like_abbrev:
                stem_a, stem_b = org_stem(norm), org_stem(next(iter(c.normalised_keys)))
                if stem_a and stem_a == stem_b:
                    target = c
                    break

        if target is None:
            target = _Cluster(entity_type="ORG", is_org_full=not looks_like_abbrev)
            self.clusters.append(target)

        target.normalised_keys.add(norm)
        target.detections.append(d)
        target.confidence = max(target.confidence, d.confidence)
        if d.span.page is not None:
            target.pages.add(d.span.page)
        if not target.full_name and not looks_like_abbrev:
            target.full_name = d.text
            target.is_org_full = True
        if looks_like_abbrev and not target.abbreviation:
            target.abbreviation = d.text
        # Seed-supplied abbreviation always wins
        if meta_abbrev and not target.abbreviation:
            target.abbreviation = meta_abbrev
        # Discovered explicit abbreviation in document
        if not target.abbreviation and norm in seeded_pairs:
            target.abbreviation = seeded_pairs[norm]

    def _ingest_person(self, d: Detection) -> None:
        norm = normalise(d.text, "PERSON")
        # Find a cluster whose normalised key set already contains this name
        target: Optional[_Cluster] = None
        for c in self.clusters:
            if c.entity_type != "PERSON":
                continue
            if norm in c.normalised_keys:
                target = c
                break
        if target is None:
            target = _Cluster(entity_type="PERSON", full_name=d.text)
            self.clusters.append(target)

        target.normalised_keys.add(norm)
        target.detections.append(d)
        target.confidence = max(target.confidence, d.confidence)
        if d.span.page is not None:
            target.pages.add(d.span.page)
        # Adopt the longer surface form as the canonical full name.
        if len(d.text.split()) > len(target.full_name.split()):
            target.full_name = d.text

    def _ingest_simple(self, d: Detection) -> None:
        norm = normalise(d.text, d.entity_type)
        target: Optional[_Cluster] = None
        for c in self.clusters:
            if c.entity_type != d.entity_type:
                continue
            if norm in c.normalised_keys:
                target = c
                break
        if target is None:
            target = _Cluster(entity_type=d.entity_type, full_name=d.text)
            self.clusters.append(target)
        target.normalised_keys.add(norm)
        target.detections.append(d)
        target.confidence = max(target.confidence, d.confidence)
        if d.span.page is not None:
            target.pages.add(d.span.page)

    def _ingest_structured(self, d: Detection) -> None:
        norm = normalise(d.text, d.entity_type)
        target: Optional[_Cluster] = None
        for c in self.clusters:
            if c.entity_type == d.entity_type and norm in c.normalised_keys:
                target = c
                break
        if target is None:
            target = _Cluster(entity_type=d.entity_type, full_name=d.text)
            self.clusters.append(target)
        target.normalised_keys.add(norm)
        target.detections.append(d)
        target.confidence = max(target.confidence, d.confidence)
        if d.span.page is not None:
            target.pages.add(d.span.page)

    # ------------------------------------------------------------------
    # Pass 2/3: post-hoc linking
    # ------------------------------------------------------------------

    def _link_surname_persons(self) -> None:
        """Merge surname-only person clusters into a unique full-name cluster."""
        full_name_clusters = [c for c in self.clusters if c.entity_type == "PERSON"
                              and any(len(k.split()) >= 2 for k in c.normalised_keys)]
        # Map surname -> list of full-name clusters
        by_surname: dict[str, list[_Cluster]] = defaultdict(list)
        for c in full_name_clusters:
            for k in c.normalised_keys:
                if len(k.split()) >= 2:
                    by_surname[person_last_name(k)].append(c)
        # Find single-token clusters and merge if unique surname owner
        merged_clusters: set[int] = set()
        for c in list(self.clusters):
            if c.entity_type != "PERSON":
                continue
            if id(c) in merged_clusters:
                continue
            # single-token: surname-only
            single_only = all(len(k.split()) == 1 for k in c.normalised_keys)
            if not single_only:
                continue
            surname = next(iter(c.normalised_keys))
            owners = [o for o in by_surname.get(surname, []) if o is not c]
            if len(owners) == 1:
                target = owners[0]
                target.normalised_keys.update(c.normalised_keys)
                target.detections.extend(c.detections)
                target.pages.update(c.pages)
                target.confidence = max(target.confidence, c.confidence)
                merged_clusters.add(id(c))
        if merged_clusters:
            self.clusters = [c for c in self.clusters if id(c) not in merged_clusters]

    def _link_bare_abbrev_orgs(self) -> None:
        """Merge ORG clusters that contain only an abbreviation into the
        matching full-name cluster, when unambiguous."""
        full_clusters = [c for c in self.clusters if c.entity_type == "ORG" and c.is_org_full]
        merged: set[int] = set()
        for c in list(self.clusters):
            if c.entity_type != "ORG" or c.is_org_full:
                continue
            if id(c) in merged:
                continue
            abbrev_text = next(iter(c.normalised_keys))
            candidates = []
            for fc in full_clusters:
                if fc.abbreviation and fc.abbreviation.lower() == abbrev_text.lower():
                    candidates.append(fc)
                    continue
                # Try initials match
                if any(_is_abbrev_of(abbrev_text, k) for k in fc.normalised_keys):
                    candidates.append(fc)
            if len(candidates) == 1:
                target = candidates[0]
                target.normalised_keys.update(c.normalised_keys)
                target.detections.extend(c.detections)
                target.pages.update(c.pages)
                if not target.abbreviation:
                    target.abbreviation = c.detections[0].text
                merged.add(id(c))
        if merged:
            self.clusters = [c for c in self.clusters if id(c) not in merged]
