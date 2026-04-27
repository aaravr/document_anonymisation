"""Tests for the regex recogniser layer.

Each recogniser is tested in isolation so failures point straight at the
faulty pattern. The mod-97 validators are exercised with both valid and
invalid inputs.
"""
from __future__ import annotations

import pytest

from idp_anonymiser.detection.regex_recognisers import (
    detect_emails,
    detect_iban,
    detect_lei,
    detect_phones,
    detect_swift_bic,
    detect_uk_postcodes,
    detect_urls,
    detect_company_no,
    detect_us_ssn,
)


class TestEmail:
    def test_finds_simple_email(self):
        d = detect_emails("Email: john.smith@acme.com please.")
        assert len(d) == 1
        assert d[0].text == "john.smith@acme.com"
        assert d[0].entity_type == "EMAIL"
        assert d[0].confidence >= 0.9

    def test_finds_multiple(self):
        d = detect_emails("a@b.co and c@d.org")
        assert {x.text for x in d} == {"a@b.co", "c@d.org"}

    def test_ignores_non_email(self):
        d = detect_emails("not an email: foo at bar dot com")
        assert d == []


class TestUkPostcode:
    @pytest.mark.parametrize("pc", ["SW1A 1AA", "EC1A 1BB", "M1 1AE", "BA1 2HP"])
    def test_finds_valid_uk_postcode(self, pc):
        d = detect_uk_postcodes(f"Address: 1 X Street, London {pc}")
        assert len(d) == 1
        assert d[0].text.replace(" ", "") == pc.replace(" ", "")

    def test_no_postcode(self):
        assert detect_uk_postcodes("nothing here") == []


class TestLei:
    def test_finds_valid_lei(self):
        # Generated valid LEI for testing
        valid = "529900T8BM49AURSDO55"
        d = detect_lei(f"LEI: {valid}")
        assert d, "Should detect LEI"
        # The high-confidence detection must have valid checksum
        high = [x for x in d if x.confidence >= 0.9]
        assert high, f"Expected a high-confidence LEI detection for {valid}"
        assert high[0].text == valid

    def test_lei_invalid_checksum_low_confidence(self):
        invalid = "5493001KJTIIGC8Y1R99"  # bad check digits
        d = detect_lei(f"LEI: {invalid}")
        assert d, "Should still emit a candidate"
        assert all(x.confidence <= 0.6 for x in d)


class TestIban:
    def test_finds_valid_iban(self):
        valid = "GB82WEST12345698765432"
        d = detect_iban(f"IBAN: {valid}")
        assert d
        good = [x for x in d if x.confidence >= 0.95]
        assert good, "Should pass mod-97 check"

    def test_iban_with_spaces(self):
        spaced = "GB82 WEST 1234 5698 7654 32"
        d = detect_iban(f"IBAN: {spaced}")
        assert d


class TestSwift:
    def test_finds_swift(self):
        d = detect_swift_bic("BIC: BARCGB22XXX please.")
        assert d
        assert d[0].text == "BARCGB22XXX"

    def test_finds_8char_swift(self):
        d = detect_swift_bic("BIC: BARCGB22 ok")
        assert d


class TestUrls:
    def test_finds_https(self):
        d = detect_urls("see https://www.example.com/path?x=1 today")
        assert d and d[0].text.startswith("https://")

    def test_finds_www(self):
        d = detect_urls("see www.example.com today")
        assert d and "example.com" in d[0].text


class TestCompanyNo:
    def test_8_digit(self):
        d = detect_company_no("Company No: 12345678")
        assert d


class TestSsn:
    def test_finds_ssn(self):
        d = detect_us_ssn("SSN: 123-45-6789")
        assert d
        assert d[0].entity_type == "NATIONAL_ID"


class TestPhone:
    def test_finds_phone(self):
        d = detect_phones("Phone: +44 20 7946 0958")
        assert d, "phone should detect a UK number"
