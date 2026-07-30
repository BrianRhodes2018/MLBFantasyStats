# V3 Historical Feature Coverage Audit

Measured from the saved V2 Parquet datasets and cached MLB StatsAPI regular-season game feeds. Rates are field availability, not evidence that a feature improves predictions.

## Decision summary

| Feature group | Status | Historical source |
|---|---|---|
| Pitch Mix | ready | cached MLB StatsAPI pitch events |
| Contact | ready | cached MLB StatsAPI pitch and batted-ball events |
| Xba | new_source_required | Baseball Savant Statcast search export |
| Bat Tracking | new_source_required | Baseball Savant bat-tracking leaderboard/export |
| Starter Workload | ready | cached MLB StatsAPI boxscores |
| Bullpen Availability | ready | cached MLB StatsAPI boxscores |

Pitch mix, contact outcomes, starter workload, and bullpen workload can be built from the existing cache. xBA and bat-tracking fields are not present in that cache and require a separately versioned Baseball Savant ingestion path before they enter an experiment.

## Game-feed coverage by season

| Season | Games | Pitch type | Velocity | Movement | EV on BIP | Starter pitch count | Bullpen pitch count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 2,430 | 100.0% | 100.0% | 100.0% | 99.7% | 100.0% | 100.0% |
| 2024 | 2,429 | 100.0% | 100.0% | 100.0% | 99.7% | 100.0% | 100.0% |
| 2025 | 2,430 | 99.9% | 99.9% | 99.9% | 99.6% | 100.0% | 100.0% |
| 2026 | 1,627 | 99.9% | 99.9% | 99.9% | 99.4% | 100.0% | 100.0% |

## Guardrails

- Missing xBA or bat-tracking data must not remove a hitter from the shared candidate cohort.
- Every new source needs a sample-count feature, missing indicator, and explicit fallback.
- Source ingestion and feature definitions must be point-in-time and independently switchable.
- Feature value is determined by chronological ablation tests, not coverage alone.
