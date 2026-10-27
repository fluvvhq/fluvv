from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import sqlglot

from fluvv import lineage, manifest
from fluvv.contract import UnresolvableSourceError, find_repo_root, load_extension, resolve_extension
from fluvv.lint import lint
from fluvv.models import FluvvExtension
from fluvv.schema import SCHEMAS_DIR
from fluvv.semantic import check_source_locations, check_transforms

# A copy of the sales example project, so these tests pass from a checkout
# of this package alone.
EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"


def _ext(sources: list[dict], transform: str | None = None) -> FluvvExtension:
    values = {"sources": sources, "ingestion_config": {"write": "spark_write_file"}}
    if transform is not None:
        values["transform"] = {"engine": "pyspark-sql", "sql": transform}
    return FluvvExtension.model_validate(values)


def _source(sid: str, contract: str | None = None, sql: str | None = None, **extra) -> dict:
    source = {"id": sid, "contract": contract, "read": "spark_read_file", **extra}
    if sql:
        source["transform"] = {"engine": "pyspark-sql", "sql": sql}
    return source


def _codes(issues) -> list[str]:
    return sorted(i.code for i in issues)


# --- sources ------------------------------------------------------------------


def test_repo_root_is_found_from_the_repository_layout(tmp_path):
    """A contracts repo never contains the fluvv package, so the root is
    recognised by the layout SPEC.md requires, not by fluvv's own files."""
    contract = tmp_path / "contracts" / "bronze" / "sales" / "cars.yaml"
    domain = tmp_path / "governance" / "domains" / "sales.yaml"
    # Domains named like the top-level folders: their parent directory then
    # holds a contracts/ or governance/ folder of its own.
    contracts_domain = tmp_path / "contracts" / "silver" / "contracts" / "x.yaml"
    governance_domain = tmp_path / "contracts" / "silver" / "governance" / "x.yaml"
    example = tmp_path / "docs" / "examples" / "minimal.yaml"
    for path in (contract, domain, contracts_domain, governance_domain, example):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for path in (contract, domain, contracts_domain, governance_domain, example):
        assert find_repo_root(path) == tmp_path, path
    with pytest.raises(FileNotFoundError, match="contracts/ or governance/"):
        find_repo_root(tmp_path.parent / "elsewhere.yaml")


def test_contract_backed_source_location_is_derived_from_upstream_servers():
    ext = load_extension(EXAMPLE / "contracts/silver/sales/cars.yaml")
    assert (ext.sources[0].location, ext.sources[0].format) == ("./data/bronze/sales/cars", "delta")
    raw = load_extension(EXAMPLE / "contracts/silver/sales/cars.yaml", resolve=False)
    assert raw.sources[0].location is None


def test_source_location_is_stated_once():
    ext = _ext([
        _source("a", contract="contracts/bronze/sales/cars.yaml", location="./x", format="delta"),
        _source("b", contract=None, location="./landing/b"),
    ])
    assert _codes(check_source_locations(ext)) == ["source-location-missing", "source-location-redundant"]


def test_unresolvable_sources_raise():
    with pytest.raises(UnresolvableSourceError):
        resolve_extension(_ext([_source("a", contract="contracts/nope.yaml")]), EXAMPLE)
    with pytest.raises(UnresolvableSourceError):
        resolve_extension(_ext([_source("a")]), EXAMPLE)


# --- transforms -----------------------------------------------------------------


def test_per_source_transform_may_only_read_its_own_source():
    ext = _ext(
        [_source("a", sql="SELECT * FROM a JOIN b ON a.k = b.k"), _source("b")],
        "SELECT * FROM a JOIN b ON a.k = b.k",
    )
    issues = check_transforms(ext)
    assert _codes(issues) == ["source-transform-foreign-reference"]
    assert "'b'" in issues[0].message


