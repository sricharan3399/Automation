"""Perception-error candidate detection.

Compares perception detections against reference annotations for the target
object class. Everything produced here is a CANDIDATE for human review.

Rules that need an approved project tolerance (bounding-box IoU floor, distance
and velocity error limits) are *skipped with a stated reason* rather than run
against an invented threshold - reporting a distance error against a made-up
tolerance would be worse than reporting nothing.
"""

from __future__ import annotations

from typing import Any

from backend.configstore import get_config_store
from backend.models.contracts import (
    AbnormalityCategory,
    DetectionContract,
    RecordStatus,
    SceneFinding,
    Severity,
)
from backend.settings import get_settings
from backend.vision.geometry2d import iou, merge_intervals


def _cfg() -> dict[str, Any]:
    return get_settings().section("perception")


def _bucket(t: float, tolerance: float) -> int:
    return int(round(t / max(tolerance, 1e-6)))


def _index_by_time(detections: list[DetectionContract], tolerance: float) -> dict[int, list[DetectionContract]]:
    index: dict[int, list[DetectionContract]] = {}
    for detection in detections:
        index.setdefault(_bucket(detection.t, tolerance), []).append(detection)
    return index


def _neighbours(
    index: dict[int, list[DetectionContract]], t: float, tolerance: float
) -> list[DetectionContract]:
    key = _bucket(t, tolerance)
    out: list[DetectionContract] = []
    for offset in (-1, 0, 1):
        out.extend(index.get(key + offset, []))
    return out


