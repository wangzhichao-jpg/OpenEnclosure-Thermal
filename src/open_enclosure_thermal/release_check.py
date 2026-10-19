"""Pre-publication guard: refuse private data, local paths and credentials in a release tree."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TEXT_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".csv", ".cfg", ".ini", ".html", ".cmd",
                 ".ps1", ".sh", ".gitignore", ".gitattributes"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist", "runs", "outputs",
             ".mypy_cache", ".ruff_cache", "node_modules"}
PRIVATE_DIRS = {"private_data", "customer_data", "企业资料", "内部资料"}

PATTERNS = {
    "local_windows_user_path": re.compile(r"[A-Za-z]:[\\/]+(Users|Documents and Settings)[\\/]+[^\\/\s\"']+", re.I),
    "local_unix_home_path": re.compile(r"/home/[a-z_][a-z0-9_-]{1,30}/"),
    "github_token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "password_assignment": re.compile(r"(password|passwd|secret|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]", re.I),
}


@dataclass
class Finding:
    path: str
    rule: str
    detail: str


def _check_project_mapping(data, rel: str, out: list[Finding]) -> None:
    if not isinstance(data, dict):
        return
    proj = data.get("project")
    if isinstance(proj, dict):
        if proj.get("source_type") == "enterprise_private":
            out.append(Finding(rel, "enterprise_private_data", "project.source_type = enterprise_private"))
        if proj.get("data_access") in ("internal", "confidential"):
            out.append(Finding(rel, "restricted_data_access", f"project.data_access = {proj.get('data_access')}"))


def check_tree(root: str | Path, denylist: list[str] | None = None) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    deny = [w for w in (denylist or []) if w.strip()]
    for p in sorted(root.rglob("*")):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in PRIVATE_DIRS for part in rel_parts):
            findings.append(Finding(rel, "private_directory", "file inside a private-data directory"))
            continue
        if not p.is_file():
            continue
        if "_confidential." in p.name or "_private." in p.name:
            findings.append(Finding(rel, "private_file_name", p.name))
        suffix = p.suffix.lower() if p.suffix else p.name
        if suffix == ".xlsx":
            try:
                from .io.excel import workbook_to_mapping

                _check_project_mapping(workbook_to_mapping(p), rel, findings)
            except Exception:  # noqa: BLE001 - non-template workbooks are fine
                pass
            continue
        if suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = p.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                continue
        for rule, rx in PATTERNS.items():
            m = rx.search(text)
            if m and not (rule == "password_assignment" and "example" in m.group(0).lower()):
                findings.append(Finding(rel, rule, m.group(0)[:80]))
        for w in deny:
            if w in text:
                findings.append(Finding(rel, "denylisted_term", f"contains a denylisted term (#{deny.index(w) + 1})"))
        if suffix in (".yaml", ".yml"):
            try:
                _check_project_mapping(yaml.safe_load(text), rel, findings)
            except yaml.YAMLError:
                pass
    return findings
