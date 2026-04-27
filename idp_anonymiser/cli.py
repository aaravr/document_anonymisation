"""Typer CLI: `idp-anonymise anonymise / dry-run / batch`."""
from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Optional

import typer

from idp_anonymiser.agent import AnonymisationAgent, AnonymisationRequest

app = typer.Typer(
    add_completion=False,
    help="IDP Anonymiser: deterministic PII / client-data anonymisation.",
)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# anonymise
# ---------------------------------------------------------------------------


@app.command()
def anonymise(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False, help="Input document path"),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Directory for anonymised outputs"),
    profile: str = typer.Option("kyc_default", "--profile", help="Profile name (kyc_default, strict, test_mode)"),
    mode: str = typer.Option("synthetic", "--mode", help="Anonymisation mode (mask|synthetic|hybrid)"),
    risk: str = typer.Option("high", "--risk", help="Risk level (low|medium|high)"),
    preserve_layout: bool = typer.Option(True, "--preserve-layout/--no-preserve-layout"),
    document_id: Optional[str] = typer.Option(None, "--document-id"),
    document_type_hint: Optional[str] = typer.Option(None, "--type-hint"),
    consistency_scope: str = typer.Option("document", "--consistency-scope"),
    debug_include_originals: bool = typer.Option(False, "--include-originals", help="Include raw originals in audit (synthetic test data only)"),
    replace_contextual_aliases: bool = typer.Option(True, "--replace-contextual-aliases/--no-contextual-aliases"),
    fuzzy_alias_threshold: int = typer.Option(88, "--fuzzy-alias-threshold"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Anonymise a single document."""
    _configure_logging(verbose)
    output_dir.mkdir(parents=True, exist_ok=True)
    request = AnonymisationRequest(
        document_id=document_id or _doc_id_for(input),
        input_path=str(input),
        output_dir=str(output_dir),
        document_type_hint=document_type_hint,
        anonymisation_mode=mode,  # type: ignore[arg-type]
        consistency_scope=consistency_scope,  # type: ignore[arg-type]
        risk_level=risk,  # type: ignore[arg-type]
        preserve_layout=preserve_layout,
        config_profile=profile,
        debug_include_originals=debug_include_originals,
        replace_contextual_aliases=replace_contextual_aliases,
        fuzzy_alias_threshold=fuzzy_alias_threshold,
    )
    agent = AnonymisationAgent()
    result = agent.run(request)
    typer.echo(json.dumps(result.model_dump(), indent=2))
    if result.status not in {"ok", "warning"}:
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


@app.command("dry-run")
def dry_run(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    output_dir: Path = typer.Option(..., "--output-dir", "-o"),
    profile: str = typer.Option("kyc_default", "--profile"),
    document_type_hint: Optional[str] = typer.Option(None, "--type-hint"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run detection + planning only (no rewrite). Writes plan + audit JSON."""
    _configure_logging(verbose)
    output_dir.mkdir(parents=True, exist_ok=True)
    from idp_anonymiser.agent import workflow as wf
    from idp_anonymiser.audit.report import build_audit_report, write_report

    profile_dict = AnonymisationAgent().profile_loader(profile)

    request = AnonymisationRequest(
        document_id=_doc_id_for(input),
        input_path=str(input),
        output_dir=str(output_dir),
        document_type_hint=document_type_hint,
        config_profile=profile,
    )
    doc_type = wf.detect_type(str(input), hint=document_type_hint)
    extracted = wf.extract(request, doc_type)
    detections = wf.detect_entities(extracted, profile_dict)
    canonical, ambiguous = wf.resolve_canonical(detections, request, extracted)
    plan, _ = wf.build_plan(request, detections, canonical, ambiguous)

    from idp_anonymiser.agent.state import ValidationReport

    validation = ValidationReport(quality_score=1.0, passed=True)
    detectors_used = sorted({d.detector for d in detections})
    report = build_audit_report(
        request=request,
        plan=plan,
        canonical_entities=canonical,
        validation=validation,
        detectors_used=detectors_used,
        input_path=request.input_path,
        output_document_path="(dry-run, no output document)",
        output_text_path=None,
        extra={"dry_run": True},
    )
    audit_path = output_dir / f"{Path(input).stem}.dry_run.audit.json"
    write_report(report, audit_path)
    typer.echo(f"Wrote dry-run audit: {audit_path}")
    typer.echo(
        json.dumps(
            {
                "detections": len(detections),
                "canonical_entities": len(canonical),
                "ambiguous": len(ambiguous),
                "replacements": len(plan.replacements),
            },
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------


@app.command()
def batch(
    input_dir: Path = typer.Option(..., "--input-dir", exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., "--output-dir", "-o"),
    profile: str = typer.Option("kyc_default", "--profile"),
    mode: str = typer.Option("synthetic", "--mode"),
    consistency_scope: str = typer.Option("batch", "--consistency-scope"),
    pattern: str = typer.Option("*.*", "--pattern"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Anonymise every file in ``input_dir`` matching ``pattern``."""
    _configure_logging(verbose)
    output_dir.mkdir(parents=True, exist_ok=True)
    agent = AnonymisationAgent()

    files = sorted(p for p in input_dir.glob(pattern) if p.is_file())
    if not files:
        typer.echo(f"No files in {input_dir} match {pattern}")
        raise typer.Exit(code=1)

    results = []
    for file in files:
        request = AnonymisationRequest(
            document_id=_doc_id_for(file),
            input_path=str(file),
            output_dir=str(output_dir),
            anonymisation_mode=mode,  # type: ignore[arg-type]
            consistency_scope=consistency_scope,  # type: ignore[arg-type]
            config_profile=profile,
        )
        try:
            result = agent.run(request)
            results.append({"file": str(file), "status": result.status, "quality": result.quality_score})
        except Exception as exc:  # noqa: BLE001 — top-level boundary
            results.append({"file": str(file), "status": "error", "error": str(exc)})
            logging.exception("Failed on %s", file)
    summary_path = output_dir / "batch_summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump({"results": results}, fh, indent=2)
    typer.echo(f"Batch complete: {len(results)} files. Summary at {summary_path}")


def _doc_id_for(path: Path) -> str:
    return f"{path.stem}-{uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex[:12]}"


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
