"""
predict_hits_today.py - Rank today's hitters by probability of getting 1+ hit.

Phase 5 of the hit-prediction plan: the daily product. One command that

  1. replays the season's cached boxscores through HitDatasetBuilder to
     (a) produce training rows and (b) leave the builder's form
     histories current through yesterday,
  2. trains the logistic model from train_hit_model.py on those rows,
  3. builds TODAY's candidates from the MLB schedule (probable pitchers)
     and each team's recent lineups (most recent lineup against a
     same-handed starter within the lookback, else most recent lineup),
  4. scores every candidate with the exact same feature code path used
     in training (HitDatasetBuilder.pregame_features), and prints the
     ranked list.

Projected lineups are a best guess until officials post — the printed
list marks each team's projection source.

Example:
    python backend/predict_hits_today.py --date 2026-07-04 --top 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import polars as pl
import statsapi
from databases import Database
from dotenv import load_dotenv

from build_hit_dataset import (
    BACKEND_DIR,
    DEFAULT_CACHE_DIR,
    BoxscoreSource,
    HitDatasetBuilder,
    parse_iso_date,
    pitcher_agg,
    safe_int,
)
from database import normalize_database_url
from hit_picks_store import close_picks_db, replace_picks
from park_factors import get_park_factor
from train_hit_model import FEATURES, make_models, prepare_frame, to_matrix

SEASON_START = "2026-03-25"
DEFAULT_RESULTS_DIR = BACKEND_DIR / "backtest_results"
LINEUP_LOOKBACK_DAYS = 14

# Version stamp written into every pick file so daily results can be
# attributed to the exact model generation that produced them.
#   v1: logistic, 2026 data only (68.2% top-10 walk-forward)
#   v2: gbm, trained on 2023-2025 + current 2026 (72.2% top-10)
MODEL_VERSION = "hit_gbm_v2"
MODEL_KIND = "gbm"

# Historical season parquets (built once by build_hit_dataset.py) that are
# prepended to the replayed current season at training time, if present.
HISTORICAL_GLOB = "hit_dataset_2*.parquet"


# ---------------------------------------------------------------------------
# Projected lineups from recent boxscores
# ---------------------------------------------------------------------------

def collect_recent_lineups(
    source: BoxscoreSource,
    builder: HitDatasetBuilder,
    end: date,
    lookback_days: int = LINEUP_LOOKBACK_DAYS,
) -> tuple[dict[str, list[dict[str, Any]]], dict[int, str]]:
    """
    Scan the last `lookback_days` of (cached) final boxscores and record,
    per team, each game's starting nine and the opposing starter's hand.
    Also returns an id -> name map for display.

    Side effect: fills gaps in builder.throws_by_pitcher from the
    boxscore feed, so probable pitchers missing from the DB still get a
    handedness (and therefore platoon features) today.
    """
    lineups: dict[str, list[dict[str, Any]]] = {}
    names: dict[int, str] = {}
    for offset in range(lookback_days, 0, -1):
        day = end - timedelta(days=offset)
        for slate_game in source.final_games(day):
            game = slate_game["game"]
            game_data = game.get("gameData", {})
            game_players = game_data.get("players", {})
            for person in game_players.values():
                pid = safe_int(person.get("id"))
                hand = ((person.get("pitchHand") or {}).get("code") or "").upper()
                if pid and hand and pid not in builder.throws_by_pitcher:
                    builder.throws_by_pitcher[pid] = hand
            box_teams = game.get("liveData", {}).get("boxscore", {}).get("teams", {})
            for side in ("away", "home"):
                other = "home" if side == "away" else "away"
                team = (box_teams.get(side, {}).get("team") or {}).get("name")
                order = [safe_int(pid) for pid in (box_teams.get(side, {}).get("battingOrder") or [])[:9]]
                if not team or len(order) < 9:
                    continue
                opp_pitchers = box_teams.get(other, {}).get("pitchers") or []
                opp_starter = safe_int(opp_pitchers[0]) if opp_pitchers else 0
                opp_hand = (
                    builder.throws_by_pitcher.get(opp_starter)
                    or ((game_players.get(f"ID{opp_starter}") or {}).get("pitchHand") or {}).get("code")
                )
                lineups.setdefault(team, []).append({
                    "date": day.isoformat(),
                    "opp_hand": opp_hand.upper() if opp_hand else None,
                    "order": order,
                })
                for pid in order:
                    person = game_players.get(f"ID{pid}") or {}
                    if person.get("fullName"):
                        names[pid] = person["fullName"]
    return lineups, names


def choose_projected_lineup(
    team_lineups: list[dict[str, Any]],
    opposing_hand: Optional[str],
) -> tuple[Optional[list[int]], str]:
    """
    Prefer the team's most recent lineup against a same-handed starter
    (platoon-aware teams sit different players vs L/R); otherwise fall
    back to their most recent lineup of any kind.
    """
    if not team_lineups:
        return None, "none"
    if opposing_hand:
        for entry in reversed(team_lineups):
            if entry["opp_hand"] == opposing_hand.upper():
                return entry["order"], f"last lineup vs {opposing_hand}HP ({entry['date']})"
    latest = team_lineups[-1]
    return latest["order"], f"most recent lineup ({latest['date']})"


# ---------------------------------------------------------------------------
# Today's slate from the schedule (probable pitchers hydrated)
# ---------------------------------------------------------------------------

def fetch_slate(target: date) -> list[dict[str, Any]]:
    """Schedule for `target` with probable pitchers. Not cached — today's
    schedule changes (postponements, pitcher scratches)."""
    data = statsapi.get("schedule", {
        "sportId": 1,
        "date": target.strftime("%m/%d/%Y"),
        "hydrate": "probablePitcher,team",
    })
    games = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def fill_missing_probable_hands(
    builder: HitDatasetBuilder,
    slate: list[dict[str, Any]],
) -> None:
    """One 'people' API call resolves handedness for any probable pitcher
    still unknown after the DB and boxscore-scan fallbacks (e.g. a
    starter returning from a long IL stint)."""
    unknown = []
    for game in slate:
        for side in ("away", "home"):
            probable = ((game.get("teams") or {}).get(side) or {}).get("probablePitcher") or {}
            pid = safe_int(probable.get("id"))
            if pid and pid not in builder.throws_by_pitcher:
                unknown.append(pid)
    if not unknown:
        return
    try:
        data = statsapi.get("people", {"personIds": ",".join(map(str, unknown))})
    except Exception as exc:
        print(f"Warning: could not resolve pitcher handedness: {exc}")
        return
    for person in data.get("people", []):
        pid = safe_int(person.get("id"))
        hand = ((person.get("pitchHand") or {}).get("code") or "").upper()
        if pid and hand:
            builder.throws_by_pitcher[pid] = hand


def build_candidates(
    builder: HitDatasetBuilder,
    slate: list[dict[str, Any]],
    lineups: dict[str, list[dict[str, Any]]],
    names: dict[int, str],
    target: date,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for game in slate:
        status = str((game.get("status") or {}).get("detailedState") or "")
        if status.lower() in {"postponed", "cancelled", "suspended"}:
            continue
        venue = (game.get("venue") or {}).get("name")
        park = get_park_factor(venue)
        teams = game.get("teams", {})

        for offense_side in ("away", "home"):
            defense_side = "home" if offense_side == "away" else "away"
            offense_team = ((teams.get(offense_side) or {}).get("team") or {}).get("name")
            defense_team = ((teams.get(defense_side) or {}).get("team") or {}).get("name")
            probable = (teams.get(defense_side) or {}).get("probablePitcher") or {}
            starter_id = safe_int(probable.get("id"))
            if not offense_team or not starter_id:
                continue
            throws = (
                builder.throws_by_pitcher.get(starter_id)
                or (probable.get("pitchHand") or {}).get("code")
            )
            order, lineup_source = choose_projected_lineup(
                lineups.get(offense_team, []), throws
            )
            if not order:
                continue

            pitcher_feats = builder.pitcher_features(starter_id)
            bullpen = pitcher_agg(builder.bullpen_history.get(defense_team, []))

            for slot, player_id in enumerate(order, start=1):
                bats = builder.bats_by_player.get(player_id)
                candidates.append({
                    "game_date": target.isoformat(),
                    "game_id": safe_int(game.get("gamePk")),
                    "player_id": player_id,
                    "player_name": names.get(player_id, str(player_id)),
                    "team": offense_team,
                    "opponent": defense_team,
                    "venue": venue,
                    "bats": bats,
                    "pitcher_id": starter_id,
                    "pitcher_name": probable.get("fullName") or str(starter_id),
                    "pitcher_throws": throws,
                    "lineup_source": lineup_source,
                    **builder.pregame_features(
                        player_id=player_id,
                        slot=slot,
                        is_home=offense_side == "home",
                        bats=bats,
                        throws=throws,
                        starter_id=starter_id,
                        park=park,
                        bullpen=bullpen,
                        pitcher_feats=pitcher_feats,
                        target=target,
                    ),
                })
    return candidates


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    env_path = BACKEND_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to backend/.env or your shell.")

    target = parse_iso_date(args.date) if args.date else date.today()
    train_end = target - timedelta(days=1)

    async_url, _ = normalize_database_url(raw_url)
    db = Database(async_url)
    await db.connect()
    try:
        source = BoxscoreSource(Path(args.cache_dir))
        builder = HitDatasetBuilder(db=db, source=source)
        await builder.load_db_context()

        print(f"Replaying season {SEASON_START} .. {train_end.isoformat()} for training data...")
        train_rows = builder.build(parse_iso_date(SEASON_START), train_end, verbose=False)
        if not train_rows:
            raise RuntimeError("No training rows produced — check cache/API access.")
        current = pl.DataFrame(train_rows, infer_schema_length=None).filter(pl.col("pa_game") > 0)

        frames = [current]
        historical = sorted((BACKEND_DIR / "data").glob(HISTORICAL_GLOB))
        for path in historical:
            frames.append(
                pl.read_parquet(path)
                .filter(pl.col("pa_game") > 0)
                .select(current.columns)
            )
            print(f"Adding historical training data: {path.name}")
        train_df = prepare_frame(pl.concat(frames, how="vertical_relaxed"))

        print(f"Training {MODEL_VERSION} on {train_df.height} batter-games...")
        model = make_models()[MODEL_KIND]
        model.fit(to_matrix(train_df), train_df["got_hit"].to_numpy())

        lineups, names = collect_recent_lineups(source, builder, target)
        slate = fetch_slate(target)
        fill_missing_probable_hands(builder, slate)
        candidates = build_candidates(builder, slate, lineups, names, target)
        if not candidates:
            print(f"No scoreable games for {target.isoformat()} (no probables/lineups yet).")
            return 1

        cand_df = prepare_frame(pl.DataFrame(candidates, infer_schema_length=None))
        probs = model.predict_proba(to_matrix(cand_df))[:, 1]
        cand_df = cand_df.with_columns(pl.Series("hit_probability", probs))
        ranked = cand_df.sort("hit_probability", descending=True)

        print(f"\nTOP {args.top} HIT CANDIDATES — {target.isoformat()}"
              f"  ({len(slate)} scheduled games, {cand_df.height} hitters scored)\n")
        header = (
            f"{'#':>2s}  {'player':22s} {'team':21s} {'slot':>4s} "
            f"{'prob':>6s}  {'L10 H/PA':>8s}  {'vs pitcher':28s}"
        )
        print(header)
        print("-" * len(header))
        for idx, row in enumerate(ranked.head(args.top).iter_rows(named=True), start=1):
            l10 = row.get("last10_hit_per_pa")
            l10_text = f"{l10:8.3f}" if l10 is not None else f"{'-':>8s}"
            hand = row.get("pitcher_throws") or "?"
            print(
                f"{idx:>2d}  {row['player_name']:22s} {str(row['team']):21s} "
                f"{row['batting_order']:>4d} {row['hit_probability'] * 100:5.1f}%  "
                f"{l10_text}  {row['pitcher_name']} ({hand}HP)"
            )

        keep = [
            "game_date", "player_id", "player_name", "team", "opponent", "venue",
            "batting_order", "bats", "pitcher_id", "pitcher_name", "pitcher_throws",
            "lineup_source", "hit_probability",
            "season_hit_per_pa", "last10_hit_per_pa", "platoon_advantage",
        ]
        output = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "date": target.isoformat(),
            "model_version": MODEL_VERSION,
            "model": f"{MODEL_KIND} (walk-forward validated in train_hit_model.py)",
            "trained_on_rows": train_df.height,
            "training_datasets": ["replayed current season"] + [p.name for p in historical],
            "candidates": ranked.select(keep).to_dicts(),
        }
        if args.output_json:
            output_path = Path(args.output_json)
        else:
            DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = DEFAULT_RESULTS_DIR / f"hit_picks_{target.isoformat()}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nSaved JSON: {output_path}")

        # Persist to the production database (PROD_DATABASE_URL, falling
        # back to DATABASE_URL) so the deployed backend can serve today's
        # list — the JSON file above only exists on this machine.
        try:
            stored = await replace_picks(
                pick_date=target.isoformat(),
                model_version=MODEL_VERSION,
                generated_at=output["generated_at"],
                trained_on_rows=train_df.height,
                candidates=output["candidates"],
            )
            print(f"Stored top {stored} picks in the picks database.")
        finally:
            await close_picks_db()
        print("Note: lineups are projected from recent boxscores until officials post.")
        return 0
    finally:
        await db.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank today's hitters by 1+ hit probability.")
    parser.add_argument("--date", help="Target YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--top", type=int, default=15, help="How many picks to print.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Shared MLB StatsAPI JSON cache directory.")
    parser.add_argument("--output-json", help="Optional path for the JSON pick list.")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
