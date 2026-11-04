from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluvv import Project
from fluvv.manifest import StaleManifestError, write_manifest

# A copy of the sales example project, so these tests pass from a checkout
# of this package alone.
EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"


@pytest.fixture(scope="module")
def project() -> Project:
    return Project(EXAMPLE)


def test_lists_filter_like_the_cli(project):
    assert [d["name"] for d in project.domains()] == ["sales"]
    assert [p["name"] for p in project.products(owner="sales-analytics-team")] == ["sales_reporting"]
    gold = project.contracts(layer="gold")
    assert [c["id"] for c in gold] == project.products()[0]["contracts"]
    assert [c["layer"] for c in project.contracts()] == ["bronze"] * 3 + ["silver"] * 3 + ["gold"] * 4
    assert project.contracts(owner="sales-data-team", layer="gold") == []
    assert {t["domain"] for t in project.terms()} == {"sales"} and len(project.terms("sales")) == 34


def test_columns_carry_their_contract_and_filter_by_classification(project):
    personal = project.columns(classification=("pii", "sensitive-pii"))
    assert len(personal) == 16
    assert {"contract": "bronze.sales.employees", "name": "email", "classification": "sensitive-pii"}.items() <= next(
        c for c in personal if c["name"] == "email"
    ).items()
    assert project.columns(classification="sensitive-pii") == [c for c in personal if c["classification"] == "sensitive-pii"]
    assert {c["contract"] for c in project.columns(contract="gold.sales.locations")} == {"gold.sales.locations"}


def test_lookups_by_name_return_the_manifest_entry_or_none(project):
    assert project.contract("gold.sales.cars")["owner"] == "sales-analytics-team"
    assert project.contract("contracts/gold/sales/cars.yaml") is project.contract("gold.sales.cars")
    assert project.product("sales_reporting")["domain"] == "sales"
    assert project.domain("sales")["owner"] == "sales-analytics-team"
    assert project.term("SalePrice")["definition"] == "The sale price of the vehicle."
    assert project.contract("nope") is project.product("nope") is project.domain("nope") is project.term("nope") is None


def test_a_term_in_several_domains_needs_its_domain():
    term = {"name": "Net", "definition": "d", "synonyms": [], "relatedTerms": []}
    project = Project(manifest={"glossaries": [{"domain": "a", "terms": [term]}, {"domain": "b", "terms": [term]}]})
    with pytest.raises(LookupError, match=r"several domains \(a, b\)"):
        project.term("Net")
    assert project.term("Net", domain="b")["domain"] == "b"


def test_impact_walks_downstream_nearest_first(project):
    assert project.impact("bronze.sales.employees.email") == [
        {"contract": "silver.sales.employees", "column": "email", "via": ["bronze.sales.employees.email"]},
        {"contract": "gold.sales.employees", "column": "email", "via": ["silver.sales.employees.email"]},
    ]
    whole = project.impact("bronze.sales.cars")
    assert [(e["contract"], e["column"]) for e in whole] == [
        ("silver.sales.cars", None), ("gold.sales.cars", None), ("gold.sales.dealership_performance", None),
    ]
    assert project.impact("gold.sales.cars") == []  # nothing reads it
    assert project.impact("gold.sales.cars.nope") is project.impact("nope.nope") is None


def test_impact_lists_a_column_reached_two_ways_once_with_both():
    def edge(src, dst):
        return {"from": {"contract": src.rsplit(".", 1)[0], "columns": [src.rsplit(".", 1)[1]]}, "to": {"contract": dst.rsplit(".", 1)[0], "column": dst.rsplit(".", 1)[1]}}

    contracts = [
        {"id": f"{layer}.d.{t}", "layer": layer, "path": f"{t}.yaml", "columns": [{"name": "x"}, {"name": "y"}]}
        for layer, t in (("bronze", "a"), ("silver", "b"), ("gold", "c"))
    ]
    lineage = {"tables": [], "columns": [edge("bronze.d.a.x", "silver.d.b.x"), edge("bronze.d.a.x", "silver.d.b.y"), edge("silver.d.b.x", "gold.d.c.x"), edge("silver.d.b.y", "gold.d.c.x")]}
    found = Project(manifest={"contracts": contracts, "lineage": lineage}).impact("bronze.d.a.x")
    assert found[-1] == {"contract": "gold.d.c", "column": "x", "via": ["silver.d.b.x", "silver.d.b.y"]}
    assert len(found) == 3


def test_reads_a_prebuilt_manifest_and_refuses_a_stale_one(tmp_path):
    prebuilt = write_manifest(EXAMPLE, tmp_path / "manifest.json")
    assert Project(EXAMPLE, prebuilt).manifest == json.loads(prebuilt.read_text())
    stale = json.loads(prebuilt.read_text())
    stale["sourceHash"] = "sha256:0"
    prebuilt.write_text(json.dumps(stale))
    with pytest.raises(StaleManifestError, match="stale"):
        Project(EXAMPLE, prebuilt)
