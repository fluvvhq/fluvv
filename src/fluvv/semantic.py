"""Checks that JSON Schema can't express, per docs/fluvv/SPEC.md Section 6:
- sources[].contract paths resolve to a file on disk, and a source states
  `location`/`format` only when it has no contract to derive them from.
- each per-source transform references only its own source; the
  contract-level transform references only declared sources, and exists
  whenever there's more than one source.
- governance: the contract's own fields are valid (classification,
  retention, freshness, the v3.1.0 team object), its references to shared
  entities (domain, glossary terms) resolve to the governance files, its
  id and path agree with its `medallionLayer` and `domain`, and its
  relationships point at existing, unique columns (fluvv.governance).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from fluvv import governance
from fluvv.models import FluvvExtension


@dataclass(frozen=True)
class SemanticIssue:
    code: str
    message: str


def check_source_contracts_exist(
    extension: FluvvExtension, repo_root: Path
) -> list[SemanticIssue]:
    issues = []
    for source in extension.sources:
        if source.contract is None:
            continue
        if not (repo_root / source.contract).is_file():
            issues.append(
                SemanticIssue(
                    "source-contract-not-found",
                    f"source '{source.id}': contract path '{source.contract}' does not exist",
                )
            )
    return issues


def check_source_locations(extension: FluvvExtension) -> list[SemanticIssue]:
    """Where a table lives is stated once: by its own contract's `servers`."""
    issues = []
    for source in extension.sources:
        stated = [f for f in ("location", "format") if getattr(source, f) is not None]
        if source.contract is not None and stated:
            issues.append(
                SemanticIssue(
                    "source-location-redundant",
                    f"source '{source.id}': remove {' and '.join(stated)} -- they're derived from "
                    f"'{source.contract}' servers",
                )
            )
        missing = [f for f in ("location", "format") if getattr(source, f) is None]
        if source.contract is None and missing:
            issues.append(
                SemanticIssue(
                    "source-location-missing",
                    f"source '{source.id}': {' and '.join(missing)} required when contract is null",
                )
            )
    return issues


def _referenced_tables(sql: str, label: str) -> tuple[set[str], list[SemanticIssue]]:
    try:
        parsed = sqlglot.parse_one(sql, dialect="spark")
    except sqlglot.errors.ParseError as exc:
        return set(), [SemanticIssue("transform-sql-unparseable", f"{label} could not be parsed as pyspark-sql: {exc}")]
    cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE)}
    return {table.name for table in parsed.find_all(exp.Table)} - cte_names, []


def check_transforms(extension: FluvvExtension) -> list[SemanticIssue]:
    issues: list[SemanticIssue] = []
    for source in extension.sources:
        if source.transform is None:
            continue
        referenced, parse_issues = _referenced_tables(source.transform.sql, f"source '{source.id}' transform.sql")
        issues += parse_issues
        issues += [
            SemanticIssue(
                "source-transform-foreign-reference",
                f"source '{source.id}' transform.sql references '{name}'; a per-source transform may only read "
                f"'{source.id}' -- combine sources in the contract-level transform",
            )
            for name in sorted(referenced - {source.id})
        ]

    if extension.transform is None:
        if len(extension.sources) > 1:
            issues.append(
                SemanticIssue(
                    "missing-combine-transform",
                    f"{len(extension.sources)} sources but no contract-level transform; add one that joins or unions them",
                )
            )
        return issues

    referenced, parse_issues = _referenced_tables(extension.transform.sql, "transform.sql")
    declared_ids = {source.id for source in extension.sources}
    issues += parse_issues
    issues += [
        SemanticIssue(
            "unknown-source-reference",
            f"transform.sql references '{name}', which is not declared in sources",
        )
        for name in sorted(referenced - declared_ids)
    ]
    return issues


def _path_below_contracts(contract_path: Path | None, repo_root: Path) -> tuple[str, ...] | None:
    if contract_path is None:
        return None
    contracts_dir = (repo_root / "contracts").resolve()
    resolved = contract_path.resolve()
    return resolved.relative_to(contracts_dir).parts if resolved.is_relative_to(contracts_dir) else None


def check_governance(doc: dict[str, Any], repo_root: Path, contract_path: Path | None = None) -> list[SemanticIssue]:
    gov = governance.load_governance(repo_root)
    return [
        SemanticIssue(issue.code, issue.message)
        for issue in (
            *governance.contract_issues(doc),
            *governance.naming_issues(doc, _path_below_contracts(contract_path, repo_root)),
            *governance.relationship_issues(doc, repo_root),
            *governance.contract_reference_issues(doc, gov),
        )
    ]


def run_semantic_checks(
    extension: FluvvExtension, repo_root: Path, doc: dict[str, Any] | None = None, contract_path: Path | None = None
) -> list[SemanticIssue]:
    return [
        *check_source_contracts_exist(extension, repo_root),
        *check_source_locations(extension),
        *check_transforms(extension),
        *(check_governance(doc, repo_root, contract_path) if doc is not None else []),
    ]


def run_governance_file_checks(raw: dict[str, Any], repo_root: Path) -> list[SemanticIssue]:
    """Reference checks for one (schema-valid) governance file."""
    gov = governance.load_governance(repo_root)
    kind = raw.get("kind")
    if kind == governance.GLOSSARY_KIND:
        issues = governance.glossary_issues(governance.parse_glossary(raw), gov)
    elif kind == governance.DATA_PRODUCT_KIND:
        contract_ids = {d["id"] for d in governance.load_contracts(repo_root).values()}
        issues = governance.data_product_issues(governance.parse_data_product(raw), gov, contract_ids)
    else:
        issues = []
    return [SemanticIssue(issue.code, issue.message) for issue in issues]
