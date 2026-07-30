# Plan: ML Engineering Environment Remediation and Reproducibility

**Status:** Option 3 environment implemented; V2 equivalence/backtest validation remains
**Created:** 2026-07-28
**Implementation updated:** 2026-07-28
**Unblocks:** [PLAN_HIT_PICKS_V3.md](PLAN_HIT_PICKS_V3.md)

## Goal

Replace the mixed global Anaconda/pip runtime with an isolated, version-locked, reproducible environment for model training, daily hit-pick generation, backtesting, CI, and deployment.

The repair must:

- eliminate the NumPy/pandas/PyArrow binary mismatch
- preserve a rollback path to the current V2 process
- make dependency changes reviewable
- make model outputs traceable to code, data, calibration, and library versions
- fail clearly before publishing picks when the environment is invalid

---

## Current state and evidence

### Runtime selected by the daily task

`backend/run_daily_hit_picks.ps1` currently hardcodes:

```text
C:\Users\brhod\anaconda3\python.exe
```

It falls back to `python` on `PATH` if that file is absent.

### Installed scientific stack reviewed on 2026-07-27

| Component | Version | Installation source observed |
|---|---:|---|
| Python | 3.12.7 | Anaconda base |
| NumPy | 2.3.2 | pip |
| pandas | 2.2.2 | conda |
| PyArrow | 16.1.0 | conda |
| SciPy | 1.18.0 | pip |
| scikit-learn | 1.9.0 | pip |
| Polars | 1.32.0 | pip |

Import behavior:

- NumPy: passes.
- SciPy: passes.
- scikit-learn: direct import passes.
- Polars: passes.
- pandas: fails with `numpy.core.multiarray failed to import`.
- PyArrow: fails with `numpy.core.multiarray failed to import`.

The failure occurs because the installed pandas and PyArrow binary extensions were built for the NumPy 1.x ABI, while the runtime loads NumPy 2.3.2.

### Observed operational effect

- The daily V2 job still completes because its core path uses Polars, NumPy arrays, and scikit-learn.
- scikit-learn's optional pandas checks trigger repeated ABI tracebacks.
- The 2026-07-27 scheduled run still trained, saved, and stored picks with exit code 0.
- The current daily log is approximately 401 KB and contains 148 copies of the NumPy ABI warning across recent runs.
- A fresh feature-ablation run generated an unusable volume of tracebacks and was stopped.
- pandas/PyArrow-dependent workflows fail outright.

### Dependency declaration gaps

`backend/requirements.txt` currently:

- leaves most packages unpinned
- declares `scikit-learn>=1.6`
- does not pin NumPy, SciPy, pandas, PyArrow, or Polars

The repository currently has:

- no project `.venv`
- no conda environment file
- no Python dependency lock
- no fixed Python version for Render
- Python 3.11 in GitHub Actions but Python 3.12.7 locally

`pip check` also reports unrelated conflicts in the global Anaconda base environment. Those global conflicts are not all caused by this project and should not be repaired destructively in place.

### Option 3 implementation validation

The replacement environment was built on 2026-07-28 with uv 0.11.33 and uv-managed CPython 3.12.7. It does not inherit its interpreter or packages from Anaconda.

Locked scientific versions:

| Component | Locked version |
|---|---:|
| NumPy | 2.5.1 |
| pandas | 3.0.5 |
| PyArrow | 23.0.1 |
| SciPy | 1.18.0 |
| scikit-learn | 1.9.0 |
| Polars | 1.43.1 |

Validation results:

- `uv pip check`: all 55 installed packages are compatible.
- Environment preflight: imports, HistGradientBoosting fit/predict, Parquet round trip, and Polars-to-pandas conversion pass.
- Backend suite: 120 tests pass.
- V2 walk-forward benchmark: pooled GBM top-10 remains 72.2%.
- Determinism: two reports are identical after removing the generation timestamp.
- Full feature-group ablation completes without NumPy ABI messages.
- The original Anaconda base remains unchanged as a rollback reference.

---

## Risk assessment

### Current production risk: medium

The daily job completes, but it relies on optional-import failures being caught. A future code path or library update could turn the warning into a fatal failure.

### Model-research risk: high

Repeated tracebacks make long backtests and ablations slow, noisy, and difficult to trust. Genuine failures can be buried in compatibility output.

### Reproducibility risk: high

The same model version string can be produced by different NumPy or scikit-learn versions. A dependency update can silently alter tree fitting, early stopping, probability output, or ranking.

### Calibration risk: medium to high

The saved isotonic curve is tied to the raw-probability distribution of the model that produced its fitting predictions. A changed library stack may produce a different raw distribution while still using the old calibration.

### Future V3 risk: high

V3 is expected to add more data ingestion and numerical experimentation. Building it on the global mixed environment would make results difficult to reproduce and may make pandas/PyArrow-based data paths fail.

