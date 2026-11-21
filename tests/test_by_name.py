"""Tables by name in a catalog: a server with ODCS's `catalog` and `schema`
and no path, as a `databricks` server has, and a source that names a table
no contract produces."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

from fluvv import manifest
from fluvv.contract import load_extension, server_table
from fluvv.lint import lint
from fluvv.models import FluvvExtension
from fluvv.schema import SCHEMAS_DIR
from fluvv.semantic import check_source_locations, run_project_checks

UNITY = {"server": "prod", "type": "databricks", "catalog": "main", "schema": "sales"}


def _contract(contract_id: str, servers: list[dict], source: dict, table: str = "cars", layer: str | None = None) -> dict:
    custom = [
        {"property": "sources", "value": [{"id": "upstream", "read": "spark_read_file", **source}]},
        {"property": "ingestion_config", "value": {"write": "spark_write_file"}},
    ]
    if layer:
        custom.append({"property": "layer", "value": layer})
    return {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": contract_id,
        "version": "0.1.0",
        "status": "active",
        "servers": servers,
        "schema": [{"name": table, "logicalType": "object", "properties": [{"name": "k", "logicalType": "string"}]}],
        "customProperties": custom,
    }


def _repo(root: Path, files: dict[str, dict]) -> Path:
    for rel, doc in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def _codes(issues) -> list[str]:
    return sorted(i.code for i in issues)


def test_a_server_names_its_table_with_catalog_and_schema_and_no_path():
    doc = {"schema": [{"name": "cars"}]}
    assert server_table(UNITY, doc) == "main.sales.cars"
    assert server_table({"server": "local", "type": "local", "path": "./t", "format": "delta"}, doc) is None
    assert server_table({**UNITY, "path": "./t"}, doc) is None  # a path is a place on storage, as before
    assert server_table({"server": "x", "type": "databricks", "catalog": "main"}, doc) is None


def test_a_contract_on_a_server_by_name_and_a_source_by_name_lint(tmp_path):
    raw = {"contract": None, "table": "main.raw.orders"}
    path = _repo(tmp_path, {"contracts/cars.yaml": _contract("cars", [UNITY], raw)}) / "contracts/cars.yaml"
    assert lint(path).passed, lint(path).report()
    assert load_extension(path).sources[0].table == "main.raw.orders"


def test_a_source_gives_a_location_and_format_or_a_table_not_both():
    def ext(*sources):
        return FluvvExtension.model_validate({
            "sources": [{"read": "spark_read_file", "id": f"s{i}", **s} for i, s in enumerate(sources)],
            "ingestion_config": {"write": "spark_write_file"},
        })

    assert check_source_locations(ext({"contract": None, "table": "main.raw.orders"})) == []
    assert _codes(check_source_locations(ext({"contract": None, "table": "main.raw.orders", "location": "./x", "format": "csv"}))) == [
        "source-table-and-location"
    ]
    assert _codes(check_source_locations(ext({"contract": "contracts/a.yaml", "table": "main.raw.orders"}))) == ["source-location-redundant"]
    assert _codes(check_source_locations(ext({"contract": None}))) == ["source-location-missing"]


def test_an_upstream_on_a_server_by_name_is_read_by_name(tmp_path):
    files = {
        "contracts/bronze.yaml": _contract("bronze.cars", [UNITY], {"contract": None, "table": "main.raw.cars"}),
        "contracts/silver.yaml": _contract(
            "silver.cars", [{**UNITY, "schema": "silver"}], {"contract": "contracts/bronze.yaml"}, table="silver_cars"
        ),
    }
    source = load_extension(_repo(tmp_path, files) / "contracts/silver.yaml").sources[0]
    assert (source.table, source.location) == ("main.sales.cars", None)

    compiled = manifest.compile_manifest(tmp_path)
    jsonschema.Draft202012Validator(json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())).validate(compiled)
    silver = next(c for c in compiled["contracts"] if c["id"] == "silver.cars")
    assert silver["ingestion"]["sources"][0]["table"] == "main.sales.cars"
    assert silver["ingestion"]["sources"][0]["location"] is None


def test_two_contracts_cant_write_one_table_by_name(tmp_path):
    """Layers apart, so their run records are too: only the name clashes."""
    raw = {"contract": None, "table": "main.raw.cars"}
    files = {"contracts/a.yaml": _contract("a", [UNITY], raw, layer="x"), "contracts/b.yaml": _contract("b", [UNITY], raw, layer="y")}
    issues = run_project_checks(_repo(tmp_path, files))
    assert _codes(issues) == ["duplicate-target", "duplicate-target"]
    assert "main.sales.cars" in issues[0].message
    apart = {
        "contracts/a.yaml": _contract("a", [UNITY], raw, layer="x"),
        "contracts/b.yaml": _contract("b", [{**UNITY, "schema": "eu"}], raw, layer="y"),
    }
    assert run_project_checks(_repo(tmp_path / "apart", apart)) == []


def test_a_table_by_name_is_its_physical_name_with_its_variables_filled_in(tmp_path, monkeypatch):
    """As its own run writes it, so the run reading it reads that table."""
    monkeypatch.setenv("CATALOG", "prod")
    monkeypatch.setenv("ENV", "eu")
    upstream = _contract("bronze.cars", [{**UNITY, "catalog": "${CATALOG}"}], {"contract": None, "table": "main.raw.cars"}, table="cars_${ENV}")
    files = {
        "contracts/bronze.yaml": upstream,
        "contracts/silver.yaml": _contract("silver.cars", [UNITY], {"contract": "contracts/bronze.yaml"}, table="silver_cars"),
    }
    assert load_extension(_repo(tmp_path, files) / "contracts/silver.yaml").sources[0].table == "prod.sales.cars_eu"
    assert server_table(UNITY, {"schema": [{"name": "cars", "physicalName": "cars_v2"}]}) == "main.sales.cars_v2"
