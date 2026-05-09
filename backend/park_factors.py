"""
park_factors.py - Static MLB Park Factor Lookup
================================================

Park factors quantify how hitter-friendly or pitcher-friendly a stadium is
relative to the league average. They are an essential input for betting and
fantasy analysis — the same hitter producing the same OPS in Coors Field
versus Oracle Park is producing very different "true" performance because of
the venues.

Scale (industry standard):
    100 = league-neutral
    > 100 = hitter-friendly (boosts run scoring)
    < 100 = pitcher-friendly (suppresses run scoring)

What's stored per park:
    runs:  Overall run-scoring factor (most useful single number)
    hr:    Home run factor (often diverges from runs — Fenway's short LF wall
           helps doubles more than HRs; Yankee Stadium boosts HRs especially
           for LHH but is otherwise neutral on runs)
    team:  The home team that plays here (for joining with matchups data)
    venue: Canonical venue name as it appears in the MLB Stats API
           `schedule.venue_name` field — used as the lookup key

Why static instead of computed:
    Real park factors are derived from multi-year park-vs-road run differentials
    using regression methods (FanGraphs, Baseball Savant, ESPN all publish them).
    Computing them ourselves would require multi-season league data we don't
    have. Static values from public sources are a perfectly fine MVP — they
    drift slowly year-to-year, and reading once a year and updating this file
    is cheap.

Data source (approximate values, 2023–2024 averages):
    These figures are rough averages of publicly published park factors from
    ESPN, Baseball Savant, and FanGraphs. They are intended as a directional
    signal, not a precise statistical instrument. Update annually after the
    season ends and the major outlets publish revised factors.

Known caveats:
    - Tropicana Field (TBR) was damaged in the 2024 hurricane season; the Rays
      played the 2025 season at Steinbrenner Field (a Yankees spring-training
      park). For 2026, check whether they're back at the Trop or not before
      relying on this factor for Tampa Bay home games.
    - The Athletics relocated to Sutter Health Park (West Sacramento) starting
      2025. No long-running park factor exists yet; we use a moderate
      pitcher-friendly default and recommend revisiting after a full season
      of data accumulates.
"""

from typing import Optional


# Keyed by venue_name as returned by `statsapi.schedule(...)`.
# Values are dicts so we can extend later (HR/handedness splits, weather effects).
PARK_FACTORS: dict[str, dict] = {
    # ---------------------------------------------------------------
    # HITTER-FRIENDLY PARKS
    # ---------------------------------------------------------------
    "Coors Field":               {"runs": 117, "hr": 115, "team": "Rockies"},
    "Great American Ball Park":  {"runs": 110, "hr": 118, "team": "Reds"},
    "Globe Life Field":          {"runs": 106, "hr": 105, "team": "Rangers"},
    "Citizens Bank Park":        {"runs": 105, "hr": 110, "team": "Phillies"},
    "Fenway Park":               {"runs": 107, "hr": 99,  "team": "Red Sox"},
    "Yankee Stadium":            {"runs": 104, "hr": 113, "team": "Yankees"},
    "Chase Field":                {"runs": 104, "hr": 103, "team": "Diamondbacks"},

    # ---------------------------------------------------------------
    # MILDLY HITTER-FRIENDLY
    # ---------------------------------------------------------------
    "Wrigley Field":              {"runs": 102, "hr": 103, "team": "Cubs"},
    "Rate Field":                 {"runs": 101, "hr": 105, "team": "White Sox"},
    "Guaranteed Rate Field":      {"runs": 101, "hr": 105, "team": "White Sox"},  # legacy name
    "Rogers Centre":              {"runs": 101, "hr": 104, "team": "Blue Jays"},

    # ---------------------------------------------------------------
    # NEUTRAL (~98–100)
    # ---------------------------------------------------------------
    "Truist Park":                {"runs": 100, "hr": 100, "team": "Braves"},
    "Target Field":               {"runs": 100, "hr": 97,  "team": "Twins"},
    "Progressive Field":          {"runs": 100, "hr": 99,  "team": "Guardians"},
    "Angel Stadium":              {"runs": 100, "hr": 99,  "team": "Angels"},
    "Nationals Park":             {"runs": 100, "hr": 99,  "team": "Nationals"},
    "Comerica Park":              {"runs": 99,  "hr": 94,  "team": "Tigers"},
    "Camden Yards":               {"runs": 99,  "hr": 96,  "team": "Orioles"},
    "Oriole Park at Camden Yards": {"runs": 99, "hr": 96,  "team": "Orioles"},  # full venue name
    "Kauffman Stadium":           {"runs": 99,  "hr": 94,  "team": "Royals"},
    "Busch Stadium":              {"runs": 98,  "hr": 95,  "team": "Cardinals"},
    "Minute Maid Park":           {"runs": 98,  "hr": 97,  "team": "Astros"},
    "Daikin Park":                {"runs": 98,  "hr": 97,  "team": "Astros"},  # 2025 rename
    "American Family Field":      {"runs": 99,  "hr": 104, "team": "Brewers"},
    "Dodger Stadium":             {"runs": 98,  "hr": 104, "team": "Dodgers"},

    # ---------------------------------------------------------------
    # PITCHER-FRIENDLY
    # ---------------------------------------------------------------
    "PNC Park":                   {"runs": 97,  "hr": 95,  "team": "Pirates"},
    "loanDepot park":             {"runs": 96,  "hr": 88,  "team": "Marlins"},
    "Citi Field":                 {"runs": 95,  "hr": 93,  "team": "Mets"},
    "Petco Park":                 {"runs": 94,  "hr": 94,  "team": "Padres"},
    "Tropicana Field":            {"runs": 94,  "hr": 95,  "team": "Rays"},
    "T-Mobile Park":              {"runs": 93,  "hr": 93,  "team": "Mariners"},
    "Oracle Park":                {"runs": 94,  "hr": 88,  "team": "Giants"},

    # ---------------------------------------------------------------
    # NEW / RELOCATED VENUES (placeholder estimates — refine when more data lands)
    # ---------------------------------------------------------------
    # Athletics relocated 2025; small-park-like dimensions but limited sample.
    "Sutter Health Park":         {"runs": 100, "hr": 100, "team": "Athletics"},
    # Rays' temporary home for 2025 (no long-run sample). Treat as neutral.
    "Steinbrenner Field":         {"runs": 100, "hr": 102, "team": "Rays"},
}


def get_park_factor(venue_name: Optional[str]) -> Optional[dict]:
    """
    Look up the park factor for a given venue name.

    Returns:
        The factor dict ({"runs": int, "hr": int, "team": str}) or None if the
        venue isn't in the lookup. Callers should treat None as "neutral / no
        data" rather than treating it as 100, so the UI can show "—" instead of
        falsely implying neutrality.
    """
    if not venue_name:
        return None
    return PARK_FACTORS.get(venue_name)


def classify_park_factor(runs_factor: int) -> str:
    """
    Convert a numeric runs factor into a coarse category label.

    Useful for UI display ("hitter-friendly" / "neutral" / "pitcher-friendly")
    when you don't want to expose the raw number. Thresholds match common
    baseball analytics conventions:
        > 103   → hitter-friendly
        97–103  → neutral
        < 97    → pitcher-friendly
    """
    if runs_factor is None:
        return "unknown"
    if runs_factor > 103:
        return "hitter-friendly"
    if runs_factor < 97:
        return "pitcher-friendly"
    return "neutral"
