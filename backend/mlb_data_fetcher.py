"""
mlb_data_fetcher.py - MLB Stats API Data Fetcher
=================================================

This module fetches real MLB player statistics from the official MLB Stats API
using the MLB-StatsAPI Python wrapper. It's used to:
1. Populate the database with season-ending stats
2. Update stats daily during the regular season

Key concepts demonstrated:
- Using the MLB Stats API (free, no API key required, NO DAILY LIMITS)
- Converting API responses to Polars DataFrames
- Async database operations for bulk inserts/updates
- Data transformation and cleaning

The MLB Stats API provides:
- Player info (name, team, position)
- Batting stats (avg, hr, rbi, sb, ops, etc.)
- Historical data by season
- Real-time updates during the season
- ALL active players on 40-man rosters (~1200 players)

API Documentation: https://appac.github.io/mlb-data-api-docs/

Usage:
    # Fetch and display sample batters (doesn't save to DB)
    python mlb_data_fetcher.py --season 2025 --preview

    # Fetch all batters with stats (~400-500 players) and save to database
    python mlb_data_fetcher.py --season 2025 --save

    # Fetch ALL rostered players (~1200+) including those with 0 at-bats
    # This takes 2-3 minutes as it fetches every player from all 30 teams
    python mlb_data_fetcher.py --season 2025 --all --save

    # Update existing players with latest stats
    python mlb_data_fetcher.py --update

    # Update ALL active players with latest stats (roster-based)
    python mlb_data_fetcher.py --all --update
"""

import statsapi
import polars as pl
from datetime import datetime
from typing import Optional
import asyncio


def get_qualified_batters(season: int, limit: int = 1000, qualified_only: bool = False) -> pl.DataFrame:
    """
    Fetch batters' statistics from the MLB Stats API.

    By default, fetches ALL batters with stats (playerPool='ALL'), giving you
    ~700-800 players — everyone who recorded at least one at-bat during the
    season. This is ideal for fantasy baseball apps where you want the full
    player pool, not just batting-title qualifiers.

    When qualified_only=True, uses playerPool='QUALIFIED' to fetch only batters
    with enough plate appearances for the batting title (~3.1 PA per team game,
    ~502 PA/season, yielding ~129-145 players). Useful for game log fetching
    where you want a smaller, more relevant set of players.

    How it works:
    1. Call the MLB Stats API 'stats' endpoint with hitting group
    2. Sort by home runs descending to get power hitters first
    3. Extract player info, team, position, and relevant stats
    4. Convert to a Polars DataFrame for easy manipulation

    Args:
        season: The MLB season year (e.g., 2024)
        limit: Maximum number of players to fetch (default 1000)
        qualified_only: If True, only fetch batting-title qualifiers (~129-145).
                        If False (default), fetch ALL players with stats (~700-800).

    Returns:
        pl.DataFrame with columns: name, team, position, batting_average,
        home_runs, rbi, stolen_bases, ops
    """
    # Choose the player pool based on qualified_only flag.
    # 'QUALIFIED' = only players meeting minimum PA threshold for batting title
    # 'ALL' = every player who has recorded at-bats this season
    pool = 'QUALIFIED' if qualified_only else 'ALL'
    pool_label = "qualified" if qualified_only else "all"
    print(f"Fetching {season} season batting stats from MLB API ({pool_label} players, limit={limit})...")

    # Call the MLB Stats API
    # stats: 'season' gets full season totals (not per-game)
    # group: 'hitting' filters to batting stats only
    # playerPool: Controls which players are included (ALL vs QUALIFIED)
    # sortStat: 'homeRuns' orders by HR descending
    stats_data = statsapi.get('stats', {
        'stats': 'season',
        'group': 'hitting',
        'season': season,
        'sportId': 1,           # 1 = MLB (not minor leagues)
        'limit': limit,
        'order': 'desc',
        'sortStat': 'homeRuns',
        'playerPool': pool
    })

    # Extract the splits (each split is one player's season stats)
    splits = stats_data.get('stats', [{}])[0].get('splits', [])

    if not splits:
        print(f"No data found for {season} season")
        return pl.DataFrame()

    # Transform API response into a list of player records
    players = []
    for split in splits:
        stat = split.get('stat', {})
        player_info = split.get('player', {})
        team_info = split.get('team', {})
        position_info = split.get('position', {})

        # Extract the position abbreviation (CF, RF, SS, etc.)
        # The API returns position info for where they played most
        position = position_info.get('abbreviation', '')

        # Some positions need normalization (e.g., 'O' for generic outfielder)
        # Map generic positions to specific ones based on common usage
        position_map = {
            'O': 'OF',      # Generic outfielder
            'IF': 'IF',     # Generic infielder (rare)
            'P': 'P',       # Pitcher (rare for qualified batters)
        }
        position = position_map.get(position, position)

        # Parse batting average and OPS from string format
        # The API returns them as strings like ".322" and "1.159"
        try:
            batting_avg = float(stat.get('avg', '0').replace('.', '0.', 1) if stat.get('avg', '0').startswith('.') else stat.get('avg', '0'))
        except (ValueError, AttributeError):
            batting_avg = 0.0

        try:
            ops = float(stat.get('ops', '0'))
        except (ValueError, AttributeError):
            ops = 0.0

        # Build the player record.
        # mlb_id stores the MLB API player ID so we can link to game logs later.
        player_record = {
            'name': player_info.get('fullName', 'Unknown'),
            'team': team_info.get('name', 'Unknown').replace('New York ', 'NY ').replace('Los Angeles ', 'LA '),
            'position': position if position else None,
            'batting_average': batting_avg,
            'home_runs': stat.get('homeRuns', 0),
            'rbi': stat.get('rbi', 0),
            'stolen_bases': stat.get('stolenBases', 0),
            'ops': ops,
            'runs': stat.get('runs', 0),
            'strikeouts': stat.get('strikeOuts', 0),
            'total_bases': stat.get('totalBases', 0),
            'at_bats': stat.get('atBats', 0),
            'walks': stat.get('baseOnBalls', 0),          # BB - needed for OBP calculation
            'hit_by_pitch': stat.get('hitByPitch', 0),    # HBP - needed for OBP calculation
            'sacrifice_flies': stat.get('sacFlies', 0),   # SF - needed for OBP calculation
            # Fantasy-relevant stats — needed for accurate ESPN fantasy point calculations.
            # These are available from the MLB API but weren't previously fetched.
            'hits': stat.get('hits', 0),                    # H - Total hits
            'doubles': stat.get('doubles', 0),              # 2B - Doubles
            'triples': stat.get('triples', 0),              # 3B - Triples
            'caught_stealing': stat.get('caughtStealing', 0),  # CS - Caught stealing
            'games_played': stat.get('gamesPlayed', 0),      # G - Games played (for Pts/G)
            'mlb_id': player_info.get('id'),  # MLB API unique player ID
        }
        players.append(player_record)

    # Create a Polars DataFrame from the list of dicts
    df = pl.DataFrame(players)

    print(f"Fetched {len(df)} {pool_label} batters from {season} season")
    return df


