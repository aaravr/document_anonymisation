"""Test-data sanitiser: convert real enterprise documents into safe synthetic test data.

Strict policy: every identifying signal — names, organisations (including
public banks), addresses, identifiers, signatures, photos, logos — is replaced
or visually redacted. Replacements preserve semantic type (a bank stays
bank-like, a person stays person-like) and remain globally consistent across
pages, chunks, and files in a batch via a persistent JSON registry.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
