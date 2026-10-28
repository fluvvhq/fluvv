"""Fluvv lint: datacontract-cli's own lint() (unchanged) plus Fluvv-specific
validation, aggregated into one pass/fail gate. Governance files (Domain,
Glossary, ODPS DataProduct) are linted by the same entry point.

Lint judges one file on its own, so it runs anywhere, inside a repository
or not. Whether the file's references resolve (domains, glossary terms,
upstream contracts) is checked by `fluvv compile`. See docs/fluvv/SPEC.md
Section 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from datacontract.data_contract import DataContract
from datacontract.model.run import ResultEnum

from fluvv.contract import (
    FLUVV_CUSTOM_PROPERTIES,
    OPTIONAL_FLUVV_CUSTOM_PROPERTIES,
    MissingCustomPropertyError,
    get_custom_property,
    load_odcs_document,
    parse_extension,
)
from fluvv.schema import GOVERNANCE_KINDS, validate_custom_property, validate_governance_document
from fluvv.semantic import SemanticIssue, run_governance_file_checks, run_semantic_checks


@dataclass
class LintResult:
    contract_path: Path
    odcs_errors: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    semantic_issues: list[SemanticIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.odcs_errors or self.schema_errors or self.semantic_issues)

    def report(self) -> str:
        if self.passed:
            return f"{self.contract_path}: PASSED"

        lines = [f"{self.contract_path}: FAILED"]
        lines += [f"  [odcs] {msg}" for msg in self.odcs_errors]
        lines += [f"  [fluvv schema] {msg}" for msg in self.schema_errors]
        lines += [f"  [fluvv semantic:{i.code}] {i.message}" for i in self.semantic_issues]
        return "\n".join(lines)


def lint(contract_path: Path | str) -> LintResult:
    """Lint a contract, or a governance file (dispatched on its `kind`)."""
    contract_path = Path(contract_path)
    doc = load_odcs_document(contract_path) or {}
    if doc.get("kind") in GOVERNANCE_KINDS:
        return lint_governance_file(contract_path, doc)
    result = LintResult(contract_path=contract_path)

    # 1. ODCS-standard validation, fully delegated to datacontract-cli.
    odcs_run = DataContract(data_contract_file=str(contract_path)).lint()
    if not odcs_run.has_passed():
        result.odcs_errors = [
            f"{check.name}: {check.reason}" if check.reason else str(check.name)
            for check in (odcs_run.checks or [])
            if check.result in (ResultEnum.failed, ResultEnum.error)
        ]

    # 2. Fluvv customProperties: presence + JSON Schema shape.
    for property_name in FLUVV_CUSTOM_PROPERTIES:
        value = get_custom_property(doc, property_name)
        if value is None:
            result.schema_errors.append(
                f"missing required customProperties entry '{property_name}'"
            )
            continue
        result.schema_errors += [
            f"{property_name}: {error.message}"
            for error in validate_custom_property(property_name, value)
        ]
    # 2b. Optional Fluvv customProperties: shape-validated only if present --
    # never "missing", a contract that omits these is still complete.
    for property_name in OPTIONAL_FLUVV_CUSTOM_PROPERTIES:
        value = get_custom_property(doc, property_name)
        if value is not None:
            result.schema_errors += [
                f"{property_name}: {error.message}"
                for error in validate_custom_property(property_name, value)
            ]

    # 3. Semantic checks (only meaningful once the shape above is valid).
    if not result.schema_errors:
        try:
            extension = parse_extension(doc, contract_path)
        except MissingCustomPropertyError as exc:
            result.schema_errors.append(str(exc))
        else:
            result.semantic_issues = run_semantic_checks(extension, doc)

    return result


def lint_governance_file(path: Path, raw: dict) -> LintResult:
    result = LintResult(contract_path=path)
    result.schema_errors = [
        f"{'/'.join(map(str, error.absolute_path)) or '(root)'}: {error.message}"
        for error in validate_governance_document(raw)
    ]
    if not result.schema_errors:
        result.semantic_issues = run_governance_file_checks(raw)
    return result