def get_all_active_players(season: int) -> pl.DataFrame:
    """
    Fetch ALL active MLB players from all 30 team rosters with their batting stats.

    This function iterates through every MLB team's 40-man roster and fetches
    each player's batting stats for the specified season. This gives you ~1200+
    players instead of just the ~129 qualified batters.

    The MLB Stats API has NO daily limits and is completely FREE to use.

    How it works:
    1. Get all 30 MLB teams
    2. For each team, fetch the 40-man roster
    3. For each player, fetch their season batting stats
    4. Combine into a single DataFrame

    Note: This takes longer than get_qualified_batters() because it makes
    many more API calls (one per player for stats). Expect ~2-3 minutes.

    Args:
        season: The MLB season year (e.g., 2024)

    Returns:
        pl.DataFrame with columns: name, team, position, batting_average,
        home_runs, rbi, stolen_bases, ops
    """
    print(f"Fetching ALL active MLB players for {season} season...")
    print("This may take 2-3 minutes (fetching stats for ~1200 players)...")

    # Get all MLB teams
    teams_data = statsapi.get('teams', {'sportId': 1})
    teams = teams_data.get('teams', [])

    all_players = []
    total_teams = len(teams)

    for i, team in enumerate(teams, 1):
        team_id = team['id']
        team_name = team['name']
        # Shorten team names for display
        short_team = team_name.replace('New York ', 'NY ').replace('Los Angeles ', 'LA ')

        print(f"  [{i}/{total_teams}] Fetching {team_name}...", end='', flush=True)

        try:
            # Get the 40-man roster
            roster_data = statsapi.get('team_roster', {
                'teamId': team_id,
                'rosterType': '40Man',
                'season': season
            })
            roster = roster_data.get('roster', [])

            team_player_count = 0
            for player in roster:
                person = player.get('person', {})
                player_id = person.get('id')
                player_name = person.get('fullName', 'Unknown')
                position_info = player.get('position', {})
                position = position_info.get('abbreviation', '')

                # Skip pitchers (they rarely have meaningful batting stats)
                if position == 'P':
                    continue

                # Fetch this player's batting stats for the season
                try:
                    stats_data = statsapi.get('people', {
                        'personIds': player_id,
                        'hydrate': f'stats(group=[hitting],type=[season],season={season})'
                    })

                    if stats_data.get('people'):
                        person_data = stats_data['people'][0]
                        stats_list = person_data.get('stats', [])

                        # Find the season hitting stats
                        batting_stats = {}
                        for stat_group in stats_list:
                            if stat_group.get('group', {}).get('displayName') == 'hitting':
                                splits = stat_group.get('splits', [])
                                if splits:
                                    batting_stats = splits[0].get('stat', {})
                                    break

                        # Only include players with at-bats (skip if no batting data)
                        at_bats = batting_stats.get('atBats', 0)
                        if at_bats > 0:
                            # Parse stats
                            try:
                                avg_str = batting_stats.get('avg', '0')
                                batting_avg = float(avg_str) if avg_str else 0.0
                            except (ValueError, TypeError):
                                batting_avg = 0.0

                            try:
                                ops_str = batting_stats.get('ops', '0')
                                ops = float(ops_str) if ops_str else 0.0
                            except (ValueError, TypeError):
                                ops = 0.0

                            # Build the player record.
                            # mlb_id stores the MLB API player ID so we can
                            # link this player to their per-game data later.
                            player_record = {
                                'name': player_name,
                                'team': short_team,
                                'position': position if position else None,
                                'batting_average': batting_avg,
                                'home_runs': batting_stats.get('homeRuns', 0),
                                'rbi': batting_stats.get('rbi', 0),
                                'stolen_bases': batting_stats.get('stolenBases', 0),
                                'ops': ops,
                                'runs': batting_stats.get('runs', 0),
                                'strikeouts': batting_stats.get('strikeOuts', 0),
                                'total_bases': batting_stats.get('totalBases', 0),
                                'at_bats': batting_stats.get('atBats', 0),
                                'walks': batting_stats.get('baseOnBalls', 0),          # BB - needed for OBP calculation
                                'hit_by_pitch': batting_stats.get('hitByPitch', 0),    # HBP - needed for OBP calculation
                                'sacrifice_flies': batting_stats.get('sacFlies', 0),   # SF - needed for OBP calculation
                                # Fantasy-relevant stats
                                'hits': batting_stats.get('hits', 0),
                                'doubles': batting_stats.get('doubles', 0),
                                'triples': batting_stats.get('triples', 0),
                                'caught_stealing': batting_stats.get('caughtStealing', 0),
                                'games_played': batting_stats.get('gamesPlayed', 0),  # G - Games played (for Pts/G)
                                'mlb_id': player_id,  # MLB API unique player ID
                            }
                            all_players.append(player_record)
                            team_player_count += 1

                except Exception as e:
                    # Skip players whose stats can't be fetched
                    pass

            print(f" {team_player_count} batters")

        except Exception as e:
            print(f" Error: {e}")

    # Create DataFrame
    df = pl.DataFrame(all_players)

    print(f"\nFetched {len(df)} total players with batting stats from {season} season")
    return df


