"""CompositeDetector: orchestrates regex + spaCy + Presidio + label-value + table.

Public API:

    detector = CompositeDetector(profile)
    detections = detector.detect(extracted_document)

The composite is configured by a :class:`DetectionConfig` dict (typically
loaded from a YAML profile) which controls which sub-detectors run, the
confidence floor, and the label rule list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from idp_anonymiser.agent.state import Detection
from idp_anonymiser.detection import (
    label_value_detector,
    regex_recognisers,
    table_detector,
)
from idp_anonymiser.detection.entity_resolution import resolve_entities
from idp_anonymiser.detection.label_value_detector import (
    DEFAULT_LABEL_RULES,
    LabelRule,
)
from idp_anonymiser.detection.presidio_detector import PresidioDetector
from idp_anonymiser.detection.spacy_detector import SpacyDetector
from idp_anonymiser.document.layout_model import ExtractedDocument

logger = logging.getLogger(__name__)


@dataclass
class DetectionConfig:
    """Tuning knobs for the composite detector."""

    enabled_entities: tuple[str, ...] = ()  # empty -> all
    disabled_entities: tuple[str, ...] = ()
    confidence_threshold: float = 0.4
    enable_regex: bool = True
    enable_label_value: bool = True
    enable_table: bool = True
    enable_spacy: bool = True
    enable_presidio: bool = False
    spacy_model: str = "en_core_web_sm"
    label_rules: tuple[LabelRule, ...] = DEFAULT_LABEL_RULES
    extra_label_rules: tuple[LabelRule, ...] = field(default_factory=tuple)


class CompositeDetector:
    """Run all configured detectors and return a flat list of detections."""

    def __init__(self, config: Optional[DetectionConfig] = None) -> None:
        self.config = config or DetectionConfig()
        self._spacy: Optional[SpacyDetector] = None
        self._presidio: Optional[PresidioDetector] = None

    def _spacy_detector(self) -> SpacyDetector:
        if self._spacy is None:
            self._spacy = SpacyDetector(model_name=self.config.spacy_model)
        return self._spacy

    def _presidio_detector(self) -> PresidioDetector:
        if self._presidio is None:
            self._presidio = PresidioDetector()
        return self._presidio

    def _filter(self, detections: list[Detection]) -> list[Detection]:
        cfg = self.config
        out: list[Detection] = []
        for d in detections:
            if d.confidence < cfg.confidence_threshold:
                continue
            if cfg.enabled_entities and d.entity_type not in cfg.enabled_entities:
                continue
            if d.entity_type in cfg.disabled_entities:
                continue
            out.append(d)
        return out

    def detect(self, extracted: ExtractedDocument) -> list[Detection]:
        text = extracted.flat_text
        all_detections: list[Detection] = []

        if self.config.enable_regex and text:
            all_detections.extend(regex_recognisers.detect_all(text))

        if self.config.enable_label_value and text:
            rules = self.config.label_rules + self.config.extra_label_rules
            all_detections.extend(
                label_value_detector.detect_label_values(text, rules=rules)
            )

        if self.config.enable_table:
            all_detections.extend(
                table_detector.detect_table_entities(
                    extracted, rules=self.config.label_rules + self.config.extra_label_rules
                )
            )

        if self.config.enable_spacy and text:
            spacy_dets = self._spacy_detector().detect(text)
            all_detections.extend(spacy_dets)

        if self.config.enable_presidio and text:
            pres_dets = self._presidio_detector().detect(text)
            all_detections.extend(pres_dets)

        return self._filter(all_detections)


__all__ = ["CompositeDetector", "DetectionConfig", "resolve_entities"]