def test_combine_transform_is_required_with_several_sources_only():
    assert _codes(check_transforms(_ext([_source("a"), _source("b")]))) == ["missing-combine-transform"]
    assert check_transforms(_ext([_source("a", sql="SELECT k FROM a")])) == []


def test_combine_transform_references_declared_sources_and_may_use_ctes():
    ext = _ext([_source("a")], "WITH x AS (SELECT * FROM a) SELECT * FROM x JOIN c ON x.k = c.k")
    issues = check_transforms(ext)
    assert [(i.code, "'c'" in i.message) for i in issues] == [("unknown-source-reference", True)]


def test_effective_sql_composes_per_source_steps_as_ctes():
    sources = [_source("a", sql="SELECT k, SUM(v) AS v FROM a GROUP BY k"), _source("b")]
    sql = lineage.effective_sql(sources, {"sql": "WITH j AS (SELECT * FROM a) SELECT j.k, j.v, b.w FROM j JOIN b ON j.k = b.k"})
    parsed = sqlglot.parse_one(sql, dialect="spark")
    assert [cte.alias for cte in parsed.args["with_"].expressions] == ["a", "j"]  # per-source step first
    assert {t.name for t in parsed.find_all(sqlglot.exp.Table)} == {"__fluvv_raw_a", "a", "b", "j"}
    assert lineage.effective_sql([_source("a")], None) == "SELECT * FROM a"


def test_implicit_combine_needs_exactly_one_source():
    with pytest.raises(ValueError):
        lineage.combine_sql([_source("a"), _source("b")], None)


# --- lint over the repo -----------------------------------------------------------


def test_new_multi_source_contract_and_governance_files_lint_clean():
    for rel in (
        "contracts/gold/sales/dealership_performance.yaml",
        "governance/domains/sales.yaml",
        "governance/glossaries/sales.yaml",
        "governance/products/sales_reporting.odps.yaml",
    ):
        result = lint(EXAMPLE / rel)
        assert result.passed, result.report()


def test_unknown_business_name_fails_lint_with_a_stub(tmp_path):
    contract = (EXAMPLE / "contracts/gold/sales/employees.yaml").read_text()
    path = tmp_path / "employees.yaml"
    path.write_text(contract.replace("businessName: JobTitle", "businessName: JobRole"))
    result = lint(path, repo_root=EXAMPLE)
    assert not result.passed
    assert [i.code for i in result.semantic_issues] == ["unknown-business-term"]
    assert "- name: JobRole" in result.report()


def test_governance_file_schema_errors_are_reported(tmp_path):
    path = tmp_path / "domain.yaml"
    path.write_text("apiVersion: fluvv/v1\nkind: Domain\nname: x\n")
    result = lint(path, repo_root=EXAMPLE)
    assert not result.passed
    assert any("owner" in e for e in result.schema_errors)


# --- manifest -----------------------------------------------------------------------


def test_manifest_matches_its_schema_and_compiles_governance():
    compiled = manifest.compile_manifest(EXAMPLE)
    schema = json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(compiled)

    terms = {t["name"]: t for t in compiled["glossaries"][0]["terms"]}
    assert terms["ModelYear"]["synonyms"] == ["year"]
    assert {"from": "silver.sales.cars", "to": "gold.sales.dealership_performance"} in compiled["lineage"]["tables"]

    contracts = {c["id"]: c for c in compiled["contracts"]}
    model_year = next(c for c in contracts["silver.sales.cars"]["columns"] if c["name"] == "model_year")
    assert model_year["transformSourceObjects"] == ["bronze.sales.cars.year"]
    # A rename with a cast: bronze keeps `year` as delivered, silver types it.
    assert model_year["transformLogic"] == "CAST(sales_cars_bronze.year AS INT)"
    assert contracts["gold.sales.cars"]["dataProducts"] == ["sales_reporting"]
    assert compiled["sourceHash"] == manifest.compile_manifest(EXAMPLE)["sourceHash"]
