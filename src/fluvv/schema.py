"""JSON Schema validation: Fluvv customProperties against
fluvv/schemas/fluvv.schema.json, and governance files and the project file
against their own schema (domain, glossary, classifications, project, or
the vendored ODPS v1.1.0 schema)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from fluvv.contract import (
    FLUVV_CUSTOM_PROPERTIES,
    LAYER_PROPERTY,
    OPTIONAL_FLUVV_CUSTOM_PROPERTIES,
    PROJECT_KIND,
    RENAMED_LAYER_PROBLEM,
    RENAMED_LAYER_PROPERTY,
    get_custom_property,
)

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMA_PATH = SCHEMAS_DIR / "fluvv.schema.json"

_DEFINITION_BY_PROPERTY = {
    "sources": "Sources",
    "transform": "Transform",
    "ingestion_config": "IngestionConfig",
    "businessMetrics": "BusinessMetrics",
    LAYER_PROPERTY: "Layer",
}
_SCHEMA_FILE_BY_KIND = {
    "Domain": "domain.schema.json",
    "Glossary": "glossary.schema.json",
    "Classifications": "classifications.schema.json",
    "DataProduct": "odps-1.1.0.schema.json",
    PROJECT_KIND: "project.schema.json",
}
SCHEMA_KINDS = tuple(_SCHEMA_FILE_BY_KIND)
"""Every file lint checks against a schema of its own, rather than as a contract."""
GOVERNANCE_KINDS = tuple(kind for kind in SCHEMA_KINDS if kind != PROJECT_KIND)


@lru_cache(maxsize=None)
def _load(file_name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / file_name).read_text())


def load_schema() -> dict[str, Any]:
    return _load(SCHEMA_PATH.name)


@lru_cache(maxsize=None)
def _validator(definition_name: str) -> jsonschema.protocols.Validator:
    schema = load_schema()
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{definition_name}", **schema})


@lru_cache(maxsize=None)
def _governance_validator(kind: str) -> jsonschema.protocols.Validator:
    schema = _load(_SCHEMA_FILE_BY_KIND[kind])
    return jsonschema.validators.validator_for(schema)(schema)


def validate_custom_property(
    property_name: str, value: Any
) -> list[jsonschema.exceptions.ValidationError]:
    """Validate one Fluvv customProperty's value. `property_name` must be a
    key of `_DEFINITION_BY_PROPERTY` above."""
    definition_name = _DEFINITION_BY_PROPERTY[property_name]
    return sorted(_validator(definition_name).iter_errors(value), key=str)


def validate_governance_document(doc: dict[str, Any]) -> list[jsonschema.exceptions.ValidationError]:
    """Validate a governance file or the project file against the schema
    for its `kind`, which must be one of SCHEMA_KINDS."""
    return sorted(_governance_validator(doc["kind"]).iter_errors(doc), key=str)


def custom_property_errors(doc: dict[str, Any]) -> list[str]:
    """Every problem with a contract's Fluvv customProperties: a required
    one missing, or any one present but not matching its schema. Optional
    ones are only shape-checked when present: a contract that omits them is
    still complete."""
    errors = [RENAMED_LAYER_PROBLEM] if get_custom_property(doc, RENAMED_LAYER_PROPERTY) is not None else []
    for property_name in FLUVV_CUSTOM_PROPERTIES:
        value = get_custom_property(doc, property_name)
        if value is None:
            errors.append(f"missing required customProperties entry '{property_name}'")
            continue
        errors += [f"{property_name}: {error.message}" for error in validate_custom_property(property_name, value)]
    for property_name in (*OPTIONAL_FLUVV_CUSTOM_PROPERTIES, LAYER_PROPERTY):
        value = get_custom_property(doc, property_name)
        if value is not None:
            errors += [f"{property_name}: {error.message}" for error in validate_custom_property(property_name, value)]
    return errors


@lru_cache(maxsize=None)
def _odcs_validator(api_version: str) -> jsonschema.protocols.Validator | None:
    """The ODCS JSON Schema datacontract-cli itself lints against, for the
    contract's `apiVersion`; None for a version it doesn't ship."""
    schema_file = resources.files("datacontract").joinpath("schemas", f"odcs-{api_version.lstrip('v')}.schema.json")
    if not schema_file.is_file():
        return None
    schema = json.loads(schema_file.read_text())
    return jsonschema.validators.validator_for(schema)(schema)


def odcs_errors(doc: dict[str, Any]) -> list[str]:
    """The contract against the ODCS JSON Schema alone: the structural part
    of datacontract-cli's lint, without the rest of its (slower) work."""
    validator = _odcs_validator(str(doc.get("apiVersion", "")))
    if validator is None:
        return []
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '(root)'}: {error.message}"
        for error in sorted(validator.iter_errors(doc), key=str)
    ]
