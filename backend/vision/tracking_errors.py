"""Tracking-error candidate detection.

Detects identity switches, track loss, fragmentation, duplicate tracks and
temporary loss for the target object class. All findings are CANDIDATEs.

Identity switching is inferred by spatial continuity: when one track ends and a
different track for the same class begins at nearly the same place and instant,
that is the signature of an identity change rather than two distinct objects.
"""

from __future__ import annotations

from typing import Any

from backend.models.contracts import (
    AbnormalityCategory,
    DetectionContract,
    RecordStatus,
    SceneFinding,
    Severity,
    TrackSummary,
)
from backend.settings import get_settings
from backend.vision.geometry2d import iou


def _cfg() -> dict[str, Any]:
    return get_settings().section("tracking")


def _detections_by_track(
    detections: list[DetectionContract], object_type: str
) -> dict[str, list[DetectionContract]]:
    grouped: dict[str, list[DetectionContract]] = {}
    for detection in detections:
        if detection.source != "perception" or not detection.track_id:
            continue
        if detection.object_type != object_type:
            continue
        grouped.setdefault(detection.track_id, []).append(detection)
    for items in grouped.values():
        items.sort(key=lambda d: d.t)
    return grouped


def _base_identity(track_id: str) -> str:
    """Strip a trailing ``-SUFFIX`` so re-issued identities group together."""
    return track_id.rsplit("-", 1)[0] if "-" in track_id else track_id


