"""Run the isolated renderer benchmark and print or save JSON results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.renderer_benchmark import RendererWorkload, benchmark_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=int, default=8)
    parser.add_argument("--points", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    workload = RendererWorkload(
        trace_count=arguments.traces,
        point_count=arguments.points,
        repeats=arguments.repeats,
        seed=arguments.seed,
    )
    payload = json.dumps(
        benchmark_suite(workload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if arguments.output is None:
        print(payload)
    else:
        arguments.output.write_text(payload + "\n", encoding="utf-8")
        print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
