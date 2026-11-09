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

import yaml
from datacontract.data_contract import DataContract
from datacontract.model.run import ResultEnum

from fluvv.contract import MissingCustomPropertyError, load_odcs_document, parse_extension, yaml_problem
from fluvv.schema import SCHEMA_KINDS, custom_property_errors, odcs_errors, validate_governance_document
from fluvv.semantic import SemanticIssue, run_governance_file_checks, run_semantic_checks


@dataclass
class LintResult:
    """What lint found in one file.

    Attributes
    ----------
    contract_path : Path
        The file linted.
    odcs_errors : list of str
        Where the file breaks ODCS.
    schema_errors : list of str
        Where its Fluvv properties don't have the shape the spec gives
        them, or the file isn't valid YAML.
    semantic_issues : list
        Breaches of Fluvv's rules within the file, each with a `code` and a
        `message`. The codes are in
        [Lint and compile codes](https://fluvv.se/docs/lint-and-compile-codes).
    passed : bool
        Whether the file passed: nothing in any of the lists.

    See Also
    --------
    lint : Lints a file and returns this.
    """

    contract_path: Path
    odcs_errors: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    semantic_issues: list[SemanticIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether the file passed: nothing in any of the lists."""
        return not (self.odcs_errors or self.schema_errors or self.semantic_issues)

    def report(self) -> str:
        """The text `fluvv lint` prints for this file.

        Returns
        -------
        str
            `<file>: PASSED`, or `<file>: FAILED` followed by one line per
            problem, each tagged with where it comes from.

        Examples
        --------
        >>> from fluvv import lint
        >>> print(lint("contracts/silver/sales/cars.yaml").report())
        contracts/silver/sales/cars.yaml: PASSED
        """
        if self.passed:
            return f"{self.contract_path}: PASSED"

        lines = [f"{self.contract_path}: FAILED"]
        lines += [f"  [odcs] {msg}" for msg in self.odcs_errors]
        lines += [f"  [fluvv schema] {msg}" for msg in self.schema_errors]
        lines += [f"  [fluvv semantic:{i.code}] {i.message}" for i in self.semantic_issues]
        return "\n".join(lines)


def lint(contract_path: Path | str) -> LintResult:
    """Lints one contract, governance file or project file, like `fluvv lint <file>`.

    Judges the file on its own: against ODCS or its governance file schema,
    then against Fluvv's rules for a single file. Whether its references
    resolve, to domains, glossary terms or upstream contracts, is checked
    when the repository compiles. A file that isn't valid YAML fails
    rather than raising.

    Parameters
    ----------
    contract_path : Path or str
        The file. Its `kind` says what it is.

    Returns
    -------
    LintResult
        What lint found. `passed` says whether the file passed.

    See Also
    --------
    Contract.lint : The same check, on a loaded contract.
    Project : Compiles the repository, which checks references.

    Examples
    --------
    >>> from fluvv import lint
    >>> lint("contracts/silver/sales/cars.yaml").passed
    True
    >>> lint("governance/domains/sales.yaml").passed
    True
    """
    contract_path = Path(contract_path)
    try:
        doc = load_odcs_document(contract_path)
    except yaml.YAMLError as exc:
        return LintResult(contract_path=contract_path, schema_errors=[yaml_problem(exc)])
    if not isinstance(doc, dict):
        return LintResult(contract_path=contract_path, schema_errors=["the file is empty or isn't a YAML mapping"])
    if doc.get("kind") in SCHEMA_KINDS:
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
    result.schema_errors = custom_property_errors(doc)

    # 3. Semantic checks (only meaningful once the shape above is valid).
    # They read the ODCS structure (schema objects, columns, team), so they
    # also wait for that to be valid; step 1 has said what's wrong with it.
    if not result.schema_errors and not odcs_errors(doc):
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
