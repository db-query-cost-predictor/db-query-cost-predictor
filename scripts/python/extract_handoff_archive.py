"""Verify and extract one of the two handoff archives on any operating system.

The official ZIP files store entry names with Windows backslash separators. Some
non-Windows unzip tools turn those into file names that contain backslashes.
This helper keeps the archives byte-for-byte unchanged and extracts them
portably:

1. read the expected size and SHA-256 for ``--asset-id`` from the asset
   catalogue (``docs/handoff_assets_v1.json`` by default);
2. verify the archive's size and SHA-256 before reading any entry;
3. inspect every entry path before writing anything: backslashes become
   directory separators; traversal, absolute, drive and UNC paths, symbolic
   links and other special entries, encrypted entries, duplicate normalized
   destinations and file/directory conflicts are refused;
4. create ``--destination``, which must not exist yet, and write each file
   exactly as stored (the ZIP CRC is checked on every read).

It never overwrites, deletes or moves existing data and opens the archive
read-only. It uses the Python standard library only and is independent of the
modeling pipeline.

Usage::

    python scripts/python/extract_handoff_archive.py --asset-id expanded_release_dataset \
        --archive <path to expanded_release.zip> --destination <new folder>

Exit codes: 0 extracted and verified; 1 refused or failed (the message says
why); 2 invalid command-line arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SUPPORTED_ASSET_IDS = ("expanded_release_dataset", "rq1_v1_results")
DEFAULT_CATALOGUE = Path(__file__).resolve().parents[2] / "docs" / "handoff_assets_v1.json"
CHUNK = 1024 * 1024
_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class ExtractError(Exception):
    """The archive or the destination failed a check; nothing unsafe was written."""


@dataclass(frozen=True)
class ExpectedAsset:
    asset_id: str
    file_name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PlannedEntry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


def load_expected(catalogue: Path, asset_id: str) -> ExpectedAsset:
    """The catalogue's recorded file name, size and SHA-256 for ``asset_id``."""
    if asset_id not in SUPPORTED_ASSET_IDS:
        raise ExtractError(f"unsupported asset id {asset_id!r}; expected one of {', '.join(SUPPORTED_ASSET_IDS)}")
    try:
        document = json.loads(Path(catalogue).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExtractError(f"cannot read the asset catalogue {catalogue}: {exc}") from exc
    matches = [a for a in document.get("assets", []) if a.get("asset_id") == asset_id]
    if len(matches) != 1:
        raise ExtractError(f"the catalogue {catalogue} has {len(matches)} entries for {asset_id!r}, expected 1")
    asset = matches[0]
    size, sha = asset.get("bytes"), str(asset.get("sha256", "")).lower()
    if not isinstance(size, int) or size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ExtractError(f"the catalogue entry for {asset_id!r} has no valid size and SHA-256")
    return ExpectedAsset(asset_id, str(asset.get("file_name", "")), size, sha)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(archive: Path, expected: ExpectedAsset) -> None:
    """Refuse an archive whose size or SHA-256 differs from the catalogue."""
    if not archive.is_file():
        raise ExtractError(f"archive not found: {archive}")
    size = archive.stat().st_size
    if size != expected.size:
        raise ExtractError(f"size mismatch for {archive.name}: {size} bytes, catalogue says {expected.size}; "
                           "do not use this copy")
    observed = sha256_of(archive)
    if observed != expected.sha256:
        raise ExtractError(f"SHA-256 mismatch for {archive.name}: {observed}, catalogue says {expected.sha256}; "
                           "do not use this copy")


def normalize_entry_name(name: str) -> tuple[tuple[str, ...], bool]:
    """Safe relative path components of a ZIP entry name, and whether it is a directory entry.

    Backslashes and forward slashes are both treated as separators.
    """
    if "\x00" in name:
        raise ExtractError(f"entry name contains a NUL character: {name!r}")
    text = name.replace("\\", "/")
    if text.startswith("/"):
        raise ExtractError(f"absolute or UNC entry path refused: {name!r}")
    if _DRIVE.match(text):
        raise ExtractError(f"drive-qualified entry path refused: {name!r}")
    is_dir = text.endswith("/")
    body = text[:-1] if is_dir else text
    parts = body.split("/") if body else []
    if not parts:
        raise ExtractError(f"empty entry path refused: {name!r}")
    for part in parts:
        if part in ("", ".", ".."):
            raise ExtractError(f"entry path with an empty, '.' or '..' component refused: {name!r}")
        if ":" in part:
            raise ExtractError(f"entry path component with ':' refused: {name!r}")
        if part != part.strip() or part.endswith("."):
            raise ExtractError(f"entry path component with a trailing space or dot refused: {name!r}")
        if part.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise ExtractError(f"entry path component with a reserved Windows device name refused: {name!r}")
    return tuple(parts), is_dir


def _collision_key(parts: Sequence[str]) -> tuple[str, ...]:
    # Case-insensitive and Unicode-normalized, so names that would collide on Windows or macOS are refused.
    return tuple(unicodedata.normalize("NFC", p).casefold() for p in parts)


def plan_entries(archive: zipfile.ZipFile) -> list[PlannedEntry]:
    """Check every entry before anything is written; return the safe extraction plan."""
    planned: list[PlannedEntry] = []
    files: dict[tuple[str, ...], str] = {}
    dirs: dict[tuple[str, ...], str] = {}
    for info in archive.infolist():
        parts, is_dir = normalize_entry_name(info.filename)
        if info.flag_bits & 0x1:
            raise ExtractError(f"encrypted entry refused: {info.filename!r}")
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFLNK:
            raise ExtractError(f"symbolic-link entry refused: {info.filename!r}")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ExtractError(f"special-file entry refused: {info.filename!r}")
        if kind == stat.S_IFDIR:
            is_dir = True
        key = _collision_key(parts)
        if is_dir:
            if key in files:
                raise ExtractError(f"directory entry {info.filename!r} conflicts with file {files[key]!r}")
            dirs.setdefault(key, info.filename)
        else:
            if key in files:
                raise ExtractError(f"duplicate destination: {info.filename!r} and {files[key]!r}")
            if key in dirs:
                raise ExtractError(f"file entry {info.filename!r} conflicts with directory {dirs[key]!r}")
            files[key] = info.filename
        for depth in range(1, len(parts)):
            parent = key[:depth]
            if parent in files:
                raise ExtractError(f"entry {info.filename!r} needs {files[parent]!r} to be a directory")
            dirs.setdefault(parent, "/".join(parts[:depth]))
        planned.append(PlannedEntry(info, parts, is_dir))
    for key, name in files.items():
        if key in dirs:
            raise ExtractError(f"file entry {name!r} conflicts with directory {dirs[key]!r}")
    return planned


def _write_entry(archive: zipfile.ZipFile, entry: PlannedEntry, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with archive.open(entry.info) as source, open(target, "xb") as sink:
        for block in iter(lambda: source.read(CHUNK), b""):
            sink.write(block)
            written += len(block)
    if written != entry.info.file_size:
        raise ExtractError(f"{entry.info.filename!r}: wrote {written} bytes, the archive records {entry.info.file_size}")


def extract(archive: Path, destination: Path, expected: ExpectedAsset) -> dict[str, int]:
    """Verify ``archive`` against ``expected`` and extract it into the new folder ``destination``."""
    archive, destination = Path(archive), Path(destination)
    if os.path.lexists(destination):
        raise ExtractError(f"destination already exists: {destination}; choose a new folder (nothing was changed)")
    if not destination.parent.is_dir():
        raise ExtractError(f"the destination's parent folder does not exist: {destination.parent}")
    verify_archive(archive, expected)
    try:
        handle = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractError(f"cannot open {archive} as a ZIP archive: {exc}") from exc
    with handle:
        plan = plan_entries(handle)
        try:
            bad = handle.testzip()  # reads every entry and checks its CRC before anything is written
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
            raise ExtractError(f"cannot read the archive entries: {exc}") from exc
        if bad is not None:
            raise ExtractError(f"CRC check failed for entry {bad!r}")
        os.mkdir(destination)  # fails if it appeared meanwhile; never reuses an existing folder
        root = destination.resolve()
        n_files = n_dirs = 0
        try:
            for entry in plan:
                target = root.joinpath(*entry.parts)
                if root not in target.resolve().parents:
                    raise ExtractError(f"entry {entry.info.filename!r} resolves outside the destination")
                if entry.is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    n_dirs += 1
                else:
                    _write_entry(handle, entry, target)
                    n_files += 1
        except (ExtractError, OSError, zipfile.BadZipFile) as exc:
            raise ExtractError(f"extraction stopped: {exc}. The new folder {destination} is incomplete; "
                               "delete it yourself before retrying (nothing outside it was changed)") from exc
    if sha256_of(archive) != expected.sha256:
        raise ExtractError(f"the archive changed during extraction: {archive}")
    return {"files": n_files, "directory_entries": n_dirs, "entries": len(plan)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a handoff archive against the asset catalogue and extract it into a new folder.")
    parser.add_argument("--asset-id", required=True, choices=SUPPORTED_ASSET_IDS,
                        help="catalogue id of the archive")
    parser.add_argument("--archive", required=True, help="path to the downloaded ZIP file (read only)")
    parser.add_argument("--destination", required=True,
                        help="new folder to create; it must not exist, and its parent must exist")
    parser.add_argument("--catalogue", default=str(DEFAULT_CATALOGUE),
                        help="asset catalogue JSON (default: docs/handoff_assets_v1.json in this repository)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        expected = load_expected(Path(args.catalogue), args.asset_id)
        archive = Path(args.archive)
        if expected.file_name and archive.name != expected.file_name:
            print(f"note: the archive is named {archive.name!r}; the catalogue name is {expected.file_name!r} "
                  "(identity is checked by size and SHA-256)")
        counts = extract(archive, Path(args.destination), expected)
    except ExtractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("EXTRACT-HANDOFF-ARCHIVE: FAIL")
        return 1
    print(f"verified: {expected.asset_id} {expected.size} bytes, SHA-256 {expected.sha256}")
    print(f"extracted {counts['files']} files ({counts['entries']} entries) into {Path(args.destination).resolve()}")
    print("EXTRACT-HANDOFF-ARCHIVE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
