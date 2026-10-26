"""Cross-platform handoff-archive extractor (scripts/python/extract_handoff_archive.py).

Synthetic archives and catalogues in tmp_path only; the official ZIP files are never touched.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "python" / "extract_handoff_archive.py"
_spec = importlib.util.spec_from_file_location("extract_handoff_archive", SCRIPT)
eha = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eha
_spec.loader.exec_module(eha)

ASSET = "expanded_release_dataset"


def make_zip(path: Path, entries: list[tuple[str, bytes | None, int | None]],
             compression: int = zipfile.ZIP_DEFLATED) -> Path:
    """Write entries with their names exactly as given (backslashes kept, even on Windows)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # duplicate-name warnings are part of some tests
        with zipfile.ZipFile(path, "w", compression) as archive:
            for name, data, mode in entries:
                info = zipfile.ZipInfo("placeholder")
                info.filename = name
                info.compress_type = compression
                if mode is not None:
                    info.create_system = 3
                    info.external_attr = mode << 16
                archive.writestr(info, data if data is not None else b"")
    return path


def make_catalogue(path: Path, archive: Path, *, size_delta: int = 0, sha: str | None = None) -> Path:
    data = archive.read_bytes()
    assets = [{"asset_id": asset_id, "file_name": archive.name, "bytes": len(data) + size_delta,
               "sha256": sha or hashlib.sha256(data).hexdigest()} for asset_id in eha.SUPPORTED_ASSET_IDS]
    path.write_text(json.dumps({"assets": assets}), encoding="utf-8")
    return path


def run_cli(tmp_path: Path, archive: Path, destination: Path, **catalogue_options) -> int:
    catalogue = make_catalogue(tmp_path / "catalogue.json", archive, **catalogue_options)
    return eha.main(["--asset-id", ASSET, "--archive", str(archive), "--destination", str(destination),
                     "--catalogue", str(catalogue)])


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


BINARY = bytes(range(256)) * 37 + b"\r\n\x00tail"
CONTENT = {"derived/keys.csv": b"modeling_key\nmk_1\r\nmk_2\n", "raw/expanded-v1/s.jsonl": BINARY,
           "reports/empty.json": b"", "top.txt": b"top-level file"}


@pytest.mark.parametrize("separator", ["\\", "/"])
def test_extracts_byte_identical_files_for_both_separators(tmp_path: Path, separator: str) -> None:
    entries = [(name.replace("/", separator), data, None) for name, data in CONTENT.items()]
    archive = make_zip(tmp_path / "release.zip", entries)
    stored_name = ("derived" + separator + "keys.csv").encode()
    assert stored_name in archive.read_bytes()  # the fixture really stores this separator
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 0
    assert tree(destination) == CONTENT
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before


def test_directory_entries_and_stored_compression(tmp_path: Path) -> None:
    entries = [("run\\", None, stat.S_IFDIR | 0o755), ("run\\figures\\", None, None),
               ("run\\figures\\a.png", BINARY, stat.S_IFREG | 0o644), ("run/report.md", b"# report\n", None)]
    archive = make_zip(tmp_path / "results.zip", entries, compression=zipfile.ZIP_STORED)
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 0
    assert tree(destination) == {"run/figures/a.png": BINARY, "run/report.md": b"# report\n"}
    assert (destination / "run" / "figures").is_dir()


def test_normalize_entry_name() -> None:
    assert eha.normalize_entry_name("derived\\keys.csv") == (("derived", "keys.csv"), False)
    assert eha.normalize_entry_name("a/b\\c.txt") == (("a", "b", "c.txt"), False)
    assert eha.normalize_entry_name("figures\\") == (("figures",), True)


def test_size_mismatch_is_refused_before_anything_is_written(tmp_path: Path, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"a", None)])
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination, size_delta=1) == 1
    assert "size mismatch" in capsys.readouterr().err
    assert not destination.exists()


def test_hash_mismatch_is_refused_before_anything_is_written(tmp_path: Path, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"a", None)])
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination, sha="0" * 64) == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err
    assert not destination.exists()


@pytest.mark.parametrize("prepare", ["empty_folder", "folder_with_data", "file"])
def test_existing_destination_is_refused_and_left_unchanged(tmp_path: Path, prepare: str, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"new", None)])
    destination = tmp_path / "out"
    if prepare == "file":
        destination.write_bytes(b"user data")
    else:
        destination.mkdir()
        if prepare == "folder_with_data":
            (destination / "a.txt").write_bytes(b"user data")
    snapshot = destination.read_bytes() if prepare == "file" else tree(destination)
    assert run_cli(tmp_path, archive, destination) == 1
    assert "already exists" in capsys.readouterr().err
    assert (destination.read_bytes() if prepare == "file" else tree(destination)) == snapshot


