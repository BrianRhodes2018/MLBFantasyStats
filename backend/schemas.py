"""
schemas.py - Pydantic Models for Request/Response Validation
============================================================

This module defines Pydantic models that FastAPI uses for:
1. Request body validation — automatically validates incoming JSON data
2. Response serialization — controls what fields are included in API responses
3. Auto-generated API docs — FastAPI uses these to build Swagger/OpenAPI docs

Key concepts:
- Pydantic's BaseModel enforces type checking at runtime. If a client sends
  a string where an int is expected, FastAPI returns a 422 error automatically.
- PlayerIn is used for POST requests (creating a new player). It has all fields
  EXCEPT `id`, because the database generates the ID.
- PlayerOut inherits from PlayerIn and adds `id`. This is used for responses
  so the client can see the assigned ID after creation.
- The `response_model` parameter on FastAPI endpoints references these schemas.
- Optional fields use `Optional[type] = None` — they can be omitted from requests
  and will default to None. This is useful for fields added after initial deployment.

Response format:
- All endpoints return a consistent JSON structure with `code` and `message` fields,
  similar to what you see in Swagger UI. This makes it easy for the frontend to
  check if a request succeeded and display appropriate feedback.
"""

from pydantic import BaseModel
from typing import Optional


class PlayerIn(BaseModel):
    """
    Schema for creating a new player/batter (POST request body).

    All stat fields are required. Position is optional (defaults to None).
    FastAPI will return a 422 Validation Error if any required field is
    missing or has the wrong type.

    Fields:
        name: Player's full name (e.g., "Aaron Judge")
        team: Team name (e.g., "Yankees")
        position: Fielding position (e.g., "RF", "DH", "1B"). Optional.
        batting_average: Season batting average as a decimal (e.g., 0.287)
        home_runs: Total home runs (integer)
        rbi: Runs Batted In (integer)
        stolen_bases: Total stolen bases (integer)
        ops: On-base Plus Slugging, a combined offensive stat (e.g., 1.019)
        runs: Runs scored (integer). Optional.
        strikeouts: Strikeouts as a batter (integer). Optional.
        total_bases: Total bases (integer). Optional.
        walks: Bases on balls (integer). Optional. Used for OBP calculation.
        hit_by_pitch: Hit by pitch count (integer). Optional. Used for OBP calculation.
        sacrifice_flies: Sacrifice flies (integer). Optional. Used for OBP calculation.
    """
    name: str
    team: str
    position: Optional[str] = None
    batting_average: float
    home_runs: int
    rbi: int
    stolen_bases: int
    ops: float
    runs: Optional[int] = None
    strikeouts: Optional[int] = None
    total_bases: Optional[int] = None
    at_bats: Optional[int] = None
    walks: Optional[int] = None              # BB - Bases on balls (needed for OBP)
    hit_by_pitch: Optional[int] = None       # HBP - Hit by pitch (needed for OBP)
    sacrifice_flies: Optional[int] = None    # SF - Sacrifice flies (needed for OBP)
    hits: Optional[int] = None              # H - Total hits (needed for fantasy points)
    doubles: Optional[int] = None           # 2B - Doubles (needed for fantasy points)
    triples: Optional[int] = None           # 3B - Triples (needed for fantasy points)
    caught_stealing: Optional[int] = None   # CS - Caught stealing (needed for fantasy points)
    mlb_id: Optional[int] = None             # MLB Stats API player ID


