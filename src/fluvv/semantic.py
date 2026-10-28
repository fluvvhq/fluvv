"""Checks that JSON Schema can't express, per docs/fluvv/SPEC.md Section 6,
in two groups.

On one file alone (`fluvv lint`, needs no repository):
- a source states `location`/`format` only when it has no contract to
  derive them from.
- each per-source transform references only its own source; the
  contract-level transform references only declared sources, and exists
  whenever there's more than one source.
- the contract's own fields are valid (classification, retention,
  freshness, the v3.1.0 team object), its id agrees with its
  `medallionLayer` and `domain`, its relationships start from its own
  columns, and it doesn't define shared things; a glossary's related terms
  are its own, a data product has an owner (fluvv.governance).

On the repository as a whole (`fluvv compile`):
- sources[].contract paths resolve to a contract file, each contract sits
  at the path its id names, and relationships point at existing, unique
  columns.
- references to shared entities resolve: domains, glossary terms, and
  the contracts a data product's output ports name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from pydantic import ValidationError

from fluvv import governance
from fluvv.contract import MissingCustomPropertyError, parse_extension
from fluvv.models import FluvvExtension


@dataclass(frozen=True)
class SemanticIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ProjectIssue:
    path: str
    """Repo-relative path of the file the issue is in."""
    code: str
    message: str


class ProjectCheckError(ValueError):
    """The repository's files don't fit together; raised by
    `fluvv.manifest.compile_manifest` instead of compiling them."""

    def __init__(self, issues: list[ProjectIssue]):
        self.issues = issues
        super().__init__(self.report())

    def report(self) -> str:
        lines = [f"{len(self.issues)} problem(s) between files; nothing compiled:"]
        for issue in self.issues:
            message = issue.message.replace("\n", "\n    ")
            lines.append(f"  {issue.path}: [{issue.code}] {message}")
        return "\n".join(lines)


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


def check_contract_fields(doc: dict[str, Any]) -> list[SemanticIssue]:
    """The contract's governance fields, judged on the contract alone."""
    return [
        SemanticIssue(issue.code, issue.message)
        for issue in (
            *governance.contract_issues(doc),
            *governance.id_issues(doc),
            *governance.relationship_shape_issues(doc),
        )
    ]


def run_semantic_checks(extension: FluvvExtension, doc: dict[str, Any] | None = None) -> list[SemanticIssue]:
    """Everything lint checks on a contract beyond its schema. Needs no
    repository: what the contract refers to is checked by
    `run_project_checks`."""
    return [
        *check_source_locations(extension),
        *check_transforms(extension),
        *(check_contract_fields(doc) if doc is not None else []),
    ]


def run_governance_file_checks(raw: dict[str, Any]) -> list[SemanticIssue]:
    """What lint checks on one (schema-valid) governance file beyond its
    schema, on the file alone."""
    kind = raw.get("kind")
    if kind == governance.GLOSSARY_KIND:
        issues = governance.glossary_issues(governance.parse_glossary(raw))
    elif kind == governance.DATA_PRODUCT_KIND:
        issues = governance.data_product_issues(governance.parse_data_product(raw))
    else:
        issues = []
    return [SemanticIssue(issue.code, issue.message) for issue in issues]


def run_project_checks(repo_root: Path | str) -> list[ProjectIssue]:
    """Every reference between the repository's files. Assumes each file
    passes lint on its own: a contract whose extension doesn't parse is
    skipped for the checks that need it."""
    repo_root = Path(repo_root).resolve()
    contracts_dir = repo_root / "contracts"
    gov = governance.load_governance(repo_root)
    docs_by_path = governance.load_contracts(repo_root)
    contract_ids = {doc.get("id") for doc in docs_by_path.values()}

    issues: list[ProjectIssue] = []
    for path, doc in docs_by_path.items():
        found: list[SemanticIssue] = []
        try:
            found += check_source_contracts_exist(parse_extension(doc, path), repo_root)
        except (MissingCustomPropertyError, ValidationError):
            pass
        found += [
            SemanticIssue(issue.code, issue.message)
            for issue in (
                *governance.contract_path_issues(doc, path.relative_to(contracts_dir).parts),
                *governance.relationship_target_issues(doc, repo_root),
                *governance.contract_reference_issues(doc, gov),
            )
        ]
        issues += [ProjectIssue(path.relative_to(repo_root).as_posix(), i.code, i.message) for i in found]

    for path, raw in governance.load_governance_documents(repo_root).items():
        kind = raw.get("kind")
        if kind == governance.GLOSSARY_KIND:
            found_gov = governance.glossary_reference_issues(governance.parse_glossary(raw), gov)
        elif kind == governance.DATA_PRODUCT_KIND:
            found_gov = governance.data_product_reference_issues(governance.parse_data_product(raw), gov, contract_ids)
        else:
            found_gov = []
        issues += [ProjectIssue(path.relative_to(repo_root).as_posix(), i.code, i.message) for i in found_gov]
    return issues
