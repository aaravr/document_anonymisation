"""Extractors for plain text, JSON, and CSV inputs.

These produce :class:`ExtractedDocument` objects with ``flat_text`` populated
so detection can run uniformly across formats. JSON keeps a parsed copy in
``json_data``, CSV keeps a ``pandas.DataFrame`` in ``csv_dataframe``.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from idp_anonymiser.document.layout_model import ExtractedDocument, ExtractedTextBlock
from idp_anonymiser.document.loader import LoadedDocument


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def _decode_text(raw: bytes | None, path: Path) -> str:
    if raw is None:
        raw = path.read_bytes()
    # Try utf-8 first then latin-1 fallback. Avoid silent loss.
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_txt(loaded: LoadedDocument) -> ExtractedDocument:
    text = _decode_text(loaded.raw_bytes, loaded.path)
    blocks = [ExtractedTextBlock(text=text, start=0, end=len(text), block_id="txt:0")]
    return ExtractedDocument(flat_text=text, blocks=blocks, metadata={"format": "txt"})


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _flatten_json(node: Any, path: str = "$") -> list[tuple[str, str]]:
    """Walk a JSON tree and return (json_path, str_value) for each leaf string.

    Numbers/booleans/None are not flattened — we only run textual detection on
    string leaves. The rewriter still descends through the full tree.
    """
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_flatten_json(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_flatten_json(v, f"{path}[{i}]"))
    elif isinstance(node, str):
        out.append((path, node))
    return out


_JSON_KEY_TO_LABEL: dict[str, str] = {
    # Keys that strongly suggest a labelled value. The label name maps onto
    # the catalogue used by detection.label_value_detector so the standard
    # label-value rules fire on JSON inputs.
    "name": "Client Name",
    "client_name": "Client Name",
    "client": "Client Name",
    "company_name": "Company Name",
    "legal_entity_name": "Legal Entity Name",
    "entity_name": "Entity Name",
    "trading_name": "Trading Name",
    "director": "Director",
    "directors": "Directors",
    "ubo": "UBO",
    "shareholder": "Shareholder",
    "address": "Address",
    "registered_address": "Registered Address",
    "office_address": "Office Address",
    "dob": "DOB",
    "date_of_birth": "Date of Birth",
    "passport": "Passport",
    "passport_number": "Passport Number",
    "national_id": "National ID",
    "company_no": "Company No",
    "company_registration_number": "Company Registration Number",
    "registration_number": "Registration Number",
    "tax_id": "Tax ID",
    "vat_number": "VAT Number",
    "lei": "LEI",
    "iban": "IBAN",
    "swift": "SWIFT",
    "bic": "BIC",
    "account_number": "Account Number",
    "email": "Email",
    "email_address": "Email",
    "phone": "Phone",
    "phone_number": "Phone Number",
    "url": "URL",
    "website": "Website",
    "case_id": "Case ID",
    "client_id": "Client ID",
}


def _json_path_label(json_path: str) -> str | None:
    """Map a json_path leaf key to a label catalogue entry, if any."""
    # json_path looks like "$.client.name" or "$[0].lei"; the last key wins.
    last = json_path.rsplit(".", 1)[-1].rsplit("]", 1)[-1].lstrip(".[")
    last_norm = last.lower().replace("-", "_").replace(" ", "_")
    return _JSON_KEY_TO_LABEL.get(last_norm)


def extract_json(loaded: LoadedDocument) -> ExtractedDocument:
    raw = loaded.raw_bytes if loaded.raw_bytes is not None else loaded.path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    leaves = _flatten_json(data)
    parts: list[str] = []
    blocks: list[ExtractedTextBlock] = []
    cursor = 0
    for jp, value in leaves:
        # If the JSON key resolves to a known label, emit "Label: value\n" so
        # the label-value detector fires naturally. The block offsets still
        # point at the value portion only, so rewriter offsets remain correct.
        label = _json_path_label(jp)
        prefix = f"{label}: " if label else ""
        parts.append(prefix)
        cursor += len(prefix)

        start = cursor
        parts.append(value)
        cursor += len(value)
        end = cursor
        parts.append("\n")
        cursor += 1
        blocks.append(
            ExtractedTextBlock(
                text=value,
                start=start,
                end=end,
                block_id=jp,
                metadata={"json_path": jp, "label": label},
            )
        )
    return ExtractedDocument(
        flat_text="".join(parts),
        blocks=blocks,
        json_data=data,
        metadata={"format": "json"},
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def extract_csv(loaded: LoadedDocument) -> ExtractedDocument:
    import pandas as pd

    sep = "\t" if loaded.path.suffix.lower() == ".tsv" else ","
    if loaded.raw_bytes is not None:
        df = pd.read_csv(io.BytesIO(loaded.raw_bytes), sep=sep, dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(loaded.path, sep=sep, dtype=str, keep_default_na=False)

    parts = []
    blocks = []
    cursor = 0
    header_text = ", ".join(map(str, df.columns))
    parts.append(header_text)
    blocks.append(
        ExtractedTextBlock(
            text=header_text,
            start=cursor,
            end=cursor + len(header_text),
            block_id="csv:header",
            metadata={"is_header": True, "columns": list(map(str, df.columns))},
        )
    )
    cursor += len(header_text) + 1
    parts.append("\n")
    for r_idx, row in df.iterrows():
        for c_idx, col in enumerate(df.columns):
            cell_value = str(row[col])
            if cell_value == "":
                continue
            start = cursor
            parts.append(cell_value)
            cursor += len(cell_value)
            end = cursor
            parts.append("\n")
            cursor += 1
            blocks.append(
                ExtractedTextBlock(
                    text=cell_value,
                    start=start,
                    end=end,
                    block_id="csv:" + str(r_idx) + ":" + str(c_idx),
                    metadata={"row": int(r_idx), "column": col, "column_index": c_idx},
                )
            )
    return ExtractedDocument(
        flat_text="".join(parts),
        blocks=blocks,
        csv_dataframe=df,
        metadata={"format": "csv", "sep": sep, "rows": len(df), "columns": list(map(str, df.columns))},
    )
