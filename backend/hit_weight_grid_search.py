"""
hit_weight_grid_search.py - Grid search for a 1+ hit candidate score.

This runner uses the historical slate/candidate builder from
outcome_backtest.py, but evaluates a much larger integer-weight search space
for a hit-focused score:

    score = form*w_form + pitcher*w_pitcher + platoon*w_platoon
          + park*w_park + bvp*w_bvp

The target is simple: did the hitter record at least one hit?

Example:
    python backend/hit_weight_grid_search.py --days 30 --profile medium
"""

from __future__ import annotations

import argparse
import asyncio
import heapq
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from databases import Database
from dotenv import load_dotenv

from database import normalize_database_url
from outcome_backtest import (
    DEFAULT_CACHE_DIR,
    DEFAULT_RESULTS_DIR,
    OutcomeBacktester,
    latest_game_log_date,
    parse_iso_date,
)


@dataclass(frozen=True)
class HitWeightConfig:
    form: int
    pitcher: int
    platoon: int
    park: int
    bvp: int

    @property
    def name(self) -> str:
        return (
            f"f{self.form}_p{self.pitcher}_"
            f"pl{self.platoon}_park{self.park}_bvp{self.bvp}"
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "form": self.form,
            "pitcher": self.pitcher,
            "platoon": self.platoon,
            "park": self.park,
            "bvp": self.bvp,
        }


SEARCH_PROFILES = {
    "narrow": {
        "form": range(35, 56),
        "pitcher": range(18, 36),
        "platoon": range(18, 36),
        "park": range(0, 7),
        "bvp": range(0, 4),
    },
    "medium": {
        "form": range(30, 61),
        "pitcher": range(15, 41),
        "platoon": range(15, 41),
        "park": range(0, 9),
        "bvp": range(0, 6),
    },
    "wide": {
        "form": range(25, 66),
        "pitcher": range(10, 46),
        "platoon": range(10, 46),
        "park": range(0, 11),
        "bvp": range(0, 6),
    },
}


def generate_weight_configs(profile: str) -> list[HitWeightConfig]:
    space = SEARCH_PROFILES[profile]
    configs = []
    for form in space["form"]:
        for pitcher in space["pitcher"]:
            for platoon in space["platoon"]:
                for park in space["park"]:
                    bvp = 100 - form - pitcher - platoon - park
                    if bvp in space["bvp"]:
                        configs.append(
                            HitWeightConfig(
                                form=form,
                                pitcher=pitcher,
                                platoon=platoon,
                                park=park,
                                bvp=bvp,
                            )
                        )
    return configs


def park_hit_value(park_multiplier: Optional[float]) -> float:
    """
    Convert the existing park multiplier into a neutral-centered [0, 1] feature.

    Neutral park (100 runs factor) maps to 0.5. Very hitter-friendly parks
    approach 1.0; pitcher-friendly parks move below 0.5. This keeps the
    feature compatible with platoon/form/pitcher scores.
    """
    if park_multiplier is None:
        return 0.5
    runs_factor = park_multiplier * 100.0
    return max(0.0, min(1.0, 0.5 + ((runs_factor - 100.0) / 34.0)))


