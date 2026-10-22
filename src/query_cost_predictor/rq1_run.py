"""RQ1 run context and lifecycle (exclusive run directory, status, output manifest).

Write order of a run directory (``QCP_DATA_ROOT/results/rq1/<spec_id>/<run_id>/``):

1. the directory is created exclusively (an existing one is refused);
2. ``RUN_STATUS.json`` = ``RUNNING`` (atomic replace; mutable lifecycle metadata);
3. each stage writes immutable payloads through :meth:`RunContext.write_payload`
   (exclusive create, never rewritten); ``report.md`` is the last payload;
4. ``run_record.json`` (immutable, covered by the manifest);
5. the files on disk must equal the registered payloads (no strays, no temp files);
6. ``output_manifest.json`` (exclusive create): SHA-256 of every covered file;
7. every covered file is re-hashed and must match the manifest;
8. ``RUN_STATUS.json`` = ``COMPLETE`` (atomic replace), carrying the manifest's SHA-256.

Covered by the manifest: every file in the run directory except
``RUN_STATUS.json`` (mutable lifecycle metadata) and ``output_manifest.json``
(itself). On any failure the status becomes ``FAILED`` and a ``report.md`` or
``output_manifest.json`` already written is renamed with a ``.FAILED`` suffix,
so a failed directory never looks like a complete study. Readers must use
:func:`read_complete_run`, which requires ``COMPLETE`` and re-verifies every hash.

:func:`execute_run` records the planned stage sequence and the active stage.
Finalization is permitted only after every planned stage has returned and
been recorded as completed, in order; a stage that tries to finalize is
refused before anything is written, and the run then fails normally.

``COMPLETE`` and ``FAILED`` are terminal: :meth:`RunContext.write_payload`,
:func:`execute_run` and :func:`finalize_run` refuse a terminal run before
touching anything, so its status, files and manifest stay unchanged. Payload
paths must be plain relative forward-slash paths that stay inside the run
directory (:func:`validate_payload_path`; symlinks and junctions are resolved).
"""

from __future__ import annotations

import json
import os
import traceback
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from query_cost_predictor.hashing import sha256_bytes, sha256_file

STATUS_FILE = "RUN_STATUS.json"
MANIFEST_FILE = "output_manifest.json"
RUN_RECORD_FILE = "run_record.json"
REPORT_FILE = "report.md"
INPUT_CHECK_FILE = "input_check.json"
EXCLUDED_FROM_MANIFEST = (STATUS_FILE, MANIFEST_FILE)
RUNNING, FAILED, COMPLETE = "RUNNING", "FAILED", "COMPLETE"
FAILED_SUFFIX = ".FAILED"
THREADPOOL_LIMIT = 1


class RunLifecycleError(RuntimeError):
    """The run directory or its lifecycle violates the RQ1 run contract."""


class RunDirectoryExistsError(RunLifecycleError):
    """The run directory already exists; runs are never overwritten or reused."""


class RunRefusedError(RunLifecycleError):
    """The production pipeline refuses to start (missing stages or ineligible source)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_run_id(when: datetime, config_sha256: str, head: str | None) -> str:
    return f"{when.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_{config_sha256[:16]}_{(head or 'nohead')[:12]}"


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


WINDOWS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                                  *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
RESERVED_PAYLOAD_NAMES = frozenset(name.casefold() for name in EXCLUDED_FROM_MANIFEST)


def validate_payload_path(relative: Any) -> str:
    """Return ``relative`` if it is an unambiguous payload path, else raise.

    Accepted: non-empty forward-slash paths of plain components, for example
    ``figures/pr_curve.png``. Rejected on every platform: absolute POSIX paths,
    Windows drive-qualified (``C:\\x``, ``C:x``), rooted (``\\x``) and UNC
    (``\\\\server\\share``) forms, backslashes, ``:`` (drives and alternate data
    streams), empty, ``.`` and ``..`` components, hidden or temporary names,
    trailing dots or spaces, Windows device names, the reserved lifecycle files
    and ``.FAILED`` names.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(relative, str) or not relative:
        raise RunLifecycleError(f"payload path must be a non-empty string, got {relative!r}")
    problems = []
    if PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).anchor:
        problems.append("absolute, drive-qualified, rooted or UNC")
    if "\\" in relative or ":" in relative or "\0" in relative:
        problems.append("backslash, colon or NUL character")
    parts = relative.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            problems.append("empty, '.' or '..' component")
        elif part.startswith(".") or part.endswith((".", " ")) or part.endswith(".tmp") or part.endswith(FAILED_SUFFIX):
            problems.append(f"hidden, temporary, trailing-dot/space or .FAILED component {part!r}")
        elif part.split(".")[0].upper() in WINDOWS_DEVICE_NAMES:
            problems.append(f"Windows device name {part!r}")
    if relative.casefold() in RESERVED_PAYLOAD_NAMES:
        problems.append("reserved lifecycle file")
    if problems:
        raise RunLifecycleError(f"{relative!r} cannot be written as a payload: " + "; ".join(sorted(set(problems))))
    return relative


