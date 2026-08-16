"""HD-map context resolution.

Order of preference:

1. map context carried inside the event bundle (the common case for exported
   datasets, and always self-consistent with the event)
2. a configured HD map service, queried per event
3. unavailable, with a reason - never an empty map presented as a valid one

An empty map context is meaningfully different from "no junctions near this
trajectory", and the distinction is preserved so ``MAP_CONTEXT_AVAILABLE`` can
fail loudly instead of the geometry engine silently producing nothing.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.models.contracts import MapContext

log = logging.getLogger(__name__)


class MapContextResolver:
    def __init__(self, service_url: str | None = None, endpoint_template: str | None = None, timeout: float = 30.0):
        self.service_url = service_url
        self.endpoint_template = endpoint_template or "/map_context/{event_id}"
        self.timeout = timeout

    @property
    def service_configured(self) -> bool:
        return bool(self.service_url)

    def resolve(self, event_id: str, bundle_context: MapContext | None) -> MapContext:
        if bundle_context is not None and bundle_context.available and bundle_context.features:
            return bundle_context

        if not self.service_configured:
            reason = (
                bundle_context.unavailable_reason
                if bundle_context is not None and bundle_context.unavailable_reason
                else "The event bundle carries no map context."
            )
            return MapContext(
                available=False,
                unavailable_reason=(
                    f"{reason} No HD map service is configured, so map-dependent analysis "
                    "(junction selection, entry/exit edges, distance markers) cannot run."
                ),
            )

        try:
            with httpx.Client(base_url=self.service_url or "", timeout=self.timeout) as client:
                response = client.get(self.endpoint_template.format(event_id=event_id))
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Map service lookup failed for %s: %s", event_id, exc)
            return MapContext(
                available=False,
                unavailable_reason=f"The HD map service could not be queried for this event: {exc}",
            )

        try:
            return MapContext(**payload)
        except (TypeError, ValueError) as exc:
            return MapContext(
                available=False,
                unavailable_reason=f"The HD map service response did not match the map contract: {exc}",
            )
