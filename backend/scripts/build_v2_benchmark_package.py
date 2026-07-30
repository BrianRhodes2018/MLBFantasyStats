"""Build the frozen V2 row-level benchmark and its reviewable summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from hit_model.benchmark import build_v2_benchmark_package
from hit_model.experiment_contract import (
    DEFAULT_CONTRACT_PATH,
    load_experiment_contract,
)
from train_hit_model import load_dataset


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dataset", nargs="+", required=True)
    result.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT_PATH),
    )
    result.add_argument(
        "--artifact-dir",
        default=str(BACKEND_DIR / "backtest_results" / "v3_baseline"),
    )
    result.add_argument(
        "--summary",
        default=str(BACKEND_DIR / "reports" / "v3" / "v2_benchmark_summary.json"),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    paths = [Path(path).resolve() for path in args.dataset]
    contract = load_experiment_contract(args.contract)
    df = load_dataset(paths)
    summary = build_v2_benchmark_package(
        df=df,
        dataset_paths=paths,
        contract=contract,
        output_dir=Path(args.artifact_dir),
        repo_root=REPO_ROOT,
    )
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "ok",
        "summary": str(summary_path),
        "rows": summary["rows"],
        "game_dates": summary["game_dates"],
        "determinism_key": summary["determinism_key"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
