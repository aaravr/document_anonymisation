"""Document-level canonical entity registry."""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from idp_anonymiser.agent.state import (
    CanonicalEntity,
    Detection,
    DocumentSpan,
    Mention,
)

logger = logging.getLogger(__name__)


_PERSON_TITLES = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame",
    "rev", "hon", "lord", "lady",
}

_LEGAL_SUFFIX_CANONICAL = {
    "ltd": "limited", "ltd.": "limited", "limited": "limited",
    "co": "company", "co.": "company", "company": "company",
    "corp": "corporation", "corp.": "corporation", "corporation": "corporation",
    "inc": "incorporated", "inc.": "incorporated", "incorporated": "incorporated",
    "plc": "plc", "llp": "llp", "lp": "lp", "llc": "llc",
    "gmbh": "gmbh", "ag": "ag", "sarl": "sarl", "sa": "sa",
    "nv": "nv", "bv": "bv",
}

_GENERIC_ALIAS_PHRASES_ORG = {
    "the client", "the company", "the firm", "the entity",
    "the corporation", "the borrower", "the issuer",
    "client", "company",
}

_GENERIC_ALIAS_PHRASES_PERSON = {
    "the director", "the signatory", "the customer",
    "the contact", "the applicant", "the beneficiary",
}


@dataclass
class EntityNormalizer:
    def normalise(self, text, entity_type):
        if not text:
            return ""
        t = unicodedata.normalize("NFKC", text).strip()
        if entity_type == "PERSON":
            return self._normalise_person(t)
        if entity_type == "ORG":
            return self._normalise_org(t)
        if entity_type == "ADDRESS":
            return self._normalise_address(t)
        if entity_type == "EMAIL":
            return t.lower().strip()
        if entity_type == "URL":
            return self._normalise_url(t)
        if entity_type == "PHONE":
            return re.sub(r"[^\d+]", "", t)
        if entity_type in {
            "IBAN", "SWIFT_BIC", "BANK_ACCOUNT", "SORT_CODE",
            "COMPANY_REG_NO", "TAX_ID", "LEI", "PASSPORT",
            "NATIONAL_ID", "CLIENT_ID", "CASE_ID", "POSTCODE",
        }:
            return re.sub(r"[\s\-./]+", "", t).lower()
        if entity_type in {"DATE_OF_BIRTH", "GENERIC_DATE"}:
            return re.sub(r"[\s./\-]+", "-", t.lower())
        return re.sub(r"\s+", " ", t).lower()

    @staticmethod
    def _normalise_person(text):
        cleaned = re.sub(r"[^\w\s.\-\']", " ", text).strip().lower()
        tokens = [t for t in re.split(r"\s+", cleaned) if t]
        while tokens and tokens[0].rstrip(".") in _PERSON_TITLES:
            tokens.pop(0)
        normalised_tokens = []
        for tok in tokens:
            tok = tok.rstrip(".")
            if not tok:
                continue
            normalised_tokens.append(tok)
        return " ".join(normalised_tokens)

    @staticmethod
    def _normalise_org(text):
        cleaned = re.sub(r"[^\w\s&.\-\']", " ", text).strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)
        tokens = cleaned.split()
        normalised_tokens = []
        for tok in tokens:
            stripped = tok.rstrip(".")
            normalised_tokens.append(_LEGAL_SUFFIX_CANONICAL.get(stripped, stripped))
        return " ".join(normalised_tokens)

    @staticmethod
    def _normalise_address(text):
        cleaned = re.sub(r"[^\w\s,]", " ", text).strip().lower()
        return re.sub(r"\s+", " ", cleaned)

    @staticmethod
    def _normalise_url(text):
        u = text.strip().lower()
        u = re.sub(r"^https?://", "", u)
        u = re.sub(r"^www\.", "", u)
        return u.rstrip("/")

    @staticmethod
    def is_generic_alias(text, entity_type):
        t = re.sub(r"\s+", " ", text.lower().strip())
        if entity_type == "ORG":
            return t in _GENERIC_ALIAS_PHRASES_ORG
        if entity_type == "PERSON":
            return t in _GENERIC_ALIAS_PHRASES_PERSON
        return False

    @staticmethod
    def acronym_of(org_name_normalised):
        tokens = [t for t in org_name_normalised.split() if t not in _LEGAL_SUFFIX_CANONICAL.values()]
        return "".join(t[0] for t in tokens if t).upper()

    @staticmethod
    def org_stem(org_name_normalised):
        tokens = org_name_normalised.split()
        while tokens and tokens[-1] in _LEGAL_SUFFIX_CANONICAL.values():
            tokens.pop()
        return " ".join(tokens)


