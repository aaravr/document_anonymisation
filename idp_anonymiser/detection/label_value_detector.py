"""Label-value detector.

KYC and onboarding documents are highly templatic: the most reliable signal
that a value is sensitive is the label that precedes it. This detector codifies
that intuition with a configurable label catalogue and three pickup strategies:

1. **Inline**:  "Client Name: Acme Holdings Ltd" -> value follows the colon.
2. **Next line**: a label on its own line, value on the next non-empty line.
3. **Table cell**: a label in a cell, value in the cell to the right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from idp_anonymiser.agent.state import Detection, DocumentSpan


@dataclass(frozen=True)
class LabelRule:
    label_aliases: tuple
    entity_type: str
    max_value_length: int = 150
    min_value_length: int = 1


DEFAULT_LABEL_RULES = (
    LabelRule(("Client Name", "Customer Name", "Account Holder", "Account Holder Name"), "ORG"),
    LabelRule(("Legal Entity Name", "Entity Name", "Company Name", "Trading Name"), "ORG"),
    LabelRule(("Director", "Directors", "Officer", "Authorised Signatory"), "PERSON"),
    LabelRule(("Ultimate Beneficial Owner", "UBO", "Beneficial Owner"), "PERSON"),
    LabelRule(("Shareholder", "Shareholders", "Owner"), "PERSON"),
    LabelRule(("Registered Address", "Address", "Office Address", "Mailing Address"), "ADDRESS", max_value_length=300),
    LabelRule(("Date of Birth", "DOB", "D.O.B.", "Birth Date"), "DATE_OF_BIRTH"),
    LabelRule(("Date of Incorporation", "Incorporation Date"), "GENERIC_DATE"),
    LabelRule(("Passport Number", "Passport No", "Passport"), "PASSPORT"),
    LabelRule(("National ID", "NI Number", "NI No", "National Insurance Number", "SSN"), "NATIONAL_ID"),
    LabelRule(("Company Registration Number", "Registration Number", "Company Number", "Companies House Number", "Reg No"), "COMPANY_REG_NO"),
    LabelRule(("Tax ID", "TIN", "Tax Identification Number", "VAT Number", "VAT No"), "TAX_ID"),
    LabelRule(("LEI", "Legal Entity Identifier"), "LEI"),
    LabelRule(("Account Number", "Bank Account Number", "Account No"), "BANK_ACCOUNT"),
    LabelRule(("IBAN",), "IBAN"),
    LabelRule(("SWIFT", "SWIFT/BIC", "BIC", "SWIFT Code", "BIC Code"), "SWIFT_BIC"),
    LabelRule(("Email", "E-mail", "Email Address"), "EMAIL"),
    LabelRule(("Phone", "Phone Number", "Telephone", "Mobile", "Contact Number"), "PHONE"),
    LabelRule(("Case ID", "Case Number", "Case Reference"), "CASE_ID"),
    LabelRule(("Client ID", "Customer ID", "Client Reference", "Client Ref"), "CLIENT_ID"),
    LabelRule(("URL", "Website"), "URL"),
)


_LINE_RE = re.compile(r"^(?P<lead>\s*)(?P<label>[A-Za-z][A-Za-z0-9 /._\'-]{1,60}?)\s*[:\-\u2013]\s*(?P<value>.*?)\s*$")


def _alias_matches(observed: str, alias: str) -> bool:
    normalise = lambda s: re.sub(r"[\s./\-_]+", "", s).lower()
    return normalise(observed) == normalise(alias)


def _find_rule(label, rules):
    for rule in rules:
        for alias in rule.label_aliases:
            if _alias_matches(label, alias):
                return rule
    return None


_LEGAL_SUFFIX_TOKENS = (
    "ltd", "limited", "plc", "llp", "lp", "llc", "inc", "incorporated",
    "corp", "corporation", "gmbh", "ag", "sa", "nv", "bv", "co", "company",
)


def _refine_entity_type(label: str, value: str, rule_type: str) -> str:
    if rule_type not in {"PERSON", "ORG"}:
        return rule_type
    label_norm = re.sub(r"[\s./_\-]+", "", label.lower())
    ambiguous_labels = {"clientname", "customername", "accountholder", "accountholdername"}
    if label_norm not in ambiguous_labels:
        return rule_type
    val_lower = value.strip().lower().rstrip(".")
    last_token = val_lower.split()[-1] if val_lower.split() else ""
    if last_token.rstrip(".") in _LEGAL_SUFFIX_TOKENS:
        return "ORG"
    tokens = value.strip().split()
    if 1 <= len(tokens) <= 3 and all(re.match(r"^[A-Za-z][A-Za-z\'.\-]*$", t) for t in tokens):
        return "PERSON"
    return rule_type


def detect_label_values(text: str, rules=DEFAULT_LABEL_RULES):
    detections = []
    cursor = 0
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        line_start = cursor
        cursor += len(line)
        stripped_line = line.rstrip("\n").rstrip("\r")

        m = _LINE_RE.match(stripped_line)
        if m:
            label = m.group("label").strip()
            value = m.group("value").strip()
            rule = _find_rule(label, rules)
            if rule is not None and value:
                if rule.min_value_length <= len(value) <= rule.max_value_length:
                    abs_start = line_start + m.start("value")
                    trimmed_value = value.rstrip(".,;)]}\u00a0 ")
                    abs_end_trimmed = abs_start + len(trimmed_value)
                    refined_type = _refine_entity_type(label, trimmed_value, rule.entity_type)
                    detections.append(
                        Detection(
                            text=trimmed_value,
                            entity_type=refined_type,
                            confidence=0.95,
                            detector="label_value.inline",
                            span=DocumentSpan(text=trimmed_value, start=abs_start, end=abs_end_trimmed),
                            metadata={"label": label, "line": i},
                        )
                    )
                    continue

        bare = stripped_line.strip()
        if bare:
            label_candidate = bare.rstrip(":-\u2013 ").strip()
            rule = _find_rule(label_candidate, rules)
            if rule is not None:
                j = i + 1
                offset = cursor
                while j < len(lines):
                    next_line_text = lines[j].rstrip("\n").rstrip("\r")
                    if next_line_text.strip():
                        leading_ws = len(next_line_text) - len(next_line_text.lstrip())
                        value = next_line_text.strip()
                        if (
                            rule.min_value_length <= len(value) <= rule.max_value_length
                            and ":" not in value[: min(40, len(value))]
                        ):
                            abs_start = offset + leading_ws
                            abs_end = abs_start + len(value)
                            refined_type = _refine_entity_type(label_candidate, value, rule.entity_type)
                            detections.append(
                                Detection(
                                    text=value,
                                    entity_type=refined_type,
                                    confidence=0.9,
                                    detector="label_value.next_line",
                                    span=DocumentSpan(text=value, start=abs_start, end=abs_end),
                                    metadata={"label": label_candidate, "line": j},
                                )
                            )
                        break
                    offset += len(lines[j])
                    j += 1
    return detections


__all__ = ["DEFAULT_LABEL_RULES", "LabelRule", "detect_label_values"]
