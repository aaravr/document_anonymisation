"""Normalise surface forms for matching."""
from __future__ import annotations

import re
import unicodedata


_PERSON_TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame",
                   "rev", "hon", "lord", "lady"}

_LEGAL_SUFFIX = {"ltd": "limited", "ltd.": "limited", "limited": "limited",
                 "co": "company", "co.": "company", "company": "company",
                 "corp": "corporation", "corp.": "corporation", "corporation": "corporation",
                 "inc": "incorporated", "inc.": "incorporated", "incorporated": "incorporated",
                 "plc": "plc", "llp": "llp", "lp": "lp", "llc": "llc",
                 "gmbh": "gmbh", "ag": "ag", "sa": "sa", "nv": "nv", "bv": "bv",
                 "bank": "bank", "group": "group", "holdings": "holdings"}


def normalise(text: str, entity_type: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).strip()
    if entity_type == "PERSON":
        cleaned = re.sub(r"[^\w\s.\-']", " ", t).strip().lower()
        toks = [x for x in re.split(r"\s+", cleaned) if x]
        while toks and toks[0].rstrip(".") in _PERSON_TITLES:
            toks.pop(0)
        return " ".join(x.rstrip(".") for x in toks if x.rstrip("."))
    if entity_type == "ORG":
        cleaned = re.sub(r"[^\w\s&.\-']", " ", t).strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)
        toks = cleaned.split()
        return " ".join(_LEGAL_SUFFIX.get(x.rstrip("."), x.rstrip(".")) for x in toks)
    if entity_type == "LOCATION":
        return re.sub(r"\s+", " ", t).strip().lower()
    if entity_type == "EMAIL":
        return t.lower()
    if entity_type in {"IBAN", "SWIFT_BIC", "BANK_ACCOUNT", "SORT_CODE",
                       "COMPANY_REG_NO", "TAX_ID", "LEI", "PASSPORT",
                       "NATIONAL_ID", "CLIENT_ID", "CASE_ID", "POSTCODE"}:
        return re.sub(r"[\s\-./]+", "", t).lower()
    if entity_type == "PHONE":
        return re.sub(r"[^\d+]", "", t)
    if entity_type in {"DATE_OF_BIRTH", "GENERIC_DATE"}:
        return re.sub(r"[\s./\-]+", "-", t.lower())
    return re.sub(r"\s+", " ", t).lower()


def org_stem(normalised: str) -> str:
    toks = normalised.split()
    while toks and toks[-1] in _LEGAL_SUFFIX.values():
        toks.pop()
    return " ".join(toks)


def person_last_name(normalised: str) -> str:
    parts = normalised.split()
    return parts[-1] if parts else ""


def person_first_name(normalised: str) -> str:
    parts = normalised.split()
    return parts[0] if parts else ""