def collect_mentions(detections, *, normaliser=None, page_resolver=None):
    norm = normaliser or EntityNormalizer()
    out = []
    for d in detections:
        page = d.span.page
        if page is None and page_resolver is not None:
            try:
                page = page_resolver(d)
            except Exception:
                page = None
        out.append(
            Mention(
                text=d.text,
                normalised=norm.normalise(d.text, d.entity_type),
                entity_type=d.entity_type,
                confidence=d.confidence,
                detector=d.detector,
                page=page,
                span=d.span,
                role_label=d.metadata.get("label"),
                metadata=d.metadata or {},
            )
        )
    return out


@dataclass
class _Cluster:
    entity_type: str
    normalised_keys: set = field(default_factory=set)
    mentions: list = field(default_factory=list)
    role_labels: set = field(default_factory=set)
    pages: set = field(default_factory=set)
    aliases: set = field(default_factory=set)
    representative_text: str = ""
    representative_confidence: float = 0.0
    related_email_domains: set = field(default_factory=set)
    related_url_domains: set = field(default_factory=set)


def _email_local_part_of(email_normalised):
    return email_normalised.split("@", 1)[0] if "@" in email_normalised else ""


def _email_domain_of(email_normalised):
    return email_normalised.split("@", 1)[1] if "@" in email_normalised else ""


def _url_domain_of(url_normalised):
    return url_normalised.split("/", 1)[0] if url_normalised else ""


def _person_initial_form(normalised):
    parts = normalised.split()
    if not parts:
        return ""
    return " ".join(p[0] if i < len(parts) - 1 else p for i, p in enumerate(parts))


def _person_last_name(normalised):
    parts = normalised.split()
    return parts[-1] if parts else ""


