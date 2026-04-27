"""Tests for the replacement generator and supporting generators."""
from __future__ import annotations

import pytest

from idp_anonymiser.agent.state import (
    AnonymisationRequest,
    CanonicalEntity,
    Detection,
    DocumentSpan,
    Mention,
)
from idp_anonymiser.replacement import id_generator
from idp_anonymiser.replacement.generator import ReplacementGenerator
from idp_anonymiser.replacement.org_generator import generate_org_name, split_suffix


def _request(**kwargs) -> AnonymisationRequest:
    return AnonymisationRequest(
        document_id="doc-test",
        input_path="/tmp/x.txt",
        output_dir="/tmp/out",
        **kwargs,
    )


def _make_canonical(et: str, original: str, normalised: str | None = None) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=f"{et}_001_abcd1234",
        entity_type=et,
        canonical_original=original,
        normalised_key=normalised or original.lower(),
        mentions=[
            Mention(
                text=original,
                normalised=normalised or original.lower(),
                entity_type=et,
                confidence=0.95,
                detector="test",
                span=DocumentSpan(text=original, start=0, end=len(original)),
            )
        ],
    )


class TestOrgGenerator:
    def test_preserves_ltd_suffix(self):
        out = generate_org_name("Acme Holdings Ltd")
        assert out.endswith("Ltd")

    def test_preserves_limited_suffix(self):
        out = generate_org_name("Acme Holdings Limited")
        assert out.endswith("Limited")

    def test_preserves_plc(self):
        assert generate_org_name("Acme Holdings PLC").endswith("PLC")

    def test_preserves_llp(self):
        assert generate_org_name("Some Partners LLP").endswith("LLP")

    def test_deterministic(self):
        a = generate_org_name("Acme Holdings Ltd")
        b = generate_org_name("Acme Holdings Ltd")
        assert a == b

    def test_split_suffix(self):
        assert split_suffix("Acme Holdings Limited") == ("Acme Holdings", "Limited")
        assert split_suffix("Acme Holdings") == ("Acme Holdings", None)


class TestIdGenerators:
    def test_iban_is_valid_mod97(self):
        from idp_anonymiser.detection.regex_recognisers import _iban_mod97_ok

        for original in ("GB82WEST12345698765432", "DE89370400440532013000"):
            new = id_generator.generate_iban(original)
            assert _iban_mod97_ok(new), f"Synthetic IBAN should be mod-97 valid: {new}"
            assert new[:2] == original[:2]  # country preserved
            assert len(new) == len(original.replace(" ", ""))

    def test_lei_is_valid_mod97(self):
        from idp_anonymiser.detection.regex_recognisers import _lei_mod97_ok

        new = id_generator.generate_lei("529900T8BM49AURSDO55")
        assert len(new) == 20
        assert _lei_mod97_ok(new)

    def test_swift_bic_shape(self):
        new = id_generator.generate_swift_bic("BARCGB22XXX")
        assert len(new) in (8, 11)
        assert new[:4].isalpha()

    def test_email_couples_with_person_and_org(self):
        out = id_generator.generate_email(
            "john.smith@acme.com",
            person_name="Michael Brown",
            org_name="Redwood Trading Ltd",
        )
        assert "michael.brown" in out
        assert "redwood-trading" in out
        assert out.endswith(".example")

    def test_email_uses_reserved_domain_when_no_org(self):
        out = id_generator.generate_email("john.smith@acme.com")
        assert any(out.endswith(d) for d in (".example", "example", "test.invalid", ".org", ".com"))

    def test_phone_format(self):
        out = id_generator.generate_phone("+44 20 7946 0958")
        assert out.startswith("+44")

    def test_company_reg_no_preserves_digits(self):
        out = id_generator.generate_company_reg_no("12345678")
        assert out.isdigit() and len(out) == 8

    def test_date_dob_format(self):
        out = id_generator.generate_date("12/05/1985", dob=True)
        assert "/" in out


class TestReplacementGenerator:
    def test_deterministic_within_document(self):
        gen = ReplacementGenerator()
        req = _request()
        ent = _make_canonical("ORG", "Acme Holdings Ltd", "acme holdings limited")
        plan_a = gen.generate_from_canonical([ent], req)
        plan_b = gen.generate_from_canonical([ent], req)
        assert plan_a[0].replacement_value == plan_b[0].replacement_value

    def test_synthetic_org_preserves_suffix(self):
        gen = ReplacementGenerator()
        req = _request()
        ent = _make_canonical("ORG", "Acme Holdings Ltd", "acme holdings limited")
        out = gen.generate_from_canonical([ent], req)
        assert out[0].replacement_value.endswith("Ltd")

    def test_mask_mode(self):
        gen = ReplacementGenerator()
        req = _request(anonymisation_mode="mask")
        ent = _make_canonical("PERSON", "John Smith", "john smith")
        out = gen.generate_from_canonical([ent], req)
        assert out[0].replacement_value.startswith("[PERSON_")
        assert out[0].strategy == "mask"

    def test_hybrid_masks_persons_but_synthesises_iban(self):
        gen = ReplacementGenerator()
        req = _request(anonymisation_mode="hybrid")
        person = _make_canonical("PERSON", "John Smith", "john smith")
        iban = _make_canonical("IBAN", "GB82WEST12345698765432", "gb82west12345698765432")
        out = gen.generate_from_canonical([person, iban], req)
        person_rep = next(r for r in out if r.entity_type == "PERSON")
        iban_rep = next(r for r in out if r.entity_type == "IBAN")
        assert person_rep.strategy == "mask"
        assert iban_rep.strategy == "synthetic"
        assert iban_rep.replacement_value.startswith("GB")

    def test_email_couples_with_org_in_canonical(self):
        gen = ReplacementGenerator()
        req = _request()
        org = _make_canonical("ORG", "Acme Holdings Ltd", "acme holdings limited")
        person = _make_canonical("PERSON", "John Smith", "john smith")
        email = CanonicalEntity(
            entity_id="EMAIL_001_x",
            entity_type="EMAIL",
            canonical_original="john.smith@acmeholdings.com",
            normalised_key="john.smith@acmeholdings.com",
            related_entity_ids=[org.entity_id, person.entity_id],
            mentions=[
                Mention(
                    text="john.smith@acmeholdings.com",
                    normalised="john.smith@acmeholdings.com",
                    entity_type="EMAIL",
                    confidence=0.99,
                    detector="test",
                    span=DocumentSpan(text="john.smith@acmeholdings.com", start=0, end=27),
                )
            ],
        )
        out = gen.generate_from_canonical([org, person, email], req)
        email_rep = next(r for r in out if r.entity_type == "EMAIL")
        assert ".example" in email_rep.replacement_value
        # The email should reuse the new person+org replacements
        person_rep = next(r for r in out if r.entity_type == "PERSON").replacement_value
        org_rep = next(r for r in out if r.entity_type == "ORG").replacement_value
        from idp_anonymiser.replacement.id_generator import _slug

        assert _slug(person_rep).replace("-", ".") in email_rep.replacement_value
        assert _slug(org_rep) in email_rep.replacement_value

    def test_preserve_casing_uppercase(self):
        gen = ReplacementGenerator()
        req = _request()
        ent = _make_canonical("ORG", "ACME HOLDINGS LTD", "acme holdings limited")
        out = gen.generate_from_canonical([ent], req)
        assert out[0].replacement_value.isupper()
