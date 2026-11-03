from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fluvv import cli, views
from fluvv.manifest import write_manifest

# A copy of the sales example project, so these tests pass from a checkout
# of this package alone.
EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"


@pytest.fixture(scope="module")
def prebuilt(tmp_path_factory) -> Path:
    return write_manifest(EXAMPLE, tmp_path_factory.mktemp("target") / "manifest.json")


@pytest.fixture
def run(monkeypatch, prebuilt):
    """Runs a command in the example repo, reading one manifest compiled
    for the module: compiling on each call would make these tests slow.
    test_a_prebuilt_manifest_matches_compiling_on_the_spot covers the default."""
    monkeypatch.chdir(EXAMPLE)
    return lambda *args: CliRunner().invoke(cli.build_app(), [*args, "--manifest", str(prebuilt)])


def _rows(output: str) -> list[list[str]]:
    return [line.split() for line in output.splitlines()]


def test_list_domains_counts_what_each_one_holds(run):
    result = run("list", "domains")
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["DOMAIN", "OWNER", "PRODUCTS", "CONTRACTS", "TERMS"],
        ["sales", "sales-analytics-team", "1", "10", "34"],
    ]


def test_list_products_and_terms(run):
    assert _rows(run("list", "products").output)[1] == ["sales_reporting", "sales", "sales-analytics-team", "4"]
    terms = run("list", "terms", "--domain", "sales")
    assert terms.exit_code == 0, terms.output
    assert ["SalePrice", "sales", "The", "sale", "price", "of", "the", "vehicle."] in _rows(terms.output)


def test_list_contracts_runs_bronze_to_gold_and_filters(run):
    every = [row[0] for row in _rows(run("list", "contracts").output)[1:]]
    assert len(every) == 10
    assert [c.split(".")[0] for c in every] == ["bronze"] * 3 + ["silver"] * 3 + ["gold"] * 4

    gold = _rows(run("list", "contracts", "--layer", "gold").output)[1:]
    assert gold[0] == ["gold.sales.cars", "sales-analytics-team", "sales_reporting", "P7D"]
    assert run("list", "contracts", "--product", "sales_reporting").output == run("list", "contracts", "--layer", "gold").output
    silver = _rows(run("list", "contracts", "--domain", "sales", "--layer", "silver").output)[1:]
    assert silver[0] == ["silver.sales.cars", "sales-data-team", "-", "-"]


def test_list_rejects_a_filter_naming_nothing_in_the_repo(run):
    for command, args, known in [
        ("contracts", ("--domain", "sale"), "sales"),
        ("contracts", ("--layer", "platinum"), "bronze, silver, gold"),
        ("contracts", ("--product", "x"), "sales_reporting"),
        ("products", ("--domain", "sale"), "sales"),
        ("terms", ("--domain", "sale"), "sales"),
    ]:
        result = run("list", command, *args)
        assert result.exit_code == 2 and isinstance(result.exception, SystemExit), result.output  # a usage error
        assert known in result.output


def test_a_domain_without_a_glossary_or_products_is_an_empty_list_not_an_error(prebuilt, tmp_path, monkeypatch):
    manifest = json.loads(prebuilt.read_text())
    manifest["domains"].append({"name": "finance", "description": None, "owner": "finance-team", "experts": []})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.chdir(EXAMPLE)
    for command in ("terms", "products", "contracts"):
        result = CliRunner().invoke(cli.build_app(), ["list", command, "--domain", "finance", "--manifest", str(tmp_path / "manifest.json")])
        assert result.exit_code == 0, result.output
        assert len(result.output.splitlines()) == 1  # the header


def test_show_a_contract_by_id_or_file(run):
    result = run("show", "gold.sales.cars")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "gold.sales.cars (contract)"
    assert "  upstream       silver.sales.cars" in lines
    assert "  references     dealership_location -> gold.sales.locations (dealership_location)" in lines
    assert "  primary key    dealership_location, sale_month" in lines
    assert ["dealership_location", "string", "DealershipLocation", "-"] in _rows(result.output)
    assert run("show", "contracts/gold/sales/cars.yaml").output == result.output
    assert run("show", str(EXAMPLE / "contracts/gold/sales/cars.yaml")).output == result.output


def test_show_a_domain_a_data_product_and_a_term(run):
    domain = run("show", "sales").output.splitlines()
    assert domain[0] == "sales (domain)"
    assert "  contracts       10 (bronze 3, silver 3, gold 4)" in domain

    product = run("show", "sales_reporting").output
    assert product.startswith("sales_reporting (data product)\n")
    assert ["gold.sales.dealership_performance", "sales-analytics-team", "P7D"] in _rows(product)

    term = run("show", "SalePrice").output
    assert "  synonyms       price" in term.splitlines()  # derived from the columns that use it
    assert _rows(term)[-2:] == [["bronze.sales.cars", "price"], ["silver.sales.cars", "price"]]


def test_show_an_unknown_name_exits_non_zero(run):
    result = run("show", "nope")
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "nothing named 'nope'" in result.output


def test_a_prebuilt_manifest_matches_compiling_on_the_spot(run):
    compiled = CliRunner().invoke(cli.build_app(), ["show", "sales_reporting"])
    assert compiled.exit_code == 0, compiled.output
    assert run("show", "sales_reporting").output == compiled.output


def test_a_prebuilt_manifest_that_cant_be_used_is_refused_naming_it_once(prebuilt, tmp_path, monkeypatch):
    stale, old_version = json.loads(prebuilt.read_text()), json.loads(prebuilt.read_text())
    stale["sourceHash"] = "sha256:0"
    old_version["manifestVersion"] = "1"
    files = {"the manifest is stale": json.dumps(stale), "manifestVersion '1'": json.dumps(old_version), "not a manifest": "{"}
    monkeypatch.chdir(EXAMPLE)
    for expected, content in files.items():
        path = tmp_path / "manifest.json"
        path.write_text(content)
        result = CliRunner().invoke(cli.build_app(), ["show", "sales", "--manifest", str(path)])
        assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output  # no traceback
        assert expected in result.output and result.output.count(str(path)) == 1


def test_a_repo_that_does_not_compile_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.build_app(), ["list", "contracts"])
    assert result.exit_code == 1 and "[not-a-repository]" in result.output


def _manifest(**overrides) -> dict:
    return {"domains": [], "dataProducts": [], "contracts": [], "glossaries": [], **overrides}


def test_show_prints_every_kind_a_name_matches():
    manifest = _manifest(
        domains=[{"name": "billing", "description": "Invoices.", "owner": "team-a", "experts": []}],
        dataProducts=[{"name": "billing", "domain": "billing", "description": {"purpose": "Billing reports."}, "owner": "team-a", "experts": [], "contracts": []}],
    )
    shown = views.show(manifest, "billing")
    assert shown.startswith("billing (domain)\n") and "\n\nbilling (data product)\n" in shown
    assert views.show(manifest, "nope") is None


def test_a_multi_line_definition_stays_on_its_row():
    manifest = _manifest(glossaries=[{"domain": "d", "terms": [{"name": "Net", "definition": "Gross less\nrefunds.\n", "synonyms": [], "relatedTerms": []}]}])
    assert views.terms(manifest).splitlines()[1:] == ["Net   d       Gross less refunds."]