---

## Decisions

### Use a project-specific virtual environment

Created:

```text
<repo>\backend\.venv\
```

The directory is already ignored by `.gitignore`.

Do not:

- modify or remove packages from the global Anaconda base environment
- make the scheduled task fall back silently to an arbitrary interpreter
- use the global environment to compile the new dependency lock

### Standardize the Python version

Selected target: Python 3.12.7.

Implemented:

- `backend/.python-version`
- the same patch version locally, in CI, and on Render
- the exact patch version in run manifests

If Render or a required dependency blocks Python 3.12, record the decision and standardize every environment on the selected alternative before model comparison.

### Use one dependency workflow

Selected workflow (Option 3):

- `backend/pyproject.toml`: human-maintained direct dependencies and intentional version ranges
- `backend/uv.lock`: exact cross-platform dependency lock used locally, in CI, and on Render
- `backend/.python-version`: Python 3.12.7 contract
- `backend/requirements.txt`: generated runtime compatibility export for tools that still require pip format
- `backend/requirements-dev.txt`: generated development compatibility export

Use uv 0.11.33 to resolve and sync the environment. Do not generate dependency state with `pip freeze` from the contaminated Anaconda base environment.

Include compatible, explicit versions for:

- NumPy
- SciPy
- pandas
- PyArrow
- Polars
- scikit-learn
- joblib
- threadpoolctl

Pandas and PyArrow should remain in the tested lock because:

- existing code calls `Polars.to_pandas()` in some CLI paths
- scikit-learn detects pandas when it is installed
- V3 data work may reasonably use Arrow interchange

---

## Phase 0 - Capture the existing baseline without changing it

### 0.1 Record current state

Save a diagnostic report containing:

- `python --version`
- resolved interpreter path
- exact package versions
- `pip check`
- import-smoke results
- latest successful daily-run timestamp
- latest V2 prediction metadata
- V2 calibration file hash
- current Git commit

Do not commit machine-specific paths or secrets.

### 0.2 Preserve rollback references

- Record the current scheduled-task action and triggers.
- Preserve the current Anaconda interpreter path in the rollout notes.
- Record the current V2 output and top-N ranking for a frozen fixture date.
- Do not delete the global environment after cutover.

### 0.3 Establish change boundaries

The remediation does not:

- upgrade unrelated frontend dependencies
- remove unrelated Anaconda packages
- change V2 features or hyperparameters
- refit calibration unless validation proves the runtime change affects raw probabilities
- alter production database credentials or access

**Exit criteria:**

- The current operational path and rollback path are documented.
- A frozen V2 fixture and package inventory are saved.

---

## Phase 1 - Define and lock a compatible environment

### 1.1 Split direct dependencies from resolved dependencies

Keep intentional direct dependencies in `pyproject.toml`; keep exact transitive versions and wheel hashes in `uv.lock`.

Compatibility requirements exports are generated from `uv.lock` and are not authoritative.

### 1.2 Resolve from a clean interpreter

Using a temporary uv bootstrap environment:

1. Install only uv 0.11.33.
2. Resolve `uv.lock` against Python 3.12.7.
3. Sync the project-local `.venv`.
4. Run `uv pip check`.
5. Run the scientific import smoke test.
6. Run a small model fit/predict test.
7. Run a Polars Parquet round trip.
8. Run a Polars-to-pandas conversion.

Reject the candidate lock if any import prints an ABI warning, even when the process exits successfully.

### 1.3 Add a Python-version contract

Added and used:

- `backend/.python-version`
- `backend/scripts/bootstrap_ml_environment.ps1`
- the selected Python version in GitHub Actions
- the selected Python version in Render configuration

### 1.4 Review transitive dependencies

Pay particular attention to packages with native binary components:

- NumPy
- SciPy
- pandas
- PyArrow
- scikit-learn
- database drivers

The lock must be tested on:

- Windows for the scheduled hit-picks runner
- Linux for CI and Render

**Exit criteria:**

- A clean Windows environment and a clean Linux CI environment install from the lock.
- `pip check` passes for project dependencies.
- All scientific imports pass without warnings.
- The lock is reviewable and generated from declared direct dependencies.

---

## Phase 2 - Add environment diagnostics and reproducibility checks

### 2.1 Add an ML environment smoke script

Implemented file:

```text
backend/scripts/check_ml_environment.py
```

It should:

- print Python executable and version
- print relevant package versions
- import NumPy, SciPy, pandas, PyArrow, Polars, and scikit-learn
- create a small NumPy matrix
- fit and predict with `HistGradientBoostingClassifier`
- write and read a temporary Parquet file with Polars
- convert a small Polars frame to pandas
- fail nonzero on warnings categorized as ABI/import incompatibility
- emit a concise JSON summary when requested