def analyse_tracking(
    detections: list[DetectionContract],
    tracks: list[TrackSummary],
    object_type: str = "bus",
) -> list[SceneFinding]:
    cfg = _cfg()
    max_gap = float(cfg.get("max_track_gap_s", 0.5))
    fragmentation_min = int(cfg.get("fragmentation_min_segments", 3))
    duplicate_iou = float(cfg.get("duplicate_iou_threshold", 0.6))
    loss_remaining = float(cfg.get("track_loss_min_remaining_s", 1.0))

    grouped = _detections_by_track(detections, object_type)
    if not grouped:
        return []

    findings: list[SceneFinding] = []
    clip_end = max(d.t for d in detections)
    ordered = sorted(grouped.items(), key=lambda item: item[1][0].t)

    # ---- BUS_TRACK_ID_SWITCH --------------------------------------------
    for i, (track_id, items) in enumerate(ordered):
        last = items[-1]
        for other_id, other_items in ordered[i + 1 :]:
            if other_id == track_id:
                continue
            first = other_items[0]
            gap = first.t - last.t
            if gap < 0 or gap > max_gap:
                continue
            overlap = iou(last.bounding_box, first.bounding_box)
            if overlap >= duplicate_iou:
                findings.append(
                    SceneFinding(
                        code="BUS_TRACK_ID_SWITCH",
                        category=AbnormalityCategory.TRACKING,
                        severity=Severity.WARNING,
                        message=(
                            f"Track '{track_id}' ended at t={last.t:.2f}s and track '{other_id}' began at "
                            f"t={first.t:.2f}s at the same image position (IoU {overlap:.2f}), which is "
                            "the signature of an identity change on one object."
                        ),
                        t=round(first.t, 3),
                        track_id=other_id,
                        camera=first.camera,
                        evidence={
                            "previous_track_id": track_id,
                            "new_track_id": other_id,
                            "gap_s": round(gap, 3),
                            "iou": overlap,
                        },
                        status=RecordStatus.CANDIDATE,
                    )
                )
                break

    # ---- BUS_TEMPORARY_LOSS and BUS_TRACK_LOSS ---------------------------
    for summary in tracks:
        if summary.max_gap_s is not None and summary.max_gap_s > max_gap:
            findings.append(
                SceneFinding(
                    code="BUS_TEMPORARY_LOSS",
                    category=AbnormalityCategory.TRACKING,
                    severity=Severity.INFO,
                    message=(
                        f"Track '{summary.track_id}' gapped for {summary.max_gap_s:.2f} s and resumed "
                        "under the same identity."
                    ),
                    track_id=summary.track_id,
                    evidence={"max_gap_s": summary.max_gap_s, "tolerance_s": max_gap},
                    status=RecordStatus.CANDIDATE,
                    requires_review=False,
                )
            )

        remaining = clip_end - summary.last_t
        if remaining >= loss_remaining:
            # Only a loss if no other track of the same class picks the object up.
            successor = any(
                other.first_t >= summary.last_t and (other.first_t - summary.last_t) <= max_gap
                for other in tracks
                if other.track_id != summary.track_id
            )
            if not successor:
                findings.append(
                    SceneFinding(
                        code="BUS_TRACK_LOSS",
                        category=AbnormalityCategory.TRACKING,
                        severity=Severity.WARNING,
                        message=(
                            f"Track '{summary.track_id}' ended at t={summary.last_t:.2f}s, "
                            f"{remaining:.2f} s before the end of the clip, with no successor track."
                        ),
                        t=round(summary.last_t, 3),
                        track_id=summary.track_id,
                        evidence={"remaining_s": round(remaining, 3), "clip_end_s": round(clip_end, 3)},
                        status=RecordStatus.CANDIDATE,
                    )
                )

    # ---- BUS_TRACK_FRAGMENTATION -----------------------------------------
    families: dict[str, list[TrackSummary]] = {}
    for summary in tracks:
        families.setdefault(_base_identity(summary.track_id), []).append(summary)
    for base, members in families.items():
        if len(members) < fragmentation_min:
            continue
        members.sort(key=lambda s: s.first_t)
        disjoint = all(
            a.last_t <= b.first_t for a, b in zip(members, members[1:], strict=False)
        )
        if disjoint:
            findings.append(
                SceneFinding(
                    code="BUS_TRACK_FRAGMENTATION",
                    category=AbnormalityCategory.TRACKING,
                    severity=Severity.WARNING,
                    message=(
                        f"{len(members)} short, non-overlapping tracks share the identity root '{base}', "
                        "which suggests one object was fragmented across several tracks."
                    ),
                    t=round(members[0].first_t, 3),
                    track_id=members[0].track_id,
                    evidence={"segments": [m.track_id for m in members]},
                    status=RecordStatus.CANDIDATE,
                )
            )

    # ---- BUS_DUPLICATE_TRACK ---------------------------------------------
    for i, track_a in enumerate(tracks):
        for track_b in tracks[i + 1 :]:
            if track_a.last_t < track_b.first_t or track_b.last_t < track_a.first_t:
                continue
            overlap_start = max(track_a.first_t, track_b.first_t)
            overlap_end = min(track_a.last_t, track_b.last_t)
            samples_a = [d for d in grouped.get(track_a.track_id, []) if overlap_start <= d.t <= overlap_end]
            samples_b = [d for d in grouped.get(track_b.track_id, []) if overlap_start <= d.t <= overlap_end]
            if not samples_a or not samples_b:
                continue
            scores = [
                iou(a.bounding_box, b.bounding_box)
                for a in samples_a
                for b in samples_b
                if abs(a.t - b.t) <= 0.06
            ]
            if scores and (sum(scores) / len(scores)) >= duplicate_iou:
                findings.append(
                    SceneFinding(
                        code="BUS_DUPLICATE_TRACK",
                        category=AbnormalityCategory.TRACKING,
                        severity=Severity.WARNING,
                        message=(
                            f"Tracks '{track_a.track_id}' and '{track_b.track_id}' overlap in time and space "
                            f"(mean IoU {sum(scores) / len(scores):.2f}), so they likely describe the same bus."
                        ),
                        t=round(overlap_start, 3),
                        track_id=track_b.track_id,
                        evidence={
                            "track_a": track_a.track_id,
                            "track_b": track_b.track_id,
                            "mean_iou": round(sum(scores) / len(scores), 4),
                            "overlap_s": round(overlap_end - overlap_start, 3),
                        },
                        status=RecordStatus.CANDIDATE,
                    )
                )

    return findings
