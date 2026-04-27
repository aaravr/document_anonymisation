"""Audit report shape tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from idp_anonymiser.agent import AnonymisationAgent, AnonymisationRequest


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    p = tmp_path / "kyc.txt"
    p.write_text(
        "Client Name: Acme Holdings Ltd\n"
        "Director: John Smith\n"
        "Email: john.smith@acme.com\n"
        "Company No: 12345678\n",
        encoding="utf-8",
    )
    return p


def _run(input_path: Path, output_dir: Path, **kwargs) -> dict:
    request = AnonymisationRequest(
        document_id="audit-test-001",
        input_path=str(input_path),
        output_dir=str(output_dir),
        config_profile="test_mode",
        anonymisation_mode="synthetic",
        **kwargs,
    )
    result = AnonymisationAgent().run(request)
    return json.loads(Path(result.audit_report_path).read_text(encoding="utf-8"))


class TestAuditReport:
    def test_required_top_level_keys(self, sample, tmp_path):
        report = _run(sample, tmp_path / "out")
        for key in (
            "schema_version",
            "tool",
            "tool_version",
            "generated_at",
            "document",
            "summary",
            "detectors_used",
            "detection_breakdown",
            "replacement_breakdown",
            "validation",
            "ambiguous_mentions",
            "redactions",
        ):
            assert key in report

    def test_summary_counts(self, sample, tmp_path):
        report = _run(sample, tmp_path / "out")
        s = report["summary"]
        assert s["total_detections"] >= 4
        assert s["unique_canonical_entities"] >= 4
        assert s["total_replacements"] >= 4

    def test_redactions_have_no_originals_by_default(self, sample, tmp_path):
        report = _run(sample, tmp_path / "out", debug_include_originals=False)
        for r in report["redactions"]:
            assert "canonical_original" not in r
            assert "aliases" not in r
            # Replacement value is fine to include.
            assert "replacement" in r

    def test_redactions_have_originals_when_debug(self, sample, tmp_path):
        report = _run(sample, tmp_path / "out", debug_include_originals=True)
        with_orig = [r for r in report["redactions"] if "canonical_original" in r]
        assert with_orig

    def test_validation_block_passes_for_clean_input(self, sample, tmp_path):
        report = _run(sample, tmp_path / "out")
        v = report["validation"]
        assert "passed" in v
        assert "quality_score" in v
        assert v["original_values_remaining_count"] == 0
