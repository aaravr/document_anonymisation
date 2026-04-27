"""Seed-list recogniser using spaCy PhraseMatcher when available, else case-insensitive regex.

Seed-list entities are *always* replaced — they are the operator's authoritative
list of known sensitive surface forms.
"""
from __future__ import annotations

import re
from sanitiser.config import SeedList
from sanitiser.state import Detection, Span


class SeedListMatcher:
    def __init__(self, seed: SeedList) -> None:
        self.seed = seed
        # Pre-compile case-insensitive whole-word patterns.
        self._patterns: list[tuple[re.Pattern, str, str | None, str]] = []
        TITLES = ("Mr", "Mrs", "Ms", "Miss", "Mx", "Dr", "Prof", "Sir", "Dame")
        for name in seed.persons:
            if not name:
                continue
            self._patterns.append((self._compile(name), "PERSON", None, "seed_list.person"))
            tokens = name.strip().split()
            if len(tokens) >= 2:
                last = tokens[-1]
                if len(last) >= 4 and last[0].isupper():
                    # Title + surname patterns (Mr Dodig, etc.)
                    for t in TITLES:
                        self._patterns.append((re.compile(r"\b" + t + r"\.?\s+" + re.escape(last) + r"\b"),
                                                "PERSON", None, "seed_list.person_title_surname"))
                    # Bare surname (only when 4+ chars to avoid common-word collisions)
                    self._patterns.append((re.compile(r"\b" + re.escape(last) + r"\b"),
                                            "PERSON", None, "seed_list.person_surname"))
                    # Initial + surname (V. Dodig)
                    if len(tokens[0]) >= 1:
                        self._patterns.append((re.compile(r"\b" + re.escape(tokens[0][0]) + r"\.\s+" + re.escape(last) + r"\b"),
                                                "PERSON", None, "seed_list.person_initial_surname"))
        # Words to strip when deriving an "org stem" (Mercedes-Benz Group AG -> Mercedes-Benz).
        # Order matters: strip structural words before legal suffix.
        STRUCTURAL_SUFFIX = (
            "Holding", "Holdings", "Mobility", "Group", "Trust", "Partners",
            "Capital", "International", "Worldwide", "Global", "Bank", "Banking",
        )
        LEGAL_SUFFIX = (
            "Ltd", "Ltd.", "Limited", "PLC", "LLP", "LLC", "Inc", "Inc.",
            "Incorporated", "Corp", "Corp.", "Corporation",
            "GmbH", "AG", "S.A.", "SA", "N.V.", "NV", "B.V.", "BV", "Co.", "Co",
            "S.p.A.", "SARL", "S.L.",
        )

        def _strip_suffix(name, words):
            tokens = name.split()
            while tokens and tokens[-1].rstrip(".") in {w.rstrip(".") for w in words}:
                tokens.pop()
            return " ".join(tokens)

        for org in seed.orgs:
            full = org["name"]
            abbrev = org.get("abbreviation")
            if full:
                self._patterns.append((self._compile(full), "ORG", abbrev, "seed_list.org_full"))
                # Derive stems: strip legal suffix, then optionally structural suffix.
                stem_no_legal = _strip_suffix(full, LEGAL_SUFFIX)
                if stem_no_legal != full and len(stem_no_legal) >= 4:
                    self._patterns.append((self._compile(stem_no_legal),
                                            "ORG", abbrev, "seed_list.org_stem_legal"))
                stem_no_struct = _strip_suffix(stem_no_legal, STRUCTURAL_SUFFIX)
                # Only add structural-stripped stem if it still has 2+ tokens or a hyphen
                # (so we don't swallow "Group" alone as a generic word).
                if (stem_no_struct != stem_no_legal and len(stem_no_struct) >= 4
                        and ("-" in stem_no_struct or len(stem_no_struct.split()) >= 1)):
                    if len(stem_no_struct.split()) >= 2 or "-" in stem_no_struct:
                        self._patterns.append((self._compile(stem_no_struct),
                                                "ORG", abbrev, "seed_list.org_stem_structural"))
            if abbrev:
                self._patterns.append((re.compile(r"\b" + re.escape(abbrev) + r"\b"),
                                        "ORG", abbrev, "seed_list.org_abbrev"))
        for loc in seed.locations:
            if loc:
                self._patterns.append((self._compile(loc), "LOCATION", None, "seed_list.location"))

    @staticmethod
    def _compile(phrase: str) -> re.Pattern:
        return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)

    def detect(self, text: str, *, page: int | None, chunk: int | None,
               offset: int = 0) -> list[Detection]:
        out: list[Detection] = []
        for pat, et, abbrev, det_name in self._patterns:
            for m in pat.finditer(text):
                meta: dict = {"seed": True}
                if abbrev:
                    meta["abbreviation"] = abbrev
                out.append(Detection(
                    text=m.group(0), entity_type=et, confidence=0.99,
                    detector=det_name,
                    span=Span(text=m.group(0), start=offset + m.start(), end=offset + m.end(),
                              page=page, chunk=chunk),
                    metadata=meta,
                ))
        return out
