"""Feature-availability contract.

Every field used anywhere in this project is registered in
``config/feature_registry.yaml`` with an availability class and a role:

* ``A`` SQL structure (text and supplied parameters)
* ``B`` schema and database statistics available before execution
* ``C`` environment and configuration
* ``D`` plain pre-execution ``EXPLAIN`` estimates
* ``E`` execution-only outcome — never a model input

Only fields with role ``feature`` and a single class in A–D may enter a model
feature matrix. Identity and grouping fields (``template_id``,
``semantic_group_id``, ``query_instance_id``, ``modeling_key`` …) have role
``identifier`` or ``group_key`` and are rejected even though they are known
before execution. Unregistered columns are rejected (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

AVAILABILITY_CLASSES: dict[str, str] = {
    "A": "SQL structure (text and supplied parameters)",
    "B": "Schema and database statistics available before execution",
    "C": "Environment and configuration",
    "D": "Plain pre-execution EXPLAIN estimates",
    "E": "Execution-only outcome (labels, diagnostics, run metadata)",
}
PREDICTIVE_CLASSES = frozenset({"A", "B", "C", "D"})
ROLES = frozenset(
    {
        "feature",        # may enter a model feature matrix (class A-D only)
        "identifier",     # identity; never a predictor
        "group_key",      # grouping/splitting/reporting only; never a predictor
        "split_control",  # holdout flags; never a predictor
        "provenance",     # hashes, timestamps, raw text/JSON kept for audit
        "annotation",     # human annotations (e.g. expected mechanism); never a predictor
        "label",          # prediction targets
        "diagnostic",     # execution-only diagnostics
        "feature_store",  # storage columns of feature_record (class per row)
    }
)


class ContractError(ValueError):
    """The registry itself is malformed."""


class LeakageError(ValueError):
    """A column that must not be a model input was offered as one."""


class UnregisteredFieldError(LeakageError):
    """A column is not in the registry (the contract fails closed)."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    classes: tuple[str, ...]
    role: str
    description: str

    @property
    def is_model_input(self) -> bool:
        return self.role == "feature" and len(self.classes) == 1 and self.classes[0] in PREDICTIVE_CLASSES


@dataclass(frozen=True)
class FeatureRegistry:
    version: int
    fields: dict[str, FieldSpec]

    def get(self, name: str) -> FieldSpec | None:
        return self.fields.get(name)

    def feature_names(self) -> list[str]:
        return sorted(name for name, spec in self.fields.items() if spec.is_model_input)

    def table_columns(self, table: str) -> set[str]:
        prefix = table + "."
        return {name[len(prefix):] for name in self.fields if name.startswith(prefix)}

    def tables(self) -> set[str]:
        return {name.split(".", 1)[0] for name in self.fields if "." in name}


def default_registry_path() -> Path:
    from query_cost_predictor.paths import config_dir

    return config_dir() / "feature_registry.yaml"


def _parse_classes(raw: object, field_name: str) -> tuple[str, ...]:
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not values:
        raise ContractError(f"{field_name}: 'class' must be a letter or a non-empty list of letters")
    classes = tuple(str(v).strip().upper() for v in values)
    for cls in classes:
        if cls not in AVAILABILITY_CLASSES:
            raise ContractError(f"{field_name}: unknown availability class {cls!r}")
    return classes


def parse_registry(document: dict) -> FeatureRegistry:
    """Validate a parsed registry document and build a :class:`FeatureRegistry`."""
    if not isinstance(document, dict) or "fields" not in document:
        raise ContractError("feature registry must be a mapping with a 'fields' list")
    version = document.get("version")
    if not isinstance(version, int):
        raise ContractError("feature registry 'version' must be an integer")
    fields: dict[str, FieldSpec] = {}
    for entry in document["fields"]:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ContractError(f"registry entry without a name: {entry!r}")
        name = str(entry["name"])
        if name in fields:
            raise ContractError(f"duplicate registry entry {name!r}")
        classes = _parse_classes(entry.get("class"), name)
        role = str(entry.get("role", ""))
        if role not in ROLES:
            raise ContractError(f"{name}: unknown role {role!r}")
        if role == "feature":
            if len(classes) != 1 or classes[0] not in PREDICTIVE_CLASSES:
                raise ContractError(f"{name}: role 'feature' requires exactly one class in A-D")
        if "E" in classes and role in ("feature", "feature_store"):
            raise ContractError(f"{name}: class E can never be a feature")
        description = str(entry.get("description", "")).strip()
        if not description:
            raise ContractError(f"{name}: description is required")
        fields[name] = FieldSpec(name=name, classes=classes, role=role, description=description)
    return FeatureRegistry(version=version, fields=fields)


def load_registry(path: Path | None = None) -> FeatureRegistry:
    target = path or default_registry_path()
    with target.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return parse_registry(document)


@lru_cache(maxsize=4)
def _cached_registry(path_text: str) -> FeatureRegistry:
    return load_registry(Path(path_text))


def default_registry() -> FeatureRegistry:
    return _cached_registry(str(default_registry_path()))


def leakage_problems(columns: Iterable[str], registry: FeatureRegistry | None = None) -> list[str]:
    """Return human-readable problems for ``columns`` (empty list means allowed)."""
    reg = registry or default_registry()
    problems: list[str] = []
    for column in columns:
        spec = reg.get(column)
        if spec is None:
            problems.append(f"{column}: not registered in the feature registry (fail closed)")
            continue
        if "E" in spec.classes:
            problems.append(f"{column}: availability class E (execution-only) can never be a model input")
        elif spec.role != "feature":
            problems.append(f"{column}: role '{spec.role}' is for grouping/audit only, not a model input")
        elif not spec.is_model_input:
            problems.append(f"{column}: not a single-class A-D feature")
    return problems


def assert_feature_matrix_allowed(columns: Iterable[str], registry: FeatureRegistry | None = None) -> None:
    """Raise :class:`LeakageError` unless every column is a registered A–D feature."""
    column_list = list(columns)
    if not column_list:
        raise LeakageError("feature matrix has no columns")
    problems = leakage_problems(column_list, registry)
    if problems:
        unregistered = [p for p in problems if "not registered" in p]
        message = "Feature matrix rejected by the availability contract:\n  " + "\n  ".join(problems)
        if unregistered and len(unregistered) == len(problems):
            raise UnregisteredFieldError(message)
        raise LeakageError(message)
