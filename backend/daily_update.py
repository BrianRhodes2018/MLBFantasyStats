"""
daily_update.py - Scheduled MLB Stats Update Script
====================================================

This script is designed to be run daily (via Task Scheduler, cron, etc.)
to keep your MLB player statistics up-to-date during the baseball season.

How to use:

    1. WINDOWS TASK SCHEDULER:
       - Open Task Scheduler
       - Create Basic Task
       - Set trigger: Daily at your preferred time (e.g., 6 AM after overnight games finish)
       - Set action: Start a program
       - Program: python (or full path to python.exe)
       - Arguments: "C:\\path\\to\\daily_update.py"
       - Start in: "C:\\path\\to\\backend\\"

    2. LINUX/MAC CRON:
       Add to crontab (crontab -e):
       0 6 * * * cd /path/to/backend && python daily_update.py >> /var/log/mlb_update.log 2>&1

    3. MANUAL RUN:
       cd backend
       python daily_update.py

The script:
- Checks if we're in the MLB regular season (roughly late March to early October)
- Fetches the latest qualified batter stats from the MLB API
- Updates existing players' stats in the database
- Adds any newly qualified players
- Logs all activity for monitoring

MLB Season Schedule (approximate):
- Spring Training: Late February - Late March
- Regular Season: Late March - Early October
- Postseason: October
- Offseason: November - February
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from database import database, engine, metadata
from models import bet_suggestions, hitter_savant_snapshots

# Set up logging
log_file = Path(__file__).parent / 'logs' / 'mlb_updates.log'
log_file.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  # Also print to console
    ]
)
logger = logging.getLogger(__name__)


def record_successful_update_timestamp() -> None:
    """
    Record the current UTC timestamp as the time of the last successful update.

    Writes (or upserts) a single row in `system_metadata` keyed on
    "last_stats_update". The frontend reads this via /system/last-updated to
    show a "Stats last updated: ..." line under each page header.

    Uses the synchronous SQLAlchemy engine (same pattern as run_migrations) so
    we don't have to manage an async connection lifecycle here. The PostgreSQL
    `ON CONFLICT` clause makes this an idempotent upsert.

    Microseconds are stripped before serializing because the resulting string
    needs to fit in `system_metadata.updated_at` (VARCHAR(30)). Without the
    strip, isoformat() emits e.g. "2026-05-09T15:01:19.836014+00:00" (32 chars)
    and the INSERT fails with a StringDataRightTruncation error. Second-level
    precision is plenty for "when did the daily update last finish".
    """
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO system_metadata (key, value, updated_at)
                    VALUES ('last_stats_update', :ts, :ts)
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value,
                            updated_at = EXCLUDED.updated_at
                    """
                ),
                {"ts": now_iso},
            )
            conn.commit()
        logger.info(f"Recorded last_stats_update = {now_iso}")
    except Exception as e:
        # Don't fail the whole update just because we couldn't write the timestamp.
        # The stats data is what matters; the banner is a nicety.
        logger.warning(f"Failed to record last_stats_update timestamp: {e}")


async def snapshot_hitter_savant_stats() -> dict:
    """
    Phase 6 of the daily update — scrape Baseball Savant's expected-stats
    leaderboard and store one row per qualified hitter as today's snapshot.

    Why snapshots and not just "latest values":
        Savant publishes season aggregates. To derive rolling-window stats
        (last 50 PAs xwOBA, rolling Barrel/PA, etc.) we need the season
        numbers at TWO points in time and subtract. Each daily run records
        one point; after ~2 weeks of accumulation, the rolling subtraction
        math has usable data.

    Idempotency:
        Upsert by (player_mlb_id, snapshot_date) so re-running the workflow
        on the same day is a no-op rather than a dupe.

    Resilience:
        - Network or parse errors log a warning and exit cleanly — the
          rest of the daily update still completes.
        - Per-row errors during the bulk insert are absorbed (the row is
          skipped rather than aborting the whole batch).

    Returns:
        {"scraped_count": int, "inserted_count": int, "error": str | None}
    """
    import xwoba

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    needs_disconnect = False
    if not database.is_connected:
        await database.connect()
        needs_disconnect = True

    try:
        try:
            rows = await xwoba.fetch_savant_expected_stats()
        except Exception as e:
            logger.warning(f"Savant expected-stats scrape failed: {e}; skipping snapshot phase")
            return {"scraped_count": 0, "inserted_count": 0, "error": str(e)}

        logger.info(f"Scraped {len(rows)} hitter snapshots from Savant")

        inserted = 0
        for row in rows:
            payload = {
                "player_mlb_id": row["player_mlb_id"],
                "snapshot_date": today_str,
                "pa": row["pa"],
                "xwoba": row.get("xwoba"),
                "woba": row.get("woba"),
                "xba": row.get("xba"),
                "xslg": row.get("xslg"),
                "ba": row.get("ba"),
                "slg": row.get("slg"),
                "barrel_ct": row.get("barrel_ct"),
                "hard_hit_ct": row.get("hard_hit_ct"),
                "barrels_per_pa": row.get("barrels_per_pa"),
                "hard_hit_percent": row.get("hard_hit_percent"),
                "exit_velocity_avg": row.get("exit_velocity_avg"),
                "recorded_at": now_iso,
            }

            try:
                # Upsert pattern: delete any existing row for this
                # (player, date), then insert. Avoids needing a unique
                # constraint at the DB level — same approach we already
                # use elsewhere in the codebase.
                await database.execute(
                    hitter_savant_snapshots.delete().where(
                        (hitter_savant_snapshots.c.player_mlb_id == row["player_mlb_id"])
                        & (hitter_savant_snapshots.c.snapshot_date == today_str)
                    )
                )
                await database.execute(
                    hitter_savant_snapshots.insert().values(**payload)
                )
                inserted += 1
            except Exception as e:
                logger.warning(
                    f"Skipping Savant snapshot for player {row['player_mlb_id']}: {e}"
                )
                continue

        # Refresh the in-memory cache the betting endpoint reads from.
        # Pulling from the local `rows` list rather than the DB so even on
        # cold-start the cache is populated immediately after the daily
        # update finishes.
        xwoba.cache_latest_snapshots(rows)

        logger.info(f"Snapshot phase complete: {inserted}/{len(rows)} rows persisted")
        return {"scraped_count": len(rows), "inserted_count": inserted, "error": None}

    finally:
        if needs_disconnect:
            await database.disconnect()


