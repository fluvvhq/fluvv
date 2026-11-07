"""Loading a Fluvv contract file and extracting its `sources`/`transform`
customProperties into the typed structure in fluvv.models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from fluvv.models import FluvvExtension

FLUVV_CUSTOM_PROPERTIES = ("sources", "ingestion_config")
OPTIONAL_FLUVV_CUSTOM_PROPERTIES = ("transform", "businessMetrics")
"""Validated the same way as FLUVV_CUSTOM_PROPERTIES when present, but
never raise MissingCustomPropertyError when absent -- a contract that
doesn't set these is still a complete, valid Fluvv contract. (`transform`
may only be omitted with a single source; lint checks that.)"""
LAYER_PROPERTIES = ("medallionLayer", "layer")
"""A contract's optional layer label, the first one set. `medallionLayer` is
its name from before fluvv 1.3 and comes first: any customProperty was allowed
then, so a contract with it may also have a `layer` that means something else.
Not in the extension model: nothing about building a table needs it."""

PROJECT_FILE = "fluvv.yaml"
PROJECT_KIND = "Project"
_TOP_FOLDERS = ("contracts", "governance")


class MissingCustomPropertyError(ValueError):
    def __init__(self, property_name: str, contract_path: Path):
        self.property_name = property_name
        self.contract_path = contract_path
        super().__init__(
            f"{contract_path}: missing required customProperties entry '{property_name}'"
        )


class UnresolvableSourceError(ValueError):
    pass


class RepoRootNotFoundError(FileNotFoundError):
    """A file outside any Fluvv repository was given to something that
    needs the files around it: resolving upstream sources, or writing the
    generated files under `contracts/`. Lint never raises it."""


class InvalidProjectFileError(RepoRootNotFoundError, ValueError):
    """The repository's `fluvv.yaml` can't be read, so where its contracts
    and governance files live isn't known. Compile reports it as `fails-lint`.
    A RepoRootNotFoundError so what already handles that, the engine included,
    reports this too; a ValueError like the other ways a manifest is refused."""


@dataclass(frozen=True)
class Layout:
    """The repository's top-level folders. These defaults hold unless its
    project file (SPEC.md Section 1.1) names others."""

    contracts: str = "contracts"
    governance: str = "governance"


def load_odcs_document(contract_path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(contract_path).read_text())


def yaml_problem(exc: yaml.YAMLError) -> str:
    """A YAML syntax error in one line, for a report rather than a traceback."""
    mark, problem = getattr(exc, "problem_mark", None), getattr(exc, "problem", None)
    if mark is not None and problem:
        return f"invalid YAML at line {mark.line + 1}: {problem}"
    return f"invalid YAML: {str(exc).splitlines()[0]}"


def _holds_project_file(folder: Path) -> bool:
    """A table or domain can be called fluvv, so the name alone doesn't make
    a project file. One that can't be read counts, so it gets reported."""
    path = folder / PROJECT_FILE
    if not path.is_file():
        return False
    try:
        raw = load_odcs_document(path)
    except yaml.YAMLError:
        return True
    return not isinstance(raw, dict) or raw.get("kind") in (None, PROJECT_KIND)


def _inside_project_folders(path: Path, root: Path) -> bool:
    try:
        layout = project_layout(root)
    except InvalidProjectFileError:
        return True  # which folders it names is unknown, and this is where that gets reported
    return any(path.is_relative_to((root / folder).resolve()) for folder in (layout.contracts, layout.governance))