def get_player_position(player_id: int) -> Optional[str]:
    """
    Fetch a specific player's primary position from the MLB API.

    Used when the stats endpoint doesn't include position info.

    Args:
        player_id: The MLB player ID

    Returns:
        Position abbreviation (e.g., "RF", "SS") or None if not found
    """
    try:
        player_data = statsapi.get('people', {'personIds': player_id})
        if player_data.get('people'):
            person = player_data['people'][0]
            position = person.get('primaryPosition', {}).get('abbreviation')
            return position
    except Exception as e:
        print(f"Error fetching position for player {player_id}: {e}")
    return None


# =============================================================================
# PITCHER DATA FETCHING
# =============================================================================

def get_qualified_pitchers(season: int, limit: int = 1000, qualified_only: bool = False) -> pl.DataFrame:
    """
    Fetch pitchers' statistics from the MLB Stats API.

    By default, fetches ALL pitchers with stats (playerPool='ALL'), giving you
    ~800-900 pitchers — everyone who pitched at least one inning during the
    season. This is ideal for fantasy baseball apps where you want the full
    pitcher pool, not just ERA-title qualifiers.

    When qualified_only=True, uses playerPool='QUALIFIED' to fetch only pitchers
    with enough innings pitched for the ERA title (~1 IP per team game, ~162 IP
    for starters, yielding ~70-80 pitchers). Useful for game log fetching
    where you want a smaller, more relevant set of pitchers.

    Args:
        season: The MLB season year (e.g., 2025)
        limit: Maximum number of pitchers to fetch (default 1000)
        qualified_only: If True, only fetch ERA-title qualifiers (~70-80).
                        If False (default), fetch ALL pitchers with stats (~800-900).

    Returns:
        pl.DataFrame with pitcher statistics
    """
    # Choose the player pool based on qualified_only flag.
    # 'QUALIFIED' = only pitchers meeting minimum IP threshold for ERA title
    # 'ALL' = every pitcher who has recorded innings this season
    pool = 'QUALIFIED' if qualified_only else 'ALL'
    pool_label = "qualified" if qualified_only else "all"
    print(f"Fetching {season} season pitching stats from MLB API ({pool_label} pitchers, limit={limit})...")

    stats_data = statsapi.get('stats', {
        'stats': 'season',
        'group': 'pitching',
        'season': season,
        'sportId': 1,
        'limit': limit,
        'order': 'asc',
        'sortStat': 'earnedRunAverage',
        'playerPool': pool
    })

    splits = stats_data.get('stats', [{}])[0].get('splits', [])

    if not splits:
        print(f"No pitching data found for {season} season")
        return pl.DataFrame()

    pitchers = []
    for split in splits:
        stat = split.get('stat', {})
        player_info = split.get('player', {})
        team_info = split.get('team', {})
        position_info = split.get('position', {})

        position = position_info.get('abbreviation', '')
        # Normalize position to SP or RP
        if position == 'P':
            # Check games started vs games to determine SP vs RP
            gs = stat.get('gamesStarted', 0)
            g = stat.get('gamesPlayed', 0)
            position = 'SP' if gs > g / 2 else 'RP'

        try:
            era = float(stat.get('era', '0'))
        except (ValueError, TypeError):
            era = 0.0

        try:
            whip = float(stat.get('whip', '0'))
        except (ValueError, TypeError):
            whip = 0.0

        try:
            ip = float(stat.get('inningsPitched', '0'))
        except (ValueError, TypeError):
            ip = 0.0

        # Compute Quality Starts from season totals.
        # A Quality Start is a start with 6+ IP and 3 or fewer ER.
        # The MLB API doesn't provide QS directly in the stats endpoint,
        # so we approximate from season totals: if the pitcher has enough IP
        # and a low enough ERA, they likely have QS. For accurate per-game QS,
        # we use the game log data (see get_pitcher_game_logs below).
        # For now, we store None and let game logs compute the real count.
        pitcher_record = {
            'name': player_info.get('fullName', 'Unknown'),
            'team': team_info.get('name', 'Unknown').replace('New York ', 'NY ').replace('Los Angeles ', 'LA '),
            'position': position if position else None,
            'wins': stat.get('wins', 0),
            'losses': stat.get('losses', 0),
            'era': era,
            'whip': whip,
            'games': stat.get('gamesPlayed', 0),
            'games_started': stat.get('gamesStarted', 0),
            'innings_pitched': ip,
            'hits_allowed': stat.get('hits', 0),
            'earned_runs': stat.get('earnedRuns', 0),
            'walks': stat.get('baseOnBalls', 0),
            'strikeouts': stat.get('strikeOuts', 0),
            'home_runs_allowed': stat.get('homeRuns', 0),
            'saves': stat.get('saves', 0),
            'quality_starts': None,                     # Computed from game logs later
            'mlb_id': player_info.get('id'),            # MLB API unique player ID
        }
        pitchers.append(pitcher_record)

    df = pl.DataFrame(pitchers)
    print(f"Fetched {len(df)} {pool_label} pitchers from {season} season")
    return df


