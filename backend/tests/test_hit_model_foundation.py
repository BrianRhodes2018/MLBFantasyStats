"""Tests for the contracts V2 and V3 must share."""

import pytest

from hit_model.cohort import (
    assert_candidate_cohort,
    freeze_candidate_cohort,
    prediction_mode,
)
from hit_model.opportunity import (
    PA_BUCKETS,
    conditional_hit_probability,
    marginal_hit_probability,
    marginal_hit_probability_constant_rate,
    plate_appearance_bucket,
    validate_pa_distribution,
)


def candidate(game_pk, player_id, slot, source="official lineup"):
    return {
        "game_pk": game_pk,
        "player_id": player_id,
        "team": "DET",
        "opponent": "CLE",
        "batting_order": slot,
        "lineup_source": source,
        "pitcher_id": 99,
    }


class TestCandidateCohort:
    def test_hash_is_order_independent_but_identity_sensitive(self):
        candidates = [candidate(700, 1, 1), candidate(700, 2, 2)]
        first = freeze_candidate_cohort(candidates)
        second = freeze_candidate_cohort(list(reversed(candidates)))
        assert first == second

        changed = [candidate(700, 1, 1), candidate(700, 3, 2)]
        with pytest.raises(ValueError, match="Candidate cohort changed"):
            assert_candidate_cohort(changed, first["candidate_cohort_id"])

    def test_doubleheader_player_is_two_distinct_candidates(self):
        frozen = freeze_candidate_cohort(
            [candidate(700, 1, 1), candidate(701, 1, 1)]
        )
        assert frozen["candidate_count"] == 2

    def test_new_cohorts_require_game_identity(self):
        with pytest.raises(ValueError, match="game_pk"):
            freeze_candidate_cohort([{"player_id": 1}])

    def test_prediction_mode_reflects_snapshot(self):
        assert prediction_mode([candidate(700, 1, 1)]) == "official"
        assert prediction_mode(
            [candidate(700, 1, 1, "projected from recent lineups")]
        ) == "projected"
        assert prediction_mode(
            [
                candidate(700, 1, 1),
                candidate(700, 2, 2, "projected from recent lineups"),
            ]
        ) == "hybrid"


class TestOpportunityContract:
    def distribution(self):
        return {
            "0": 0.02,
            "1": 0.03,
            "2": 0.05,
            "3": 0.15,
            "4": 0.40,
            "5": 0.30,
            "6+": 0.05,
        }

    def test_contract_includes_zero_through_six_plus(self):
        assert PA_BUCKETS == ("0", "1", "2", "3", "4", "5", "6+")
        assert plate_appearance_bucket(0) == "0"
        assert plate_appearance_bucket(2) == "2"
        assert plate_appearance_bucket(8) == "6+"
        assert validate_pa_distribution(self.distribution()) == self.distribution()

    def test_incomplete_distribution_fails_closed(self):
        incomplete = {key: value for key, value in self.distribution().items() if key != "0"}
        with pytest.raises(ValueError, match="exactly"):
            validate_pa_distribution(incomplete)

    def test_zero_pa_can_never_produce_a_hit(self):
        conditionals = {
            bucket: conditional_hit_probability(0.25, min(int(bucket[0]), 6))
            for bucket in PA_BUCKETS
        }
        conditionals["0"] = 0.0
        probability = marginal_hit_probability(self.distribution(), conditionals)
        assert 0 < probability < 1

        conditionals["0"] = 0.1
        with pytest.raises(ValueError, match=r"PA=0"):
            marginal_hit_probability(self.distribution(), conditionals)

    def test_low_opportunity_buckets_change_the_total(self):
        full = marginal_hit_probability_constant_rate(self.distribution(), 0.25)
        no_low_pa = {
            "0": 0.0,
            "1": 0.0,
            "2": 0.0,
            "3": 0.20,
            "4": 0.40,
            "5": 0.35,
            "6+": 0.05,
        }
        optimistic = marginal_hit_probability_constant_rate(no_low_pa, 0.25)
        assert full < optimistic
