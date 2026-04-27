from sanitiser.detect.regex_recognisers import detect_regex


def test_email_phone_url():
    text = "Contact us at john.smith@example.com or +44 20 7946 0958. See https://www.acme.com."
    dets = detect_regex(text, page=0, chunk=0)
    types = {d.entity_type for d in dets}
    assert {"EMAIL", "PHONE", "URL"}.issubset(types)


def test_iban_swift_sort():
    text = "IBAN GB82WEST12345698765432 BIC BARCGB22XXX sort 12-34-56"
    dets = detect_regex(text, page=0, chunk=0)
    types = {d.entity_type for d in dets}
    assert "IBAN" in types
    assert "SWIFT_BIC" in types
    assert "SORT_CODE" in types