def analyse_perception(
    detections: list[DetectionContract],
    reference_data_available: bool,
    object_type: str = "bus",
) -> list[SceneFinding]:
    cfg = _cfg()
    tolerance = float(cfg.get("match_time_tolerance_s", 0.06))
    iou_threshold = float(cfg.get("match_iou_threshold", 0.3))
    min_duration = float(cfg.get("min_finding_duration_s", 0.4))
    low_floor = float(cfg.get("low_confidence_floor", 0.5))
    low_min_duration = float(cfg.get("low_confidence_min_duration_s", 1.0))

    perception = [d for d in detections if d.source == "perception"]
    reference = [d for d in detections if d.source == "reference"]

    findings: list[SceneFinding] = []

    # ---- LOW_CONFIDENCE_BUS: needs no reference data ---------------------
    targets = [d for d in perception if d.object_type == object_type]
    low_times = [d.t for d in targets if d.confidence is not None and d.confidence < low_floor]
    for start, end in merge_intervals(low_times, tolerance * 4):
        if end - start >= low_min_duration:
            findings.append(
                SceneFinding(
                    code="LOW_CONFIDENCE_BUS",
                    category=AbnormalityCategory.PERCEPTION,
                    severity=Severity.INFO,
                    message=(
                        f"{object_type} detections stayed below confidence {low_floor:.2f} for "
                        f"{end - start:.2f} s ({start:.2f}s - {end:.2f}s)."
                    ),
                    t=round(start, 3),
                    evidence={"t_start": round(start, 3), "t_end": round(end, 3), "floor": low_floor},
                    status=RecordStatus.CANDIDATE,
                    requires_review=False,
                )
            )

    if not reference_data_available or not reference:
        findings.append(
            SceneFinding(
                code="PERCEPTION_REFERENCE_UNAVAILABLE",
                category=AbnormalityCategory.ANNOTATION,
                severity=Severity.INFO,
                message=(
                    "No reference annotations are available for this event, so missed detections, "
                    "false positives and classification errors could not be evaluated. "
                    "Absence of findings here does not mean perception was correct."
                ),
                status=RecordStatus.CANDIDATE,
                requires_review=False,
            )
        )
        return findings

    perception_index = _index_by_time(perception, tolerance)
    reference_index = _index_by_time(reference, tolerance)

    # ---- BUS_MISSED_DETECTION -------------------------------------------
    missed_times: list[float] = []
    misclassified_times: list[float] = []
    for annotation in (d for d in reference if d.object_type == object_type):
        neighbours = _neighbours(perception_index, annotation.t, tolerance)
        matches = [(iou(annotation.bounding_box, p.bounding_box), p) for p in neighbours]
        matches = [(score, p) for score, p in matches if score >= iou_threshold]
        if not matches:
            missed_times.append(annotation.t)
            continue
        best = max(matches, key=lambda item: item[0])[1]
        if best.object_type != object_type:
            misclassified_times.append(annotation.t)

    for start, end in merge_intervals(missed_times, tolerance * 4):
        if end - start >= min_duration:
            findings.append(
                SceneFinding(
                    code="BUS_MISSED_DETECTION",
                    category=AbnormalityCategory.PERCEPTION,
                    severity=Severity.WARNING,
                    message=(
                        f"A reference {object_type} had no matching perception detection for "
                        f"{end - start:.2f} s ({start:.2f}s - {end:.2f}s)."
                    ),
                    t=round(start, 3),
                    evidence={
                        "t_start": round(start, 3),
                        "t_end": round(end, 3),
                        "iou_threshold": iou_threshold,
                    },
                    status=RecordStatus.CANDIDATE,
                )
            )

    for start, end in merge_intervals(misclassified_times, tolerance * 4):
        if end - start >= min_duration:
            findings.append(
                SceneFinding(
                    code="BUS_WRONG_CLASSIFICATION",
                    category=AbnormalityCategory.PERCEPTION,
                    severity=Severity.WARNING,
                    message=(
                        f"A reference {object_type} was matched but classified as another object type for "
                        f"{end - start:.2f} s ({start:.2f}s - {end:.2f}s)."
                    ),
                    t=round(start, 3),
                    evidence={"t_start": round(start, 3), "t_end": round(end, 3)},
                    status=RecordStatus.CANDIDATE,
                )
            )

    # ---- BUS_FALSE_POSITIVE ----------------------------------------------
    false_positive_times: list[float] = []
    for detection in (d for d in perception if d.object_type == object_type):
        neighbours = _neighbours(reference_index, detection.t, tolerance)
        if not any(iou(detection.bounding_box, r.bounding_box) >= iou_threshold for r in neighbours):
            false_positive_times.append(detection.t)

    for start, end in merge_intervals(false_positive_times, tolerance * 4):
        if end - start >= min_duration:
            findings.append(
                SceneFinding(
                    code="BUS_FALSE_POSITIVE",
                    category=AbnormalityCategory.PERCEPTION,
                    severity=Severity.WARNING,
                    message=(
                        f"A perceived {object_type} had no matching reference annotation for "
                        f"{end - start:.2f} s ({start:.2f}s - {end:.2f}s)."
                    ),
                    t=round(start, 3),
                    evidence={"t_start": round(start, 3), "t_end": round(end, 3)},
                    status=RecordStatus.CANDIDATE,
                )
            )

    findings.extend(_project_threshold_notices())
    return findings


def _project_threshold_notices() -> list[SceneFinding]:
    """Report which perception rules are waiting on approved project tolerances."""
    store = get_config_store()
    waiting = [
        rule
        for rule in store.rules()
        if rule.category == "PERCEPTION" and rule.awaiting_project_threshold
    ]
    if not waiting:
        return []
    names = ", ".join(rule.id for rule in waiting)
    return [
        SceneFinding(
            code="PERCEPTION_RULES_AWAITING_THRESHOLD",
            category=AbnormalityCategory.PERCEPTION,
            severity=Severity.INFO,
            message=(
                f"These perception rules did not run because they require an approved project "
                f"tolerance that has not been supplied: {names}."
            ),
            evidence={"rules": [rule.id for rule in waiting]},
            status=RecordStatus.CANDIDATE,
            requires_review=False,
        )
    ]