def get_all_pitchers(season: int) -> pl.DataFrame:
    """
    Fetch ALL pitchers from all 30 team rosters with their pitching stats.

    Similar to get_all_active_players but for pitchers.

    Args:
        season: The MLB season year (e.g., 2025)

    Returns:
        pl.DataFrame with pitcher statistics
    """
    print(f"Fetching ALL pitchers for {season} season...")
    print("This may take 2-3 minutes...")

    teams_data = statsapi.get('teams', {'sportId': 1})
    teams = teams_data.get('teams', [])

    all_pitchers = []
    total_teams = len(teams)

    for i, team in enumerate(teams, 1):
        team_id = team['id']
        team_name = team['name']
        short_team = team_name.replace('New York ', 'NY ').replace('Los Angeles ', 'LA ')

        print(f"  [{i}/{total_teams}] Fetching {team_name} pitchers...", end='', flush=True)

        try:
            roster_data = statsapi.get('team_roster', {
                'teamId': team_id,
                'rosterType': '40Man',
                'season': season
            })
            roster = roster_data.get('roster', [])

            team_pitcher_count = 0
            for player in roster:
                person = player.get('person', {})
                player_id = person.get('id')
                player_name = person.get('fullName', 'Unknown')
                position_info = player.get('position', {})
                position = position_info.get('abbreviation', '')

                # Only include pitchers
                if position != 'P':
                    continue

                try:
                    stats_data = statsapi.get('people', {
                        'personIds': player_id,
                        'hydrate': f'stats(group=[pitching],type=[season],season={season})'
                    })

                    if stats_data.get('people'):
                        person_data = stats_data['people'][0]
                        stats_list = person_data.get('stats', [])

                        pitching_stats = {}
                        for stat_group in stats_list:
                            if stat_group.get('group', {}).get('displayName') == 'pitching':
                                splits = stat_group.get('splits', [])
                                if splits:
                                    pitching_stats = splits[0].get('stat', {})
                                    break

                        # Only include pitchers with innings pitched
                        ip_str = pitching_stats.get('inningsPitched', '0')
                        try:
                            ip = float(ip_str) if ip_str else 0.0
                        except (ValueError, TypeError):
                            ip = 0.0

                        if ip > 0:
                            try:
                                era = float(pitching_stats.get('era', '0'))
                            except (ValueError, TypeError):
                                era = 0.0

                            try:
                                whip = float(pitching_stats.get('whip', '0'))
                            except (ValueError, TypeError):
                                whip = 0.0

                            # Determine SP vs RP
                            gs = pitching_stats.get('gamesStarted', 0)
                            g = pitching_stats.get('gamesPlayed', 0)
                            pos = 'SP' if gs > g / 2 else 'RP'

                            pitcher_record = {
                                'name': player_name,
                                'team': short_team,
                                'position': pos,
                                'wins': pitching_stats.get('wins', 0),
                                'losses': pitching_stats.get('losses', 0),
                                'era': era,
                                'whip': whip,
                                'games': g,
                                'games_started': gs,
                                'innings_pitched': ip,
                                'hits_allowed': pitching_stats.get('hits', 0),
                                'earned_runs': pitching_stats.get('earnedRuns', 0),
                                'walks': pitching_stats.get('baseOnBalls', 0),
                                'strikeouts': pitching_stats.get('strikeOuts', 0),
                                'home_runs_allowed': pitching_stats.get('homeRuns', 0),
                                'saves': pitching_stats.get('saves', 0),
                                'quality_starts': None,     # Computed from game logs later
                                'mlb_id': player_id,        # MLB API unique player ID
                            }
                            all_pitchers.append(pitcher_record)
                            team_pitcher_count += 1

                except Exception:
                    pass

            print(f" {team_pitcher_count} pitchers")

        except Exception as e:
            print(f" Error: {e}")

    df = pl.DataFrame(all_pitchers)
    print(f"\nFetched {len(df)} total pitchers from {season} season")
    return df


# =============================================================================
# GAME LOG FETCHING
# =============================================================================
# Game logs are per-game statistics for each player. Unlike season totals (which
# are cumulative), game logs let us compute stats over ANY time window — "last 5
# days", "last 15 days", etc. This is essential for fantasy baseball where you
# need to see who's hot RIGHT NOW, not just who's been good all season.
#
# The MLB Stats API provides game-by-game data through the "gameLog" stat type.
# We fetch these logs once and store them in the database, then compute rolling
# averages on demand using Polars in the API endpoints.
# =============================================================================

def get_batter_game_logs(player_id: int, player_name: str, team: str, season: int) -> list[dict]:
    """
    Fetch per-game batting stats for a single player from the MLB Stats API.

    The API's "gameLog" stat type returns one entry per game the player appeared in.
    Each entry contains the player's stats for that specific game (hits, at-bats,
    home runs, etc.), allowing us to compute rolling averages over any date range.

    How the API call works:
    - 'people' endpoint with 'personIds' to target a specific player
    - 'hydrate' parameter tells the API what extra data to include
    - 'stats(group=[hitting],type=[gameLog],season=YYYY)' requests:
      - group=[hitting]: batting stats only (not pitching/fielding)
      - type=[gameLog]: per-game entries (not season totals)
      - season=YYYY: which season's games to include

    Args:
        player_id: The MLB API player ID (e.g., 592450 for Aaron Judge)
        player_name: Player's full name (for labeling the records)
        team: Player's team name (shortened, e.g., "NY Yankees")
        season: Which season to fetch game logs for

    Returns:
        List of dicts, each representing one game's batting stats.
        Empty list if the API call fails or returns no data.
    """
    try:
        # The 'hydrate' parameter is a powerful feature of the MLB Stats API.
        # It tells the API to include additional nested data in the response.
        # Here we're asking for hitting game logs for the specified season.
        data = statsapi.get('people', {
            'personIds': player_id,
            'hydrate': f'stats(group=[hitting],type=[gameLog],season={season})'
        })

        if not data.get('people'):
            return []

        person = data['people'][0]
        stats_list = person.get('stats', [])

        # Find the hitting game log stats group in the response.
        # The API can return multiple stat groups (hitting, fielding, etc.),
        # so we need to find the one with displayName == 'hitting'.
        game_logs = []
        for stat_group in stats_list:
            if stat_group.get('group', {}).get('displayName') == 'hitting':
                splits = stat_group.get('splits', [])

                for game in splits:
                    game_stat = game.get('stat', {})
                    # The game date comes from the 'date' field in each split.
                    # Format: "YYYY-MM-DD" — perfect for string-based date comparisons.
                    game_date = game.get('date', '')

                    # Extract the opponent team name from the game data.
                    # The API nests this under opponent -> team -> name.
                    opponent = game.get('opponent', {}).get('team', {}).get('name', 'Unknown')
                    opponent = opponent.replace('New York ', 'NY ').replace('Los Angeles ', 'LA ')

                    game_logs.append({
                        'player_id': player_id,
                        'player_name': player_name,
                        'team': team,
                        'game_date': game_date,
                        'opponent': opponent,
                        'at_bats': game_stat.get('atBats', 0),
                        'hits': game_stat.get('hits', 0),
                        'doubles': game_stat.get('doubles', 0),
                        'triples': game_stat.get('triples', 0),
                        'home_runs': game_stat.get('homeRuns', 0),
                        'rbi': game_stat.get('rbi', 0),
                        'runs': game_stat.get('runs', 0),
                        'stolen_bases': game_stat.get('stolenBases', 0),
                        'walks': game_stat.get('baseOnBalls', 0),
                        'strikeouts': game_stat.get('strikeOuts', 0),
                        'hit_by_pitch': game_stat.get('hitByPitch', 0),
                        'sacrifice_flies': game_stat.get('sacFlies', 0),
                    })
                break  # Found hitting group, no need to check others

        return game_logs

    except Exception as e:
        print(f"  Error fetching game logs for {player_name}: {e}")
        return []


