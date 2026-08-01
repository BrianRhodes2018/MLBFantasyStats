"""Train and chronologically backtest the three Pitcher Ks approaches."""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import date, timedelta
from pathlib import Path

from build_hit_dataset import BoxscoreSource, DEFAULT_CACHE_DIR, parse_iso_date
from pitcher_ks.artifacts import write_artifact_checksum
from pitcher_ks.features import build_training_rows
from pitcher_ks.modeling import train_model_package


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_ARTIFACT = BACKEND_DIR / "model_artifacts" / "pitcher_ks_v1.pkl"
DEFAULT_REPORT = BACKEND_DIR / "reports" / "pitcher_ks" / "v1_backtest.json"


def _public_report(package: dict) -> dict:
    return {
        "model_version": package["model_version"],
        "feature_names": package["feature_names"],
        "trained_through": package["trained_through"],
        "trained_on_rows": package["trained_on_rows"],
        "data_profile": package["data_profile"],
        "backtest": package["backtest"],
        "approaches": {
            "decomposed": "gradient-boosted K/BF plus workload distribution",
            "count": "Poisson mean plus 10th/90th quantile gradient boosting",
            "bayes": "empirical-Bayes pitcher, opponent, and workload mixture",
        },
    }


def run(args: argparse.Namespace) -> int:
    train_end = parse_iso_date(args.end_date) if args.end_date else date.today() - timedelta(days=1)
    source = BoxscoreSource(Path(args.cache_dir), request_delay_seconds=args.request_delay_seconds)
    print(f"Building Pitcher Ks rows through {train_end.isoformat()}...")
    rows = build_training_rows(
        source=source,
        first_season=args.first_season,
        last_date=train_end,
        verbose=args.verbose,
    )
    print(f"Training and backtesting on {len(rows)} starter-games...")
    package = train_model_package(rows)

    artifact_path = Path(args.artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(pickle.dumps(package, protocol=pickle.HIGHEST_PROTOCOL))
    checksum_path = write_artifact_checksum(artifact_path)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(_public_report(package), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(json.dumps(package["data_profile"], indent=2, sort_keys=True))
    print(json.dumps(package["backtest"]["aggregate"], indent=2, sort_keys=True))
    print(f"Saved artifact: {artifact_path}")
    print(f"Saved checksum: {checksum_path}")
    print(f"Saved report: {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the three Pitcher Ks models.")
    parser.add_argument("--first-season", type=int, default=2023)
    parser.add_argument("--end-date", help="Last training date, YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--request-delay-seconds", type=float, default=0.02)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