class CanonicalEntityRegistry:
    def __init__(self, *, normaliser=None, replace_contextual_aliases=True, fuzzy_threshold=88):
        self.normaliser = normaliser or EntityNormalizer()
        self.replace_contextual_aliases = replace_contextual_aliases
        self.fuzzy_threshold = fuzzy_threshold
        self._clusters = []
        self._ambiguous = []

    def ingest(self, mentions):
        primary, deferred = [], []
        for m in mentions:
            if self.normaliser.is_generic_alias(m.text, m.entity_type):
                deferred.append(m)
                continue
            primary.append(m)
        for m in primary:
            self._ingest_strong(m)
        for m in deferred:
            self._ingest_alias(m)
        self._link_cross_field()

    def sweep_text_for_known_aliases(self, text, *, page_resolver=None):
        legal_suffix_pattern = (
            r"(?:\s+(?:Ltd\.?|Limited|PLC|LLP|LLC|Inc\.?|Incorporated"
            r"|Corp\.?|Corporation|GmbH|AG|SA|NV|BV|Co\.?|Company))?"
        )
        # Single-word generic role nouns we must never broadcast-replace.
        # These words appear thousands of times in financial reports as job-
        # title qualifiers ("Senior VP", "Junior Analyst") and are NOT entity
        # names — they only get into the registry when a label-value rule
        # mistakenly tags an adjacent role/title as a PERSON.
        generic_role_words = {
            "senior", "junior", "lead", "chief", "head", "manager", "director",
            "officer", "associate", "principal", "partner", "analyst",
            "assistant", "vice", "deputy", "executive", "president",
            "client", "customer", "company", "issuer", "borrower",
        }
        added = 0
        for cluster in self._clusters:
            if cluster.entity_type not in {"PERSON", "ORG", "ADDRESS"}:
                continue
            candidates = []
            seen = set()
            for m in cluster.mentions:
                if m.is_alias or m.text in seen:
                    continue
                # Skip dangerous single-word PERSON candidates
                if cluster.entity_type == "PERSON":
                    tokens = m.text.strip().split()
                    if len(tokens) < 2:
                        continue
                    if m.text.strip().lower() in generic_role_words:
                        continue
                seen.add(m.text)
                candidates.append((m.text, False))
            if cluster.entity_type == "ORG":
                for m in cluster.mentions:
                    if m.is_alias:
                        continue
                    stem = self.normaliser.org_stem(m.normalised)
                    if stem and len(stem) >= 4:
                        title_stem = stem.title()
                        if title_stem not in seen:
                            seen.add(title_stem)
                            candidates.append((title_stem, True))
            candidates.sort(key=lambda x: -len(x[0]))
            existing_spans = {(m.span.start, m.span.end) for m in cluster.mentions}
            claimed = []
            def _intersects(a, b, claimed=claimed):
                for cs, ce in claimed:
                    if a < ce and cs < b:
                        return True
                return False
            for surface, allow_suffix in candidates:
                if not surface or len(surface.strip()) < 4:
                    continue
                if allow_suffix:
                    pat = r"\b" + re.escape(surface) + legal_suffix_pattern + r"\b"
                else:
                    pat = r"\b" + re.escape(surface) + r"\b"
                pattern = re.compile(pat, re.IGNORECASE)
                for m in pattern.finditer(text):
                    span = (m.start(), m.end())
                    if span in existing_spans:
                        continue
                    if _intersects(span[0], span[1]):
                        continue
                    if any(
                        (mm.span.start, mm.span.end) == span
                        for c2 in self._clusters
                        for mm in c2.mentions
                    ):
                        continue
                    existing_spans.add(span)
                    claimed.append(span)
                    page = None
                    if page_resolver is not None:
                        try:
                            page = page_resolver(span[0])
                        except Exception:
                            page = None
                    new_mention = Mention(
                        text=m.group(0),
                        normalised=self.normaliser.normalise(m.group(0), cluster.entity_type),
                        entity_type=cluster.entity_type,
                        confidence=0.85,
                        detector="registry.text_sweep",
                        page=page,
                        span=DocumentSpan(text=m.group(0), start=span[0], end=span[1]),
                        is_alias=True,
                    )
                    cluster.mentions.append(new_mention)
                    cluster.aliases.add(m.group(0))
                    added += 1
        return added

    def _ingest_strong(self, m):
        c = self._find_exact(m.entity_type, m.normalised)
        if c is None:
            if m.entity_type in {"PERSON", "ORG", "ADDRESS"}:
                c = self._find_fuzzy(m)
        if c is None:
            c = _Cluster(entity_type=m.entity_type)
            self._clusters.append(c)
        self._add_mention_to_cluster(c, m)

    def _ingest_alias(self, m):
        if not self.replace_contextual_aliases:
            c = _Cluster(entity_type=m.entity_type)
            self._clusters.append(c)
            self._add_mention_to_cluster(c, m, is_alias=True)
            return
        target_type = m.entity_type
        candidates = [c for c in self._clusters if c.entity_type == target_type]
        if len(candidates) == 1:
            self._add_mention_to_cluster(candidates[0], m, is_alias=True)
        elif len(candidates) == 0:
            c = _Cluster(entity_type=m.entity_type)
            self._clusters.append(c)
            self._add_mention_to_cluster(c, m, is_alias=True)
        else:
            self._ambiguous.append(m)

    def _find_exact(self, entity_type, normalised):
        for c in self._clusters:
            if c.entity_type != entity_type:
                continue
            if normalised in c.normalised_keys:
                return c
        return None

    def _find_fuzzy(self, m):
        try:
            from rapidfuzz import fuzz
            has_fuzz = True
        except ImportError:
            fuzz = None
            has_fuzz = False
        best = None
        for c in self._clusters:
            if c.entity_type != m.entity_type:
                continue
            for key in c.normalised_keys:
                score = self._score_pair(m, key, fuzz=fuzz, has_fuzz=has_fuzz)
                if score >= self.fuzzy_threshold and (best is None or score > best[1]):
                    best = (c, score)
        if best is None:
            return None
        rivals = []
        for c in self._clusters:
            if c.entity_type != m.entity_type:
                continue
            for key in c.normalised_keys:
                score = self._score_pair(m, key, fuzz=fuzz, has_fuzz=has_fuzz)
                if score >= self.fuzzy_threshold and c is not best[0]:
                    rivals.append(c)
                    break
        if rivals:
            self._ambiguous.append(m)
            return None
        return best[0]

    def _score_pair(self, m, key, *, fuzz=None, has_fuzz=False):
        if has_fuzz and fuzz is not None:
            score = int(fuzz.token_set_ratio(m.normalised, key))
        else:
            score = 0
            if m.normalised == key:
                score = 100
            elif m.normalised in key or key in m.normalised:
                score = 90
        if m.entity_type == "ORG":
            score = max(score, self._org_match_score(m.normalised, key))
        if m.entity_type == "PERSON":
            score = max(score, self._person_match_score(m.normalised, key))
        return score

    def _org_match_score(self, a, b):
        stem_a, stem_b = self.normaliser.org_stem(a), self.normaliser.org_stem(b)
        if not stem_a or not stem_b:
            return 0
        if stem_a == stem_b:
            return 100
        if stem_a in stem_b or stem_b in stem_a:
            return 92
        acro_b = self.normaliser.acronym_of(b)
        if a.upper() == acro_b and len(acro_b) >= 3:
            return 90
        return 0

    def _person_match_score(self, a, b):
        if not a or not b:
            return 0
        a_tokens, b_tokens = a.split(), b.split()
        if len(a_tokens) == 1 and a_tokens[0] == _person_last_name(b):
            return 80
        if _person_initial_form(a) == _person_initial_form(b):
            return 95
        if set(a_tokens).issubset(set(b_tokens)) or set(b_tokens).issubset(set(a_tokens)):
            return 90
        return 0

    def _link_cross_field(self):
        orgs = [c for c in self._clusters if c.entity_type == "ORG"]
        persons = [c for c in self._clusters if c.entity_type == "PERSON"]
        emails = [c for c in self._clusters if c.entity_type == "EMAIL"]
        urls = [c for c in self._clusters if c.entity_type == "URL"]

        for ec in emails:
            for key in ec.normalised_keys:
                domain = _email_domain_of(key)
                if not domain:
                    continue
                domain_stem = re.sub(r"\.[a-z]{2,}$", "", domain).replace(".", " ")
                for oc in orgs:
                    org_stem = self.normaliser.org_stem(next(iter(oc.normalised_keys), ""))
                    if not org_stem:
                        continue
                    if any(tok in domain_stem for tok in org_stem.split() if len(tok) > 2):
                        oc.related_email_domains.add(domain)

        for uc in urls:
            for key in uc.normalised_keys:
                domain = _url_domain_of(key)
                if not domain:
                    continue
                domain_stem = re.sub(r"\.[a-z]{2,}$", "", domain).replace(".", " ")
                for oc in orgs:
                    org_stem = self.normaliser.org_stem(next(iter(oc.normalised_keys), ""))
                    if not org_stem:
                        continue
                    if any(tok in domain_stem for tok in org_stem.split() if len(tok) > 2):
                        oc.related_url_domains.add(domain)

        for ec in emails:
            for key in ec.normalised_keys:
                local = _email_local_part_of(key).replace(".", " ").replace("-", " ").replace("_", " ")
                if not local:
                    continue
                for pc in persons:
                    for pkey in pc.normalised_keys:
                        if _person_last_name(pkey) and _person_last_name(pkey) in local:
                            ec.aliases.add("linked-person:" + pkey)
                            pc.aliases.add("linked-email:" + key)

    def _add_mention_to_cluster(self, cluster, m, *, is_alias=False):
        cluster.normalised_keys.add(m.normalised)
        cluster.aliases.add(m.text)
        if m.role_label:
            cluster.role_labels.add(m.role_label)
        if m.page is not None:
            cluster.pages.add(m.page)
        m_with_flag = m.model_copy(update={"is_alias": is_alias})
        cluster.mentions.append(m_with_flag)
        if (
            not is_alias
            and (
                cluster.representative_confidence < m.confidence
                or (
                    cluster.representative_confidence == m.confidence
                    and len(m.text) > len(cluster.representative_text)
                )
            )
        ):
            cluster.representative_text = m.text
            cluster.representative_confidence = m.confidence

    def canonical_entities(self):
        out = []
        per_type_counter = defaultdict(int)
        for cluster in self._clusters:
            per_type_counter[cluster.entity_type] += 1
            type_index = per_type_counter[cluster.entity_type]
            canonical_key = self._pick_canonical_key(cluster)
            entity_id = self._make_entity_id(cluster.entity_type, canonical_key, type_index)
            representative = cluster.representative_text or (
                cluster.mentions[0].text if cluster.mentions else canonical_key
            )
            out.append(
                CanonicalEntity(
                    entity_id=entity_id,
                    entity_type=cluster.entity_type,
                    canonical_original=representative,
                    normalised_key=canonical_key,
                    mentions=list(cluster.mentions),
                    aliases=sorted({a for a in cluster.aliases if a != representative}),
                    confidence=max((mm.confidence for mm in cluster.mentions), default=0.0),
                    role_labels=sorted(cluster.role_labels),
                    pages=sorted(cluster.pages),
                )
            )
        self._wire_related_ids(out)
        return out

    def ambiguous_mentions(self):
        return list(self._ambiguous)

    @staticmethod
    def _pick_canonical_key(cluster):
        keys = sorted(cluster.normalised_keys, key=lambda s: (-len(s), s))
        return keys[0] if keys else ""

    @staticmethod
    def _make_entity_id(entity_type, canonical_key, index):
        h = hashlib.sha1((entity_type + chr(1) + canonical_key).encode("utf-8")).hexdigest()
        idx_str = ("000" + str(index))[-3:]
        return entity_type + "_" + idx_str + "_" + h[:8]

    def _wire_related_ids(self, entities):
        idx_by_key = {(e.entity_type, e.normalised_key): e.entity_id for e in entities}
        for e in entities:
            related = set()
            for alias in e.aliases:
                if alias.startswith("linked-email:"):
                    key = alias.split(":", 1)[1]
                    rid = idx_by_key.get(("EMAIL", key))
                    if rid:
                        related.add(rid)
                if alias.startswith("linked-person:"):
                    key = alias.split(":", 1)[1]
                    rid = idx_by_key.get(("PERSON", key))
                    if rid:
                        related.add(rid)
            e.related_entity_ids = sorted(related)


__all__ = ["CanonicalEntityRegistry", "EntityNormalizer", "collect_mentions"]
type, canonical_key, index):
        h = hashlib.sha1((entity_type + chr(1) + canonical_key).encode("utf-8")).hexdigest()
        idx_str = ("000" + str(index))[-3:]
        return entity_type + "_" + idx_str + "_" + h[:8]

    def _wire_related_ids(self, entities):
        idx_by_key = {(e.entity_type, e.normalised_key): e.entity_id for e in entities}
        for e in entities:
            related = set()
            for alias in e.aliases:
                if alias.startswith("linked-email:"):
                    key = alias.split(":", 1)[1]
                    rid = idx_by_key.get(("EMAIL", key))
                    if rid:
                        related.add(rid)
                if alias.startswith("linked-person:"):
                    key = alias.split(":", 1)[1]
                    rid = idx_by_key.get(("PERSON", key))
                    if rid:
                        related.add(rid)
            e.related_entity_ids = sorted(related)


__all__ = ["CanonicalEntityRegistry", "EntityNormalizer", "collect_mentions"]