def get_pitcher_game_logs(player_id: int, player_name: str, team: str, season: int) -> list[dict]:
    """
    Fetch per-game pitching stats for a single pitcher from the MLB Stats API.

    Same pattern as get_batter_game_logs but for pitching stats. Also computes
    Quality Start (QS) for each game appearance: a start where the pitcher goes
    6+ innings and allows 3 or fewer earned runs.

    The QS calculation happens here (at fetch time) rather than at query time
    because it requires per-game IP and ER — once we have the 0/1 flag stored,
    we can simply SUM it over any date range to get the rolling QS count.

    Args:
        player_id: The MLB API pitcher ID
        player_name: Pitcher's full name
        team: Pitcher's team name (shortened)
        season: Which season to fetch game logs for

    Returns:
        List of dicts, each representing one game's pitching stats.
        Includes a computed 'quality_start' field (0 or 1).
    """
    try:
        data = statsapi.get('people', {
            'personIds': player_id,
            'hydrate': f'stats(group=[pitching],type=[gameLog],season={season})'
        })

        if not data.get('people'):
            return []

        person = data['people'][0]
        stats_list = person.get('stats', [])

        game_logs = []
        for stat_group in stats_list:
            if stat_group.get('group', {}).get('displayName') == 'pitching':
                splits = stat_group.get('splits', [])

                for game in splits:
                    game_stat = game.get('stat', {})
                    game_date = game.get('date', '')

                    opponent = game.get('opponent', {}).get('team', {}).get('name', 'Unknown')
                    opponent = opponent.replace('New York ', 'NY ').replace('Los Angeles ', 'LA ')

                    # Parse innings pitched — the API returns it as a string like "6.2"
                    # which actually means 6 and 2/3 innings (NOT 6.2 decimal).
                    # However, for our aggregation purposes, the decimal representation
                    # from the API works fine since we'll be summing IP across games.
                    try:
                        ip = float(game_stat.get('inningsPitched', '0'))
                    except (ValueError, TypeError):
                        ip = 0.0

                    earned_runs = game_stat.get('earnedRuns', 0)

                    # Compute Quality Start: a game where the pitcher:
                    # 1. Pitched at least 6.0 innings, AND
                    # 2. Allowed 3 or fewer earned runs.
                    # This is a key fantasy baseball stat — it measures reliable starts.
                    # We store it as 0 or 1 so we can SUM over a rolling window.
                    quality_start = 1 if ip >= 6.0 and earned_runs <= 3 else 0

                    # Extract win/loss/save as 0 or 1 for this game.
                    # The API provides these as part of per-game stats.
                    # Storing as integers lets us SUM them during rolling aggregation.
                    wins = 1 if game_stat.get('wins', 0) > 0 else 0
                    losses = 1 if game_stat.get('losses', 0) > 0 else 0
                    saves = 1 if game_stat.get('saves', 0) > 0 else 0

                    game_logs.append({
                        'player_id': player_id,
                        'player_name': player_name,
                        'team': team,
                        'game_date': game_date,
                        'opponent': opponent,
                        'innings_pitched': ip,
                        'hits_allowed': game_stat.get('hits', 0),
                        'earned_runs': earned_runs,
                        'walks': game_stat.get('baseOnBalls', 0),
                        'strikeouts': game_stat.get('strikeOuts', 0),
                        'home_runs_allowed': game_stat.get('homeRuns', 0),
                        'wins': wins,
                        'losses': losses,
                        'saves': saves,
                        'quality_start': quality_start,
                        'pitches': game_stat.get('numberOfPitches', 0),
                    })
                break  # Found pitching group, done

        return game_logs

    except Exception as e:
        print(f"  Error fetching game logs for {player_name}: {e}")
        return []


def fetch_all_game_logs(season: int, player_type: str = 'batters') -> list[dict]:
    """
    Fetch game logs for ALL qualified batters or pitchers.

    This is the high-level function that orchestrates fetching per-game data.
    It first fetches the list of qualified players (using the season stats
    functions), then iterates through each player to get their game logs.

    Why qualified players only?
    - Qualified batters (~129 players) and pitchers (~80) represent the players
      with meaningful playing time. Fetching game logs for ALL 1200+ roster
      players would take much longer and most wouldn't have useful rolling data.
    - For fantasy baseball, you mainly care about starters and regular players.

    Progress display:
    - Shows a progress bar with player name so you can see it's working.
    - Prints running total of game log entries found.

    Args:
        season: The MLB season year (e.g., 2024)
        player_type: 'batters' or 'pitchers' — which group to fetch logs for

    Returns:
        List of all game log dicts across all players.
        For batters: typically 15,000-20,000 entries (129 players × ~150 games each).
        For pitchers: typically 3,000-5,000 entries (80 pitchers × ~30-35 starts each).
    """
    if player_type == 'batters':
        # Get the list of qualified batters with their season stats.
        # We need their mlb_id to fetch game logs.
        # Use qualified_only=True here — game log fetching makes one API call
        # per player, so we limit to qualified players (~129 batters) to keep
        # the total runtime manageable (~5-10 minutes instead of 30+ minutes
        # for all 500+ batters).
        df = get_qualified_batters(season, limit=150, qualified_only=True)
    else:
        # Same reasoning — limit to qualified pitchers (~80) for game logs.
        df = get_qualified_pitchers(season, limit=150, qualified_only=True)

    if df.is_empty():
        print(f"No {player_type} found to fetch game logs for")
        return []

    all_logs = []
    total = len(df)

    print(f"\nFetching game logs for {total} qualified {player_type}...")
    print("=" * 60)

    for i, row in enumerate(df.to_dicts(), 1):
        name = row['name']
        team = row['team']

        # Get the MLB API player ID. If it's missing (None), we can't fetch logs.
        mlb_id = row.get('mlb_id')
        if mlb_id is None:
            print(f"  [{i}/{total}] {name} — skipped (no mlb_id)")
            continue

        print(f"  [{i}/{total}] {name}...", end='', flush=True)

        # Call the appropriate game log function based on player type
        if player_type == 'batters':
            logs = get_batter_game_logs(mlb_id, name, team, season)
        else:
            logs = get_pitcher_game_logs(mlb_id, name, team, season)

        all_logs.extend(logs)
        print(f" {len(logs)} games")

    print("=" * 60)
    print(f"Total: {len(all_logs)} game log entries for {total} {player_type}")
    return all_logs