The script must not connect to production services.

### 2.2 Add a dependency fingerprint

Calculate a stable hash from:

- Python major/minor
- locked dependency file
- relevant package versions

Expose it to the training and prediction scripts.

### 2.3 Add model-run provenance

Every hit-model run should record:

```json
{
  "model_version": "hit_gbm_v2_cal",
  "python_version": "3.12.x",
  "numpy_version": "...",
  "scipy_version": "...",
  "scikit_learn_version": "...",
  "polars_version": "...",
  "dependency_lock_sha256": "...",
  "code_commit": "...",
  "feature_schema_sha256": "...",
  "calibration_sha256": "...",
  "training_data_manifest_sha256": "..."
}
```

Do not include secrets, database URLs, or user-specific paths.

### 2.4 Couple calibrator and base-model fingerprints

- Store the expected base-model and feature-schema fingerprints in calibration metadata.
- Refuse or clearly fail when a calibrator is loaded for an incompatible model bundle.
- Provide an explicit override only for offline investigation, never for the scheduled production job.

**Exit criteria:**

- One command validates the environment before expensive work begins.
- Every generated prediction can be traced to a dependency, code, feature, data, and calibration fingerprint.

---

## Phase 3 - Validate V2 in the clean environment

This phase determines whether changing the runtime changes the model.

### 3.1 Run the automated test suite

- Backend unit tests.
- Hit-model tests.
- Calibration monotonicity and order-preservation tests.
- Database-free prediction fixtures.
- Frontend tests remain unchanged unless model metadata affects the UI.

### 3.2 Run deterministic model fixtures

For a frozen dataset:

- Train V2 twice in the same clean environment.
- Compare raw probabilities.
- Compare calibrated probabilities.
- Compare top-5/10/15 rankings.
- Confirm the same early-stopping result.

Use exact equality where supported. Otherwise define and document a small numerical tolerance before examining differences.

### 3.3 Compare old and new environments

Using a frozen date:

- score the same candidates
- compare raw probabilities
- compare calibrated probabilities
- compare ordering
- investigate every material difference

Do not publish probabilities generated by the new environment under the old model label unless equivalence is established.

If equivalence is not established:

- create a new environment-specific model version
- rerun walk-forward validation
- fit a new calibration on valid out-of-sample predictions
- retain V2 as the rollback model

### 3.4 Run the complete backtest and ablation smoke

- Run the standard walk-forward report.
- Run at least one complete feature-group ablation.
- Confirm logs remain concise.
- Confirm runtime is within the scheduled operating window.
- Save a summary with the new dependency fingerprint.

**Exit criteria:**

- V2 behavior is either reproduced or intentionally versioned.
- Full backtests complete without ABI messages.
- Calibration compatibility is proven or a new calibrator is created.

---

## Phase 4 - Cut over the Windows scheduled task safely

### 4.1 Bootstrap the project environment

Implemented helper:

```text
backend/scripts/bootstrap_ml_environment.ps1
```

Responsibilities:

- verify the selected Python version
- create `.venv`
- install the locked runtime dependencies
- run `pip check`
- run `check_ml_environment.py`
- run a database-free hit-model smoke test

It must stop on the first failed check.

### 4.2 Remove interpreter ambiguity

Update `backend/run_daily_hit_picks.ps1` to:

- resolve `<repo>\backend\.venv\Scripts\python.exe`
- fail if that interpreter is missing
- never fall back silently to `PATH`
- run the ML environment smoke check before model generation
- log the dependency fingerprint once per run
- preserve existing grade/predict exit-code monitoring

### 4.3 Stage the cutover

1. Build and validate `.venv` without modifying the scheduled task.
2. Run grading and prediction manually in dry-run or non-production-write mode.
3. Compare output with the current scheduled runner.
4. Update the script interpreter path.
5. Trigger one supervised scheduled run.
6. Verify file output, database persistence, healthcheck success, and logs.
7. Leave the old Anaconda environment untouched for rollback.

### 4.4 Rollback

If the new runner fails:

- restore the recorded previous script revision or interpreter path
- run the last verified V2 process
- preserve the failed run's logs and manifest
- do not partially publish mixed-environment predictions

**Exit criteria:**

- The scheduled task uses only the project `.venv`.
- Missing or invalid environments fail before publishing.
- A supervised run completes without ABI warnings.
- Rollback has been tested or rehearsed.

---

## Phase 5 - Align CI and Render

### 5.1 GitHub Actions

Update workflows to:

- use the selected Python minor version
- cache using the generated lock file
- install the lock, not open-ended requirements
- run `pip check`
- run `check_ml_environment.py`
- run backend tests

Add a small Windows scientific-stack job because the scheduled model runner is Windows-based. The full test suite can remain on Linux if runtime cost is a concern.

