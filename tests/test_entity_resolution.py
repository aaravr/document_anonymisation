"""Tests for entity resolution and the canonical entity registry."""
from __future__ import annotations

from idp_anonymiser.agent.state import Detection, DocumentSpan
from idp_anonymiser.detection.canonical_registry import (
    CanonicalEntityRegistry,
    EntityNormalizer,
    collect_mentions,
)
from idp_anonymiser.detection.entity_resolution import (
    canonicalise,
    deduplicate_overlapping,
    resolve_entities,
)


def _det(text: str, et: str, start: int, end: int, conf: float = 0.9, det: str = "t") -> Detection:
    return Detection(
        text=text,
        entity_type=et,
        confidence=conf,
        detector=det,
        span=DocumentSpan(text=text, start=start, end=end),
    )


class TestCanonicalise:
    def test_normalises_org(self):
        assert canonicalise("Acme Holdings  Ltd", "ORG").lower().endswith("ltd")

    def test_normalises_iban_strips_spaces(self):
        assert canonicalise("GB82 WEST 1234 5698 7654 32", "IBAN") == "gb82west12345698765432"

    def test_normalises_phone_keeps_plus(self):
        assert canonicalise("+44 (20) 7946-0958", "PHONE") == "+442079460958"


class TestDeduplicate:
    def test_keeps_higher_confidence_overlap(self):
        a = _det("Smith", "PERSON", 0, 5, 0.7)
        b = _det("Smith", "PERSON", 0, 5, 0.95)
        out = deduplicate_overlapping([a, b])
        assert len(out) == 1
        assert out[0].confidence == 0.95

    def test_keeps_disjoint(self):
        a = _det("Smith", "PERSON", 0, 5, 0.9)
        b = _det("Acme Ltd", "ORG", 10, 18, 0.9)
        out = deduplicate_overlapping([a, b])
        assert len(out) == 2


class TestEntityNormalizer:
    def test_strips_titles_from_person(self):
        n = EntityNormalizer()
        assert n.normalise("Mr John William Smith", "PERSON") == "john william smith"

    def test_drops_initials_dots(self):
        n = EntityNormalizer()
        assert n.normalise("J. W. Smith", "PERSON") == "j w smith"

    def test_canonical_legal_suffix(self):
        n = EntityNormalizer()
        a = n.normalise("Acme Holdings Ltd", "ORG")
        b = n.normalise("Acme Holdings Limited", "ORG")
        assert a == b

    def test_email_lowercased(self):
        n = EntityNormalizer()
        assert n.normalise("John.Smith@ACME.com", "EMAIL") == "john.smith@acme.com"

    def test_url_strip_scheme(self):
        n = EntityNormalizer()
        assert n.normalise("https://www.acme.com/", "URL") == "acme.com"

    def test_acronym_of_org(self):
        n = EntityNormalizer()
        assert n.acronym_of("acme holdings limited") == "AH"


class TestRegistryClustering:
    def test_merges_org_variants(self):
        norm = EntityNormalizer()
        mentions = collect_mentions(
            [
                _det("Acme Holdings Limited", "ORG", 0, 21),
                _det("ACME HOLDINGS LTD", "ORG", 30, 47),
                _det("Acme Holdings", "ORG", 60, 73),
            ]
        )
        reg = CanonicalEntityRegistry(normaliser=norm, replace_contextual_aliases=True)
        reg.ingest(mentions)
        canonical = reg.canonical_entities()
        orgs = [c for c in canonical if c.entity_type == "ORG"]
        assert len(orgs) == 1, f"Expected 1 org cluster, got {len(orgs)}: {[c.aliases for c in orgs]}"
        assert len(orgs[0].mentions) == 3

    def test_links_generic_alias_when_unambiguous(self):
        mentions = collect_mentions(
            [
                _det("Acme Holdings Ltd", "ORG", 0, 17),
                _det("the Client", "ORG", 100, 110, conf=0.5, det="alias"),
            ]
        )
        reg = CanonicalEntityRegistry(replace_contextual_aliases=True)
        reg.ingest(mentions)
        canonical = reg.canonical_entities()
        orgs = [c for c in canonical if c.entity_type == "ORG"]
        assert len(orgs) == 1
        # alias should have been merged
        assert any(m.is_alias for m in orgs[0].mentions)

    def test_does_not_link_generic_alias_when_ambiguous(self):
        mentions = collect_mentions(
            [
                _det("Acme Holdings Ltd", "ORG", 0, 17),
                _det("Beta Trading PLC", "ORG", 30, 46),
                _det("the Client", "ORG", 100, 110, det="alias"),
            ]
        )
        reg = CanonicalEntityRegistry(replace_contextual_aliases=True)
        reg.ingest(mentions)
        ambiguous = reg.ambiguous_mentions()
        assert any(m.text == "the Client" for m in ambiguous)

    def test_disabled_alias_replacement(self):
        mentions = collect_mentions(
            [
                _det("Acme Holdings Ltd", "ORG", 0, 17),
                _det("the Client", "ORG", 100, 110, det="alias"),
            ]
        )
        reg = CanonicalEntityRegistry(replace_contextual_aliases=False)
        reg.ingest(mentions)
        canonical = reg.canonical_entities()
        orgs = [c for c in canonical if c.entity_type == "ORG"]
        # Two clusters because the alias is kept separate
        assert len(orgs) == 2

    def test_person_initials_form_links(self):
        mentions = collect_mentions(
            [
                _det("John William Smith", "PERSON", 0, 18),
                _det("J. W. Smith", "PERSON", 40, 51),
            ]
        )
        reg = CanonicalEntityRegistry()
        reg.ingest(mentions)
        canonical = reg.canonical_entities()
        persons = [c for c in canonical if c.entity_type == "PERSON"]
        assert len(persons) == 1
        assert len(persons[0].mentions) == 2

    def test_surname_only_ambiguous_with_two_smiths(self):
        mentions = collect_mentions(
            [
                _det("John Smith", "PERSON", 0, 10),
                _det("Andrew Smith", "PERSON", 30, 42),
                _det("Mr Smith", "PERSON", 80, 88),
            ]
        )
        reg = CanonicalEntityRegistry()
        reg.ingest(mentions)
        canonical = reg.canonical_entities()
        persons = [c for c in canonical if c.entity_type == "PERSON"]
        # Two distinct persons, 'Mr Smith' should not be silently merged into one
        # of them - either flagged ambiguous or kept as own cluster.
        ambiguous = reg.ambiguous_mentions()
        merged_into_one_of_them = any(
            any(m.text == "Mr Smith" for m in p.mentions) for p in persons
        )
        was_flagged_or_separate = (
            any(m.text == "Mr Smith" for m in ambiguous)
            or len(persons) >= 3
        )
        assert was_flagged_or_separate or not merged_into_one_of_them

    def test_resolve_entities_with_registry(self):
        detections = [
            _det("Acme Holdings Limited", "ORG", 0, 21),
            _det("Acme Holdings Ltd", "ORG", 30, 47),
            _det("John Smith", "PERSON", 60, 70),
        ]
        out = resolve_entities(detections, use_registry=True)
        types = sorted({e.entity_type for e in out})
        assert types == ["ORG", "PERSON"]
