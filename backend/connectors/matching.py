"""Client-side evaluation of a :class:`ScoutQuery` against event metadata.

Sources that support native filtering do it themselves; this module is used by
file-based adapters and as a post-filter safety net for every adapter, so a
source that silently ignores a filter cannot leak non-matching events into a
run. Each rejection carries a reason, which the Event Explorer surfaces.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from backend.models.contracts import EventMetadata, ScoutQuery


def _norm(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _any_match(selected: list[str], values: list[str] | str | None) -> bool:
    """Empty selection means "Any" and always matches."""
    if not selected:
        return True
    if values is None:
        return False
    pool = {_norm(v) for v in ([values] if isinstance(values, str) else values)}
    return bool(pool & {_norm(s) for s in selected})


def _in_time_window(dt: datetime, query: ScoutQuery) -> tuple[bool, str | None]:
    tr = query.time_range
    if tr.start_date:
        try:
            start = datetime.fromisoformat(tr.start_date)
            if dt.date() < start.date():
                return False, f"event date {dt.date()} is before start date {tr.start_date}"
        except ValueError:
            pass
    if tr.end_date:
        try:
            end = datetime.fromisoformat(tr.end_date)
            if dt.date() > end.date():
                return False, f"event date {dt.date()} is after end date {tr.end_date}"
        except ValueError:
            pass

    local = dt.timetz()
    if tr.start_time:
        try:
            hh, mm = (int(x) for x in tr.start_time.split(":")[:2])
            if local.replace(tzinfo=None) < time(hh, mm):
                return False, f"event time {local.strftime('%H:%M')} is before {tr.start_time}"
        except (ValueError, TypeError):
            pass
    if tr.end_time:
        try:
            hh, mm = (int(x) for x in tr.end_time.split(":")[:2])
            if local.replace(tzinfo=None) > time(hh, mm):
                return False, f"event time {local.strftime('%H:%M')} is after {tr.end_time}"
        except (ValueError, TypeError):
            pass

    is_weekend = dt.weekday() >= 5
    if tr.weekdays_only and is_weekend:
        return False, "weekday-only filter excludes weekend events"
    if tr.weekends_only and not is_weekend:
        return False, "weekend-only filter excludes weekday events"
    return True, None


def _lane_match(query: ScoutQuery, metadata: EventMetadata) -> tuple[bool, str | None]:
    lanes = query.lanes
    count = metadata.lane_count

    if not lanes.lane_count_any:
        if count is None:
            return False, "lane count filter is set but the event has no lane_count"
        if lanes.lane_count_exact and count not in lanes.lane_count_exact:
            # 5+ is expressed as the sentinel value 5.
            if not (5 in lanes.lane_count_exact and count >= 5):
                return False, f"lane_count {count} is not in {sorted(lanes.lane_count_exact)}"
    if lanes.min_lanes is not None:
        if count is None:
            return False, "minimum lane filter is set but the event has no lane_count"
        if count < lanes.min_lanes:
            return False, f"lane_count {count} is below minimum {lanes.min_lanes}"
    if lanes.max_lanes is not None:
        if count is None:
            return False, "maximum lane filter is set but the event has no lane_count"
        if count > lanes.max_lanes:
            return False, f"lane_count {count} is above maximum {lanes.max_lanes}"

    if not _any_match(lanes.lane_configuration, metadata.lane_configuration):
        return False, "lane configuration does not match the selection"
    return True, None


def evaluate(query: ScoutQuery, metadata: EventMetadata) -> tuple[bool, str | None]:
    """Return ``(matches, rejection_reason)``."""
    # Country is resolved from authoritative metadata, never from a filename.
    if query.country_code:
        if not metadata.country_code:
            return False, "event has no country_code in its authoritative metadata"
        if metadata.country_code.upper() != query.country_code.upper():
            return False, f"country_code {metadata.country_code} != {query.country_code}"

    if not _any_match(query.regions, metadata.region):
        return False, "region does not match the selection"
    if not _any_match(query.cities, metadata.city):
        return False, "city does not match the selection"
    if not _any_match(query.test_areas, metadata.test_area):
        return False, "test area does not match the selection"
    if not _any_match(query.routes, metadata.route):
        return False, "route does not match the selection"

    if not _any_match(query.object_types, metadata.object_type):
        return False, "object type does not match the selection"
    if not _any_match(query.bus_subtypes, metadata.bus_type):
        return False, "bus subtype does not match the selection"
    if query.scenario_tags and not _any_match(query.scenario_tags, metadata.scenario_tags):
        return False, "no selected scenario tag is present on the event"

    if not _any_match(query.road_types, metadata.road_type):
        return False, "road type does not match the selection"

    ok, reason = _lane_match(query, metadata)
    if not ok:
        return False, reason

    if not _any_match(query.intersection_types, metadata.intersection_type):
        return False, "intersection type does not match the selection"
    if not _any_match(query.intersection_complexity, metadata.intersection_complexity):
        return False, "intersection complexity does not match the selection"
    if not _any_match(query.traffic_control_entities, metadata.traffic_control_entity):
        return False, "traffic control entity does not match the selection"
    if not _any_match(query.traffic_light_states, metadata.traffic_light_state):
        return False, "traffic light state does not match the selection"
    if not _any_match(query.vehicle_maneuvers, metadata.vehicle_maneuver):
        return False, "vehicle maneuver does not match the selection"

    if not _any_match(query.weather, metadata.weather):
        return False, "weather does not match the selection"
    if not _any_match(query.lighting, metadata.lighting):
        return False, "lighting does not match the selection"

    ds = query.dataset
    for attr in (
        "project",
        "dataset",
        "dataset_version",
        "drive_collection",
        "vehicle_build",
        "software_version",
        "map_version",
    ):
        wanted = getattr(ds, attr)
        if wanted and _norm(getattr(metadata, attr, None)) != _norm(wanted):
            return False, f"{attr} does not match the selection"

    if metadata.event_time is not None:
        ok, reason = _in_time_window(metadata.event_time, query)
        if not ok:
            return False, reason
    elif query.time_range.start_date or query.time_range.end_date:
        return False, "date filter is set but the event has no event_time"

    return True, None


def matches(query: ScoutQuery, metadata: EventMetadata) -> bool:
    return evaluate(query, metadata)[0]