class PlayerUpdate(BaseModel):
    """
    Schema for updating an existing player (PUT request body).

    All fields are Optional — only the fields provided in the request body
    will be updated. Fields omitted (or set to null) are left unchanged.
    This is called a "partial update" pattern.

    Why a separate schema from PlayerIn?
    - PlayerIn has REQUIRED fields (name, team, batting_average, etc.) because
      creating a new player needs all data to be present.
    - PlayerUpdate has ALL OPTIONAL fields because when updating, you might only
      want to change one field (e.g., update just home_runs after a game).

    How partial updates work with Pydantic:
    - When the client sends: { "home_runs": 55 }
    - Pydantic creates: PlayerUpdate(name=None, team=None, ..., home_runs=55, ...)
    - In the endpoint, we call player.dict(exclude_unset=True)
    - exclude_unset=True is the KEY: it returns ONLY the fields the client
      explicitly included in the JSON. Fields that defaulted to None (because
      they weren't sent) are EXCLUDED from the dict.
    - Result: {"home_runs": 55} — only what the client sent
    - Without exclude_unset: {"name": None, "team": None, ..., "home_runs": 55, ...}
      which would overwrite everything with None — NOT what we want!

    This is a common FastAPI pattern for PATCH/PUT endpoints. The alternative
    approach is to use a PATCH endpoint, but PUT with optional fields achieves
    the same result and is simpler to implement.

    Example usage:
        PUT /players/3 with body: { "home_runs": 55 }
        → Only home_runs is updated. All other fields remain unchanged.

        PUT /players/3 with body: { "team": "Mets", "ops": 0.875 }
        → Only team and ops are updated.

    Fields (all Optional — only send what you want to change):
        name: Player's full name
        team: Team name
        position: Fielding position
        batting_average: Season batting average
        home_runs: Total home runs
        rbi: Runs Batted In
        stolen_bases: Total stolen bases
        ops: On-base Plus Slugging
        runs: Runs scored
        strikeouts: Strikeouts as a batter
        total_bases: Total bases
        at_bats: At bats
    """
    name: Optional[str] = None
    team: Optional[str] = None
    position: Optional[str] = None
    batting_average: Optional[float] = None
    home_runs: Optional[int] = None
    rbi: Optional[int] = None
    stolen_bases: Optional[int] = None
    ops: Optional[float] = None
    runs: Optional[int] = None
    strikeouts: Optional[int] = None
    total_bases: Optional[int] = None
    at_bats: Optional[int] = None
    walks: Optional[int] = None
    hit_by_pitch: Optional[int] = None
    sacrifice_flies: Optional[int] = None
    hits: Optional[int] = None
    doubles: Optional[int] = None
    triples: Optional[int] = None
    caught_stealing: Optional[int] = None
    mlb_id: Optional[int] = None


class PlayerOut(PlayerIn):
    """
    Schema for player responses (includes the database-generated ID).

    Inherits all fields from PlayerIn and adds `id`.
    Used as the `response_model` on GET and POST endpoints so the client
    receives the full player record including the ID.

    This inheritance pattern avoids duplicating field definitions.
    """
    id: int


# =============================================================================
# PITCHER SCHEMAS
# =============================================================================

class PitcherIn(BaseModel):
    """
    Schema for creating a new pitcher (POST request body).

    Fields:
        name: Pitcher's full name (e.g., "Gerrit Cole")
        team: Team name (e.g., "Yankees")
        position: SP (Starting Pitcher) or RP (Relief Pitcher). Optional.
        wins: Total wins (integer)
        losses: Total losses (integer)
        era: Earned Run Average — earned runs per 9 innings (float)
        whip: Walks + Hits per Inning Pitched (float)
        games: Games appeared (integer). Optional.
        games_started: Games started (integer). Optional.
        innings_pitched: Innings pitched, e.g., 6.2 = 6 2/3 innings (float)
        hits_allowed: Hits allowed (integer)
        earned_runs: Earned runs allowed (integer)
        walks: Walks issued (integer)
        strikeouts: Strikeouts (integer)
        home_runs_allowed: Home runs allowed (integer). Optional.
        saves: Saves — for relief pitchers (integer). Optional.
    """
    name: str
    team: str
    position: Optional[str] = None
    wins: int
    losses: int
    era: float
    whip: float
    games: Optional[int] = None
    games_started: Optional[int] = None
    innings_pitched: float
    hits_allowed: int
    earned_runs: int
    walks: int
    strikeouts: int
    home_runs_allowed: Optional[int] = None
    saves: Optional[int] = None
    quality_starts: Optional[int] = None     # QS - Quality Starts (6+ IP, 3 or fewer ER)
    mlb_id: Optional[int] = None             # MLB Stats API player ID


class PitcherUpdate(BaseModel):
    """
    Schema for updating an existing pitcher (PUT request body).
    All fields optional — only send what you want to change.
    """
    name: Optional[str] = None
    team: Optional[str] = None
    position: Optional[str] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    era: Optional[float] = None
    whip: Optional[float] = None
    games: Optional[int] = None
    games_started: Optional[int] = None
    innings_pitched: Optional[float] = None
    hits_allowed: Optional[int] = None
    earned_runs: Optional[int] = None
    walks: Optional[int] = None
    strikeouts: Optional[int] = None
    home_runs_allowed: Optional[int] = None
    saves: Optional[int] = None
    quality_starts: Optional[int] = None
    mlb_id: Optional[int] = None


