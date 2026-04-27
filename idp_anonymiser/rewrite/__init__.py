"""Per-format rewriters that apply a plan back to a document."""
from __future__ import annotations

from idp_anonymiser.rewrite.text_rewriter import rewrite_text
from idp_anonymiser.rewrite.json_rewriter import rewrite_json

__all__ = ["rewrite_text", "rewrite_json"]