async def populate_game_logs(season: int = 2025, player_type: str = 'batters', clear_existing: bool = True):
    """
    Fetch game logs from the MLB API and store them in the database.

    This is the async function that ties together:
    1. Fetching game logs via fetch_all_game_logs() (synchronous API calls)
    2. Storing them in the database via execute_many() (async DB operations)

    The clear_existing flag controls whether to wipe existing game logs first.
    During initial setup, you'd want clear_existing=True. For incremental
    updates during the season, you might want False (and handle deduplication).

    Args:
        season: The MLB season year
        player_type: 'batters' or 'pitchers'
        clear_existing: If True, delete all existing game logs first
    """
    from database import database
    from models import batter_game_logs, pitcher_game_logs

    await database.connect()

    try:
        # Fetch all game logs (this makes many API calls — takes a few minutes)
        all_logs = fetch_all_game_logs(season, player_type)

        if not all_logs:
            print(f"No game logs to insert for {player_type}")
            return

        # Choose the correct database table based on player type
        table = batter_game_logs if player_type == 'batters' else pitcher_game_logs

        if clear_existing:
            print(f"Clearing existing {player_type} game logs from database...")
            await database.execute(table.delete())

        # Bulk insert all game logs.
        # execute_many() is much faster than inserting one at a time because
        # it batches the INSERT statements into a single database transaction.
        print(f"Inserting {len(all_logs)} {player_type} game log entries into database...")
        await database.execute_many(
            query=table.insert(),
            values=all_logs
        )

        print(f"Successfully stored {len(all_logs)} {player_type} game logs from {season} season!")

    finally:
        await database.disconnect()


async def populate_pitchers_from_mlb(season: int = 2025, clear_existing: bool = True, all_pitchers: bool = False):
    """
    Fetch MLB pitcher stats and populate the pitchers database table.

    Three modes based on all_pitchers flag:
    - False (default): Uses the stats API with playerPool='ALL' to get all pitchers
      who recorded innings (~300-400 pitchers). This is a single fast API call.
    - True (--all flag): Uses the roster-based approach, iterating through all 30
      team rosters to fetch every pitcher individually (~400+). Takes 2-3 minutes
      but catches pitchers who haven't appeared yet.

    Args:
        season: The MLB season to fetch (default 2025)
        clear_existing: If True, delete all existing pitchers first
        all_pitchers: If True, use roster-based fetch for ALL pitchers (~400+).
                      If False, use stats API for all pitchers with innings (~300-400).
    """
    from database import database
    from models import pitchers

    await database.connect()

    try:
        # Fetch data from MLB API.
        # Default (no --all): stats API with playerPool='ALL' — fast single call.
        # With --all: roster-based approach — slower but catches everyone.
        if all_pitchers:
            df = get_all_pitchers(season)
        else:
            df = get_qualified_pitchers(season)

        if df.is_empty():
            print("No pitcher data to insert")
            return

        if clear_existing:
            print("Clearing existing pitchers from database...")
            await database.execute(pitchers.delete())

        pitcher_records = df.to_dicts()
        print(f"Inserting {len(pitcher_records)} pitchers into database...")
        await database.execute_many(
            query=pitchers.insert(),
            values=pitcher_records
        )

        print(f"Successfully populated database with {len(pitcher_records)} pitchers from {season} season!")

    finally:
        await database.disconnect()


async def populate_database_from_mlb(season: int = 2025, clear_existing: bool = True, all_players: bool = False):
    """
    Fetch MLB stats and populate the database.

    This function:
    1. Fetches player stats from the MLB API
    2. Optionally clears existing players from the database
    3. Inserts all fetched players as new records

    Three modes based on all_players flag:
    - False (default): Uses the stats API with playerPool='ALL' to get all batters
      who recorded at-bats (~400-500 players). This is a single fast API call.
    - True (--all flag): Uses the roster-based approach, iterating through all 30
      team rosters to fetch every player individually (~1200+). Takes 2-3 minutes
      but catches players with 0 at-bats (bench players, September call-ups, etc.).

    Args:
        season: The MLB season to fetch (default 2025)
        clear_existing: If True, delete all existing players first
        all_players: If True, use roster-based fetch for ALL players (~1200+).
                     If False, use stats API for all players with at-bats (~400-500).
    """
    from database import database
    from models import players

    # Connect to the database
    await database.connect()

    try:
        # Fetch data from MLB API.
        # Default (no --all): stats API with playerPool='ALL' — fast single call,
        #   gets all batters with stats (~400-500 players).
        # With --all: roster-based approach — slower but gets ALL rostered players
        #   including those with 0 at-bats (~1200+ players).
        if all_players:
            df = get_all_active_players(season)
        else:
            df = get_qualified_batters(season)

        if df.is_empty():
            print("No data to insert")
            return

        if clear_existing:
            # Delete all existing players
            print("Clearing existing players from database...")
            await database.execute(players.delete())

        # Convert DataFrame to list of dicts for insertion
        player_records = df.to_dicts()

        # Insert all players
        print(f"Inserting {len(player_records)} players into database...")
        await database.execute_many(
            query=players.insert(),
            values=player_records
        )

        print(f"Successfully populated database with {len(player_records)} players from {season} season!")

    finally:
        await database.disconnect()


