"""Wrapper around Faker producing deterministic values seeded by entity id.

We seed a fresh Faker instance per call so the same input deterministically
produces the same output, and the mapping store remains the authoritative
cache. Faker is optional at runtime: if not installed, we fall back to
trivially deterministic generators.
"""
from __future__ import annotations

import hashlib
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


_FAKE_FIRST_NAMES = [
    "Michael", "Sarah", "David", "Emma", "James", "Olivia", "Robert", "Sophia",
    "William", "Isabella", "Christopher", "Mia", "Daniel", "Charlotte", "Matthew", "Amelia",
    "Andrew", "Harper", "Joseph", "Evelyn", "Ryan", "Abigail", "Brandon", "Emily",
]
_FAKE_LAST_NAMES = [
    "Brown", "Carter", "Murphy", "Bennett", "Hughes", "Coleman", "Jenkins", "Morgan",
    "Foster", "Reed", "Hayes", "Bryant", "Russell", "Griffin", "Diaz", "Hayes",
    "Myers", "Ford", "Hamilton", "Graham", "Sullivan", "Wallace", "West", "Cole",
]


def seed_for(value: str) -> int:
    """Return a deterministic 64-bit integer seed derived from ``value``.

    Used to seed Faker so the same canonical input always yields the same fake.
    """
    h = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=False)


class FakerProvider:
    """Thin wrapper that seeds Faker per-call for determinism."""

    def __init__(self, locale: str = "en_GB") -> None:
        self.locale = locale
        self._faker = None
        try:
            from faker import Faker

            self._faker = Faker(locale)
        except ImportError:
            logger.info("Faker not installed; using built-in fallback provider.")

    def _faker_seeded(self, value: str):
        f = self._faker
        if f is not None:
            f.seed_instance(seed_for(value))
        return f

    def fake_person_name(self, value: str) -> str:
        f = self._faker_seeded(value)
        if f is not None:
            return f.name()
        rng = random.Random(seed_for(value))
        return f"{rng.choice(_FAKE_FIRST_NAMES)} {rng.choice(_FAKE_LAST_NAMES)}"

    def fake_first_name(self, value: str) -> str:
        f = self._faker_seeded(value)
        if f is not None:
            return f.first_name()
        rng = random.Random(seed_for(value))
        return rng.choice(_FAKE_FIRST_NAMES)

    def fake_last_name(self, value: str) -> str:
        f = self._faker_seeded(value)
        if f is not None:
            return f.last_name()
        rng = random.Random(seed_for(value))
        return rng.choice(_FAKE_LAST_NAMES)

    def fake_city(self, value: str) -> str:
        f = self._faker_seeded(value)
        if f is not None:
            return f.city()
        rng = random.Random(seed_for(value))
        return rng.choice(["Bristol", "Leeds", "Manchester", "Edinburgh", "Bath", "Cambridge"])

    def fake_street(self, value: str) -> str:
        f = self._faker_seeded(value)
        if f is not None:
            return f.street_name()
        rng = random.Random(seed_for(value))
        return rng.choice(["Market Road", "High Street", "Park Lane", "Mill Lane", "Church Road"])

    def fake_phone(self, value: str) -> Optional[str]:
        f = self._faker_seeded(value)
        if f is not None:
            try:
                return f.phone_number()
            except Exception:
                return None
        return None