def _assert_contained(run_dir: Path, path: Path, name: str) -> None:
    root = run_dir.resolve(strict=True)
    if path.is_symlink() or not path.resolve(strict=False).is_relative_to(root):
        raise RunLifecycleError(f"payload {name!r} would leave the run directory through {path}")


def _contained_target(run_dir: Path, name: str) -> Path:
    """Check every existing component (symlinks and junctions resolve) before anything is created."""
    current = run_dir
    for part in name.split("/"):
        current = current / part
        if os.path.lexists(current):
            _assert_contained(run_dir, current, name)
    target = run_dir / name
    if not target.resolve(strict=False).is_relative_to(run_dir.resolve(strict=True)):
        raise RunLifecycleError(f"payload {name!r} resolves outside the run directory")
    return target


@dataclass
class RunContext:
    """State passed through every stage of one run."""

    run_id: str
    run_label: str
    run_dir: Path
    config: Any
    evidence_dir: Path | None = None
    archive: Path | None = None
    data_root: Path | None = None
    repo_root: Path | None = None
    source_state: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=utc_now)
    status_history: list[dict[str, Any]] = field(default_factory=list)
    completed_stages: list[str] = field(default_factory=list)
    payloads: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)  # in-memory results passed between stages

    state: str = RUNNING
    planned_stages: list[str] | None = None  # set by execute_run; finalization needs all of them completed, in order
    active_stage: str | None = None          # the stage currently executing (finalization is refused meanwhile)

    # ----------------------------------------------------------------- lifecycle guard
    def require_running(self, operation: str) -> None:
        """Refuse ``operation`` on a terminal run (in memory or on disk); changes nothing."""
        on_disk = None
        status_path = self.run_dir / STATUS_FILE
        if status_path.is_file():
            on_disk = json.loads(status_path.read_text(encoding="utf-8")).get("status")
        if self.state != RUNNING or on_disk not in (None, RUNNING):
            raise RunLifecycleError(f"cannot {operation}: run {self.run_id} is {on_disk or self.state}, "
                                    "and a terminal run (COMPLETE or FAILED) is never modified")

    # ----------------------------------------------------------------- payloads
    def write_payload(self, relative: str, data: bytes) -> Path:
        """Write one immutable result file (exclusive create) and register its hash.

        ``relative`` must be a plain forward-slash path inside the run directory
        (see :func:`validate_payload_path`). Containment is checked before any
        directory or byte is created, and again after the parent directories exist.
        """
        self.require_running("write a payload")
        name = validate_payload_path(relative)
        folded = {existing.casefold() for existing in self.payloads}
        if name.casefold() in folded:
            raise RunLifecycleError(f"{name} was already written; payloads are immutable")
        target = _contained_target(self.run_dir, name)
        current = self.run_dir
        for part in name.split("/")[:-1]:
            current = current / part
            current.mkdir(exist_ok=True)
            _assert_contained(self.run_dir, current, name)
        _assert_contained(self.run_dir, target.parent, name)
        try:
            with target.open("xb") as handle:
                handle.write(data)
        except FileExistsError as exc:
            raise RunLifecycleError(f"{name} already exists in the run directory; payloads are never overwritten") from exc
        self.payloads[name] = sha256_bytes(data)
        return target

    def write_json_payload(self, relative: str, document: Any) -> Path:
        text = json.dumps(document, indent=2, sort_keys=True, default=str) + "\n"
        return self.write_payload(relative, text.encode("utf-8"))

    # ----------------------------------------------------------------- status
    def _status(self, status: str, **extra: Any) -> None:
        if self.state != RUNNING:
            raise RunLifecycleError(f"run {self.run_id} is {self.state}; its status is final")
        entry = {"status": status, "at_utc": utc_now(), **{k: v for k, v in extra.items() if k == "stage"}}
        self.status_history.append(entry)
        document = {"run_id": self.run_id, "run_label": self.run_label, "status": status,
                    "planned_stages": None if self.planned_stages is None else list(self.planned_stages),
                    "completed_stages": list(self.completed_stages), "status_history": self.status_history,
                    "updated_at_utc": entry["at_utc"], **extra}
        _atomic_write_json(self.run_dir / STATUS_FILE, document)
        self.state = status