def candidate_features(candidate: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    values = candidate["signal_values"]
    return (
        float(values["recent_form"]),
        float(values["pitcher_vulnerability"]),
        float(values["platoon"]),
        park_hit_value(candidate.get("park_multiplier")),
        float(values["bvp"]),
    )


def hit_score(config: HitWeightConfig, features: tuple[float, float, float, float, float]) -> float:
    form, pitcher, platoon, park, bvp = features
    return (
        config.form * form
        + config.pitcher * pitcher
        + config.platoon * platoon
        + config.park * park
        + config.bvp * bvp
    )


def summarize_picks(picks: list[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(picks)
    if count == 0:
        return {
            "picks": 0,
            "hit_rate": None,
            "tb_2_plus_rate": None,
            "hr_rate": None,
            "avg_total_bases": None,
            "bust_rate": None,
        }

    def rate(key: str) -> float:
        return sum(1 for pick in picks if pick["outcome"][key]) / count

    return {
        "picks": count,
        "hit_rate": round(rate("hit"), 4),
        "tb_2_plus_rate": round(rate("tb_2_plus"), 4),
        "hr_rate": round(rate("hr"), 4),
        "avg_total_bases": round(
            sum(pick["outcome"]["total_bases"] for pick in picks) / count,
            4,
        ),
        "bust_rate": round(rate("bust"), 4),
    }


def pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def split_days(all_days: list[str], holdout_days: int) -> tuple[set[str], set[str]]:
    ordered = sorted(all_days)
    holdout = set(ordered[-holdout_days:])
    train = set(ordered[:-holdout_days])
    return train, holdout


def build_day_rows(candidates: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        row = dict(candidate)
        row["_features"] = candidate_features(candidate)
        by_day.setdefault(row["date"], []).append(row)
    return by_day


def selected_picks_for_config(
    by_day: Mapping[str, list[dict[str, Any]]],
    config: HitWeightConfig,
    *,
    top_n: int,
    allowed_days: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    picks = []
    for day, rows in by_day.items():
        if allowed_days is not None and day not in allowed_days:
            continue
        scored_rows = [
            (hit_score(config, row["_features"]), idx, row)
            for idx, row in enumerate(rows)
        ]
        for score, _, row in heapq.nlargest(top_n, scored_rows, key=lambda item: item[0]):
            picked = {
                "date": row["date"],
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "batting_order": row["batting_order"],
                "score": round(score, 2),
                "signals": row["signals"],
                "outcome": row["outcome"],
            }
            picks.append(picked)
    return picks


def result_key(result: Mapping[str, Any]) -> tuple[float, float, float, float]:
    train_hit = result["train"]["hit_rate"] or 0.0
    holdout_hit = result["holdout"]["hit_rate"] or 0.0
    all_hit = result["all"]["hit_rate"] or 0.0
    holdout_bust = result["holdout"]["bust_rate"] or 1.0
    return (min(train_hit, holdout_hit), holdout_hit, all_hit, -holdout_bust)


def keep_best(
    leaderboard: list[tuple[tuple[float, float, float, float], int, dict[str, Any]]],
    result: dict[str, Any],
    *,
    counter: int,
    limit: int,
) -> None:
    entry = (result_key(result), counter, result)
    if len(leaderboard) < limit:
        heapq.heappush(leaderboard, entry)
        return
    if entry[0] > leaderboard[0][0]:
        heapq.heapreplace(leaderboard, entry)


def evaluate_grid(
    by_day: Mapping[str, list[dict[str, Any]]],
    configs: list[HitWeightConfig],
    *,
    top_ns: list[int],
    holdout_days: int,
    keep: int,
) -> dict[str, list[dict[str, Any]]]:
    train_days, holdout_days_set = split_days(list(by_day.keys()), holdout_days)
    leaderboards: dict[int, list[tuple[tuple[float, float, float, float], int, dict[str, Any]]]] = {
        top_n: []
        for top_n in top_ns
    }
    counter = 0
    for config in configs:
        counter += 1
        for top_n in top_ns:
            all_picks = selected_picks_for_config(by_day, config, top_n=top_n)
            train_picks = [pick for pick in all_picks if pick["date"] in train_days]
            holdout_picks = [pick for pick in all_picks if pick["date"] in holdout_days_set]
            result = {
                "config": config.as_dict(),
                "config_name": config.name,
                "top_n_per_day": top_n,
                "all": summarize_picks(all_picks),
                "train": summarize_picks(train_picks),
                "holdout": summarize_picks(holdout_picks),
                "top_examples": all_picks[:10],
            }
            keep_best(leaderboards[top_n], result, counter=counter, limit=keep)

    return {
        str(top_n): [
            entry[2]
            for entry in sorted(leaderboards[top_n], key=lambda item: item[0], reverse=True)
        ]
        for top_n in top_ns
    }


def feature_baselines(
    by_day: Mapping[str, list[dict[str, Any]]],
    *,
    top_ns: list[int],
) -> dict[str, dict[str, Any]]:
    features = {
        "recent_form": lambda row: row["_features"][0],
        "pitcher_vulnerability": lambda row: row["_features"][1],
        "platoon": lambda row: row["_features"][2],
        "park": lambda row: row["_features"][3],
    }
    output: dict[str, dict[str, Any]] = {}
    for feature_name, scorer in features.items():
        output[feature_name] = {}
        for top_n in top_ns:
            picks = []
            for rows in by_day.values():
                scored = [
                    (scorer(row), idx, row)
                    for idx, row in enumerate(rows)
                ]
                picks.extend(
                    row
                    for _, _, row in heapq.nlargest(top_n, scored, key=lambda item: item[0])
                )
            output[feature_name][str(top_n)] = summarize_picks(picks)
    return output


def signal_lift(candidates: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = list(candidates)
    base = summarize_picks(rows)
    output = {"baseline": base}
    for signal_name in ["recent_form", "pitcher_vulnerability", "platoon", "park_factor"]:
        fired = [
            row
            for row in rows
            if row["signals"].get(signal_name, {}).get("fired")
        ]
        output[signal_name] = summarize_picks(fired)
    return output


async def build_candidates_for_windows(
    db: Database,
    *,
    start: date,
    end: date,
    form_windows: list[int],
    cache_dir: Path,
    min_pitcher_ip: float,
    refresh_cache: bool,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    candidates_by_window = {}
    meta_by_window = {}
    for window in form_windows:
        backtester = OutcomeBacktester(
            db=db,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
            min_pitcher_ip=min_pitcher_ip,
            rolling_days=window,
        )
        await backtester.load_db_context()
        candidates, meta = await backtester.build_candidates(start, end, verbose=False)
        candidates_by_window[str(window)] = candidates
        meta_by_window[str(window)] = meta
        print(
            f"window={window:2d} days: "
            f"games={meta['total_games']} eligible={meta['total_eligible_batters']}"
        )
    return candidates_by_window, meta_by_window


def print_grid_summary(results: Mapping[str, Any], *, keep: int) -> None:
    print("\nBEST STABLE HIT-RATE CONFIGS")
    for window, window_result in results["windows"].items():
        print(f"\nForm window: {window} days")
        for top_n, leaders in window_result["leaderboards"].items():
            print(f"  Top {top_n}/day")
            for rank, result in enumerate(leaders[: min(5, keep)], start=1):
                config = result["config"]
                print(
                    f"    {rank}. hit all={pct(result['all']['hit_rate'])} "
                    f"train={pct(result['train']['hit_rate'])} "
                    f"holdout={pct(result['holdout']['hit_rate'])} "
                    f"bust holdout={pct(result['holdout']['bust_rate'])} "
                    f"weights={config}"
                )

    print("\nSINGLE-FEATURE SORT BASELINES")
    for window, window_result in results["windows"].items():
        print(f"\nForm window: {window} days")
        baselines = window_result["feature_baselines"]
        for feature_name, by_top_n in baselines.items():
            row = by_top_n.get("10")
            if not row:
                continue
            print(
                f"  {feature_name:22s} top10/day hit={pct(row['hit_rate'])} "
                f"2+TB={pct(row['tb_2_plus_rate'])} bust={pct(row['bust_rate'])}"
            )


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
        form_windows = [int(part.strip()) for part in args.form_windows.split(",") if part.strip()]
        top_ns = [int(part.strip()) for part in args.top_ns.split(",") if part.strip()]
        configs = generate_weight_configs(args.profile)

        print("HIT WEIGHT GRID SEARCH")
        print(f"Dates: {start.isoformat()} through {end.isoformat()}")
        print(f"Profile: {args.profile} ({len(configs)} configs)")
        print(f"Form windows: {form_windows}")
        print(f"Top-N cuts: {top_ns}")
        print(f"Holdout: last {args.holdout_days} days")
        print("Target: 1+ hit")
        print("BvP note: neutral/no-fire in the candidate builder for this run.")
        print()

        candidates_by_window, meta_by_window = await build_candidates_for_windows(
            db,
            start=start,
            end=end,
            form_windows=form_windows,
            cache_dir=Path(args.cache_dir),
            min_pitcher_ip=args.min_pitcher_ip,
            refresh_cache=args.refresh_cache,
        )

        window_results = {}
        for window in form_windows:
            key = str(window)
            by_day = build_day_rows(candidates_by_window[key])
            print(f"evaluating window={window} days...")
            leaderboards = evaluate_grid(
                by_day,
                configs,
                top_ns=top_ns,
                holdout_days=args.holdout_days,
                keep=args.keep,
            )
            window_results[key] = {
                "leaderboards": leaderboards,
                "feature_baselines": feature_baselines(by_day, top_ns=top_ns),
                "signal_lift": signal_lift(candidates_by_window[key]),
                "meta": meta_by_window[key],
            }

        output = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "input": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": args.days,
                "profile": args.profile,
                "config_count": len(configs),
                "form_windows": form_windows,
                "top_ns": top_ns,
                "holdout_days": args.holdout_days,
                "min_pitcher_ip": args.min_pitcher_ip,
            },
            "search_space": {
                name: [min(values), max(values)]
                for name, values in SEARCH_PROFILES[args.profile].items()
            },
            "windows": window_results,
            "limitations": [
                "Outcome backtest only; no historical sportsbook odds or ROI.",
                "BvP is neutral/no-fire because historical pair fetches are not hydrated here.",
                "Final app score should be validated on more dates before treating this as predictive.",
            ],
        }

        print_grid_summary(output, keep=args.keep)

        output_path = Path(args.output_json) if args.output_json else None
        if output_path is None:
            DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output_path = DEFAULT_RESULTS_DIR / (
                f"hit_weight_grid_{start.isoformat()}_{end.isoformat()}_"
                f"{args.profile}_{stamp}.json"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nSaved JSON: {output_path}")
        return 0
    finally:
        await db.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grid search hit-focused hitter weights.")
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD end date. Defaults to latest game-log date.")
    parser.add_argument("--days", type=int, default=30, help="Number of days ending at --end-date.")
    parser.add_argument("--profile", choices=sorted(SEARCH_PROFILES), default="medium")
    parser.add_argument("--form-windows", default="5,8,10,14", help="Comma-separated rolling form windows.")
    parser.add_argument("--top-ns", default="5,10,15", help="Comma-separated daily pick counts to evaluate.")
    parser.add_argument("--holdout-days", type=int, default=10, help="Last N dates used as holdout.")
    parser.add_argument("--min-pitcher-ip", type=float, default=20.0)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--keep", type=int, default=10, help="Leaderboard entries retained per window/top-N.")
    parser.add_argument("--output-json", help="Optional detailed JSON output path.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
