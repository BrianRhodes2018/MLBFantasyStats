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
from datetime import datetime
from pathlib import Path

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

    # Check if we're in season
    if not is_mlb_season():
        logger.info("Currently offseason - skipping update")
        logger.info("Stats will resume updating when the season starts (late March)")
        return

    current_year = datetime.now().year
    total_phases = 2 if skip_gamelogs else 4
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

        logger.info(f"Daily update completed successfully! (all {total_phases} phases)")

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

    try:
        from mlb_data_fetcher import populate_database_from_mlb

        await populate_database_from_mlb(season=season, clear_existing=True, all_players=all_players)

        logger.info("Full refresh completed successfully!")

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
        from mlb_data_fetcher import update_player_stats, update_pitcher_stats
        season = args.season or datetime.now().year
        asyncio.run(update_player_stats(season, all_players=args.all))
        asyncio.run(update_pitcher_stats(season, all_pitchers=args.all))
    else:
        # Normal daily update (all 4 phases unless --skip-gamelogs)
        asyncio.run(run_daily_update(skip_gamelogs=args.skip_gamelogs))