def test_missing_destination_parent_is_refused(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"a", None)])
    assert run_cli(tmp_path, archive, tmp_path / "missing" / "out") == 1
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("name", [
    "../evil.txt", "a/../../evil.txt", "..\\evil.txt", "a\\..\\..\\evil.txt", "/abs.txt", "\\abs.txt",
    "\\\\server\\share\\evil.txt", "//server/share/evil.txt", "C:\\evil.txt", "C:evil.txt", "c:/evil.txt",
    "a/./b.txt", "a//b.txt", "a\\\\b.txt", "a/b:stream.txt", "CON", "nul.txt", "a/com1.log", "trailing. ",
    "dir./x.txt", "",
])
def test_unsafe_entry_paths_are_refused_before_writing(tmp_path: Path, name: str, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [("ok.txt", b"ok", None), (name, b"payload", None)])
    destination = tmp_path / "work" / "out"
    destination.parent.mkdir()
    assert run_cli(tmp_path, archive, destination) == 1
    assert "refused" in capsys.readouterr().err
    assert not destination.exists()
    assert not list(tmp_path.rglob("evil.txt"))


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644, stat.S_IFCHR | 0o644])
def test_symlink_and_special_entries_are_refused(tmp_path: Path, mode: int, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [("link", b"../outside", mode)])
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 1
    assert "refused" in capsys.readouterr().err
    assert not destination.exists()


@pytest.mark.parametrize("names", [
    ["same.txt", "same.txt"],
    ["d\\f.txt", "d/f.txt"],
    ["Data.csv", "data.csv"],
    ["caf\u00e9.txt", "cafe\u0301.txt"],
])
def test_duplicate_normalized_destinations_are_refused(tmp_path: Path, names: list[str], capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", [(n, b"x", None) for n in names])
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 1
    assert "duplicate destination" in capsys.readouterr().err
    assert not destination.exists()


@pytest.mark.parametrize("entries", [
    [("a", b"file", None), ("a/b.txt", b"x", None)],
    [("a/b.txt", b"x", None), ("a", b"file", None)],
    [("a\\", None, None), ("a", b"file", None)],
    [("a", b"file", None), ("A\\", None, None)],
])
def test_file_and_directory_conflicts_are_refused(tmp_path: Path, entries, capsys) -> None:
    archive = make_zip(tmp_path / "release.zip", entries)
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 1
    message = capsys.readouterr().err
    assert "conflicts" in message or "needs" in message
    assert not destination.exists()


def test_crc_failure_is_found_before_writing(tmp_path: Path, capsys) -> None:
    payload = b"PAYLOAD-" * 64
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"fine", None), ("b.txt", payload, None)],
                       compression=zipfile.ZIP_STORED)
    raw = archive.read_bytes()
    archive.write_bytes(raw.replace(payload, payload[:-1] + b"!", 1))  # same size, corrupted content
    destination = tmp_path / "out"
    assert run_cli(tmp_path, archive, destination) == 1
    assert "CRC" in capsys.readouterr().err
    assert not destination.exists()


def test_unsupported_asset_id_is_a_usage_error(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "release.zip", [("a.txt", b"a", None)])
    with pytest.raises(SystemExit) as exit_info:
        eha.main(["--asset-id", "something_else", "--archive", str(archive), "--destination", str(tmp_path / "o")])
    assert exit_info.value.code == 2


def test_repository_catalogue_records_both_official_archives() -> None:
    dataset = eha.load_expected(eha.DEFAULT_CATALOGUE, "expanded_release_dataset")
    results = eha.load_expected(eha.DEFAULT_CATALOGUE, "rq1_v1_results")
    assert (dataset.file_name, dataset.size, dataset.sha256) == (
        "expanded_release.zip", 18966990, "f4bc90bc74e02579ece9cbbdafb4b9b792f655d90742de143d5a5f1d60e0291a")
    assert (results.file_name, results.size, results.sha256) == (
        "rq1_results_review_20261022_100326_043.zip", 13692737,
        "e8271dbe03a95eea51ac9d7bdd91b521ada3bb04caf5231f2da634ec849c7490")