async def update_player_stats(season: Optional[int] = None, all_players: bool = False):
    """
    Update existing players with fresh stats from the MLB API.

    This is designed for daily updates during the season:
    - Players that exist in the DB get their stats updated (matched by mlb_id)
    - New players get added (anyone with at-bats this season)
    - Stale entries from previous seasons are removed

    Why mlb_id matching?
      Previously we matched by name + team, which caused duplicates when a player
      changed teams between seasons (e.g., Framber Valdez with 2025 Astros stats
      AND a new 2026 row). The MLB API's unique player ID (mlb_id) is stable
      across seasons and team changes, so it's the correct key for upserts.

    Why cleanup?
      The primary database holds ONLY the current season's data. Without cleanup,
      players who retired or moved to the minors would linger with stale stats,
      creating confusing duplicates alongside fresh data.

    Args:
        season: The season to fetch. Defaults to current year.
        all_players: If True, use roster-based fetch for ALL players (~1200+).
                     If False, use stats API for all players with at-bats (~400-500).
    """
    from database import database
    from models import players

    if season is None:
        season = datetime.now().year

    await database.connect()

    try:
        # Fetch latest stats.
        # Default: stats API with playerPool='ALL' — gets all batters with stats.
        # With --all: roster-based approach — gets ALL rostered players.
        if all_players:
            df = get_all_active_players(season)
        else:
            df = get_qualified_batters(season)

        if df.is_empty():
            print("No data to update")
            return

        updated_count = 0
        inserted_count = 0

        # Collect all mlb_ids from the fresh API data so we can clean up stale rows after.
        fresh_mlb_ids = set()

        for player_record in df.to_dicts():
            mlb_id = player_record.get('mlb_id')
            if mlb_id:
                fresh_mlb_ids.add(mlb_id)

            # Match by mlb_id (unique MLB API player ID) — this is stable across
            # seasons and team changes, unlike name + team which can drift.
            existing = None
            if mlb_id:
                existing = await database.fetch_one(
                    players.select().where(players.c.mlb_id == mlb_id)
                )

            if existing:
                # Update existing player — overwrite all stats with fresh data.
                # This also updates their team name if they were traded.
                await database.execute(
                    players.update()
                    .where(players.c.id == existing._mapping['id'])
                    .values(**player_record)
                )
                updated_count += 1
            else:
                # Insert new player
                await database.execute(
                    players.insert().values(**player_record)
                )
                inserted_count += 1

        # Cleanup: remove stale rows whose mlb_id isn't in this season's data.
        # This prevents ghost entries from previous seasons lingering in the DB.
        # Only delete rows that HAVE an mlb_id (custom-added players without one are kept).
        if fresh_mlb_ids:
            from sqlalchemy import and_
            stale = await database.fetch_all(
                players.select().where(
                    and_(
                        players.c.mlb_id.isnot(None),
                        players.c.mlb_id.notin_(fresh_mlb_ids),
                    )
                )
            )
            if stale:
                await database.execute(
                    players.delete().where(
                        and_(
                            players.c.mlb_id.isnot(None),
                            players.c.mlb_id.notin_(fresh_mlb_ids),
                        )
                    )
                )
                print(f"Cleanup: removed {len(stale)} stale player entries from previous seasons")

        print(f"Update complete: {updated_count} updated, {inserted_count} new players added")

    finally:
        await database.disconnect()


async def update_pitcher_stats(season: Optional[int] = None, all_pitchers: bool = False):
    """
    Update existing pitchers with fresh stats from the MLB API.

    This is designed for daily updates during the season:
    - Pitchers that exist in the DB get their stats updated (matched by mlb_id)
    - New pitchers get added (anyone with innings this season)
    - Stale entries from previous seasons are removed

    Mirrors update_player_stats() — see that function's docstring for the
    rationale behind mlb_id matching and stale data cleanup.

    Args:
        season: The season to fetch. Defaults to current year.
        all_pitchers: If True, use roster-based fetch for ALL pitchers (~400+).
                      If False, use stats API for all pitchers with innings (~300-400).
    """
    from database import database
    from models import pitchers

    if season is None:
        season = datetime.now().year

    await database.connect()

    try:
        if all_pitchers:
            df = get_all_pitchers(season)
        else:
            df = get_qualified_pitchers(season)

        if df.is_empty():
            print("No pitcher data to update")
            return

        updated_count = 0
        inserted_count = 0

        # Collect all mlb_ids from the fresh API data for stale cleanup.
        fresh_mlb_ids = set()

        for pitcher_record in df.to_dicts():
            mlb_id = pitcher_record.get('mlb_id')
            if mlb_id:
                fresh_mlb_ids.add(mlb_id)

            # Match by mlb_id — stable across seasons and team changes.
            # This prevents duplicates like "Framber Valdez (2025)" and
            # "Framber Valdez (2026)" appearing as separate rows.
            existing = None
            if mlb_id:
                existing = await database.fetch_one(
                    pitchers.select().where(pitchers.c.mlb_id == mlb_id)
                )

            if existing:
                # Update existing pitcher — overwrites stats AND team name
                await database.execute(
                    pitchers.update()
                    .where(pitchers.c.id == existing._mapping['id'])
                    .values(**pitcher_record)
                )
                updated_count += 1
            else:
                # Insert new pitcher
                await database.execute(
                    pitchers.insert().values(**pitcher_record)
                )
                inserted_count += 1

        # Cleanup: remove stale rows from previous seasons.
        # Pitchers with mlb_id not in the fresh data are no longer active.
        if fresh_mlb_ids:
            from sqlalchemy import and_
            stale = await database.fetch_all(
                pitchers.select().where(
                    and_(
                        pitchers.c.mlb_id.isnot(None),
                        pitchers.c.mlb_id.notin_(fresh_mlb_ids),
                    )
                )
            )
            if stale:
                await database.execute(
                    pitchers.delete().where(
                        and_(
                            pitchers.c.mlb_id.isnot(None),
                            pitchers.c.mlb_id.notin_(fresh_mlb_ids),
                        )
                    )
                )
                print(f"Cleanup: removed {len(stale)} stale pitcher entries from previous seasons")

        print(f"Pitcher update complete: {updated_count} updated, {inserted_count} new pitchers added")

    finally:
        await database.disconnect()


