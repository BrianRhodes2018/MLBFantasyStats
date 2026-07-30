"""Point-in-time feature history for the Hit Picks V3 challenger.

The existing V2 dataset builder already owns the chronological replay.  This
module adds pitch-event aggregates that can be snapshotted before a game and
updated only after the complete game date has been featurized.

Only fields present in the cached MLB StatsAPI game feed are used here.  xBA
and bat-tracking data intentionally remain outside this module because the
coverage audit proved they require a separately versioned Baseball Savant
source.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Optional


HIT_EVENT_TYPES = {"single", "double", "triple", "home_run"}
WHIFF_DESCRIPTIONS = {
    "swinging strike",
    "swinging strike (blocked)",
    "missed bunt",
    "bunt foul tip",
}
SWING_TOKENS = (
    "swing",
    "foul",
    "in play",
    "hit into play",
    "missed bunt",
    "bunt foul tip",
)

# Group similar pitch labels so a batter does not need a large sample against
# every provider-specific raw code. Velocity and movement remain separate
# continuous features, so the family label is not the only representation.
PITCH_FAMILIES = {
    "FA": "four_seam",
    "FF": "four_seam",
    "FT": "sinker",
    "SI": "sinker",
    "FC": "cutter",
    "SL": "slider",
    "ST": "slider",
    "SV": "slider",
    "CU": "curve",
    "KC": "curve",
    "CS": "curve",
    "CH": "changeup",
    "FS": "splitter",
    "FO": "splitter",
    "SC": "offspeed_other",
    "KN": "offspeed_other",
    "EP": "offspeed_other",
}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def pitch_family(raw_code: Optional[str]) -> str:
    code = str(raw_code or "").upper()
    return PITCH_FAMILIES.get(code, "other")


def shrunk_rate(
    player_rate: Optional[float],
    sample_size: int,
    league_rate: Optional[float],
    *,
    strength: float,
) -> Optional[float]:
    """Blend a small player sample toward the corresponding league rate."""
    if strength < 0:
        raise ValueError("strength cannot be negative.")
    if sample_size < 0:
        raise ValueError("sample_size cannot be negative.")
    if player_rate is None:
        return league_rate
    if league_rate is None or strength == 0:
        return player_rate
    weight = sample_size / (sample_size + strength)
    return (weight * player_rate) + ((1.0 - weight) * league_rate)


@dataclass
class PitchAggregate:
    pitches: int = 0
    swings: int = 0
    whiffs: int = 0
    contacts: int = 0
    balls_in_play: int = 0
    hits_on_bip: int = 0
    exit_velocity_total: float = 0.0
    exit_velocity_count: int = 0
    hard_hits: int = 0
    velocity_total: float = 0.0
    velocity_count: int = 0
    horizontal_break_total: float = 0.0
    horizontal_break_count: int = 0
    induced_vertical_break_total: float = 0.0
    induced_vertical_break_count: int = 0
    extension_total: float = 0.0
    extension_count: int = 0

    def add(
        self,
        *,
        swing: bool,
        whiff: bool,
        in_play: bool,
        hit_on_bip: bool,
        exit_velocity: Optional[float],
        velocity: Optional[float],
        horizontal_break: Optional[float],
        induced_vertical_break: Optional[float],
        extension: Optional[float],
    ) -> None:
        self.pitches += 1
        if swing:
            self.swings += 1
        if whiff:
            self.whiffs += 1
        if swing and not whiff:
            self.contacts += 1
        if in_play:
            self.balls_in_play += 1
            if hit_on_bip:
                self.hits_on_bip += 1
        if exit_velocity is not None:
            self.exit_velocity_total += exit_velocity
            self.exit_velocity_count += 1
            if exit_velocity >= 95.0:
                self.hard_hits += 1
        if velocity is not None:
            self.velocity_total += velocity
            self.velocity_count += 1
        if horizontal_break is not None:
            self.horizontal_break_total += horizontal_break
            self.horizontal_break_count += 1
        if induced_vertical_break is not None:
            self.induced_vertical_break_total += induced_vertical_break
            self.induced_vertical_break_count += 1
        if extension is not None:
            self.extension_total += extension
            self.extension_count += 1

    def snapshot(self) -> dict[str, Optional[float]]:
        return {
            "pitches": self.pitches,
            "swings": self.swings,
            "balls_in_play": self.balls_in_play,
            "contact_rate": (
                self.contacts / self.swings if self.swings else None
            ),
            "whiff_rate": self.whiffs / self.swings if self.swings else None,
            "bip_hit_rate": (
                self.hits_on_bip / self.balls_in_play
                if self.balls_in_play
                else None
            ),
            "avg_exit_velocity": (
                self.exit_velocity_total / self.exit_velocity_count
                if self.exit_velocity_count
                else None
            ),
            "hard_hit_rate": (
                self.hard_hits / self.exit_velocity_count
                if self.exit_velocity_count
                else None
            ),
            "avg_velocity": (
                self.velocity_total / self.velocity_count
                if self.velocity_count
                else None
            ),
            "avg_horizontal_break": (
                self.horizontal_break_total / self.horizontal_break_count
                if self.horizontal_break_count
                else None
            ),
            "avg_induced_vertical_break": (
                self.induced_vertical_break_total
                / self.induced_vertical_break_count
                if self.induced_vertical_break_count
                else None
            ),
            "avg_extension": (
                self.extension_total / self.extension_count
                if self.extension_count
                else None
            ),
        }


def _aggregate(
    collection: dict[Any, PitchAggregate],
    key: Any,
) -> PitchAggregate:
    return collection.setdefault(key, PitchAggregate())


class V3FeatureHistory:
    """Incremental pitch history with point-in-time snapshots."""

    def __init__(self) -> None:
        self.batter: dict[int, PitchAggregate] = {}
        self.batter_vs_hand: dict[tuple[int, str], PitchAggregate] = {}
        self.batter_pitch: dict[tuple[int, str], PitchAggregate] = {}
        self.pitcher: dict[int, PitchAggregate] = {}
        self.pitcher_pitch: dict[tuple[int, str], PitchAggregate] = {}
        self.pitcher_families: dict[int, set[str]] = {}
        self.league: PitchAggregate = PitchAggregate()
        self.league_pitch: dict[str, PitchAggregate] = {}

    def add_game(self, game: Mapping[str, Any]) -> None:
        """Fold one completed game into history.

        The caller controls timing. HitDatasetBuilder invokes this only after
        every row for the date has already been produced.
        """
        plays = (
            game.get("liveData", {})
            .get("plays", {})
            .get("allPlays", [])
        )
        for play in plays:
            matchup = play.get("matchup") or {}
            batter_id = _safe_int((matchup.get("batter") or {}).get("id"))
            pitcher_id = _safe_int((matchup.get("pitcher") or {}).get("id"))
            pitcher_hand = str(
                (matchup.get("pitchHand") or {}).get("code") or ""
            ).upper()
            if not batter_id or not pitcher_id:
                continue
            result_type = str(
                (play.get("result") or {}).get("eventType") or ""
            ).lower()

            for event in play.get("playEvents", []):
                if not event.get("isPitch"):
                    continue
                details = event.get("details") or {}
                pitch_data = event.get("pitchData") or {}
                breaks = pitch_data.get("breaks") or {}
                hit_data = event.get("hitData") or {}
                description = str(details.get("description") or "").lower()
                family = pitch_family((details.get("type") or {}).get("code"))
                swing = any(token in description for token in SWING_TOKENS)
                whiff = description in WHIFF_DESCRIPTIONS
                in_play = bool(details.get("isInPlay"))
                hit_on_bip = in_play and result_type in HIT_EVENT_TYPES
                values = {
                    "swing": swing,
                    "whiff": whiff,
                    "in_play": in_play,
                    "hit_on_bip": hit_on_bip,
                    "exit_velocity": _safe_float(hit_data.get("launchSpeed")),
                    "velocity": _safe_float(pitch_data.get("startSpeed")),
                    "horizontal_break": _safe_float(
                        breaks.get("breakHorizontal")
                    ),
                    "induced_vertical_break": _safe_float(
                        breaks.get("breakVerticalInduced")
                    ),
                    "extension": _safe_float(pitch_data.get("extension")),
                }
                targets = [
                    _aggregate(self.batter, batter_id),
                    _aggregate(self.batter_pitch, (batter_id, family)),
                    _aggregate(self.pitcher, pitcher_id),
                    _aggregate(self.pitcher_pitch, (pitcher_id, family)),
                    _aggregate(self.league_pitch, family),
                    self.league,
                ]
                if pitcher_hand:
                    targets.append(
                        _aggregate(
                            self.batter_vs_hand,
                            (batter_id, pitcher_hand),
                        )
                    )
                for target in targets:
                    target.add(**values)
                self.pitcher_families.setdefault(pitcher_id, set()).add(family)

    @staticmethod
    def _prefixed(
        prefix: str,
        aggregate: Optional[PitchAggregate],
    ) -> dict[str, Optional[float]]:
        snapshot = (aggregate or PitchAggregate()).snapshot()
        return {
            f"{prefix}_pitch_sample": snapshot["pitches"],
            f"{prefix}_swing_sample": snapshot["swings"],
            f"{prefix}_bip_sample": snapshot["balls_in_play"],
            f"{prefix}_contact_rate": snapshot["contact_rate"],
            f"{prefix}_whiff_rate": snapshot["whiff_rate"],
            f"{prefix}_bip_hit_rate": snapshot["bip_hit_rate"],
            f"{prefix}_avg_exit_velocity": snapshot["avg_exit_velocity"],
            f"{prefix}_hard_hit_rate": snapshot["hard_hit_rate"],
        }

    def contact_features(
        self,
        batter_id: int,
        pitcher_hand: Optional[str],
    ) -> dict[str, Optional[float]]:
        hand = str(pitcher_hand or "").upper()
        overall = self._prefixed("batter", self.batter.get(batter_id))
        split = self._prefixed(
            "batter_hand",
            self.batter_vs_hand.get((batter_id, hand)) if hand else None,
        )
        overall["batter_contact_missing"] = int(
            not overall["batter_pitch_sample"]
        )
        overall["batter_hand_contact_missing"] = int(
            not split["batter_hand_pitch_sample"]
        )
        return {**overall, **split}

    def pitcher_features(self, pitcher_id: int) -> dict[str, Optional[float]]:
        snapshot = (self.pitcher.get(pitcher_id) or PitchAggregate()).snapshot()
        return {
            "pitcher_pitch_sample": snapshot["pitches"],
            "pitcher_swing_sample": snapshot["swings"],
            "pitcher_bip_sample": snapshot["balls_in_play"],
            "pitcher_contact_allowed": snapshot["contact_rate"],
            "pitcher_whiff_rate": snapshot["whiff_rate"],
            "pitcher_bip_hit_rate": snapshot["bip_hit_rate"],
            "pitcher_avg_exit_velocity_allowed": snapshot[
                "avg_exit_velocity"
            ],
            "pitcher_hard_hit_rate_allowed": snapshot["hard_hit_rate"],
            "pitcher_avg_velocity": snapshot["avg_velocity"],
            "pitcher_avg_horizontal_break": snapshot[
                "avg_horizontal_break"
            ],
            "pitcher_avg_induced_vertical_break": snapshot[
                "avg_induced_vertical_break"
            ],
            "pitcher_avg_extension": snapshot["avg_extension"],
            "pitcher_pitch_data_missing": int(not snapshot["pitches"]),
        }

    def arsenal_features(
        self,
        batter_id: int,
        pitcher_id: int,
        *,
        shrinkage_strength: float = 40.0,
    ) -> dict[str, Optional[float]]:
        families = self.pitcher_families.get(pitcher_id, set())
        pitcher_aggs = {
            family: self.pitcher_pitch[(pitcher_id, family)]
            for family in families
            if (pitcher_id, family) in self.pitcher_pitch
        }
        total_pitches = sum(agg.pitches for agg in pitcher_aggs.values())
        if not total_pitches:
            return {
                "arsenal_match_contact": None,
                "arsenal_match_whiff": None,
                "arsenal_match_bip_hit": None,
                "arsenal_match_exit_velocity": None,
                "arsenal_coverage": 0.0,
                "arsenal_pitch_families": 0,
                "arsenal_usage_entropy": None,
                "arsenal_matchup_missing": 1,
            }

        weighted: dict[str, float] = {
            "contact": 0.0,
            "whiff": 0.0,
            "bip_hit": 0.0,
            "exit_velocity": 0.0,
        }
        weights_present = {key: 0.0 for key in weighted}
        coverage = 0.0
        entropy = 0.0
        for family, pitcher_agg in pitcher_aggs.items():
            usage = pitcher_agg.pitches / total_pitches
            if usage > 0:
                entropy -= usage * math.log(usage)
            batter_agg = self.batter_pitch.get((batter_id, family))
            batter_snapshot = (batter_agg or PitchAggregate()).snapshot()
            league_snapshot = (
                self.league_pitch.get(family) or self.league
            ).snapshot()
            if batter_agg and batter_agg.pitches:
                coverage += usage

            candidates = {
                "contact": (
                    batter_snapshot["contact_rate"],
                    int(batter_snapshot["swings"] or 0),
                    league_snapshot["contact_rate"],
                ),
                "whiff": (
                    batter_snapshot["whiff_rate"],
                    int(batter_snapshot["swings"] or 0),
                    league_snapshot["whiff_rate"],
                ),
                "bip_hit": (
                    batter_snapshot["bip_hit_rate"],
                    int(batter_snapshot["balls_in_play"] or 0),
                    league_snapshot["bip_hit_rate"],
                ),
                "exit_velocity": (
                    batter_snapshot["avg_exit_velocity"],
                    int(batter_snapshot["balls_in_play"] or 0),
                    league_snapshot["avg_exit_velocity"],
                ),
            }
            for key, (player_rate, sample, league_rate) in candidates.items():
                value = shrunk_rate(
                    player_rate,
                    sample,
                    league_rate,
                    strength=shrinkage_strength,
                )
                if value is not None:
                    weighted[key] += usage * value
                    weights_present[key] += usage

        def normalized(key: str) -> Optional[float]:
            weight = weights_present[key]
            return weighted[key] / weight if weight else None

        return {
            "arsenal_match_contact": normalized("contact"),
            "arsenal_match_whiff": normalized("whiff"),
            "arsenal_match_bip_hit": normalized("bip_hit"),
            "arsenal_match_exit_velocity": normalized("exit_velocity"),
            "arsenal_coverage": coverage,
            "arsenal_pitch_families": len(pitcher_aggs),
            "arsenal_usage_entropy": entropy,
            "arsenal_matchup_missing": 0,
        }

    def features(
        self,
        *,
        batter_id: int,
        pitcher_id: int,
        pitcher_hand: Optional[str],
    ) -> dict[str, Optional[float]]:
        return {
            **self.contact_features(batter_id, pitcher_hand),
            **self.pitcher_features(pitcher_id),
            **self.arsenal_features(batter_id, pitcher_id),
        }


def summarize_recent_team_games(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = 10,
) -> dict[str, Optional[float]]:
    recent = list(rows)[-limit:]
    if not recent:
        return {
            "team_recent_pa_per_game": None,
            "team_recent_runs_per_game": None,
            "team_recent_hits_per_game": None,
            "team_recent_games": 0,
        }
    count = len(recent)
    return {
        "team_recent_pa_per_game": sum(
            _safe_int(row.get("plate_appearances")) for row in recent
        ) / count,
        "team_recent_runs_per_game": sum(
            _safe_int(row.get("runs")) for row in recent
        ) / count,
        "team_recent_hits_per_game": sum(
            _safe_int(row.get("hits")) for row in recent
        ) / count,
        "team_recent_games": count,
    }


def starter_workload_features(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_date: str,
) -> dict[str, Optional[float]]:
    history = list(rows)
    starts = [row for row in history if row.get("started")]
    recent = starts[-3:]
    if not recent:
        return {
            "starter_workload_sample": 0,
            "starter_last3_pitches": None,
            "starter_last3_batters_faced": None,
            "starter_last3_innings": None,
            "starter_days_rest": None,
            "starter_short_start_rate": None,
        }
    from datetime import date

    target = date.fromisoformat(target_date)
    last_date = date.fromisoformat(str(recent[-1]["game_date"]))
    return {
        "starter_workload_sample": len(starts),
        "starter_last3_pitches": sum(
            _safe_int(row.get("pitches_thrown")) for row in recent
        ) / len(recent),
        "starter_last3_batters_faced": sum(
            _safe_int(row.get("batters_faced")) for row in recent
        ) / len(recent),
        "starter_last3_innings": sum(
            _safe_float(row.get("innings_pitched")) or 0.0 for row in recent
        ) / len(recent),
        "starter_days_rest": (target - last_date).days,
        "starter_short_start_rate": sum(
            1 for row in starts if _safe_int(row.get("batters_faced")) < 18
        ) / len(starts),
    }


def bullpen_workload_features(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_date: str,
) -> dict[str, Optional[float]]:
    from datetime import date

    target = date.fromisoformat(target_date)
    recent = []
    for row in rows:
        game_date = row.get("game_date")
        if not game_date:
            continue
        days_ago = (target - date.fromisoformat(str(game_date))).days
        if 1 <= days_ago <= 3:
            recent.append((days_ago, row))
    yesterday = [row for days_ago, row in recent if days_ago == 1]
    unique_yesterday = {
        _safe_int(row.get("pitcher_id"))
        for row in yesterday
        if _safe_int(row.get("pitcher_id"))
    }
    unique_three = {
        _safe_int(row.get("pitcher_id"))
        for _, row in recent
        if _safe_int(row.get("pitcher_id"))
    }
    return {
        "bullpen_pitches_yesterday": sum(
            _safe_int(row.get("pitches_thrown")) for row in yesterday
        ),
        "bullpen_pitches_last3_days": sum(
            _safe_int(row.get("pitches_thrown")) for _, row in recent
        ),
        "bullpen_relievers_yesterday": len(unique_yesterday),
        "bullpen_relievers_last3_days": len(unique_three),
        "bullpen_recent_appearances": len(recent),
    }

