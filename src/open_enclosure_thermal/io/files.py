"""Project file loading/saving (YAML, JSON, Excel template) and file fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..schema import BoxModel, TestProject

ProjectModel = TestProject | BoxModel


class InputError(ValueError):
    """Raised for unreadable or invalid input, with a location-specific message."""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_validation_error(err: ValidationError, path: Path) -> str:
    lines = [f"{path}: invalid input"]
    for e in err.errors():
        loc = ".".join(str(x) for x in e["loc"]) or "<root>"
        lines.append(f"  - {loc}: {e['msg']}")
    return "\n".join(lines)


def read_mapping(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    elif suffix in (".xlsx", ".xlsm"):
        from .excel import workbook_to_mapping

        data = workbook_to_mapping(path)
    else:
        raise InputError(f"{path}: unsupported project file type '{suffix}' (use .yaml/.json/.xlsx)")
    if not isinstance(data, dict):
        raise InputError(f"{path}: top level must be a mapping")
    return data


def model_from_mapping(data: dict[str, Any], origin: Path | str = "<memory>") -> ProjectModel:
    kind = data.get("kind")
    if kind is None:
        kind = "box_ventilation" if "ventilation" in data else "test_analysis"
        data = {**data, "kind": kind}
    cls = {"test_analysis": TestProject, "box_ventilation": BoxModel}.get(kind)
    if cls is None:
        raise InputError(f"{origin}: unknown kind '{kind}' (test_analysis | box_ventilation)")
    try:
        return cls.model_validate(data)
    except ValidationError as err:
        raise InputError(_format_validation_error(err, Path(str(origin)))) from None


def load_project(path: str | Path) -> ProjectModel:
    path = Path(path)
    if not path.exists():
        raise InputError(f"{path}: file not found")
    return model_from_mapping(read_mapping(path), path)


def model_to_mapping(model: ProjectModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def save_project(model: ProjectModel, path: str | Path, *, data_frames: dict | None = None) -> Path:
    """Save as YAML/JSON, or as an Excel template workbook.

    ``data_frames`` (run id -> DataFrame) is only used for Excel test projects, to embed data sheets.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    data = model_to_mapping(model)
    if suffix in (".yaml", ".yml"):
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif suffix == ".json":
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif suffix == ".xlsx":
        from .excel import mapping_to_workbook

        mapping_to_workbook(data, path, data_frames=data_frames)
    else:
        raise InputError(f"{path}: unsupported output type '{suffix}'")
    return path
