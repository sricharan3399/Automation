"""Scene understanding, perception-error and tracking-error engines.

These engines produce CANDIDATE findings only. Nothing here converts an
observation into a confirmed defect - that decision belongs to a human
reviewer, or to a validated project oracle that explicitly supports the
classification.
"""

from backend.vision.perception_errors import analyse_perception
from backend.vision.scene import analyse_scene
from backend.vision.tracking_errors import analyse_tracking

__all__ = ["analyse_scene", "analyse_perception", "analyse_tracking"]