Workflows in scope:

- `.github/workflows/ci.yml`
- `.github/workflows/daily-update.yml`

### 5.2 Render

Update `render.yaml` and supporting files to:

- declare the selected Python version
- install the exact runtime lock
- fail the build on dependency conflicts
- run a lightweight import smoke check during build

Do not run the expensive full model backtest in a Render build.

### 5.3 Prevent dependency drift

Add a CI check that fails when:

- `requirements.in` changes without regenerating the lock
- the lock was generated with the wrong Python minor version
- direct dependency metadata and lock fingerprints disagree

**Exit criteria:**

- Local Windows, CI Linux, CI Windows smoke, and Render use the same declared Python and package versions.
- A dependency update is visible as a reviewed lock-file change.

---

## Phase 6 - Improve scheduled-run observability

### 6.1 Structured run summary

At the end of each run, log:

- grade exit code
- prediction exit code
- model version
- dependency fingerprint
- code commit
- training row count
- candidate count
- lineup-source counts
- calibration fingerprint
- elapsed time

### 6.2 Fail on known bad signatures

Treat these as preflight failures:

- `numpy.core.multiarray failed to import`
- `_ARRAY_API not found`
- NumPy ABI incompatibility
- calibrator fingerprint mismatch
- feature-schema mismatch

### 6.3 Log retention

- Rotate or cap `hit_picks_daily.log`.
- Keep enough history for diagnosis.
- Do not allow repeated tracebacks to grow indefinitely.
- Never write secrets or connection strings.

### 6.4 Healthcheck semantics

- Signal `/start` after preflight begins.
- Signal success only after both grading and prediction persistence succeed.
- Signal `/fail` with a concise summary and bounded log tail.

**Exit criteria:**

- A failed environment cannot produce a false successful healthcheck.
- Normal logs are concise enough that a real traceback is visible.

---

## Required tests

### Environment tests

- Every scientific import succeeds.
- `pip check` succeeds for project dependencies.
- Minimal HistGradientBoosting fit/predict succeeds.
- Polars Parquet round trip succeeds.
- Polars-to-pandas conversion succeeds.

### Reproducibility tests

- Two identical training runs produce identical or tolerance-bounded probabilities.
- Dependency fingerprint is stable for an unchanged lock.
- Changing the lock changes the fingerprint.
- Calibration refuses a mismatched model fingerprint.

### Scheduler tests

- Missing `.venv` fails before grading or prediction.
- Smoke-check failure returns nonzero.
- Grade failure reports failure.
- Prediction failure reports failure.
- Successful dry run records provenance.
- Paths containing spaces are handled safely.

### Cross-platform tests

- Linux lock installation and imports.
- Windows lock installation and imports.
- Backend tests on the selected Python version.
- Render build command against the locked runtime dependencies.

---

## Rollout checklist

- [x] Record the current interpreter and scheduled runtime.
- [x] Select and document Python 3.12.7.
- [x] Create `pyproject.toml` with direct dependencies.
- [x] Generate `uv.lock` and compatibility exports from a clean resolver.
- [x] Validate the Windows environment; Linux validation is configured in CI.
- [x] Add the ML environment smoke script.
- [x] Add dependency and prediction-file provenance.
- [x] Couple calibration to the model, feature-schema, and dependency fingerprints.
- [x] Reproduce V2's 72.2% pooled GBM top-10 benchmark deterministically.
- [x] Run the complete walk-forward and feature-group ablation smoke.
- [x] Build the project `.venv`.
- [x] Compare old and new daily outputs (exact-context probability delta was zero).
- [x] Cut over the scheduled-runner script to the locked `.venv`.
- [x] Verify healthcheck, logs, immutable JSON, and database writes in production.
- [x] Align CI, the daily GitHub workflow, and Render configuration.
- [x] Retain and document the untouched Anaconda rollback environment.

---

## Definition of done

The engineering remediation is complete when:

- pandas and PyArrow import successfully with the selected NumPy.
- No NumPy ABI warnings appear in tests, backtests, or daily logs.
- The repository has one documented Python version and exact dependency locks.
- Local scheduled training, CI, and Render install from the reviewed lock.
- The daily task uses the repository `.venv` with no silent fallback.
- V2 is reproducible or has been explicitly re-versioned and recalibrated.
- Every model run records code, dependency, feature, data, and calibration provenance.
- Full walk-forward and ablation runs complete with concise logs.
- A tested rollback path remains available.

---

## Follow-up after remediation

Once this plan is complete:

1. Begin Phase 0 of [PLAN_HIT_PICKS_V3.md](PLAN_HIT_PICKS_V3.md).
2. Reproduce the V2 benchmark in the locked environment.
3. Do not start V3 feature comparisons until that benchmark and calibration compatibility are approved.
