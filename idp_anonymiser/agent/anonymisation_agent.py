"""High-level anonymisation agent.

Provides one public method, :meth:`AnonymisationAgent.run`, that takes an
:class:`AnonymisationRequest` and produces an :class:`AnonymisationResult`.

The agent reads a YAML profile (or accepts an in-memory dict), invokes each
workflow stage, writes the plan + audit report, and returns the result. It
deliberately does not log raw PII — only counts and entity types.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    AnonymisationRequest,
    AnonymisationResult,
)
from idp_anonymiser.agent import workflow as wf
from idp_anonymiser.audit.report import build_audit_report, write_report

logger = logging.getLogger(__name__)


def _load_profile(profile_name: str) -> dict[str, Any]:
    """Load a YAML profile from the package's bundled config dir."""
    here = Path(__file__).parent.parent
    profile_path = here / "config" / "profiles" / f"{profile_name}.yaml"
    if not profile_path.exists():
        raise FileNotFoundError(
            f"Unknown anonymisation profile: {profile_name} (expected at {profile_path})"
        )
    with profile_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class AnonymisationAgent:
    """Run the deterministic anonymisation pipeline end-to-end."""

    def __init__(self, profile_loader=_load_profile) -> None:
        self.profile_loader = profile_loader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, request: AnonymisationRequest) -> AnonymisationResult:
        """Execute the pipeline. Never modifies the input file."""
        logger.info(
            "Starting anonymisation: doc_id=%s profile=%s mode=%s",
            request.document_id,
            request.config_profile,
            request.anonymisation_mode,
        )
        profile = self.profile_loader(request.config_profile)

        # 1. Type-detect
        doc_type = wf.detect_type(request.input_path, hint=request.document_type_hint)
        logger.info("Detected document type: %s", doc_type.value)

        # 2. Extract
        extracted = wf.extract(request, doc_type)

        # 3. Detect entities
        detections = wf.detect_entities(extracted, profile)
        logger.info("Detections: %d", len(detections))

        # 4. Resolve canonical entities (multi-page, alias-aware)
        canonical_entities, ambiguous = wf.resolve_canonical(detections, request, extracted)
        logger.info(
            "Canonical entities: %d, ambiguous mentions: %d",
            len(canonical_entities),
            len(ambiguous),
        )

        # 5. Build plan + replacement values
        plan, _generator = wf.build_plan(request, detections, canonical_entities, ambiguous)

        # 6. Rewrite document
        out_dir = Path(request.output_dir)
        output_doc_path, output_text_path, applied, rewrite_warnings = wf.rewrite(
            request, doc_type, extracted, plan, canonical_entities, out_dir
        )

        # Read rewritten text for validation (TXT path always exists)
        rewritten_text = (
            Path(output_text_path).read_text(encoding="utf-8")
            if output_text_path
            else ""
        )

        # 7. Validate
        validation = wf.validate(
            original_text=extracted.flat_text,
            rewritten_text=rewritten_text,
            request=request,
            plan=plan,
            canonical_entities=canonical_entities,
            doc_format=doc_type.value,
            input_path=request.input_path,
            output_path=output_doc_path,
            profile=profile,
        )
        for w in rewrite_warnings:
            if w not in validation.warnings:
                validation.warnings.append(w)

        # 8. Persist plan & audit report
        plan_path = out_dir / f"{Path(request.input_path).stem}.plan.json"
        with plan_path.open("w", encoding="utf-8") as fh:
            json.dump(
                self._plan_to_dict(plan, request.debug_include_originals),
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        detectors_used = sorted({d.detector for d in detections})
        report = build_audit_report(
            request=request,
            plan=plan,
            canonical_entities=canonical_entities,
            validation=validation,
            detectors_used=detectors_used,
            input_path=request.input_path,
            output_document_path=output_doc_path,
            output_text_path=output_text_path,
        )
        audit_path = out_dir / f"{Path(request.input_path).stem}.audit.json"
        write_report(report, audit_path)

        status = "ok" if validation.passed else ("warning" if validation.warnings else "ok")
        logger.info(
            "Anonymisation complete: status=%s replacements=%d quality=%.3f",
            status,
            len(plan.replacements),
            validation.quality_score,
        )
        return AnonymisationResult(
            document_id=request.document_id,
            status=status,
            output_document_path=output_doc_path,
            output_text_path=output_text_path,
            plan_path=str(plan_path),
            audit_report_path=str(audit_path),
            pii_count=len(detections),
            replacement_count=len(plan.replacements),
            unresolved_count=len(plan.unresolved),
            quality_score=validation.quality_score,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_to_dict(plan: AnonymisationPlan, include_originals: bool) -> dict[str, Any]:
        d = plan.model_dump(mode="json")
        if not include_originals:
            for r in d.get("replacements", []):
                r["original_value_for_runtime_only"] = None
        return d
