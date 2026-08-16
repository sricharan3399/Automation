"""Single source of truth for the versions recorded on every run.

Reproducibility requirement (spec section 93): every run must capture the
software, contract, rule, model and configuration-profile versions that
produced it. The first two live here; the remainder are resolved at runtime
from the configuration store and the active adapters.
"""

from __future__ import annotations

SOFTWARE_VERSION = "1.0.0"

# Version of the JSON contracts exchanged between pipeline modules. Bump the
# minor component for additive changes, the major component for breaking ones.
CONTRACT_VERSION = "1.0.0"

# Version of the built-in analytical methods (junction ranking, timestamp
# interpolation, tracking heuristics). Recorded as `model_version` on every
# recommendation so a finding can always be traced back to how it was produced.
METHOD_VERSION = "analytic-1.0.0"

API_PREFIX = "/api/v1"
