# Sanitiser

Offline test-data sanitisation tool for converting real enterprise documents
into safe, realistic synthetic test documents. Preserves structure, layout
meaning, business semantics and extraction difficulty so the sanitised
documents are still useful for IDP testing.

Designed for: TXT, DOCX, searchable PDF, including unstructured annual
reports, KYC packs, board reports, and legal documents up to 700+ pages.

## What it does

1. Detects sensitive entities with five layered detectors: regex (emails,
   phones, IBANs, SWIFT, sort codes, IDs, dates), label-value rules
   (`Director:`, `Client Name:` etc.), spaCy NER (PERSON / ORG / LOCATION),
   an operator-supplied seed list, and a board/executive section recogniser.
2. Resolves detections into canonical entities. Full names and abbreviations
   ("Canadian Imperial Bank of Commerce" + "CIBC") link into one entity.
   Person variants ("Victor Dodig" + "Mr Dodig" + "Dodig" + "V. Dodig")
   collapse into one entity unless ambiguous.
3. Assigns realistic fictional replacements from curated pools. Banks remain
   bank-like, orgs keep their suffix, persons keep their title/surname-only
   variants. The same real entity always maps to the same fake across pages,
   chunks, and files in a batch.
4. Applies replacements page-by-page (TXT / DOCX / PDF) and redacts every
   embedded image (logos, photos, signatures, stamps, QR / barcodes) on
   PDF pages.
5. Emits a QA report flagging likely missed entities (capitalised noun
   phrases that survived, residual regex PII, unexplained abbreviations,
   pages with images requiring human review).

## Strict policy

This tool is for preparing real data for test environments. It replaces
**all** identifying signals — including public organisations like CIBC, ING,
HSBC, etc. — because the source document itself must not be identifiable.

## Install

```bash
pip install -e .
pip install spacy pydantic typer pyyaml faker rapidfuzz phonenumbers \
            python-docx PyMuPDF
python -m spacy download en_core_web_sm
```

## CLI

```bash
python -m sanitiser run \
  --input ./input_docs/ \
  --output ./output_docs/ \
  --profile strict_test_data \
  --spacy-model en_core_web_sm \
  --seed 42 \
  --seed-list ./seed_entities.yaml
```

A single file or a whole directory can be passed. The same registry is used
across every file in the run, so an entity that appears in document A and
document B is replaced with the same synthetic entity in both.

## Output layout

```
output_docs/
  document_001/
    sanitised.txt
    sanitised.pdf            # for PDF inputs
    sanitised.docx           # for DOCX inputs
    audit.json               # one row per replacement
    replacement_map.json     # canonical_id -> {original, replacement} per entity
    qa_report.json           # likely missed entities and visual review list
    visual_redaction_report.json
    run_summary.json
  batch_summary.json
  replacement_map.json       # the merged registry across the run
```

## Configuration profiles

```yaml
profile: strict_test_data
enable_spacy: true
fail_if_spacy_unavailable: true
spacy_model: en_core_web_sm
replace_real_organisations: true
replace_public_banks: true
replace_dates: false
replace_locations: true
visual_elements:
  flag_images: true
  redact_images: true
qa:
  flag_remaining_capitalised_names: true
  flag_remaining_org_suffixes: true
  fail_on_remaining_regex_pii: true
```

## Seed list

Operator-supplied list of entities to *always* replace, even if NER misses:

```yaml
PERSON:
  - Victor Dodig
  - Katharine B. Stevenson

ORG:
  - Canadian Imperial Bank of Commerce:
      abbreviation: CIBC
  - ING Group:
      abbreviation: ING
```

For each PERSON seed entry, the matcher generates patterns for the full
name, `Mr <surname>`, `<surname>` (when 4+ chars), and `<initial>. <surname>`.

For each ORG seed entry, the full name and the abbreviation are both
patterns. They are linked into the same canonical entity and replaced with a
fake full name and a derived fake abbreviation.

## Behaviour example

Input:

```
Canadian Imperial Bank of Commerce announced that Victor Dodig would retire
as President and CEO. CIBC confirmed that Mr Dodig will remain until the
transition is complete.
```

Output:

```
Northbridge International Banking Corporation announced that James Whitmore
would retire as President and CEO. NIBC confirmed that Mr Whitmore will
remain until the transition is complete.
```

`replacement_map.json`:

```json
{
  "ORG_0001": {
    "entity_type": "ORG",
    "original_full_name": "Canadian Imperial Bank of Commerce",
    "original_abbreviation": "CIBC",
    "replacement_full_name": "Northbridge International Banking Corporation",
    "replacement_abbreviation": "NIBC"
  },
  "PERSON_0001": {
    "entity_type": "PERSON",
    "original_full_name": "Victor Dodig",
    "original_variants": ["Victor Dodig", "Mr Dodig", "Dodig"],
    "replacement_full_name": "James Whitmore",
    "replacement_variants": ["James Whitmore", "Mr Whitmore", "Whitmore"]
  }
}
```

## Tests

```bash
pytest tests_sanitiser/ -v
```

Tests cover regex detection, full-name/abbreviation linking, person variant
grouping, two-Smiths ambiguity, registry persistence, fail-loud spaCy, and
the full TXT pipeline with seed list.

## Limitations

- spaCy must be installed and the model downloaded. Strict profile fails
  loudly when missing — by design.
- PDF rewrite redacts and overlays at the original bbox; long replacements
  shrink in font size to fit, with a labelled fallback.
- DOCX run-level styling around partially replaced runs is rebuilt as a
  single run.
- Pixel-level QR/barcode classification is out of scope; QR codes appear in
  the visual report as generic images for human review.
