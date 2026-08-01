"""Score today's probable starters with all three Pitcher Ks approaches."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pickle
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

import statsapi
from databases import Database
from dotenv import load_dotenv

from build_hit_dataset import BoxscoreSource, DEFAULT_CACHE_DIR, parse_iso_date, safe_int
from database import normalize_database_url
from migrations import run_migrations
from pitcher_ks.features import replay_current_season
from pitcher_ks.modeling import APPROACH_ORDER, score_approaches
from pitcher_ks.store import store_daily_bundle
from train_pitcher_ks import DEFAULT_ARTIFACT


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = BACKEND_DIR / "backtest_results" / "pitcher_ks" / "daily"


def fetch_slate(target: date) -> list[dict[str, Any]]:
    payload = statsapi.get("schedule", {
        "sportId": 1,
        "date": target.strftime("%m/%d/%Y"),
        "hydrate": "probablePitcher,team",
    })
    games: list[dict[str, Any]] = []
    for day in payload.get("dates") or []:
        games.extend(day.get("games") or [])
    return games


def hydrate_live_context(
    slate: list[Mapping[str, Any]],
) -> tuple[dict[int, dict[str, list[int]]], dict[int, str], dict[int, str]]:
    confirmed: dict[int, dict[str, list[int]]] = {}
    hands: dict[int, str] = {}
    names: dict[int, str] = {}
    for game in slate:
        game_pk = safe_int(game.get("gamePk"))
        status = str((game.get("status") or {}).get("detailedState") or "").lower()
        if not game_pk or status in {"postponed", "cancelled", "suspended"}:
            continue
        try:
            feed = statsapi.get("game", {"gamePk": game_pk})
        except Exception as exc:
            print(f"Warning: live game context unavailable for {game_pk}: {exc}")
            continue
        for person in (feed.get("gameData", {}).get("players") or {}).values():
            player_id = safe_int(person.get("id"))
            if not player_id:
                continue
            if person.get("fullName"):
                names[player_id] = person["fullName"]
            hand = ((person.get("pitchHand") or {}).get("code") or "").upper()
            if hand:
                hands[player_id] = hand
        box_teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
        for side in ("away", "home"):
            order = [safe_int(value) for value in (box_teams.get(side, {}).get("battingOrder") or [])[:9]]
            order = [value for value in order if value]
            if len(order) == 9:
                confirmed.setdefault(game_pk, {})[side] = order
    return confirmed, hands, names


def _cohort_id(candidates: list[Mapping[str, Any]]) -> str:
    identities = sorted(
        f"{candidate['game_pk']}|{candidate['pitcher_id']}"
        for candidate in candidates
    )
    if len(identities) != len(set(identities)):
        raise ValueError("Daily Pitcher Ks candidate slate has duplicate identities.")
    return sha256("\n".join(identities).encode("utf-8")).hexdigest()


async def run(args: argparse.Namespace) -> int:
    load_dotenv(BACKEND_DIR / ".env")
    target = parse_iso_date(args.date) if args.date else date.today()
    artifact_path = Path(args.artifact)
    if not artifact_path.exists():
        raise RuntimeError(
            f"Pitcher Ks artifact is missing: {artifact_path}. "
            "Run train_pitcher_ks.py first."
        )
    package = pickle.loads(artifact_path.read_bytes())
    if str(package["trained_through"]) >= target.isoformat():
        raise RuntimeError(
            "Refusing to score a date at or before the artifact's training cutoff; "
            "that would leak outcomes into the projection."
        )

    source = BoxscoreSource(Path(args.cache_dir), request_delay_seconds=args.request_delay_seconds)
    print(f"Replaying {target.year} through {target.isoformat()} without same-day outcomes...")
    builder = replay_current_season(source=source, target=target, verbose=False)
    slate = fetch_slate(target)
    confirmed, live_hands, live_names = hydrate_live_context(slate)
    builder.pitcher_hands.update(live_hands)
    builder.player_names.update(live_names)
    candidates = builder.daily_candidates(
        slate=slate,
        target=target,
        confirmed_lineups=confirmed,
    )
    if not candidates:
        print(f"No probable starters are scoreable for {target.isoformat()}.")
        return 0

    cohort_id = _cohort_id(candidates)
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    as_of_timestamp = as_of.isoformat()
    prediction_window = args.prediction_window or (
        "morning"
        if as_of.astimezone(ZoneInfo("America/New_York")).hour < 12
        else "afternoon"
    )
    comparison_group_id = str(uuid5(
        NAMESPACE_URL,
        "|".join((
            "mlb-fantasy-stats/pitcher-ks",
            target.isoformat(),
            prediction_window,
            as_of_timestamp,
            cohort_id,
        )),
    ))
    scored = score_approaches(package, candidates)
    aggregate_backtest = package.get("backtest", {}).get("aggregate", {})
    bundle = {
        "projection_date": target.isoformat(),
        "generated_at": as_of_timestamp,
        "as_of_timestamp": as_of_timestamp,
        "prediction_window": prediction_window,
        "comparison_group_id": comparison_group_id,
        "candidate_cohort_id": cohort_id,
        "runs": {},
    }
    for approach in APPROACH_ORDER:
        bundle["runs"][approach] = {
            "run_id": str(uuid4()),
            "model_version": f"{package['model_version']}_{approach}",
            "trained_through": package["trained_through"],
            "trained_on_rows": package["trained_on_rows"],
            "backtest": aggregate_backtest.get(approach) or {},
            "model_manifest": {
                "feature_names": package["feature_names"],
                "data_identity_sha256": package["data_profile"]["identity_sha256"],
                "artifact": artifact_path.name,
            },
            "predictions": scored[approach],
        }

    output_path = Path(args.output) if args.output else DEFAULT_RESULTS_DIR / f"pitcher_ks_{target.isoformat()}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        **{key: value for key, value in bundle.items() if key != "runs"},
        "runs": bundle["runs"],
    }
    output_path.write_text(json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8")

    raw_url = os.environ.get("PITCHER_KS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError("DATABASE_URL is required to publish Pitcher Ks projections.")
    run_migrations()
    async_url, _ = normalize_database_url(raw_url)
    db = Database(async_url)
    await db.connect()
    try:
        counts = await store_daily_bundle(bundle, db=db)
    finally:
        await db.disconnect()

    for approach in APPROACH_ORDER:
        ranked = sorted(
            scored[approach],
            key=lambda row: -float(row["projected_ks"]),
        )
        leader = ranked[0]
        print(
            f"{approach:10s}: {counts[approach]:2d} starters; "
            f"leader {leader['pitcher_name']} {leader['projected_ks']:.2f} Ks"
        )
    print(f"Saved daily bundle: {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project strikeouts for today's probable starters.")
    parser.add_argument("--date", help="Target YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--output")
    parser.add_argument("--prediction-window", choices=("morning", "afternoon"))
    parser.add_argument("--request-delay-seconds", type=float, default=0.02)
    return parser


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
