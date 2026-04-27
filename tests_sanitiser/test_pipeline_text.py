import json
from pathlib import Path
from sanitiser.config import Profile, SeedList, VisualConfig, QAConfig
from sanitiser.pipeline import sanitise_document


def _profile_for_test() -> Profile:
    return Profile(
        enable_spacy=False, fail_if_spacy_unavailable=False,
        synthetic_replacements=True, replace_locations=False,
        visual_elements=VisualConfig(redact_images=False),
        qa=QAConfig(fail_on_remaining_regex_pii=False),
        seed=42,
    )


def test_full_pipeline_with_seed_list(tmp_path):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Canadian Imperial Bank of Commerce announced that Victor Dodig would retire as President and CEO. "
        "CIBC confirmed that Mr Dodig will remain until the transition is complete. "
        "Email: victor.dodig@cibc.com\n"
        "Account Number: 12345678\n"
        "IBAN: GB82WEST12345698765432\n",
        encoding="utf-8",
    )
    seed = SeedList(
        persons=["Victor Dodig"],
        orgs=[{"name": "Canadian Imperial Bank of Commerce", "abbreviation": "CIBC"}],
    )
    summary = sanitise_document(src, tmp_path / "out", profile=_profile_for_test(),
                                  seed_list=seed)
    out_dir = tmp_path / "out" / "doc"
    sanitised = (out_dir / "sanitised.txt").read_text(encoding="utf-8")
    # All originals removed
    for needle in ["Victor Dodig", "CIBC", "Canadian Imperial Bank of Commerce", "Mr Dodig"]:
        assert needle not in sanitised, "leaked: " + needle + " in " + sanitised
    # Replacement map has both full name and abbreviation
    rmap = json.loads((out_dir / "replacement_map.json").read_text())
    org_entries = [v for v in rmap.values() if v["entity_type"] == "ORG"]
    assert org_entries
    assert any("original_abbreviation" in e for e in org_entries)


def test_strict_profile_fails_when_spacy_unavailable(tmp_path):
    """When fail_if_spacy_unavailable=True and the model isn't installed,
    the pipeline must raise rather than silently proceed."""
    from sanitiser.detect.spacy_loader import SpacyUnavailableError
    src = tmp_path / "doc.txt"
    src.write_text("hello world", encoding="utf-8")
    prof = Profile(enable_spacy=True, fail_if_spacy_unavailable=True,
                    spacy_model="totally_made_up_model")
    try:
        sanitise_document(src, tmp_path / "out", profile=prof)
    except SpacyUnavailableError:
        return
    raise AssertionError("expected SpacyUnavailableError")
