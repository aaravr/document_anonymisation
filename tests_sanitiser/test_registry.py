from sanitiser.replace.registry import Registry
from sanitiser.resolve.resolver import _Cluster
from sanitiser.state import Detection, Span


def _det(t, et, s, e):
    return Detection(text=t, entity_type=et, confidence=0.95, detector="x",
                     span=Span(text=t, start=s, end=e, page=0))


def test_org_full_and_abbreviation_pair_is_replaced_consistently():
    cluster = _Cluster(entity_type="ORG", full_name="Canadian Imperial Bank of Commerce",
                       is_org_full=True, abbreviation="CIBC")
    cluster.normalised_keys = {"canadian imperial bank of commerce", "cibc"}
    cluster.detections = [
        _det("Canadian Imperial Bank of Commerce", "ORG", 0, 35),
        _det("CIBC", "ORG", 50, 54),
    ]
    cluster.confidence = 0.95
    reg = Registry(seed=42)
    ent = reg.replacement_for_cluster(cluster)
    assert ent.replacement_full_name and ent.replacement_full_name != cluster.full_name
    assert ent.replacement_abbreviation
    # Determinism: second call returns same entity
    ent2 = reg.replacement_for_cluster(cluster)
    assert ent.canonical_id == ent2.canonical_id


def test_person_variants_in_replacement():
    cluster = _Cluster(entity_type="PERSON", full_name="Victor Dodig")
    cluster.normalised_keys = {"victor dodig", "dodig", "mr dodig"}
    cluster.detections = [
        _det("Victor Dodig", "PERSON", 0, 12),
        _det("Mr Dodig", "PERSON", 40, 48),
        _det("Dodig", "PERSON", 70, 75),
    ]
    cluster.confidence = 0.95
    reg = Registry(seed=42)
    ent = reg.replacement_for_cluster(cluster)
    # Replacement variants should include full name, "Mr <surname>", and surname-only
    assert ent.replacement_full_name
    parts = ent.replacement_full_name.split()
    assert any(v == parts[-1] for v in ent.replacement_variants), ent.replacement_variants
    assert any(v.startswith("Mr ") for v in ent.replacement_variants), ent.replacement_variants


def test_persistence_round_trip(tmp_path):
    cluster = _Cluster(entity_type="PERSON", full_name="John Smith")
    cluster.normalised_keys = {"john smith"}
    cluster.detections = [_det("John Smith", "PERSON", 0, 10)]
    cluster.confidence = 0.99
    reg = Registry(seed=42)
    ent = reg.replacement_for_cluster(cluster)
    p = tmp_path / "map.json"
    reg.save(p)
    reg2 = Registry.load(p)
    # Looking up the same normalised key returns the same canonical id.
    assert reg2._by_normalised[("PERSON", "john smith")] == ent.canonical_id