class PitcherOut(PitcherIn):
    """
    Schema for pitcher responses (includes the database-generated ID).
    Inherits all fields from PitcherIn and adds `id`.
    """
    id: int


# =============================================================================
# COMMON RESPONSE SCHEMA
# =============================================================================

class ApiResponse(BaseModel):
    """
    Standard API response wrapper — similar to what you see in Swagger UI.

    Every mutating endpoint (POST, PUT, DELETE) returns this structure so
    the frontend always knows:
    - code: HTTP status code (e.g., 200 for success, 400 for bad request)
    - message: Human-readable description of what happened

    This pattern is common in production APIs. It gives the frontend a
    consistent contract to check, regardless of which endpoint was called.

    Fields:
        code: HTTP-style status code (200 = success, 400 = error, etc.)
        message: Descriptive message (e.g., "Player 'Aaron Judge' created successfully")
        data: Optional payload — the actual response data (player object, search results, etc.)
    """
    code: int
    message: str
    data: Optional[dict | list] = None


# =============================================================================
# FANTASY LEAGUE SCHEMAS
# =============================================================================

class FantasyLeagueIn(BaseModel):
    """
    Schema for connecting a new fantasy league (POST request body).

    Supports both ESPN and Yahoo providers. The `provider` field determines
    which set of fields is relevant:

    ESPN (provider="espn"):
        - league_id: The ESPN fantasy league ID number (from the league URL)
        - espn_s2 + swid: Optional cookies for private leagues

    Yahoo (provider="yahoo"):
        - yahoo_league_key: League key in format "458.l.12345"
        - yahoo_consumer_key + yahoo_consumer_secret: From Yahoo Developer app
        - yahoo_authorization_code: The verification code from Yahoo OAuth flow

    Fields:
        provider: "espn" or "yahoo" — determines which API/flow to use
        league_id: ESPN league ID (required for ESPN, ignored for Yahoo)
        season_year: The season year to fetch scoring for (default: current year)
        espn_s2: ESPN cookie for private league access (optional)
        swid: ESPN SWID cookie for private league access (optional)
        yahoo_league_key: Yahoo league key like "458.l.12345" (required for Yahoo)
        yahoo_consumer_key: Yahoo Developer app Consumer Key (required for Yahoo)
        yahoo_consumer_secret: Yahoo Developer app Consumer Secret (required for Yahoo)
        yahoo_authorization_code: OAuth verification code from Yahoo (required for Yahoo)
    """
    provider: Optional[str] = "espn"
    league_id: Optional[int] = None
    season_year: Optional[int] = 2025
    espn_s2: Optional[str] = None
    swid: Optional[str] = None
    # Yahoo-specific fields:
    yahoo_league_key: Optional[str] = None
    yahoo_consumer_key: Optional[str] = None
    yahoo_consumer_secret: Optional[str] = None
    yahoo_authorization_code: Optional[str] = None


class FantasyLeagueOut(BaseModel):
    """
    Schema for league responses — includes the database-generated ID
    and the scoring settings fetched from ESPN or Yahoo.

    This is what the frontend receives when listing saved leagues.
    The scoring_settings field is a JSON string that the frontend can
    parse to see the point values for each stat category.

    Fields:
        id: Database auto-increment ID (used in API calls like /fantasy/points/batters/{id})
        provider: "espn" or "yahoo" — which fantasy platform this league is from
        league_id: ESPN league ID (null for Yahoo leagues)
        yahoo_league_key: Yahoo league key like "458.l.12345" (null for ESPN leagues)
        league_name: Human-readable league name from ESPN/Yahoo
        season_year: Season year for this scoring configuration
        scoring_settings: JSON string of scoring rules
        created_at: ISO timestamp of when the league was added
    """
    id: int
    provider: Optional[str] = "espn"
    league_id: Optional[int] = None
    yahoo_league_key: Optional[str] = None
    league_name: str
    season_year: int
    scoring_settings: str  # JSON string — frontend will parse it if needed
    created_at: Optional[str] = None
