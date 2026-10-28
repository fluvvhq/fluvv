"""Loading a Fluvv contract file and extracting its `sources`/`transform`
customProperties into the typed structure in fluvv.models."""

from __future__ import annotations

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


def load_odcs_document(contract_path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(contract_path).read_text())


def find_repo_root(start: Path) -> Path:
    """The repository root for `start` (SPEC.md Section 1), recognised by
    the layout, not by fluvv's own files: a contracts repo doesn't
    contain the package.

    A file where the layout puts it names its root by position --
    `contracts/<layer>/<domain>/<table>.yaml` or `governance/<kind>/<file>`
    -- so a domain called `contracts` or `governance` can't be mistaken for
    the top-level folder. Anything else resolves to the nearest directory
    holding `contracts/` or `governance/`."""
    path = start.resolve()
    for top, depth in (("contracts", 3), ("governance", 2)):
        if len(path.parents) > depth and path.parents[depth - 1].name == top:
            return path.parents[depth]
    for candidate in (path, *path.parents):
        if (candidate / "contracts").is_dir() or (candidate / "governance").is_dir():
            return candidate
    raise RepoRootNotFoundError(
        f"{start} isn't inside a Fluvv repository: no folder above it holds a "
        "contracts/ or governance/ directory (SPEC.md Section 1)"
    )


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
            raise UnresolvableSourceError(f"source '{source.id}': contract '{source.contract}' does not exist")
        server = load_odcs_document(upstream_path)["servers"][0]
        sources.append(source.model_copy(update={"location": server["path"], "format": server["format"]}))
    return extension.model_copy(update={"sources": sources})


def load_extension(contract_path: Path, resolve: bool = True) -> FluvvExtension:
    contract_path = Path(contract_path)
    doc = load_odcs_document(contract_path)
    extension = parse_extension(doc, contract_path)
    return resolve_extension(extension, find_repo_root(contract_path)) if resolve else extension
