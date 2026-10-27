"""JSON Schema validation: Fluvv customProperties against
fluvv/schemas/fluvv.schema.json, and governance files against their own
schema (domain, glossary, or the vendored ODPS v1.1.0 schema)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMA_PATH = SCHEMAS_DIR / "fluvv.schema.json"

_DEFINITION_BY_PROPERTY = {
    "sources": "Sources",
    "transform": "Transform",
    "ingestion_config": "IngestionConfig",
    "businessMetrics": "BusinessMetrics",
}
_SCHEMA_FILE_BY_KIND = {
    "Domain": "domain.schema.json",
    "Glossary": "glossary.schema.json",
    "DataProduct": "odps-1.1.0.schema.json",
}
GOVERNANCE_KINDS = tuple(_SCHEMA_FILE_BY_KIND)


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
    """Validate a governance file against the schema for its `kind`, which
    must be one of GOVERNANCE_KINDS."""
    return sorted(_governance_validator(doc["kind"]).iter_errors(doc), key=str)
