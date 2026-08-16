"""Regenerate the golden dataset.

    python tests/golden_dataset/generate.py

The fixtures are SYNTHETIC and deterministic: the generator lives in
``backend.connectors.synthetic`` and always produces identical output, so the
committed files can be regenerated and diffed rather than being opaque blobs.

No production AV data is ever committed here. Every document is stamped
``is_synthetic: true`` and names the defects deliberately injected into it, so a
fixture can never be mistaken for a real recording.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.connectors.synthetic import default_specs, generate_documents  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "events"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for existing in OUTPUT_DIR.glob("*.json"):
        existing.unlink()

    specs = default_specs()
    documents = generate_documents(specs)

    index = []
    for spec, document in zip(specs, documents, strict=True):
        path = OUTPUT_DIR / f"{spec.event_id}.json"
        # Compact: these files are dominated by per-sample stream timestamps, and
        # pretty-printing them multiplies the repository size several times over.
        # This generator is the readable source of truth for what they contain.
        path.write_text(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        index.append(
            {
                "event_id": spec.event_id,
                "file": path.name,
                "difficulty": spec.difficulty,
                "country_code": spec.country_code,
                "injected_faults": sorted(spec.faults),
                "intersection": spec.intersection,
                "turn": spec.turn,
                "stop_type": spec.stop_type,
                "reference_data": spec.reference_data,
            }
        )

    manifest = {
        "schema": "av-scout-golden-dataset/1.0",
        "synthetic": True,
        "description": (
            "Deterministic synthetic fixtures covering easy cases, difficult cases, every "
            "intersection category and every blocking validation rule."
        ),
        "event_count": len(index),
        "events": index,
    }
    (OUTPUT_DIR.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total_bytes = sum(p.stat().st_size for p in OUTPUT_DIR.glob("*.json"))
    print(f"Wrote {len(index)} synthetic events to {OUTPUT_DIR} ({total_bytes / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
