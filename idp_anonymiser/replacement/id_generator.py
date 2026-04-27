"""Deterministic synthetic identifier generators.

Each generator preserves the broad format/length of the original where it
matters for downstream IDP rules (e.g. an IBAN must remain 22 chars and start
with a country code; an LEI must be 20 chars and pass mod-97-10).

Determinism is achieved by seeding a per-value RNG.
"""
from __future__ import annotations

import hashlib
import random
import re
import string

from idp_anonymiser.replacement.faker_provider import seed_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_rng(value: str, salt: str = "") -> random.Random:
    h = hashlib.sha256(f"{salt}\u0001{value}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big", signed=False))


def _alnum(rng: random.Random, n: int, *, upper_only: bool = True) -> str:
    pool = string.ascii_uppercase + string.digits if upper_only else string.ascii_letters + string.digits
    return "".join(rng.choices(pool, k=n))


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choices(string.digits, k=n))


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------


def _iban_check_digits(country: str, bban: str) -> str:
    """Compute the two ISO 13616 check digits for ``country`` + ``bban``."""
    rearranged = bban + country + "00"
    digits = []
    for c in rearranged.upper():
        if c.isdigit():
            digits.append(c)
        else:
            digits.append(str(ord(c) - 55))
    n = int("".join(digits))
    return f"{98 - (n % 97):02d}"


def generate_iban(original: str) -> str:
    """Return a valid synthetic IBAN preserving country and length."""
    s = re.sub(r"\s+", "", original).upper()
    rng = _seeded_rng(original, "iban")
    country = s[:2] if len(s) >= 4 and s[:2].isalpha() else "GB"
    target_len = max(15, min(34, len(s))) or 22
    bban_len = target_len - 4
    # BBAN: synth as 4 letters + digits to look bank-account-like
    bban = _alnum(rng, 4) + _digits(rng, max(0, bban_len - 4))
    check = _iban_check_digits(country, bban)
    return f"{country}{check}{bban}"


# ---------------------------------------------------------------------------
# LEI
# ---------------------------------------------------------------------------


def _lei_check_digits(prefix18: str) -> str:
    rearranged = prefix18 + "00"
    digits = []
    for c in rearranged.upper():
        if c.isdigit():
            digits.append(c)
        else:
            digits.append(str(ord(c) - 55))
    n = int("".join(digits))
    return f"{98 - (n % 97):02d}"


def generate_lei(original: str) -> str:
    rng = _seeded_rng(original, "lei")
    prefix = _alnum(rng, 18)
    # Ensure the first 4 chars look like an LOU code (alphanumeric is fine).
    return prefix + _lei_check_digits(prefix)


# ---------------------------------------------------------------------------
# SWIFT/BIC
# ---------------------------------------------------------------------------


def generate_swift_bic(original: str) -> str:
    rng = _seeded_rng(original, "bic")
    bank = "".join(rng.choices(string.ascii_uppercase, k=4))
    country = "".join(rng.choices(string.ascii_uppercase, k=2))
    location = _alnum(rng, 2)
    if len(original) >= 11:
        branch = _alnum(rng, 3)
        return bank + country + location + branch
    return bank + country + location


# ---------------------------------------------------------------------------
# Bank account / sort code
# ---------------------------------------------------------------------------


def generate_bank_account(original: str) -> str:
    """Preserve the digit count of the original."""
    rng = _seeded_rng(original, "acct")
    digit_count = sum(1 for c in original if c.isdigit())
    if digit_count == 0:
        digit_count = 8
    return _digits(rng, digit_count)


def generate_sort_code(original: str) -> str:
    rng = _seeded_rng(original, "sort")
    raw = _digits(rng, 6)
    if "-" in original:
        return f"{raw[:2]}-{raw[2:4]}-{raw[4:]}"
    if " " in original:
        return f"{raw[:2]} {raw[2:4]} {raw[4:]}"
    return raw


# ---------------------------------------------------------------------------
# Misc IDs (passport, national id, company reg no, tax id, generic)
# ---------------------------------------------------------------------------


def generate_passport(original: str) -> str:
    rng = _seeded_rng(original, "pass")
    return _alnum(rng, max(8, min(12, len(original.strip()))) or 9)


def generate_company_reg_no(original: str) -> str:
    rng = _seeded_rng(original, "creg")
    s = original.strip()
    if len(s) >= 2 and s[:2].isalpha():
        return s[:2].upper() + _digits(rng, max(6, len(s) - 2))
    return _digits(rng, max(8, len(re.sub(r"\D", "", s)) or 8))


