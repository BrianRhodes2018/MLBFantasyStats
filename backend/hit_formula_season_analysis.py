"""
hit_formula_season_analysis.py - Season backtest for the free 1+ hit model.

This evaluates the live hit candidate helper against completed historical
games. It uses only pregame player/pitcher logs before each game date, then
checks whether each selected hitter recorded at least one hit in that game.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from databases import Database
from dotenv import load_dotenv

from database import normalize_database_url
from hit_model import score_hit_candidate
from outcome_backtest import (
    DEFAULT_CACHE_DIR,
    DEFAULT_RESULTS_DIR,
    OutcomeBacktester,
    latest_game_log_date,
    parse_iso_date,
    plate_appearances,
    safe_int,
)


def pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def avg(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def summarize(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        return {
            "picks": 0,
            "hit_rate": None,
            "tb_2_plus_rate": None,
            "bust_rate": None,
            "avg_hit_probability": None,
            "avg_score": None,
        }

    def rate(key: str) -> float:
        return sum(1 for row in rows if row["outcome"][key]) / count

    return {
        "picks": count,
        "hit_rate": round(rate("hit"), 4),
        "tb_2_plus_rate": round(rate("tb_2_plus"), 4),
        "bust_rate": round(rate("bust"), 4),
        "avg_hit_probability": round(avg(row["hit_probability"] for row in rows), 4),
        "avg_score": round(avg(row["hit_score"] for row in rows), 2),
    }


def print_table(headers: list[str], rows: list[list[Any]]) -> None:
    widths = [len(header) for header in headers]
    rendered = [[str(cell) for cell in row] for row in rows]
    for row in rendered:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rendered:
        print("  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))


def hit_rate_from_logs(rows: Iterable[Mapping[str, Any]]) -> Optional[float]:
    hits = 0
    pa = 0
    for row in rows:
        hits += safe_int(row.get("hits"))
        pa += plate_appearances(row)
    if pa <= 0:
        return None
    return hits / pa


def pregame_hit_rates(
    backtester: OutcomeBacktester,
    player_id: int,
    target: date,
    *,
    rolling_days: int,
) -> dict[str, Optional[float]]:
    logs = backtester.batter_logs_by_player.get(player_id, [])
    target_iso = target.isoformat()
    cutoff_iso = (target - timedelta(days=rolling_days)).isoformat()
    season_rows = [row for row in logs if row["game_date"] < target_iso]
    rolling_rows = [row for row in season_rows if row["game_date"] >= cutoff_iso]
    return {
        "season_hit_rate_per_pa": hit_rate_from_logs(season_rows),
        "rolling_hit_rate_per_pa": hit_rate_from_logs(rolling_rows),
    }


def probability_bucket(probability: float) -> str:
    if probability >= 0.80:
        return "80%+"
    if probability >= 0.70:
        return "70-79%"
    if probability >= 0.60:
        return "60-69%"
    if probability >= 0.50:
        return "50-59%"
    return "<50%"


def batting_order_bucket(slot: Optional[int]) -> str:
    if not slot:
        return "unknown"
    if slot <= 2:
        return "1-2"
    if slot <= 5:
        return "3-5"
    if slot <= 7:
        return "6-7"
    return "8-9"


def k_bucket(k_pct: Optional[float]) -> str:
    if k_pct is None:
        return "missing"
    if k_pct <= 17:
        return "<=17%"
    if k_pct <= 26:
        return "17-26%"
    return ">26%"


def min_rate_bucket(value: Optional[float]) -> str:
    if value is None:
        return "missing"
    if value >= 0.28:
        return ">=28%"
    if value >= 0.24:
        return "24-28%"
    if value >= 0.20:
        return "20-24%"
    return "<20%"


def select_top_by_day(
    rows: list[dict[str, Any]],
    *,
    top_n: int,
    scorer: Callable[[dict[str, Any]], float],
    filterer: Optional[Callable[[dict[str, Any]], bool]] = None,
) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if filterer and not filterer(row):
            continue
        by_day[row["date"]].append(row)

    selected: list[dict[str, Any]] = []
    for day_rows in by_day.values():
        selected.extend(
            sorted(
                day_rows,
                key=lambda row: (
                    scorer(row),
                    row["season_hit_rate_per_pa"] or 0.0,
                    -(row["batting_order"] or 99),
                ),
                reverse=True,
            )[:top_n]
        )
    return selected


def lift_summary(
    rows: list[dict[str, Any]],
    *,
    top_ns: list[int],
) -> dict[str, dict[str, Any]]:
    scorers: dict[str, Callable[[dict[str, Any]], float]] = {
        "new_hit_probability": lambda row: row["hit_probability"],
        "new_hit_score": lambda row: row["hit_score"],
        "season_hit_rate": lambda row: row["season_hit_rate_per_pa"] or 0.0,
        "rolling_hit_rate": lambda row: row["rolling_hit_rate_per_pa"] or 0.0,
        "recent_form_signal": lambda row: row["components"]["form"],
        "pitcher_vulnerability": lambda row: row["components"]["pitcher"],
        "lineup_slot": lambda row: -(row["batting_order"] or 99),
        "platoon": lambda row: row["components"]["platoon"],
    }
    output: dict[str, dict[str, Any]] = {}
    for name, scorer in scorers.items():
        output[name] = {}
        for top_n in top_ns:
            output[name][str(top_n)] = summarize(select_top_by_day(rows, top_n=top_n, scorer=scorer))
    return output


def gate_summary(rows: list[dict[str, Any]], *, top_n: int) -> dict[str, Any]:
    gates: dict[str, Callable[[dict[str, Any]], bool]] = {
        "no_gate": lambda row: True,
        "top_1_5_only": lambda row: (row["batting_order"] or 99) <= 5,
        "season_hpa_20_plus": lambda row: (row["season_hit_rate_per_pa"] or 0.0) >= 0.20,
        "season_hpa_22_plus": lambda row: (row["season_hit_rate_per_pa"] or 0.0) >= 0.22,
        "avoid_k_over_26": lambda row: row["rolling_k_pct"] is None or row["rolling_k_pct"] <= 26.0,
        "top_1_5_and_hpa_22": lambda row: (row["batting_order"] or 99) <= 5
        and (row["season_hit_rate_per_pa"] or 0.0) >= 0.22,
        "top_1_5_hpa_22_avoid_k": lambda row: (row["batting_order"] or 99) <= 5
        and (row["season_hit_rate_per_pa"] or 0.0) >= 0.22
        and (row["rolling_k_pct"] is None or row["rolling_k_pct"] <= 26.0),
    }
    return {
        name: summarize(
            select_top_by_day(
                rows,
                top_n=top_n,
                scorer=lambda row: row["hit_probability"],
                filterer=gate,
            )
        )
        for name, gate in gates.items()
    }


def grouped_summary(
    rows: list[dict[str, Any]],
    *,
    grouper: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[grouper(row)].append(row)
    return {
        key: summarize(value)
        for key, value in sorted(groups.items())
    }


async def run(args: argparse.Namespace) -> int:
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set.")

    async_url, _ = normalize_database_url(raw_url)
    db = Database(async_url)
    await db.connect()
    try:
        end = parse_iso_date(args.end_date) if args.end_date else await latest_game_log_date(db)
        start = parse_iso_date(args.start_date) if args.start_date else end - timedelta(days=args.days - 1)

        backtester = OutcomeBacktester(
            db=db,
            cache_dir=Path(args.cache_dir),
            refresh_cache=args.refresh_cache,
            request_delay_seconds=args.request_delay_seconds,
            min_pitcher_ip=args.min_pitcher_ip,
            rolling_days=args.hit_form_days,
        )
        db_meta = await backtester.load_db_context()

        print("1+ HIT FORMULA SEASON ANALYSIS")
        print(f"Dates: {start.isoformat()} through {end.isoformat()}")
        print(f"DB game logs: {db_meta['game_log_min_date']} through {db_meta['game_log_max_date']}")
        print(f"Hit form window: {args.hit_form_days} days")
        print("Target: player records at least one hit in the completed game")
        print()

        candidates, slate_meta = await backtester.build_candidates(start, end)
        scored_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            target = parse_iso_date(candidate["date"])
            rates = pregame_hit_rates(
                backtester,
                candidate["player_id"],
                target,
                rolling_days=args.hit_form_days,
            )
            hit = score_hit_candidate(
                batting_order=candidate.get("batting_order"),
                lineup_source="confirmed",
                lineup_confidence=None,
                season_hit_rate_per_pa=rates["season_hit_rate_per_pa"],
                rolling_hit_rate_per_pa=rates["rolling_hit_rate_per_pa"],
                rolling_k_pct=candidate["context"].get("rolling_k_pct"),
                form_signal=candidate["signal_values"]["recent_form"],
                pitcher_signal=candidate["signal_values"]["pitcher_vulnerability"],
                platoon_signal=candidate["signal_values"]["platoon"],
                park_runs_factor=candidate["context"].get("park_runs_factor"),
                bvp_signal=candidate["signal_values"]["bvp"],
            )
            scored_rows.append({
                **candidate,
                "hit_score": hit["score"],
                "hit_probability": hit["hit_probability"],
                "per_pa_hit_probability": hit["per_pa_hit_probability"],
                "expected_pa": hit["expected_pa"],
                "components": hit["components"],
                "season_hit_rate_per_pa": rates["season_hit_rate_per_pa"],
                "rolling_hit_rate_per_pa": rates["rolling_hit_rate_per_pa"],
                "rolling_k_pct": candidate["context"].get("rolling_k_pct"),
                "hit_model": hit,
            })

        top_ns = [int(part.strip()) for part in args.top_ns.split(",") if part.strip()]
        baseline = summarize(scored_rows)
        lift = lift_summary(scored_rows, top_ns=top_ns)
        gates = gate_summary(scored_rows, top_n=args.gate_top_n)
        probability_buckets = grouped_summary(scored_rows, grouper=lambda row: probability_bucket(row["hit_probability"]))
        order_buckets = grouped_summary(scored_rows, grouper=lambda row: batting_order_bucket(row.get("batting_order")))
        k_buckets = grouped_summary(scored_rows, grouper=lambda row: k_bucket(row.get("rolling_k_pct")))
        season_rate_buckets = grouped_summary(scored_rows, grouper=lambda row: min_rate_bucket(row.get("season_hit_rate_per_pa")))
        rolling_rate_buckets = grouped_summary(scored_rows, grouper=lambda row: min_rate_bucket(row.get("rolling_hit_rate_per_pa")))

        print("UNIVERSE")
        print(f"Eligible hitter-games: {baseline['picks']}")
        print(f"Overall 1+ hit rate: {pct(baseline['hit_rate'])}")
        print()

        print("TOP-N PER DAY COMPARISON")
        comparison_rows = []
        for method, by_top_n in lift.items():
            for top_n in top_ns:
                summary = by_top_n[str(top_n)]
                comparison_rows.append([
                    method,
                    f"top {top_n}",
                    summary["picks"],
                    pct(summary["hit_rate"]),
                    pct(summary["tb_2_plus_rate"]),
                    pct(summary["bust_rate"]),
                    pct(summary["avg_hit_probability"]),
                ])
        print_table(["Method", "Cut", "Picks", "Hit%", "2+TB%", "Bust%", "AvgProb"], comparison_rows)

        print("\nPROBABILITY BUCKETS")
        print_table(
            ["Bucket", "Rows", "Hit%", "2+TB%", "Bust%", "AvgProb"],
            [
                [
                    key,
                    value["picks"],
                    pct(value["hit_rate"]),
                    pct(value["tb_2_plus_rate"]),
                    pct(value["bust_rate"]),
                    pct(value["avg_hit_probability"]),
                ]
                for key, value in probability_buckets.items()
            ],
        )

        print("\nPOSSIBLE GATES: TOP %s/DAY BY HIT PROBABILITY" % args.gate_top_n)
        print_table(
            ["Gate", "Picks", "Hit%", "2+TB%", "Bust%", "AvgProb"],
            [
                [
                    key,
                    value["picks"],
                    pct(value["hit_rate"]),
                    pct(value["tb_2_plus_rate"]),
                    pct(value["bust_rate"]),
                    pct(value["avg_hit_probability"]),
                ]
                for key, value in gates.items()
            ],
        )

        output = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "input": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": args.days,
                "hit_form_days": args.hit_form_days,
                "top_ns": top_ns,
                "gate_top_n": args.gate_top_n,
                "min_pitcher_ip": args.min_pitcher_ip,
            },
            "db_meta": db_meta,
            "slate_meta": slate_meta,
            "baseline": baseline,
            "top_n_lift": lift,
            "gates": gates,
            "probability_buckets": probability_buckets,
            "batting_order_buckets": order_buckets,
            "rolling_k_buckets": k_buckets,
            "season_hit_rate_buckets": season_rate_buckets,
            "rolling_hit_rate_buckets": rolling_rate_buckets,
            "top_examples": sorted(
                scored_rows,
                key=lambda row: row["hit_probability"],
                reverse=True,
            )[:25],
            "limitations": [
                "Outcome backtest only; no historical odds or market comparison.",
                "Historical rows use completed-game confirmed lineups, not pregame projected-lineup uncertainty.",
                "BvP is neutral because the historical runner does not hydrate career batter-vs-pitcher pairs.",
                "Season and rolling hitter baselines are computed only from games before the scored date.",
            ],
        }

        output_path = Path(args.output_json) if args.output_json else None
        if output_path is None:
            DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output_path = DEFAULT_RESULTS_DIR / (
                f"hit_formula_season_{start.isoformat()}_{end.isoformat()}_{stamp}.json"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nSaved JSON: {output_path}")
        return 0
    finally:
        await db.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest the free 1+ hit model across season data.")
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD end date. Defaults to latest game-log date.")
    parser.add_argument("--days", type=int, default=120, help="Number of days ending at --end-date when --start-date is omitted.")
    parser.add_argument("--hit-form-days", type=int, default=5)
    parser.add_argument("--top-ns", default="5,10,15")
    parser.add_argument("--gate-top-n", type=int, default=10)
    parser.add_argument("--min-pitcher-ip", type=float, default=20.0)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--output-json", help="Optional detailed JSON output path.")
    return parser


def main() -> int:
    return asyncio.run(run(build_arg_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