async def backfill_bet_suggestion_actuals() -> dict:
    """
    Pull actual game stats for any bet_suggestions rows still missing actuals.

    Phase 5 of the daily update — runs after the season-stat and game-log
    refreshes so the MLB API has fresh data for yesterday's games. For every
    suggestion where suggested_date is in the past AND actual_recorded_at is
    NULL, fetches that player's gameLog for the season and matches the row's
    suggested_date to the corresponding game's stat line.

    Resilience:
        - One API call per (player, season) pair, so the cost is small
          (typically ~8 candidates/day -> ~8 calls).
        - If the API fetch fails for a player, we leave actual_recorded_at
          NULL so the next run retries. We don't want to mark "skipped" for
          transient errors and lose data forever.
        - If the API succeeds but the player didn't play that date, we mark
          actual_skip_reason = "did not play" + stamp actual_recorded_at so
          we stop retrying.

    Returns:
        dict with {scanned, backfilled, skipped} counts for logger output.
    """
    import statsapi

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Connect to database if not already (this function may be called from
    # a context that hasn't connected yet — daily_update runs as a script).
    needs_disconnect = False
    if not database.is_connected:
        await database.connect()
        needs_disconnect = True

    try:
        rows = await database.fetch_all(
            bet_suggestions.select().where(
                bet_suggestions.c.actual_recorded_at.is_(None)
                & (bet_suggestions.c.suggested_date < today_str)
            )
        )

        if not rows:
            logger.info("No bet suggestions need backfill")
            return {"scanned": 0, "backfilled": 0, "skipped": 0}

        logger.info(f"Backfilling actuals for {len(rows)} bet suggestion(s)...")

        # Group by (player_mlb_id, season) so one MLB API call covers all
        # of a given player's suggestion dates within that season.
        by_player_season = defaultdict(list)
        for row in rows:
            mapping = dict(row._mapping)
            season = mapping["suggested_date"][:4]  # "YYYY-MM-DD" -> "YYYY"
            by_player_season[(mapping["player_mlb_id"], season)].append(mapping)

        backfilled = 0
        skipped = 0
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        for (player_id, season), player_rows in by_player_season.items():
            try:
                data = await asyncio.to_thread(
                    statsapi.get,
                    "people",
                    {
                        "personIds": player_id,
                        "hydrate": f"stats(group=[hitting],type=[gameLog],season={season})",
                    },
                )
            except Exception as e:
                # Transient error — leave for retry next run rather than
                # poisoning the row with a permanent skip.
                logger.warning(f"  Player {player_id} season {season}: API error ({e}); will retry next run")
                continue

            # Build a date -> stat dict for this player.
            game_stats_by_date: dict[str, dict] = {}
            for person in data.get("people", []):
                for stat_group in person.get("stats", []):
                    if stat_group.get("group", {}).get("displayName") != "hitting":
                        continue
                    for split in stat_group.get("splits", []):
                        gd = split.get("date", "")
                        if gd:
                            game_stats_by_date[gd] = split.get("stat", {})

            for row in player_rows:
                game_stat = game_stats_by_date.get(row["suggested_date"])
                update_data = {"actual_recorded_at": now_iso}

                if game_stat:
                    # Player played — extract stats. Compute total bases from
                    # the parts since MLB API doesn't return TB directly.
                    h = game_stat.get("hits", 0) or 0
                    dbl = game_stat.get("doubles", 0) or 0
                    tpl = game_stat.get("triples", 0) or 0
                    hr = game_stat.get("homeRuns", 0) or 0
                    singles = max(h - dbl - tpl - hr, 0)  # guard against API quirks
                    tb = singles + 2 * dbl + 3 * tpl + 4 * hr

                    update_data.update({
                        "actual_at_bats": game_stat.get("atBats", 0) or 0,
                        "actual_hits": h,
                        "actual_doubles": dbl,
                        "actual_triples": tpl,
                        "actual_home_runs": hr,
                        "actual_total_bases": tb,
                        "actual_rbi": game_stat.get("rbi", 0) or 0,
                        "actual_runs": game_stat.get("runs", 0) or 0,
                        "actual_walks": game_stat.get("baseOnBalls", 0) or 0,
                        "actual_strikeouts": game_stat.get("strikeOuts", 0) or 0,
                    })
                    backfilled += 1
                else:
                    # Player was suggested but didn't actually play (scratched,
                    # bench, sent down). Stamp it so we stop retrying.
                    update_data["actual_skip_reason"] = "did not play"
                    skipped += 1

                await database.execute(
                    bet_suggestions.update()
                    .where(bet_suggestions.c.id == row["id"])
                    .values(**update_data)
                )

        logger.info(
            f"Backfill complete: {backfilled} populated, {skipped} marked 'did not play'"
        )
        return {"scanned": len(rows), "backfilled": backfilled, "skipped": skipped}

    finally:
        if needs_disconnect:
            await database.disconnect()


def is_mlb_season() -> bool:
    """
    Check if we're currently in the MLB regular season.

    Returns True roughly from late March through early October.
    During the offseason, we skip updates since stats don't change.

    The exact dates vary by year, so we use approximate boundaries:
    - Season typically starts: March 20-April 1
    - Season typically ends: September 29-October 2
    - Postseason runs through October

    Returns:
        True if it's currently baseball season, False otherwise
    """
    today = datetime.now()
    month = today.month
    day = today.day

    # Regular season: late March through September
    # Also include October for postseason updates
    if month == 3 and day >= 20:
        return True
    elif month in [4, 5, 6, 7, 8, 9]:
        return True
    elif month == 10:
        return True  # Postseason
    else:
        return False


async def run_daily_update(skip_gamelogs: bool = False):
    """
    Execute the daily stats update.

    Fetches current season stats from the MLB API and updates the database.
    Only runs during the baseball season to avoid unnecessary API calls.

    Runs in 4 phases:
      1. Update batter season stats (incremental, ~5-10 sec)
      2. Update pitcher season stats (incremental, ~5-10 sec)
      3. Refresh batter game logs (clear + reload, ~3-7 min)
      4. Refresh pitcher game logs (clear + reload, ~3-7 min)

    Args:
        skip_gamelogs: If True, skip phases 3 and 4 (game log refresh).
                       Useful for quick manual runs.
    """
    logger.info("=" * 60)
    logger.info("Starting MLB Stats Daily Update")
    logger.info("=" * 60)

    # Ensure tables and schema are up-to-date before writing. main.py creates
    # tables on startup via metadata.create_all(); we mirror that here so this
    # entry point doesn't depend on the FastAPI server having booted first
    # (e.g., a brand-new table like system_metadata that GitHub Actions may
    # need to write before Render has redeployed).
    metadata.create_all(bind=engine)

    from migrations import run_migrations
    run_migrations()

    # Check if we're in season
    if not is_mlb_season():
        logger.info("Currently offseason - skipping update")
        logger.info("Stats will resume updating when the season starts (late March)")
        return

    current_year = datetime.now().year
    # Phases 5 (bet-suggestion backfill) and 6 (Savant snapshot) always
    # run — both are cheap and decouple analytics features from the
    # heavier game-log refresh.
    total_phases = 4 if skip_gamelogs else 6
    logger.info(f"Updating stats for {current_year} season ({total_phases} phases)")

    try:
        from mlb_data_fetcher import update_player_stats, update_pitcher_stats, populate_game_logs

        # Phase 1: Update batter season stats
        logger.info(f"Phase 1/{total_phases}: Updating batter season stats...")
        await update_player_stats(season=current_year)
        logger.info("Batter stats updated successfully")

        # Phase 2: Update pitcher season stats
        logger.info(f"Phase 2/{total_phases}: Updating pitcher season stats...")
        await update_pitcher_stats(season=current_year)
        logger.info("Pitcher stats updated successfully")

        if not skip_gamelogs:
            # Phase 3: Refresh batter game logs
            logger.info(f"Phase 3/{total_phases}: Refreshing batter game logs...")
            await populate_game_logs(season=current_year, player_type='batters', clear_existing=True)
            logger.info("Batter game logs refreshed successfully")

            # Phase 4: Refresh pitcher game logs
            logger.info(f"Phase 4/{total_phases}: Refreshing pitcher game logs...")
            await populate_game_logs(season=current_year, player_type='pitchers', clear_existing=True)
            logger.info("Pitcher game logs refreshed successfully")

        # Phase N-1: Backfill actuals for past bet_suggestions rows.
        # Cheap (~1 API call per unique player), so always runs — it's how
        # the Bet Audit page fills in over time.
        backfill_phase_num = total_phases - 1
        logger.info(f"Phase {backfill_phase_num}/{total_phases}: Backfilling bet-suggestion actuals...")
        await backfill_bet_suggestion_actuals()
        logger.info("Bet-suggestion backfill complete")

        # Phase N: Snapshot today's Savant expected-stats for every
        # qualified hitter. Feeds the betting form-signal upgrade
        # (rolling xwOBA / Barrel-PA / Hard-Hit% via subtraction math)
        # once ~2 weeks of snapshots accumulate.
        logger.info(f"Phase {total_phases}/{total_phases}: Snapshotting Savant hitter stats...")
        await snapshot_hitter_savant_stats()
        logger.info("Savant snapshot complete")

        logger.info(f"Daily update completed successfully! (all {total_phases} phases)")
        record_successful_update_timestamp()

    except Exception as e:
        logger.error(f"Error during daily update: {e}", exc_info=True)
        raise


async def run_full_refresh(season: int = None, all_players: bool = False):
    """
    Perform a full database refresh (replaces all data).

    Use this for:
    - Initial setup
    - Start of a new season
    - Recovering from data corruption

    Args:
        season: The season to load. Defaults to current year.
        all_players: If True, fetch ALL active players (~1200+).
                     If False, only qualified batters (~129).
    """
    if season is None:
        season = datetime.now().year

    player_type = "ALL active players (~1200+)" if all_players else "qualified batters (~129)"

    logger.info("=" * 60)
    logger.info(f"Starting FULL REFRESH for {season} season")
    logger.info(f"Fetching: {player_type}")
    logger.info("WARNING: This will clear all existing player data!")
    logger.info("=" * 60)

    # Apply schema migrations before any writes (see run_daily_update for context).
    metadata.create_all(bind=engine)
    from migrations import run_migrations
    run_migrations()

    try:
        from mlb_data_fetcher import populate_database_from_mlb

        await populate_database_from_mlb(season=season, clear_existing=True, all_players=all_players)

        logger.info("Full refresh completed successfully!")
        record_successful_update_timestamp()

    except Exception as e:
        logger.error(f"Error during full refresh: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description='MLB Stats Daily Update Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python daily_update.py                          # Full daily update (stats + game logs)
  python daily_update.py --skip-gamelogs          # Quick update (stats only, ~10 sec)
  python daily_update.py --all                    # Full update with ALL active players
  python daily_update.py --force                  # Force update even in offseason
  python daily_update.py --refresh                # Full refresh with qualified batters
  python daily_update.py --refresh --all          # Full refresh with ALL active players (~1200+)
  python daily_update.py --refresh --season 2024  # Refresh with specific season
        """
    )
    parser.add_argument('--force', action='store_true',
                        help='Force update even during offseason')
    parser.add_argument('--refresh', action='store_true',
                        help='Full refresh (clears and reloads all data)')
    parser.add_argument('--season', type=int, default=None,
                        help='Season year for refresh (default: current year)')
    parser.add_argument('--all', action='store_true',
                        help='Fetch ALL active players (~1200) instead of just qualified batters (~129)')
    parser.add_argument('--skip-gamelogs', action='store_true',
                        help='Skip game log refresh (phases 3-4) for a quick stats-only update')

    args = parser.parse_args()

    if args.refresh:
        # Full refresh mode
        asyncio.run(run_full_refresh(args.season, all_players=args.all))
    elif args.force:
        # Force update (bypass season check)
        logger.info("Force flag set - bypassing season check")
        metadata.create_all(bind=engine)
        from migrations import run_migrations
        run_migrations()
        from mlb_data_fetcher import update_player_stats, update_pitcher_stats
        season = args.season or datetime.now().year
        asyncio.run(update_player_stats(season, all_players=args.all))
        asyncio.run(update_pitcher_stats(season, all_pitchers=args.all))
        record_successful_update_timestamp()
    else:
        # Normal daily update (all 4 phases unless --skip-gamelogs)
        asyncio.run(run_daily_update(skip_gamelogs=args.skip_gamelogs))
