"""Global registry of canonical entity -> synthetic replacement, with persistence.

The registry is the single source of truth for "what real entity becomes what
fake entity". It is:
* deterministic — seeded from the configured ``seed`` so the same input
  produces the same output
* globally consistent — same canonical entity always maps to the same fake,
  across pages, chunks, and files in a batch
* persistent — written to a JSON file so subsequent runs (or sibling
  documents) can re-use the existing mapping

Public entry points: :meth:`Registry.replacement_for_cluster` (assigns a
replacement on first call, returns the cached value subsequently) and
:meth:`Registry.save` / :meth:`Registry.load`.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sanitiser.state import CanonicalEntity
from sanitiser.replace import pools


def _seeded_rng(seed: int, key: str) -> random.Random:
    h = hashlib.sha256((str(seed) + "::" + key).encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big", signed=False))


_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(Ltd\.?|Limited|PLC|LLP|LLC|Inc\.?|Incorporated|Corp\.?|Corporation"
    r"|GmbH|AG|SA|NV|BV|Co\.?|Company|Bank|Group|Holdings|Trust|Partners)\s*$",
    re.IGNORECASE,
)


def _is_bank_like(name: str) -> bool:
    return bool(re.search(r"\bbank\b|\bbanking\b|\btrust\b|\bcapital\b|\bfederal\b",
                          name, re.IGNORECASE))


def _split_org_suffix(name: str) -> tuple[str, str]:
    m = _LEGAL_SUFFIX_RE.search(name.strip())
    if m:
        return name[: m.start()].strip(), m.group(1)
    return name.strip(), ""


@dataclass
class Registry:
    seed: int = 42
    by_canonical_id: dict[str, CanonicalEntity] = field(default_factory=dict)
    # Reverse index: normalised key -> canonical_id (for O(1) lookup during sanitisation)
    _by_normalised: dict[tuple[str, str], str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        out = {
            "seed": self.seed,
            "entities": {
                cid: e.model_dump(mode="json") for cid, e in self.by_canonical_id.items()
            },
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        reg = cls(seed=raw.get("seed", 42))
        for cid, payload in raw.get("entities", {}).items():
            ent = CanonicalEntity.model_validate(payload)
            reg.by_canonical_id[cid] = ent
            for v in ent.variants:
                reg._by_normalised[(ent.entity_type, v.lower())] = cid
        return reg

    # ------------------------------------------------------------------
    # Lookup / assignment
    # ------------------------------------------------------------------

    def _next_id(self, entity_type: str) -> str:
        self._counters[entity_type] = self._counters.get(entity_type, 0) + 1
        return entity_type + "_" + ("000" + str(self._counters[entity_type]))[-4:]

    def replacement_for_cluster(self, cluster) -> CanonicalEntity:
        """Return a CanonicalEntity for this cluster, creating it on first sight.

        ``cluster`` is the resolver's _Cluster (duck-typed: needs entity_type,
        full_name, normalised_keys, abbreviation, detections).
        """
        # Try to match against existing entities by any normalised key
        for k in cluster.normalised_keys:
            cid = self._by_normalised.get((cluster.entity_type, k))
            if cid is not None:
                return self.by_canonical_id[cid]

        # New entity — assign a deterministic synthetic replacement
        cid = self._next_id(cluster.entity_type)
        if cluster.entity_type == "ORG":
            ent = self._mint_org(cid, cluster)
        elif cluster.entity_type == "PERSON":
            ent = self._mint_person(cid, cluster)
        elif cluster.entity_type == "LOCATION":
            ent = self._mint_location(cid, cluster)
        else:
            ent = self._mint_structured(cid, cluster)

        self.by_canonical_id[cid] = ent
        for k in cluster.normalised_keys:
            self._by_normalised[(cluster.entity_type, k)] = cid
        return ent

    # ------------------------------------------------------------------
    # Mint per type
    # ------------------------------------------------------------------

    def _mint_org(self, cid: str, cluster) -> CanonicalEntity:
        original = cluster.full_name or next(iter(cluster.normalised_keys), "")
        rng = _seeded_rng(self.seed, "ORG::" + original)
        # Pick from bank pool if original looks bank-like, else corp pool
        pool = pools.ORG_BANK_POOL if _is_bank_like(original) else pools.ORG_CORP_POOL
        idx = rng.randrange(len(pool))
        full_replacement, abbrev_replacement = pool[idx]
        # Preserve original suffix where possible (Ltd / Limited / PLC / Bank ...)
        stem, suffix = _split_org_suffix(original)
        if suffix:
            full_replacement = full_replacement + " " + suffix
        # If pool full name doesn't naturally end in a word matching original suffix, keep as-is.
        variants = list({original.strip(), cluster.full_name})
        replacement_variants = [full_replacement]
        if cluster.abbreviation:
            replacement_variants.append(abbrev_replacement)
            variants.append(cluster.abbreviation)
        return CanonicalEntity(
            canonical_id=cid,
            entity_type="ORG",
            full_name=original,
            abbreviation=cluster.abbreviation,
            variants=sorted(set(filter(None, variants))),
            replacement_full_name=full_replacement,
            replacement_abbreviation=abbrev_replacement if cluster.abbreviation else None,
            replacement_variants=sorted(set(filter(None, replacement_variants))),
            confidence=cluster.confidence,
            detectors=sorted({d.detector for d in cluster.detections}),
            pages=sorted(cluster.pages),
        )

    def _mint_person(self, cid: str, cluster) -> CanonicalEntity:
        original = cluster.full_name or next(iter(cluster.normalised_keys), "")
        rng = _seeded_rng(self.seed, "PERSON::" + original)
        first = rng.choice(pools.PERSON_FIRST_NAMES)
        last = rng.choice(pools.PERSON_LAST_NAMES)
        full_replacement = first + " " + last
        # Build variants matching the original surface forms we saw.
        # E.g. if we saw "Mr Dodig" we synthesise "Mr Whitmore".
        variants = sorted({d.text for d in cluster.detections})
        replacement_variants: list[str] = [full_replacement]
        for v in variants:
            tokens = v.split()
            if len(tokens) == 1:
                replacement_variants.append(last)
            elif tokens[0].rstrip(".").lower() in {"mr", "mrs", "ms", "dr", "miss", "mx", "prof"}:
                replacement_variants.append(tokens[0] + " " + last)
            elif len(tokens) == 2 and len(tokens[0]) <= 2 and tokens[0].endswith("."):
                # initial form: "V. Dodig" -> "J. Whitmore"
                replacement_variants.append(first[0] + ". " + last)
        return CanonicalEntity(
            canonical_id=cid,
            entity_type="PERSON",
            full_name=original,
            variants=variants,
            replacement_full_name=full_replacement,
            replacement_variants=sorted(set(replacement_variants)),
            confidence=cluster.confidence,
            detectors=sorted({d.detector for d in cluster.detections}),
            pages=sorted(cluster.pages),
        )

    def _mint_location(self, cid: str, cluster) -> CanonicalEntity:
        original = cluster.full_name or next(iter(cluster.normalised_keys), "")
        rng = _seeded_rng(self.seed, "LOC::" + original)
        repl = rng.choice(pools.LOCATION_POOL)
        variants = sorted({d.text for d in cluster.detections})
        return CanonicalEntity(
            canonical_id=cid,
            entity_type="LOCATION",
            full_name=original,
            variants=variants,
            replacement_full_name=repl,
            replacement_variants=[repl],
            confidence=cluster.confidence,
            detectors=sorted({d.detector for d in cluster.detections}),
            pages=sorted(cluster.pages),
        )

    def _mint_structured(self, cid: str, cluster) -> CanonicalEntity:
        original = cluster.full_name or next(iter(cluster.normalised_keys), "")
        et = cluster.entity_type
        # Reuse the IDP id_generator if available; otherwise a deterministic stub.
        rng = _seeded_rng(self.seed, et + "::" + original)
        try:
            from idp_anonymiser.replacement import id_generator as ig

            mapping = {
                "EMAIL": ig.generate_email,
                "URL": ig.generate_url,
                "PHONE": ig.generate_phone,
                "IBAN": ig.generate_iban,
                "SWIFT_BIC": ig.generate_swift_bic,
                "BANK_ACCOUNT": ig.generate_bank_account,
                "SORT_CODE": ig.generate_sort_code,
                "LEI": ig.generate_lei,
                "PASSPORT": ig.generate_passport,
                "COMPANY_REG_NO": ig.generate_company_reg_no,
                "TAX_ID": ig.generate_tax_id,
                "NATIONAL_ID": ig.generate_national_id,
                "POSTCODE": lambda v: "BS1 4AB",
                "GENERIC_DATE": lambda v: ig.generate_date(v, dob=False),
                "DATE_OF_BIRTH": lambda v: ig.generate_date(v, dob=True),
                "CLIENT_ID": lambda v: ig.generate_generic_id(v, prefix="CL"),
                "CASE_ID": lambda v: ig.generate_generic_id(v, prefix="CA"),
                "ADDRESS": lambda v: rng.choice(pools.LOCATION_POOL),
            }
            fn = mapping.get(et)
            replacement = fn(original) if fn else "[" + et + "_" + cid + "]"
        except Exception:
            replacement = "[" + et + "_" + cid + "]"
        return CanonicalEntity(
            canonical_id=cid,
            entity_type=et,
            full_name=original,
            variants=[original],
            replacement_full_name=replacement,
            replacement_variants=[replacement],
            confidence=cluster.confidence,
            detectors=sorted({d.detector for d in cluster.detections}),
            pages=sorted(cluster.pages),
        )

    # ------------------------------------------------------------------
    # Public dump for the replacement_map.json output
    # ------------------------------------------------------------------

    def to_export_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for cid, e in self.by_canonical_id.items():
            row: dict[str, Any] = {
                "entity_type": e.entity_type,
                "original_full_name": e.full_name,
                "replacement_full_name": e.replacement_full_name,
                "original_variants": e.variants,
                "replacement_variants": e.replacement_variants,
            }
            if e.abbreviation:
                row["original_abbreviation"] = e.abbreviation
                row["replacement_abbreviation"] = e.replacement_abbreviation
            out[cid] = row
        return out
