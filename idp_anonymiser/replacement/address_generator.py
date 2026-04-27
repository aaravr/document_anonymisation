"""Deterministic synthetic UK-style address generator.

Tries to preserve the broad shape of the original (number + street + town +
postcode) so downstream extraction tests still see "looks like an address".
"""
from __future__ import annotations

import hashlib
import random
import re

from idp_anonymiser.replacement.faker_provider import seed_for

_FAKE_TOWNS = [
    "Bristol",
    "Leeds",
    "Manchester",
    "Edinburgh",
    "Bath",
    "Cambridge",
    "Oxford",
    "Newcastle",
    "Sheffield",
    "Coventry",
]

_FAKE_STREETS = [
    "Market Road",
    "High Street",
    "Park Lane",
    "Mill Lane",
    "Church Road",
    "Station Road",
    "Victoria Avenue",
    "Queens Walk",
    "Castle View",
    "Riverside",
]

# UK postcode area pool, paired with "second-half" numeric+letter blocks
_FAKE_POSTCODE_AREAS = [
    "BS1 4AB", "LS2 8DG", "M1 3DZ", "EH1 1QS", "BA1 2HP",
    "CB1 1NR", "OX1 4AR", "NE1 4ST", "S1 2HE", "CV1 5RY",
]


_UK_POSTCODE_RE = re.compile(
    r"\b(GIR ?0AA|"
    r"[A-PR-UWYZ](?:[0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)"
    r" ?[0-9][ABD-HJLNP-UW-Z]{2})\b",
    re.IGNORECASE,
)


def generate_postcode(value: str) -> str:
    rng = random.Random(seed_for(value))
    return rng.choice(_FAKE_POSTCODE_AREAS)


def generate_address(original: str) -> str:
    """Return a synthetic address whose shape roughly matches ``original``."""
    rng = random.Random(seed_for(original))
    # Try to preserve a leading number if present
    m = re.match(r"\s*(\d{1,4})", original)
    number = m.group(1) if m else str(rng.randint(1, 200))
    street = rng.choice(_FAKE_STREETS)
    town = rng.choice(_FAKE_TOWNS)
    postcode = rng.choice(_FAKE_POSTCODE_AREAS)
    # If original had a country fragment we keep "UK"
    if re.search(r"\bunited kingdom\b|\buk\b", original, re.IGNORECASE):
        return f"{number} {street}, {town} {postcode}, UK"
    # Multi-line addresses become comma-separated single line — easier for IDP tests
    return f"{number} {street}, {town} {postcode}"


__all__ = ["generate_address", "generate_postcode"]