def find_repo_root(start: Path) -> Path:
    """The repository root for `start` (SPEC.md Section 1.1), recognised by
    the repository's own files, not by fluvv's: a contracts repo doesn't
    contain the package.

    A project file settles it for a file inside a folder it names. Otherwise
    a file where the example layout puts it names its root by position --
    `contracts/<layer>/<domain>/<table>.yaml` or `governance/<kind>/<file>`
    -- so a domain called `contracts` or `governance` can't be mistaken for
    the top-level folder, unless the level below the top folder has such a
    name too, which says the top folder is really the repository. Anything
    else resolves to the nearest directory holding a project file,
    `contracts/` or `governance/`."""
    path = start.resolve()
    ancestors = (path, *path.parents)
    marked = [folder for folder in ancestors if _holds_project_file(folder)]
    for folder in marked:
        if _inside_project_folders(path, folder):
            return folder
    for top, depth in (("contracts", 3), ("governance", 2)):
        if len(path.parents) > depth and path.parents[depth - 1].name == top and path.parents[depth - 2].name not in _TOP_FOLDERS:
            return path.parents[depth]
    for folder in ancestors:
        if folder in marked or any((folder / top).is_dir() for top in _TOP_FOLDERS):
            return folder
    raise RepoRootNotFoundError(
        f"{start} isn't inside a Fluvv repository: no folder above it holds a "
        f"{PROJECT_FILE} file or a contracts/ or governance/ directory (SPEC.md Section 1)"
    )


def project_layout(repo_root: Path | str) -> Layout:
    """Raises InvalidProjectFileError if the project file can't be read. Its
    fields' types are lint's to check (fluvv/schemas/project.schema.json)."""
    path = Path(repo_root) / PROJECT_FILE
    if not path.is_file():
        return Layout()
    try:
        raw = load_odcs_document(path)
    except yaml.YAMLError as exc:
        raise InvalidProjectFileError(f"{path}: {yaml_problem(exc)}") from None
    if not isinstance(raw, dict):
        raise InvalidProjectFileError(f"{path}: the file is empty or isn't a YAML mapping")
    return Layout(**{key: raw[key] for key in ("contracts", "governance") if isinstance(raw.get(key), str)})


def contract_files(repo_root: Path | str) -> list[Path]:
    root = Path(repo_root)
    return sorted((root / project_layout(root).contracts).glob("**/*.yaml"))


def governance_files(repo_root: Path | str) -> list[Path]:
    root = Path(repo_root)
    return sorted((root / project_layout(root).governance).glob("**/*.yaml"))


def get_custom_property(doc: dict[str, Any], property_name: str) -> Any | None:
    for entry in doc.get("customProperties") or []:
        if entry.get("property") == property_name:
            return entry.get("value")
    return None


def parse_extension(doc: dict[str, Any], contract_path: Path) -> FluvvExtension:
    """The extension exactly as written: sources that name a `contract`
    have no `location`/`format` yet. See resolve_extension()."""
    values: dict[str, Any] = {}
    for property_name in FLUVV_CUSTOM_PROPERTIES:
        value = get_custom_property(doc, property_name)
        if value is None:
            raise MissingCustomPropertyError(property_name, contract_path)
        values[property_name] = value
    for property_name in OPTIONAL_FLUVV_CUSTOM_PROPERTIES:
        value = get_custom_property(doc, property_name)
        if value is not None:
            values[property_name] = value
    return FluvvExtension.model_validate(values)


def resolve_extension(extension: FluvvExtension, repo_root: Path) -> FluvvExtension:
    """Fills each contract-backed source's `location`/`format` from that
    contract's own `servers[0]`, so where a table lives is stated once --
    in the contract that owns it. Pre-contract sources (`contract: null`)
    keep what they declare."""
    sources = []
    for source in extension.sources:
        if source.contract is None:
            if not (source.location and source.format):
                raise UnresolvableSourceError(f"source '{source.id}': `location` and `format` are required when `contract` is null")
            sources.append(source)
            continue
        upstream_path = Path(repo_root) / source.contract
        if not upstream_path.is_file():
            raise UnresolvableSourceError(
                f"source '{source.id}': contract '{source.contract}' does not exist under {repo_root}; "
                f"if that isn't the repository's root, a {PROJECT_FILE} there settles it"
            )
        server = load_odcs_document(upstream_path)["servers"][0]
        sources.append(source.model_copy(update={"location": server["path"], "format": server["format"]}))
    return extension.model_copy(update={"sources": sources})


def load_extension(contract_path: Path, resolve: bool = True) -> FluvvExtension:
    contract_path = Path(contract_path)
    doc = load_odcs_document(contract_path)
    extension = parse_extension(doc, contract_path)
    return resolve_extension(extension, find_repo_root(contract_path)) if resolve else extension
