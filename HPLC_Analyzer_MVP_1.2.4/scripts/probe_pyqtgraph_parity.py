"""Run the optional PyQtGraph screen feature-parity probe."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.renderer_parity import probe_pyqtgraph_parity


def main() -> int:
    print(json.dumps(probe_pyqtgraph_parity(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