def generate_tax_id(original: str) -> str:
    rng = _seeded_rng(original, "tax")
    s = original.strip().upper()
    # If it looks like a VAT number with country prefix, preserve it.
    m = re.match(r"^([A-Z]{2})\s?(\d{6,12})$", s)
    if m:
        return m.group(1) + _digits(rng, len(m.group(2)))
    return _digits(rng, max(8, len(re.sub(r"\D", "", s)) or 9))


def generate_national_id(original: str) -> str:
    rng = _seeded_rng(original, "nid")
    s = original.strip()
    # US SSN style
    if re.match(r"^\d{3}-\d{2}-\d{4}$", s):
        return f"{_digits(rng, 3)}-{_digits(rng, 2)}-{_digits(rng, 4)}"
    # UK NI style (e.g. AB 12 34 56 C)
    m = re.match(r"^([A-CEGHJ-NPR-TW-Z]{2})\s?(\d{2})\s?(\d{2})\s?(\d{2})\s?([A-D])$", s, re.IGNORECASE)
    if m:
        ni_prefix = "".join(rng.choices("ABCEGHJKLMNPRSTWXYZ", k=2))
        return f"{ni_prefix} {_digits(rng, 2)} {_digits(rng, 2)} {_digits(rng, 2)} {rng.choice('ABCD')}"
    return _alnum(rng, max(8, len(s)))


def generate_generic_id(original: str, prefix: str = "") -> str:
    rng = _seeded_rng(original, prefix or "gen")
    digit_only = re.fullmatch(r"\d+", original.strip())
    if digit_only:
        return _digits(rng, len(original.strip()))
    if prefix:
        return f"{prefix}{_alnum(rng, max(6, len(original.strip())))}"
    return _alnum(rng, max(8, len(original.strip()) or 8))


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


_RESERVED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "test.invalid",
    "example",
)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "user"


def generate_email(
    original: str,
    *,
    person_name: str | None = None,
    org_name: str | None = None,
) -> str:
    """Return a synthetic email, ideally consistent with replacement person/org."""
    rng = _seeded_rng(original, "email")
    if person_name:
        local = _slug(person_name).replace("-", ".")
    else:
        local = _alnum(rng, 8, upper_only=False).lower()
    if org_name:
        domain = _slug(org_name) + ".example"
    else:
        domain = rng.choice(_RESERVED_EMAIL_DOMAINS)
    return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


def generate_url(original: str, org_name: str | None = None) -> str:
    if org_name:
        return f"https://www.{_slug(org_name)}.example"
    rng = _seeded_rng(original, "url")
    slug = _alnum(rng, 8, upper_only=False).lower()
    return f"https://www.{slug}.example"


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------


def generate_phone(original: str) -> str:
    rng = _seeded_rng(original, "phone")
    s = original.strip()
    if s.startswith("+"):
        return "+44 20 " + _digits(rng, 4) + " " + _digits(rng, 4)
    return "020 " + _digits(rng, 4) + " " + _digits(rng, 4)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def generate_date(original: str, *, dob: bool = False) -> str:
    """Return a synthetic date in the same surface format as ``original``."""
    rng = _seeded_rng(original, "dob" if dob else "date")
    if dob:
        year = rng.randint(1955, 1995)
    else:
        year = rng.randint(2000, 2024)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    s = original.strip()
    # Detect format heuristics
    if re.match(r"^\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}$", s):
        sep = "-" if "-" in s else ("/" if "/" in s else ".")
        return f"{year:04d}{sep}{month:02d}{sep}{day:02d}"
    if re.match(r"^\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}$", s):
        sep = "/" if "/" in s else ("-" if "-" in s else ".")
        # Preserve 2-digit year if original used one
        year_repr = f"{year % 100:02d}" if len(re.split(r"[/\-.]", s)[-1]) == 2 else f"{year:04d}"
        return f"{day:02d}{sep}{month:02d}{sep}{year_repr}"
    if re.search(r"[A-Za-z]", s):
        return f"{day} {_MONTHS[month-1]} {year}"
    return f"{day:02d}/{month:02d}/{year:04d}"


__all__ = [
    "generate_iban",
    "generate_lei",
    "generate_swift_bic",
    "generate_bank_account",
    "generate_sort_code",
    "generate_passport",
    "generate_company_reg_no",
    "generate_tax_id",
    "generate_national_id",
    "generate_generic_id",
    "generate_email",
    "generate_url",
    "generate_phone",
    "generate_date",
]