Stage = tuple[str, Callable[[RunContext], None]]


def create_run_context(base_dir: Path, run_id: str, run_label: str, config: Any, **kwargs: Any) -> RunContext:
    """Create the run directory exclusively and write ``RUNNING``."""
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / run_id
    try:
        run_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise RunDirectoryExistsError(f"run directory already exists (runs are never overwritten): {run_dir}") from exc
    context = RunContext(run_id=run_id, run_label=run_label, run_dir=run_dir, config=config, **kwargs)
    context._status(RUNNING)
    return context


def _disk_files(run_dir: Path) -> list[str]:
    return sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file())


def _quarantine(run_dir: Path) -> list[str]:
    moved = []
    for name in (REPORT_FILE, MANIFEST_FILE):
        path = run_dir / name
        if path.exists():
            os.replace(path, path.with_name(name + FAILED_SUFFIX))
            moved.append(name)
    return moved


def finalize_run(context: RunContext) -> dict[str, Any]:
    """Steps 4-8 of the write order: run record, manifest, verification, COMPLETE.

    Permitted only by the orchestrator after every planned stage has returned
    and been recorded as completed, in order. A call from inside an active
    stage (or with stages missing) is refused before anything is written.
    """
    context.require_running("finalize")
    if context.active_stage is not None:
        raise RunLifecycleError(f"cannot finalize from inside the active stage {context.active_stage!r}; "
                                "only the orchestrator finalizes after the last planned stage")
    if not context.planned_stages or list(context.completed_stages) != list(context.planned_stages):
        raise RunLifecycleError(f"cannot finalize: completed stages {context.completed_stages} do not equal the "
                                f"planned stage sequence {context.planned_stages}")
    record = {"run_id": context.run_id, "run_label": context.run_label, "created_at_utc": context.created_at_utc,
              "completed_stages": list(context.completed_stages),
              "status_history": list(context.status_history),  # up to finalization; RUN_STATUS.json holds the rest
              **context.provenance}
    context.write_json_payload(RUN_RECORD_FILE, record)
    on_disk = [f for f in _disk_files(context.run_dir) if f not in EXCLUDED_FROM_MANIFEST]
    registered = sorted(context.payloads)
    if on_disk != registered:
        raise RunLifecycleError(f"files on disk differ from registered payloads: unregistered "
                                f"{sorted(set(on_disk) - set(registered))}, missing {sorted(set(registered) - set(on_disk))}")
    manifest = {"run_id": context.run_id, "algorithm": "sha256", "excluded": list(EXCLUDED_FROM_MANIFEST),
                "files": {name: context.payloads[name] for name in registered}}
    manifest_path = context.run_dir / MANIFEST_FILE
    with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    for name, expected in manifest["files"].items():
        if sha256_file(context.run_dir / name) != expected:
            raise RunLifecycleError(f"{name} changed after it was written")
    manifest_sha = sha256_file(manifest_path)
    context._status(COMPLETE, output_manifest_sha256=manifest_sha)
    return manifest


