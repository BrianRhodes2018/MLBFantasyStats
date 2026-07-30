from __future__ import annotations

from pathlib import Path

import polars as pl

from ml_environment import (
    canonical_text_sha256,
    dataframe_fingerprint,
    json_fingerprint,
    sha256_file,
    source_fingerprint,
    training_manifest,
)


def test_json_fingerprint_is_stable_and_order_independent() -> None:
    left = json_fingerprint({"packages": {"numpy": "2", "polars": "1"}})
    right = json_fingerprint({"packages": {"polars": "1", "numpy": "2"}})
    assert left == right


def test_sha256_file_and_training_manifest(tmp_path: Path) -> None:
    fixture = tmp_path / "season.parquet"
    fixture.write_bytes(b"fixed training fixture")

    assert sha256_file(fixture) == sha256_file(fixture)
    manifest = training_manifest([fixture], row_count=12, frame_sha256="frame-hash")
    assert manifest["row_count"] == 12
    assert manifest["frame_sha256"] == "frame-hash"
    assert manifest["files"] == [
        {
            "name": "season.parquet",
            "sha256": sha256_file(fixture),
            "size_bytes": 22,
        }
    ]
    assert isinstance(manifest["sha256"], str)


def test_text_fingerprint_is_stable_across_line_endings(tmp_path: Path) -> None:
    windows = tmp_path / "windows.lock"
    linux = tmp_path / "linux.lock"
    windows.write_bytes(b"version = 1\r\npackage = 'numpy'\r\n")
    linux.write_bytes(b"version = 1\npackage = 'numpy'\n")
    assert canonical_text_sha256(windows) == canonical_text_sha256(linux)


def test_source_fingerprint_changes_with_source_contents(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text("MODEL_VERSION = 1\n", encoding="utf-8")
    before = source_fingerprint([source])

    source.write_text("MODEL_VERSION = 2\n", encoding="utf-8")
    assert source_fingerprint([source]) != before


def test_dataframe_fingerprint_is_order_sensitive() -> None:
    frame = pl.DataFrame({"player_id": [1, 2], "got_hit": [0, 1]})
    assert dataframe_fingerprint(frame) == dataframe_fingerprint(frame.clone())
    assert dataframe_fingerprint(frame) != dataframe_fingerprint(frame.reverse())
