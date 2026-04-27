from sanitiser.detect.regex_recognisers import detect_regex
from sanitiser.detect.label_value import detect_label_values
from sanitiser.resolve.resolver import EntityResolver
from sanitiser.state import Detection, Span


def _det(t, et, s, e, page=0):
    return Detection(text=t, entity_type=et, confidence=0.95, detector="x",
                     span=Span(text=t, start=s, end=e, page=page))


def test_org_full_and_abbrev_link():
    text = ("Canadian Imperial Bank of Commerce announced a deal. "
            "CIBC confirmed the announcement.")
    dets = [
        _det("Canadian Imperial Bank of Commerce", "ORG", 0, 35),
        _det("CIBC", "ORG", 60, 64),
    ]
    r = EntityResolver()
    clusters = r.resolve(dets, [text])
    org_clusters = [c for c in clusters if c.entity_type == "ORG"]
    assert len(org_clusters) == 1
    c = org_clusters[0]
    assert "CIBC" in c.normalised_keys or any(k.upper() == "CIBC" for k in c.normalised_keys)


def test_person_full_then_surname_only():
    text = "Victor Dodig is the CEO. Earlier today, Mr Dodig spoke to investors. Dodig steps down."
    dets = [
        _det("Victor Dodig", "PERSON", 0, 12),
        _det("Mr Dodig", "PERSON", 40, 48),
        _det("Dodig", "PERSON", 70, 75),
    ]
    r = EntityResolver()
    clusters = r.resolve(dets, [text])
    person_clusters = [c for c in clusters if c.entity_type == "PERSON"]
    assert len(person_clusters) == 1


def test_two_smiths_keep_separate():
    text = "John Smith presented to Andrew Smith."
    dets = [
        _det("John Smith", "PERSON", 0, 10),
        _det("Andrew Smith", "PERSON", 24, 36),
    ]
    r = EntityResolver()
    clusters = r.resolve(dets, [text])
    assert sum(1 for c in clusters if c.entity_type == "PERSON") == 2


def test_explicit_full_name_paren_abbrev():
    text = "Acme Holdings International (AHI) announced that AHI will continue trading."
    dets = [
        _det("Acme Holdings International", "ORG", 0, 27),
        _det("AHI", "ORG", 29, 32),
        _det("AHI", "ORG", 49, 52),
    ]
    r = EntityResolver()
    clusters = r.resolve(dets, [text])
    org_clusters = [c for c in clusters if c.entity_type == "ORG"]
    assert len(org_clusters) == 1