def execute_run(context: RunContext, stages: Sequence[Stage]) -> RunContext:
    """Run every stage with one context; ``COMPLETE`` only after successful finalization.

    A run-scoped thread limit of 1 applies, and a scikit-learn
    ``ConvergenceWarning`` raised by any stage fails the run.
    """
    from sklearn.exceptions import ConvergenceWarning
    from threadpoolctl import threadpool_info, threadpool_limits

    context.require_running("execute stages")  # a terminal run is refused before anything is touched
    if context.active_stage is not None or context.planned_stages is not None:
        raise RunLifecycleError("execute_run may be called once per run, and never from inside a stage")
    names = [name for name, _ in stages]
    if not names:
        raise RunLifecycleError("a run needs at least one planned stage")
    context.planned_stages = names
    current = None
    try:
        with threadpool_limits(limits=THREADPOOL_LIMIT), warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            context.provenance.setdefault("threadpool", {
                "requested_limit": THREADPOOL_LIMIT,
                "effective": [{k: info.get(k) for k in ("user_api", "internal_api", "num_threads", "version")}
                              for info in threadpool_info()]})
            for name, function in stages:
                current = name
                context.active_stage = name
                function(context)
                context.active_stage = None
                context.completed_stages.append(name)
                context._status(RUNNING, stage=name)
            current = "finalization"
            finalize_run(context)
    except BaseException as exc:
        if context.state != RUNNING:
            raise  # already terminal (for example COMPLETE before a late interrupt): never rewritten
        moved = _quarantine(context.run_dir)
        context._status(FAILED, failed_stage=current,
                        last_completed_stage=context.completed_stages[-1] if context.completed_stages else None,
                        error={"type": exc.__class__.__name__, "message": str(exc)[:2000],
                               "traceback": traceback.format_exc()[-4000:]},
                        quarantined=moved)
        raise
    return context


def read_status(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / STATUS_FILE).read_text(encoding="utf-8"))


def read_complete_run(run_dir: Path) -> dict[str, Any]:
    """Return the verified output manifest of a COMPLETE run; refuse anything else."""
    status = read_status(run_dir)
    if status.get("status") != COMPLETE:
        raise RunLifecycleError(f"run {run_dir.name} is {status.get('status')}, not COMPLETE; its outputs are not a study")
    planned = status.get("planned_stages")
    if not planned or status.get("completed_stages") != planned:
        raise RunLifecycleError(f"run {run_dir.name} is marked COMPLETE but completed {status.get('completed_stages')} "
                                f"of the planned stages {planned}; its outputs are not a study")
    manifest_path = run_dir / MANIFEST_FILE
    if sha256_file(manifest_path) != status.get("output_manifest_sha256"):
        raise RunLifecycleError("output_manifest.json does not match the hash recorded at completion")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    on_disk = [f for f in _disk_files(run_dir) if f not in EXCLUDED_FROM_MANIFEST]
    if on_disk != sorted(manifest["files"]):
        raise RunLifecycleError("files in the run directory differ from the output manifest")
    for name, expected in manifest["files"].items():
        if sha256_file(run_dir / name) != expected:
            raise RunLifecycleError(f"{name} differs from the output manifest")
    return manifest


# ----------------------------------------------------------------------------------
# Production pipeline (Slice A: input_check; Slice B: regression; Slice C: the rest)
# ----------------------------------------------------------------------------------
def stage_input_check(context: RunContext) -> None:
    from query_cost_predictor.rq1_inputs import check_inputs

    protected = [context.repo_root / p.rstrip("/") for p in context.config.allowed_untracked_paths] if context.repo_root else []
    report = check_inputs(context.config, context.evidence_dir, repo_root=context.repo_root, archive=context.archive,
                          data_root=context.data_root, protected_paths=protected)
    context.artifacts["input_report"] = report
    context.artifacts["dataset"] = report.dataset
    context.write_json_payload(INPUT_CHECK_FILE, report.to_json())


def stage_regression(context: RunContext) -> None:
    from query_cost_predictor.rq1_evaluate import stage_regression as run_regression

    run_regression(context)


PRODUCTION_STAGES: dict[str, Callable[[RunContext], None] | None] = {
    "input_check": stage_input_check,
    "regression": stage_regression,
    "classification_1s": None,   # Slice C
    "diagnostics": None,         # Slice C
    "handoff": None,             # Slice C
}


def unimplemented_stages(stage_names: Sequence[str]) -> list[str]:
    return [name for name in stage_names if PRODUCTION_STAGES.get(name) is None]


def production_stages(stage_names: Sequence[str]) -> list[Stage]:
    """The declared stages, or :class:`RunRefusedError` if any is not implemented (never skipped)."""
    missing = unimplemented_stages(stage_names)
    if missing:
        raise RunRefusedError(f"the RQ1 pipeline is incomplete: stages not implemented yet: {missing}. "
                              "Nothing was fitted and no run directory was created.")
    return [(name, PRODUCTION_STAGES[name]) for name in stage_names]  # type: ignore[misc]