def preview_stats(season: int = 2024, pitchers: bool = False):
    """
    Fetch and display stats without saving to database.
    Useful for testing the API connection and data format.
    """
    if pitchers:
        df = get_qualified_pitchers(season, limit=20, qualified_only=True)
        label = "Top Pitchers"
    else:
        df = get_qualified_batters(season, limit=20, qualified_only=True)
        label = "Top Batters"

    if df.is_empty():
        print("No data found")
        return

    print(f"\n=== Top 20 {label} ({season} Season) ===\n")
    print(df.to_pandas().to_string(index=False))
    print(f"\nTotal: {len(df)} players")


if __name__ == "__main__":
    import argparse

    # =========================================================================
    # CLI (Command Line Interface) Argument Parsing
    # =========================================================================
    # argparse is Python's built-in module for parsing command-line arguments.
    # It automatically generates --help text and validates arguments.
    #
    # How it works:
    # 1. Create a parser with a description
    # 2. Add arguments with add_argument() — each becomes a flag you can pass
    # 3. Call parse_args() to read what the user typed on the command line
    # 4. Access values via args.flag_name (e.g., args.season, args.save)
    #
    # Types of arguments used here:
    # - store_true: Boolean flags (--save, --preview). Present = True, absent = False.
    # - type=int: Converts the string argument to an integer (--season 2024).
    # - default: Value to use if the flag isn't provided.
    # =========================================================================
    parser = argparse.ArgumentParser(
        description='Fetch MLB player and pitcher stats from the MLB Stats API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Batters (season stats)
  python mlb_data_fetcher.py --season 2025 --preview         # Preview batters
  python mlb_data_fetcher.py --season 2025 --save            # Save all batters with stats (~400-500)
  python mlb_data_fetcher.py --season 2025 --all --save      # Save ALL rostered batters (~1200+, slow)

  # Pitchers (season stats)
  python mlb_data_fetcher.py --pitchers --preview            # Preview pitchers
  python mlb_data_fetcher.py --pitchers --save               # Save all pitchers with stats (~300-400)
  python mlb_data_fetcher.py --pitchers --all --save         # Save ALL rostered pitchers (~400+, slow)

  # Game logs (per-game stats for rolling averages)
  python mlb_data_fetcher.py --game-logs --save              # Save batter game logs (qualified only)
  python mlb_data_fetcher.py --game-logs --pitchers --save   # Save pitcher game logs (qualified only)

  # Full setup (both season stats and game logs for batters + pitchers)
  python mlb_data_fetcher.py --save && python mlb_data_fetcher.py --pitchers --save && python mlb_data_fetcher.py --game-logs --save && python mlb_data_fetcher.py --game-logs --pitchers --save
        """
    )
    parser.add_argument('--season', type=int, default=2025,
                        help='MLB season year (default: 2025)')
    parser.add_argument('--preview', action='store_true',
                        help='Preview stats without saving to database')
    parser.add_argument('--save', action='store_true',
                        help='Save stats to database (clears existing data)')
    parser.add_argument('--update', action='store_true',
                        help='Update existing players without clearing')
    parser.add_argument('--all', action='store_true',
                        help='Use roster-based fetch for ALL players (including 0 at-bats). Default already fetches all with stats.')
    parser.add_argument('--pitchers', action='store_true',
                        help='Fetch pitcher stats instead of batter stats')
    parser.add_argument('--game-logs', action='store_true',
                        help='Fetch per-game logs (for rolling time-period stats)')

    args = parser.parse_args()

    # =========================================================================
    # Route to the correct operation based on CLI flags
    # =========================================================================
    # The logic branches on three axes:
    #   1. --game-logs: Per-game data (for rolling averages) vs season totals
    #   2. --pitchers: Pitcher data vs batter data
    #   3. --save / --preview / --update: What to do with the data
    # =========================================================================

    if args.game_logs:
        # --- Game Log Operations ---
        # Game logs are per-game stats used to compute rolling averages
        # (Last 5 days, Last 10 days, etc.) in the frontend.
        # The --pitchers flag controls whether we fetch batter or pitcher logs.
        player_type = 'pitchers' if args.pitchers else 'batters'

        if args.save:
            # Fetch game logs from MLB API and store in database
            asyncio.run(populate_game_logs(args.season, player_type=player_type, clear_existing=True))
        elif args.preview:
            # Preview mode: fetch a few game logs and display them
            # (Just show the first player's logs as a sample)
            print(f"Fetching sample game logs for {player_type}...")
            logs = fetch_all_game_logs(args.season, player_type)
            if logs:
                # Show first 10 entries as a preview
                sample = logs[:10]
                df = pl.DataFrame(sample)
                print(f"\n=== Sample Game Logs ({player_type}) ===\n")
                print(df.to_pandas().to_string(index=False))
                print(f"\nTotal game log entries: {len(logs)}")
            else:
                print("No game log data found")
        else:
            print("Use --save to fetch and store game logs, or --preview to preview them")
            print(f"  python mlb_data_fetcher.py --game-logs {'--pitchers ' if args.pitchers else ''}--save")

    elif args.pitchers:
        # --- Pitcher Season Stats Operations ---
        if args.preview:
            preview_stats(args.season, pitchers=True)
        elif args.save:
            asyncio.run(populate_pitchers_from_mlb(args.season, clear_existing=True, all_pitchers=args.all))
        else:
            preview_stats(args.season, pitchers=True)
    else:
        # --- Batter Season Stats Operations ---
        if args.preview:
            preview_stats(args.season, pitchers=False)
        elif args.save:
            asyncio.run(populate_database_from_mlb(args.season, clear_existing=True, all_players=args.all))
        elif args.update:
            asyncio.run(update_player_stats(args.season, all_players=args.all))
        else:
            preview_stats(args.season, pitchers=False)
