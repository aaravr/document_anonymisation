"""Label-value detector tailored for KYC / corporate / governance documents."""
from __future__ import annotations

import re
from dataclasses import dataclass
from sanitiser.state import Detection, Span


@dataclass(frozen=True)
class LabelRule:
    aliases: tuple
    entity_type: str
    max_value_length: int = 200


DEFAULT_RULES = (
    LabelRule(("Client Name", "Customer Name", "Account Holder"), "ORG"),
    LabelRule(("Legal Entity Name", "Entity Name", "Company Name", "Trading Name", "Issuer"), "ORG"),
    LabelRule(("Bank", "Banking Partner", "Banker"), "ORG"),
    LabelRule(("Subsidiary", "Subsidiaries", "Affiliate"), "ORG"),
    LabelRule(("Director", "Directors", "Board Member", "Officer", "Authorised Signatory", "Signatory", "Reviewer", "Relationship Manager", "RM", "Approver"), "PERSON"),
    LabelRule(("Ultimate Beneficial Owner", "UBO", "Beneficial Owner"), "PERSON"),
    LabelRule(("Shareholder", "Shareholders", "Owner"), "PERSON"),
    LabelRule(("President", "CEO", "CFO", "COO", "CRO", "CIO", "Chair of the Board", "Chair", "Chairperson", "Chairman"), "PERSON"),
    LabelRule(("Registered Address", "Address", "Office Address", "Mailing Address", "Place of Incorporation"), "ADDRESS", max_value_length=300),
    LabelRule(("Date of Birth", "DOB", "D.O.B.", "Birth Date"), "DATE_OF_BIRTH"),
    LabelRule(("Date of Incorporation", "Incorporation Date"), "GENERIC_DATE"),
    LabelRule(("Passport Number", "Passport No", "Passport"), "PASSPORT"),
    LabelRule(("National ID", "NI Number", "NI No", "National Insurance Number", "SSN"), "NATIONAL_ID"),
    LabelRule(("Company Registration Number", "Registration Number", "Company Number", "Companies House Number", "Reg No"), "COMPANY_REG_NO"),
    LabelRule(("Tax ID", "TIN", "Tax Identification Number", "VAT Number", "VAT No", "EIN"), "TAX_ID"),
    LabelRule(("LEI", "Legal Entity Identifier"), "LEI"),
    LabelRule(("Account Number", "Bank Account Number", "Account No", "A/C No"), "BANK_ACCOUNT"),
    LabelRule(("IBAN",), "IBAN"),
    LabelRule(("SWIFT", "SWIFT/BIC", "BIC", "SWIFT Code", "BIC Code"), "SWIFT_BIC"),
    LabelRule(("Email", "E-mail", "Email Address"), "EMAIL"),
    LabelRule(("Phone", "Phone Number", "Telephone", "Mobile", "Contact Number"), "PHONE"),
    LabelRule(("Case ID", "Case Number", "Case Reference"), "CASE_ID"),
    LabelRule(("Client ID", "Customer ID", "Client Reference", "Client Ref", "Reference Number", "Transaction Reference"), "CLIENT_ID"),
    LabelRule(("URL", "Website"), "URL"),
)

_LINE_RE = re.compile(r"^(?P<lead>\s*)(?P<label>[A-Za-z][A-Za-z0-9 /._'-]{1,60}?)\s*[:\-\u2013]\s*(?P<value>.*?)\s*$")

_LEGAL_SUFFIX = {"ltd", "limited", "plc", "llp", "lp", "llc", "inc", "incorporated",
                 "corp", "corporation", "gmbh", "ag", "sa", "nv", "bv", "co", "company",
                 "bank", "group", "holdings", "trust", "partners"}


def _alias_eq(a: str, b: str) -> bool:
    return re.sub(r"[\s./_-]+", "", a).lower() == re.sub(r"[\s./_-]+", "", b).lower()


def _refine(label: str, value: str, et: str) -> str:
    if et not in {"PERSON", "ORG"}:
        return et
    label_norm = re.sub(r"[\s./_-]+", "", label.lower())
    if label_norm not in {"clientname", "customername", "accountholder", "accountholdername"}:
        return et
    last = (value.strip().lower().rstrip(".").split() or [""])[-1]
    if last in _LEGAL_SUFFIX:
        return "ORG"
    toks = value.strip().split()
    if 1 <= len(toks) <= 4 and all(re.match(r"^[A-Za-z][A-Za-z'.\-]*$", t) for t in toks):
        return "PERSON"
    return et


def detect_label_values(text: str, *, page: int | None, chunk: int | None, offset: int = 0,
                        rules=DEFAULT_RULES) -> list[Detection]:
    out: list[Detection] = []
    cursor = 0
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        line_start = cursor
        cursor += len(line)
        stripped = line.rstrip("\n").rstrip("\r")
        m = _LINE_RE.match(stripped)
        if m:
            label = m.group("label").strip()
            value = m.group("value").strip()
            rule = next((r for r in rules for a in r.aliases if _alias_eq(a, label)), None)
            if rule is not None and value and len(value) <= rule.max_value_length:
                trimmed = value.rstrip(".,;)]}\u00a0 ")
                if trimmed:
                    abs_start = offset + line_start + m.start("value")
                    et = _refine(label, trimmed, rule.entity_type)
                    out.append(Detection(
                        text=trimmed, entity_type=et, confidence=0.95,
                        detector="label_value.inline",
                        span=Span(text=trimmed, start=abs_start, end=abs_start + len(trimmed),
                                  page=page, chunk=chunk),
                        metadata={"label": label},
                    ))
    return out
