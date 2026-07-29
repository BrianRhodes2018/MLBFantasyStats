"""Freeze and verify the daily candidate cohort used by competing models.

The cohort is deliberately model-agnostic: it records who was eligible,
which game they were in, their lineup slot/source, and the expected opposing
starter.  V2 and V3 must produce the same cohort hash before their rankings
can be compared.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

COHORT_SCHEMA_VERSION = 1
COHORT_FIELDS = (
    "game_pk",
    "player_id",
    "team",
    "opponent",
    "batting_order",
    "lineup_source",
    "pitcher_id",
)


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    identity = {field: candidate.get(field) for field in COHORT_FIELDS}
    # `game_id` is accepted only as a compatibility input. New outputs use
    # the unambiguous MLB name `game_pk`.
    identity["game_pk"] = _integer(
        candidate.get("game_pk", candidate.get("game_id"))
    )
    identity["player_id"] = _integer(candidate.get("player_id"))
    identity["batting_order"] = _integer(candidate.get("batting_order"))
    identity["pitcher_id"] = _integer(candidate.get("pitcher_id"))
    for field in ("team", "opponent", "lineup_source"):
        value = identity[field]
        identity[field] = str(value) if value is not None else None
    return identity


def freeze_candidate_cohort(
    candidates: Iterable[Mapping[str, Any]],
    *,
    require_game_pk: bool = True,
) -> dict[str, Any]:
    """Return a canonical manifest and SHA-256 id for a candidate slate."""
    identities = [_candidate_identity(candidate) for candidate in candidates]
    identities.sort(
        key=lambda row: (
            row["game_pk"] if row["game_pk"] is not None else -1,
            row["team"] or "",
            row["batting_order"] if row["batting_order"] is not None else 99,
            row["player_id"] if row["player_id"] is not None else -1,
        )
    )

    keys = [(row["game_pk"], row["player_id"]) for row in identities]
    if len(keys) != len(set(keys)):
        raise ValueError("Candidate cohort contains a duplicate game/player identity.")
    if any(player_id is None for _, player_id in keys):
        raise ValueError("Every candidate must have player_id.")
    if require_game_pk and any(game_pk is None for game_pk, _ in keys):
        raise ValueError("Every candidate must have both game_pk and player_id.")

    manifest = {
        "schema_version": COHORT_SCHEMA_VERSION,
        "candidate_count": len(identities),
        "candidates": identities,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return {
        "candidate_cohort_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "candidate_count": len(identities),
        "candidate_manifest": manifest,
    }


def assert_candidate_cohort(
    candidates: Iterable[Mapping[str, Any]],
    expected_cohort_id: str,
) -> dict[str, Any]:
    """Fail closed if a scorer silently adds, removes, or changes a candidate."""
    frozen = freeze_candidate_cohort(candidates)
    if frozen["candidate_cohort_id"] != expected_cohort_id:
        raise ValueError(
            "Candidate cohort changed during scoring: "
            f"expected {expected_cohort_id}, got {frozen['candidate_cohort_id']}."
        )
    return frozen


def prediction_mode(candidates: Iterable[Mapping[str, Any]]) -> str:
    """Classify the lineup snapshot as projected, official, or hybrid."""
    sources = [
        str(candidate.get("lineup_source") or "").strip().lower()
        for candidate in candidates
    ]
    official = sum(source == "official lineup" for source in sources)
    if sources and official == len(sources):
        return "official"
    if official:
        return "hybrid"
    return "projected"
