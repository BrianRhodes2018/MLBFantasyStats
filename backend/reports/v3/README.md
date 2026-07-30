# Hit Model V3 Research Evidence

Small, reviewable summaries are committed here. Row-level predictions,
top-N selections, and rebuilt datasets are retained locally under the
gitignored `backend/backtest_results/` directory; their SHA-256 hashes are
recorded in the summaries.

## Frozen inputs

- Experiment contract: `backend/config/hit_model_v3_experiment.json`
- Point-in-time park snapshots:
  `backend/reference_data/park_factor_snapshots.json`
- V2 production datasets: the four season Parquet files under the local
  gitignored `backend/data/` directory
- E1 corrected datasets: the official and projected Parquet files under
  local `backend/backtest_results/v3_foundation/`
- V3 augmented projected datasets: one file per season under local
  `backend/backtest_results/v3_development/`
- Frozen E4 candidate: `backend/config/hit_model_v3_candidate.json`

## Reproduction commands

Run commands from `backend/` in the locked project environment.

```powershell
.\.venv\Scripts\python.exe scripts\build_v2_benchmark_package.py `
  --dataset data\hit_dataset_2023.parquet data\hit_dataset_2024.parquet `
            data\hit_dataset_2025.parquet data\hit_dataset.parquet

.\.venv\Scripts\python.exe scripts\evaluate_e1_baseline.py `
  --dataset backtest_results\v3_foundation\e1_projected.parquet `
  --summary reports\v3\e1_projected_baseline_summary.json

.\.venv\Scripts\python.exe scripts\audit_v3_feature_coverage.py `
  --dataset data\hit_dataset_2023.parquet data\hit_dataset_2024.parquet `
            data\hit_dataset_2025.parquet data\hit_dataset.parquet `
  --game-cache .backtest_cache

.\.venv\Scripts\python.exe scripts\evaluate_v3_ladder.py `
  --dataset backtest_results\v3_development\hit_v3_projected_2023.parquet `
            backtest_results\v3_development\hit_v3_projected_2024.parquet `
            backtest_results\v3_development\hit_v3_projected_2025.parquet `
            backtest_results\v3_development\hit_v3_projected_2026.parquet

.\.venv\Scripts\python.exe scripts\evaluate_v3_decomposed.py `
  --dataset backtest_results\v3_development\hit_v3_projected_2023.parquet `
            backtest_results\v3_development\hit_v3_projected_2024.parquet `
            backtest_results\v3_development\hit_v3_projected_2025.parquet `
            backtest_results\v3_development\hit_v3_projected_2026.parquet
```

The V2 package must produce the same `determinism_key` on repeated runs.
The locked final was opened after E4 and its calibration protocol were frozen.
`v3_locked_final.json` records the rejected shadow-entry decision. Do not
rerun or use that final block to tune a replacement candidate.
