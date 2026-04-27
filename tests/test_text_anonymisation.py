"""End-to-end tests for TXT and JSON anonymisation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from idp_anonymiser.agent import AnonymisationAgent, AnonymisationRequest


SAMPLE_TXT = """\
Client Name: Acme Holdings Ltd
Director: John Smith
Email: john.smith@acme.com
Company No: 12345678
Address: 10 King Street, London SW1A 1AA
LEI: 5493001KJTIIGC8Y1R12
"""


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_txt(workdir: Path) -> Path:
    p = workdir / "sample.txt"
    p.write_text(SAMPLE_TXT, encoding="utf-8")
    return p


def _run(input_path: Path, output_dir: Path, profile: str = "test_mode") -> dict:
    request = AnonymisationRequest(
        document_id="test-doc-001",
        input_path=str(input_path),
        output_dir=str(output_dir),
        config_profile=profile,
        anonymisation_mode="synthetic",
    )
    agent = AnonymisationAgent()
    result = agent.run(request)
    return {
        "result": result,
        "anonymised_text": Path(result.output_text_path).read_text(encoding="utf-8")
        if result.output_text_path
        else "",
        "audit": json.loads(Path(result.audit_report_path).read_text(encoding="utf-8")),
        "plan": json.loads(Path(result.plan_path).read_text(encoding="utf-8")),
    }


class TestTextAnonymisation:
    def test_runs_end_to_end(self, sample_txt, workdir):
        out = _run(sample_txt, workdir / "out")
        assert out["result"].status in {"ok", "warning"}
        assert out["anonymised_text"]
        assert "Client Name:" in out["anonymised_text"]  # structure preserved

    def test_no_originals_remain(self, sample_txt, workdir):
        out = _run(sample_txt, workdir / "out")
        a = out["anonymised_text"]
        # Each original surface form must be gone
        for needle in [
            "Acme Holdings Ltd",
            "John Smith",
            "john.smith@acme.com",
            "12345678",
            "10 King Street",
            "SW1A 1AA",
            "5493001KJTIIGC8Y1R12",
        ]:
            assert needle not in a, f"Original value leaked: {needle!r}"

    def test_org_suffix_preserved(self, sample_txt, workdir):
        out = _run(sample_txt, workdir / "out")
        a = out["anonymised_text"]
        # The first line should still end in "Ltd"
        first_line = next(line for line in a.splitlines() if line.startswith("Client Name:"))
        assert first_line.strip().endswith("Ltd")

    def test_email_replaced_with_safe_domain(self, sample_txt, workdir):
        out = _run(sample_txt, workdir / "out")
        a = out["anonymised_text"]
        # The email line still says Email:
        email_line = next(line for line in a.splitlines() if line.startswith("Email:"))
        # Should be a reserved test domain
        assert any(d in email_line for d in (".example", ".test", "test.invalid", ".org"))
        assert "acme.com" not in email_line

    def test_lei_replaced_with_valid_synthetic(self, sample_txt, workdir):
        from idp_anonymiser.detection.regex_recognisers import _lei_mod97_ok

        out = _run(sample_txt, workdir / "out")
        a = out["anonymised_text"]
        # Find the LEI replacement on the LEI line
        lei_line = next(line for line in a.splitlines() if line.startswith("LEI:"))
        new_lei = lei_line.split(":", 1)[1].strip()
        assert len(new_lei) == 20
        assert _lei_mod97_ok(new_lei)

    def test_audit_contains_canonical_entities(self, sample_txt, workdir):
        out = _run(sample_txt, workdir / "out")
        audit = out["audit"]
        assert audit["summary"]["unique_canonical_entities"] >= 5
        assert "redactions" in audit

    def test_audit_excludes_originals_by_default(self, sample_txt, workdir):
        request = AnonymisationRequest(
            document_id="test-doc-002",
            input_path=str(sample_txt),
            output_dir=str(workdir / "out"),
            config_profile="kyc_default",
            debug_include_originals=False,
        )
        result = AnonymisationAgent().run(request)
        audit = json.loads(Path(result.audit_report_path).read_text())
        for r in audit["redactions"]:
            assert "canonical_original" not in r

    def test_deterministic_within_document(self, sample_txt, workdir):
        out1 = _run(sample_txt, workdir / "out1")
        out2 = _run(sample_txt, workdir / "out2")
        assert out1["anonymised_text"] == out2["anonymised_text"]


class TestJsonAnonymisation:
    def test_json_anonymises_strings(self, workdir):
        p = workdir / "client.json"
        data = {
            "client": {
                "name": "Acme Holdings Ltd",
                "director": "John Smith",
                "contact": {"email": "john.smith@acme.com"},
            },
            "ids": {"lei": "529900T8BM49AURSDO55"},
        }
        p.write_text(json.dumps(data), encoding="utf-8")

        out = _run(p, workdir / "out")
        anonymised = json.loads(
            Path(out["result"].output_document_path).read_text(encoding="utf-8")
        )
        assert anonymised["client"]["name"] != "Acme Holdings Ltd"
        assert anonymised["client"]["name"].endswith("Ltd")
        assert "@acme.com" not in anonymised["client"]["contact"]["email"]


class TestComplexConsistency:
    def test_org_referenced_three_ways_collapses_to_one_replacement(self, workdir):
        text = (
            "Section 1.\n"
            "Client Name: Acme Holdings Limited\n"
            "Section 2.\n"
            "Description: ACME HOLDINGS LTD undertakes the following...\n"
            "Section 3.\n"
            "Reference to Acme Holdings as the issuer.\n"
        )
        p = workdir / "long.txt"
        p.write_text(text, encoding="utf-8")
        out = _run(p, workdir / "out")
        a = out["anonymised_text"]
        # Only one synthetic org name should appear; the three originals should be gone.
        assert "Acme Holdings" not in a
        # The audit must report a single canonical org with multiple aliases.
        org_entries = [
            r for r in out["audit"]["redactions"] if r["entity_type"] == "ORG"
        ]
        assert len(org_entries) == 1
        assert org_entries[0]["mention_count"] >= 3

    def test_email_matches_replacement_org_domain(self, workdir):
        text = (
            "Client Name: Acme Holdings Limited\n"
            "Director: John Smith\n"
            "Email: john.smith@acmeholdings.com\n"
        )
        p = workdir / "consistent.txt"
        p.write_text(text, encoding="utf-8")
        out = _run(p, workdir / "out")
        a = out["anonymised_text"]
        org_line = next(l for l in a.splitlines() if l.startswith("Client Name:"))
        email_line = next(l for l in a.splitlines() if l.startswith("Email:"))
        # Strip the suffix from the synthetic org and check it appears in email domain.
        org_name = org_line.split(":", 1)[1].strip()
        # Drop legal suffix
        from idp_anonymiser.replacement.org_generator import split_suffix
        from idp_anonymiser.replacement.id_generator import _slug

        stem, _ = split_suffix(org_name)
        slug = _slug(stem)
        assert slug in email_line
