"""What a repository may choose for itself (its layers, ids, file layout,
domains, glossaries and classifications) and the few rules that remain,
because breaking them makes Fluvv wrong. See SPEC.md Section 1.2."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from fluvv import Project, cli, export_artifacts, governance, manifest
from fluvv.contract import UnresolvableSourceError, find_repo_root, load_extension, project_layout
from fluvv.lint import lint
from fluvv.schema import SCHEMAS_DIR
from fluvv.semantic import ProjectCheckError, run_project_checks

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"


def _contract(
    contract_id: str,
    table: str | None = None,
    target: str | None = None,
    reads: str | None = None,
    columns: list[dict] | None = None,
    mode: str | None = None,
    **top_level,
) -> dict:
    """A complete contract, with nothing the format doesn't require."""
    layer = top_level.pop("layer", None)
    table = table or contract_id
    source = (
        {"id": "upstream", "contract": reads, "read": "spark_read_file"}
        if reads
        else {"id": "upstream", "contract": None, "location": "./incoming", "format": "csv", "read": "spark_read_file"}
    )
    ingestion = {"write": "spark_write_file", **({"mode": mode} if mode else {})}
    custom = [{"property": "sources", "value": [source]}, {"property": "ingestion_config", "value": ingestion}]
    if layer:
        custom.append({"property": "layer", "value": layer})
    return {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": contract_id,
        "version": "0.1.0",
        "status": "active",
        "servers": [{"server": "local", "type": "local", "path": target or f"./tables/{contract_id}", "format": "delta"}],
        "schema": [{"name": table, "logicalType": "object", "properties": columns or [{"name": "k", "logicalType": "string"}]}],
        "customProperties": custom,
        **top_level,
    }


def _repo(root: Path, files: dict[str, dict]) -> Path:
    for rel, doc in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def _codes(issues) -> list[tuple[str, str]]:
    return sorted((i.path, i.code) for i in issues)


def _glossary(terms: list[str], domain: str | None = None) -> dict:
    return {
        "apiVersion": "fluvv/v1",
        "kind": "Glossary",
        **({"domain": domain} if domain else {}),
        "terms": [{"name": t, "definition": f"{t} means this."} for t in terms],
    }


def _domain(name: str) -> dict:
    return {"apiVersion": "fluvv/v1", "kind": "Domain", "name": name, "owner": "team-a"}


# --- layers, ids, paths and domains are free ------------------------------------------


def test_a_contract_needs_no_layer_domain_or_shaped_id_or_path(tmp_path):
    """Nothing from the example's conventions: it lints, compiles and lists."""
    path = _repo(tmp_path, {"contracts/orders.yaml": _contract("orders")}) / "contracts/orders.yaml"
    assert lint(path).passed, lint(path).report()

    compiled = manifest.compile_manifest(tmp_path)
    jsonschema.Draft202012Validator(json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())).validate(compiled)
    entry = compiled["contracts"][0]
    assert (entry["id"], entry["layer"], entry["domain"], entry["path"]) == ("orders", None, None, "contracts/orders.yaml")

    project = Project(tmp_path)
    assert [c["id"] for c in project.contracts()] == ["orders"]
    assert [c["contract"] for c in project.columns()] == ["orders"]
    assert project.impact("orders") == []


def test_cli_shows_a_contract_without_layer_or_domain(tmp_path, monkeypatch):
    _repo(tmp_path, {"contracts/orders.yaml": _contract("orders")})
    monkeypatch.chdir(tmp_path)
    run = lambda *args: CliRunner().invoke(cli.build_app(), list(args))
    shown = run("show", "orders")
    assert shown.exit_code == 0, shown.output
    assert "  domain         -" in shown.output.splitlines()
    assert "  layer          -" in shown.output.splitlines()
    listed = run("list", "contracts")
    assert listed.exit_code == 0 and "orders" in listed.output
    assert run("list", "contracts", "--layer", "platinum").exit_code == 2  # no contract has it


def test_the_three_names_1x_always_took_still_work_where_no_contract_has_them(tmp_path, monkeypatch):
    """A script looping over bronze, silver and gold keeps working: an empty
    list, as in 1.2, not a usage error."""
    _repo(tmp_path, {"contracts/orders.yaml": _contract("orders", layer="bronze")})
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.build_app(), ["list", "contracts", "--layer", "gold"])
    assert result.exit_code == 0, result.output
    assert len(result.output.splitlines()) == 1  # the header


def test_any_layer_name_works_and_contracts_list_upstream_first(tmp_path, monkeypatch):
    _repo(tmp_path, {
        # Named so that sorting by name would get the flow wrong.
        "contracts/a.yaml": _contract("a_marts", reads="contracts/b.yaml", layer="marts", domain="shop"),
        "contracts/b.yaml": _contract("b_staging", reads="contracts/c.yaml", layer="staging", domain="shop"),
        "contracts/c.yaml": _contract("c_raw", layer="raw", domain="shop"),
        "governance/shop.yaml": _domain("shop"),
    })
    project = Project(tmp_path)
    assert [c["layer"] for c in project.contracts()] == ["raw", "staging", "marts"]
    assert [c["id"] for c in project.contracts(layer="staging")] == ["b_staging"]

    monkeypatch.chdir(tmp_path)
    run = lambda *args: CliRunner().invoke(cli.build_app(), list(args))
    assert [line.split()[0] for line in run("list", "contracts", "--layer", "marts").output.splitlines()] == ["CONTRACT", "a_marts"]
    assert "raw, staging, marts" in " ".join(run("list", "contracts", "--layer", "platinum").output.replace("│", " ").split())
    assert "  contracts       3 (raw 1, staging 1, marts 1)" in run("show", "shop").output.splitlines()


def test_medallion_layers_keep_their_order_whatever_reads_what():
    """1.x listed bronze, silver, gold, then by id. A silver reading a silver
    and a gold reading landing would sort differently by lineage alone."""
    layers = {"bronze.a": "bronze", "silver.b": "silver", "silver.c": "silver", "gold.d": "gold", "gold.e": "gold"}
    contracts = [{"id": cid, "domain": None, "layer": layer, "owner": None, "dataProducts": []} for cid, layer in layers.items()]
    edges = [{"from": "bronze.a", "to": "silver.b"}, {"from": "silver.b", "to": "silver.c"}, {"from": "bronze.a", "to": "gold.d"}]
    project = Project(manifest={"contracts": contracts, "lineage": {"tables": edges, "columns": []}})
    assert [c["id"] for c in project.contracts()] == list(layers)


def test_medallion_layer_wins_so_a_contracts_own_layer_property_means_what_it_did(tmp_path):
    """Any customProperty was allowed before 1.3, so a contract may already
    have one called `layer` for something else. Only one without
    `medallionLayer`, which 1.2 would have refused, can mean ours by it."""
    both = {"customProperties": [{"property": "medallionLayer", "value": "gold"}, {"property": "layer", "value": "presentation"}]}
    assert governance.medallion_layer(both) == "gold"
    assert governance.medallion_layer({"customProperties": both["customProperties"][1:]}) == "presentation"
    assert governance.medallion_layer({"customProperties": []}) is None
    assert [c["layer"] for c in Project(EXAMPLE).contracts(layer="gold")] == ["gold"] * 4  # the example still uses the old name

    doc = _contract("orders")
    doc["customProperties"] += [{"property": "medallionLayer", "value": "silver"}, {"property": "layer", "value": "not/a.label"}]
    path = tmp_path / "orders.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert lint(path).passed, lint(path).report()


@pytest.mark.parametrize("value", ["a/b", "a.b", ["raw"]])
def test_a_layer_that_cant_name_a_folder_or_catalog_level_fails_lint(tmp_path, value):
    doc = _contract("orders")
    doc["customProperties"].append({"property": "layer", "value": value})
    path = tmp_path / "orders.yaml"
    path.write_text(yaml.safe_dump(doc))
    result = lint(path)
    assert not result.passed and any(e.startswith("layer:") for e in result.schema_errors), result.report()


def test_a_named_domain_must_still_exist(tmp_path):
    _repo(tmp_path, {"contracts/orders.yaml": _contract("orders", domain="nowhere")})
    assert _codes(run_project_checks(tmp_path)) == [("contracts/orders.yaml", "unknown-domain")]


@pytest.mark.parametrize("name", ["sales.eu", "a/b"])
def test_a_contracts_domain_cant_be_what_cant_name_a_folder_or_catalog_level(tmp_path, name):
    """1.2's id and path checks refused these contracts. Like a layer, a
    domain can become a folder and a catalog level. A Domain file no contract
    uses was fine in 1.2, so it stays fine."""
    _repo(tmp_path, {"governance/d.yaml": _domain(name)})
    assert lint(tmp_path / "governance/d.yaml").passed
    assert run_project_checks(tmp_path) == []

    _repo(tmp_path, {"contracts/a.yaml": _contract("a", domain=name)})
    result = lint(tmp_path / "contracts/a.yaml")
    assert [i.code for i in result.semantic_issues] == ["invalid-domain-name"], result.report()
    assert _codes(run_project_checks(tmp_path)) == [("contracts/a.yaml", "fails-lint")]


def test_a_contract_with_no_id_is_reported_not_a_crash(tmp_path):
    doc = _contract("orders")
    del doc["id"]
    _repo(tmp_path, {"contracts/orders.yaml": doc})
    assert _codes(run_project_checks(tmp_path)) == [("contracts/orders.yaml", "fails-lint")]


# --- what keeps contracts apart instead ---------------------------------------------------


def test_two_contracts_cant_share_an_id(tmp_path):
    _repo(tmp_path, {"contracts/a.yaml": _contract("same", table="a", target="./a"), "contracts/b.yaml": _contract("same", table="b", target="./b")})
    with pytest.raises(ProjectCheckError) as excinfo:
        manifest.compile_manifest(tmp_path)
    assert _codes(excinfo.value.issues) == [("contracts/a.yaml", "duplicate-contract-id"), ("contracts/b.yaml", "duplicate-contract-id")]
    assert excinfo.value.report().count("[duplicate-contract-id]") == 1  # one problem, naming both files


def test_two_contracts_cant_write_to_one_location_however_its_spelled(tmp_path):
    _repo(tmp_path, {"contracts/a.yaml": _contract("a", target="./data/t"), "contracts/b.yaml": _contract("b", target="data/t/")})
    assert _codes(run_project_checks(tmp_path)) == [("contracts/a.yaml", "duplicate-target"), ("contracts/b.yaml", "duplicate-target")]


def test_a_table_name_only_clashes_where_it_would_share_run_records_and_a_catalog_entry(tmp_path):
    domains = {"governance/x.yaml": _domain("x"), "governance/y.yaml": _domain("y")}
    apart = {
        "contracts/a.yaml": _contract("a", table="t", layer="raw", domain="x"),
        "contracts/b.yaml": _contract("b", table="t", layer="marts", domain="x"),
        "contracts/c.yaml": _contract("c", table="t", layer="raw", domain="y"),
    }
    assert run_project_checks(_repo(tmp_path / "apart", {**domains, **apart})) == []

    same = {"contracts/a.yaml": _contract("a", table="t", layer="raw", domain="x"), "contracts/b.yaml": _contract("b", table="t", layer="raw", domain="x")}
    assert {code for _, code in _codes(run_project_checks(_repo(tmp_path / "same", {**domains, **same})))} == {"duplicate-target"}

    # A layer x and a domain x are different places.
    apart = {"contracts/a.yaml": _contract("a", table="t", layer="x"), "contracts/b.yaml": _contract("b", table="t", domain="x")}
    assert run_project_checks(_repo(tmp_path / "gappy", {**domains, **apart})) == []


def test_contracts_may_append_to_one_table_but_not_alongside_an_overwrite(tmp_path):
    """Appending fans in without losing anything, so nothing is wrong; an
    overwrite would destroy what the others wrote."""
    fan_in = {
        "contracts/eu.yaml": _contract("eu", table="cars", target="./data/cars", mode="append", layer="bronze"),
        "contracts/us.yaml": _contract("us", table="cars", target="./data/cars", mode="append", layer="bronze"),
    }
    assert run_project_checks(_repo(tmp_path / "fan_in", fan_in)) == []

    mixed = {**fan_in, "contracts/us.yaml": _contract("us", table="cars", target="./data/cars", layer="bronze")}
    assert {code for _, code in _codes(run_project_checks(_repo(tmp_path / "mixed", mixed)))} == {"duplicate-target"}


def test_sources_can_flow_any_way_but_in_a_cycle(tmp_path):
    # A gold table reading landing and a bronze one reading gold: fine, by these rules.
    backwards = {
        "contracts/gold.yaml": _contract("gold.t", layer="gold"),
        "contracts/bronze.yaml": _contract("bronze.t", reads="contracts/gold.yaml", layer="bronze"),
    }
    assert run_project_checks(_repo(tmp_path / "backwards", backwards)) == []

    cycle = {
        "contracts/a.yaml": _contract("a", reads="contracts/b.yaml"),
        "contracts/b.yaml": _contract("b", reads="contracts/c.yaml"),
        "contracts/c.yaml": _contract("c", reads="contracts/a.yaml"),
        "contracts/outside.yaml": _contract("outside", reads="contracts/a.yaml"),
    }
    with pytest.raises(ProjectCheckError) as excinfo:
        manifest.compile_manifest(_repo(tmp_path / "cycle", cycle))
    assert _codes(excinfo.value.issues) == [(f"contracts/{name}.yaml", "source-cycle") for name in "abc"]
    assert "a reads b reads c reads a" in excinfo.value.report()


def test_a_contract_cant_read_itself(tmp_path):
    _repo(tmp_path, {"contracts/a.yaml": _contract("a", reads="contracts/a.yaml")})
    issues = run_project_checks(tmp_path)
    assert _codes(issues) == [("contracts/a.yaml", "source-cycle")] and "a reads a" in issues[0].message


def test_a_manifest_with_a_cycle_passed_in_directly_still_lists():
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
    contracts = [{"id": cid, "domain": None, "layer": None, "owner": None, "dataProducts": []} for cid in "ab"]
    project = Project(manifest={"contracts": contracts, "lineage": {"tables": edges, "columns": []}})
    assert {c["id"] for c in project.contracts()} == {"a", "b"}


# --- glossaries --------------------------------------------------------------------------


def test_a_shared_glossary_serves_every_contract_and_a_domains_own_term_wins(tmp_path):
    term = lambda name, business: {"name": name, "logicalType": "string", "businessName": business}
    _repo(tmp_path, {
        "governance/shared.yaml": _glossary(["Customer", "Net"]),
        "governance/sales.yaml": _glossary(["Net"], domain="sales"),
        "governance/domains/sales.yaml": _domain("sales"),
        "contracts/orders.yaml": _contract("orders", domain="sales", columns=[term("client", "Customer"), term("net_sales", "Net")]),
        "contracts/ledger.yaml": _contract("ledger", columns=[term("account_holder", "Customer"), term("net_ledger", "Net")]),
    })
    compiled = manifest.compile_manifest(tmp_path)
    jsonschema.Draft202012Validator(json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())).validate(compiled)

    by_domain = {g["domain"]: {t["name"]: t for t in g["terms"]} for g in compiled["glossaries"]}
    assert [g["domain"] for g in compiled["glossaries"]] == [None, "sales"]
    assert by_domain[None]["Customer"]["synonyms"] == ["account_holder", "client"]  # used from both
    assert by_domain[None]["Net"]["synonyms"] == ["net_ledger"]  # sales has its own
    assert by_domain["sales"]["Net"]["synonyms"] == ["net_sales"]


def test_show_of_a_shared_term_lists_the_columns_that_resolve_to_it(tmp_path, monkeypatch):
    term = lambda name, business: {"name": name, "logicalType": "string", "businessName": business}
    _repo(tmp_path, {
        "governance/shared.yaml": _glossary(["Net"]),
        "governance/sales.yaml": _glossary(["Net"], domain="sales"),
        "governance/domains/sales.yaml": _domain("sales"),
        "contracts/orders.yaml": _contract("orders", domain="sales", columns=[term("net_sales", "Net")]),
        "contracts/ledger.yaml": _contract("ledger", columns=[term("net_ledger", "Net")]),
    })
    monkeypatch.chdir(tmp_path)
    shown = CliRunner().invoke(cli.build_app(), ["show", "Net"]).output
    shared, own = [block for block in shown.split("\n\n") if "USED BY" in block]
    assert "ledger" in shared and "orders" not in shared
    assert "orders" in own and "ledger" not in own
    with pytest.raises(LookupError, match=r"several glossaries \(shared, sales\)"):
        Project(tmp_path).term("Net")


def test_a_business_name_must_still_resolve_somewhere(tmp_path):
    column = {"name": "x", "logicalType": "string", "businessName": "Nope"}
    _repo(tmp_path, {
        "governance/shared.yaml": _glossary(["Customer"]),
        "contracts/a.yaml": _contract("a", columns=[column]),
    })
    issues = run_project_checks(tmp_path)
    assert _codes(issues) == [("contracts/a.yaml", "unknown-business-term")]
    assert "the shared glossary" in issues[0].message and "  - name: Nope" in issues[0].message


def test_two_files_cant_define_the_same_glossary_or_classifications(tmp_path):
    classifications = {"apiVersion": "fluvv/v1", "kind": "Classifications", "values": [{"name": "open"}]}
    _repo(tmp_path, {
        "contracts/a.yaml": _contract("a"),
        "governance/one.yaml": _glossary(["A"]),
        "governance/two.yaml": _glossary(["B"]),
        "governance/c1.yaml": classifications,
        "governance/c2.yaml": classifications,
    })
    assert _codes(run_project_checks(tmp_path)) == [
        (f"governance/{name}.yaml", "duplicate-definition") for name in ("c1", "c2", "one", "two")
    ]


# --- classifications ------------------------------------------------------------------------


def test_a_repository_can_define_its_own_classifications(tmp_path, monkeypatch):
    columns = [{"name": "k", "logicalType": "string", "classification": "restricted"}]
    _repo(tmp_path, {
        "contracts/a.yaml": _contract("a", columns=columns),
        "governance/classifications.yaml": {
            "apiVersion": "fluvv/v1",
            "kind": "Classifications",
            "values": [{"name": "open"}, {"name": "restricted", "catalogTag": "Security.Restricted"}],
        },
    })
    assert lint(tmp_path / "governance/classifications.yaml").passed
    compiled = manifest.compile_manifest(tmp_path)
    assert compiled["classifications"] == [{"name": "open", "catalogTag": None}, {"name": "restricted", "catalogTag": "Security.Restricted"}]

    monkeypatch.chdir(tmp_path)
    run = lambda *args: CliRunner().invoke(cli.build_app(), list(args))
    assert "restricted" in run("list", "columns", "--classification", "restricted").output
    rejected = run("list", "columns", "--classification", "pii")  # the default list no longer applies
    assert rejected.exit_code == 2 and "open, restricted" in " ".join(rejected.output.replace("│", " ").split())


def test_an_unknown_classification_is_compiles_to_report_not_lints(tmp_path):
    columns = [{"name": "k", "logicalType": "string", "classification": "restricted"}]
    _repo(tmp_path, {"contracts/a.yaml": _contract("a", columns=columns)})
    assert lint(tmp_path / "contracts/a.yaml").passed  # lint sees one file, and the vocabulary is the repository's
    assert _codes(run_project_checks(tmp_path)) == [("contracts/a.yaml", "unknown-classification")]


def test_the_default_classifications_are_in_every_manifest():
    compiled = manifest.compile_manifest(EXAMPLE)
    assert {c["name"]: c["catalogTag"] for c in compiled["classifications"]} == governance.DEFAULT_CLASSIFICATIONS


def test_a_manifest_from_before_it_held_classifications_gets_the_default():
    assert [c["name"] for c in Project(manifest={}).classifications()] == list(governance.DEFAULT_CLASSIFICATIONS)


def test_a_manifest_from_before_it_held_classifications_still_matches_the_schema():
    compiled = manifest.compile_manifest(EXAMPLE)
    del compiled["classifications"]
    jsonschema.Draft202012Validator(json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())).validate(compiled)


# --- the project file ---------------------------------------------------------------------


def _moved_repo(root: Path) -> Path:
    """The example, with its folders moved and renamed by a project file."""
    shutil.copytree(EXAMPLE / "contracts", root / "data_contracts" / "tables")
    shutil.copytree(EXAMPLE / "governance", root / "meta")
    for path in (root / "data_contracts" / "tables").rglob("*.yaml"):
        path.write_text(path.read_text().replace("contracts/", "data_contracts/tables/"))
    (root / "fluvv.yaml").write_text("apiVersion: fluvv/v1\nkind: Project\ncontracts: data_contracts/tables\ngovernance: meta\n")
    return root


def test_a_project_file_names_the_top_level_folders(tmp_path, monkeypatch):
    repo = _moved_repo(tmp_path)
    assert project_layout(repo).contracts == "data_contracts/tables"
    assert len(governance.load_contracts(repo)) == 10  # also what the engine's license counts

    compiled = manifest.compile_manifest(repo)
    assert len(compiled["contracts"]) == 10 and compiled["domains"][0]["name"] == "sales"
    assert compiled["sourceHash"] == manifest.compile_manifest(repo)["sourceHash"]
    manifest.check_fresh(compiled, repo)

    monkeypatch.chdir(repo)
    linted = CliRunner().invoke(cli.build_app(), ["lint"])
    assert linted.exit_code == 0, linted.output
    assert linted.output.count("PASSED") == 14  # 10 contracts, 3 governance files and the project file


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_the_project_file_settles_the_root_of_a_file_in_its_folders(tmp_path):
    (tmp_path / "fluvv.yaml").write_text("apiVersion: fluvv/v1\nkind: Project\ncontracts: pipelines/contracts\n")
    # Without the project file, the nested folder named `contracts` would win.
    assert find_repo_root(_touch(tmp_path / "pipelines/contracts/x/y/contracts/t.yaml")) == tmp_path
    assert find_repo_root(_touch(tmp_path / "docs/examples/minimal.yaml")) == tmp_path
    assert find_repo_root(tmp_path / "fluvv.yaml") == tmp_path


def test_a_file_called_fluvv_yaml_is_only_a_project_file_if_it_says_so(tmp_path):
    """A table or a domain can be called fluvv."""
    (tmp_path / "contracts/silver/sales").mkdir(parents=True)
    (tmp_path / "contracts/silver/sales/fluvv.yaml").write_text(yaml.safe_dump(_contract("fluvv")))
    (tmp_path / "governance/domains").mkdir(parents=True)
    (tmp_path / "governance/domains/fluvv.yaml").write_text(yaml.safe_dump(_domain("fluvv")))
    assert find_repo_root(_touch(tmp_path / "contracts/silver/sales/cars.yaml")) == tmp_path
    assert find_repo_root(_touch(tmp_path / "governance/domains/sales.yaml")) == tmp_path


def test_a_broken_project_file_still_marks_the_root_so_it_gets_reported(tmp_path):
    (tmp_path / "fluvv.yaml").write_text("contracts: [unclosed\n")
    assert find_repo_root(_touch(tmp_path / "specs/a.yaml")) == tmp_path


def test_an_outer_project_file_doesnt_claim_a_repository_nested_inside_it(tmp_path):
    (tmp_path / "fluvv.yaml").write_text("apiVersion: fluvv/v1\nkind: Project\ncontracts: specs\n")
    assert find_repo_root(_touch(tmp_path / "vendor/sales/contracts/a/b/t.yaml")) == tmp_path / "vendor/sales"
    assert find_repo_root(_touch(tmp_path / "vendor/other/contracts/t.yaml")) == tmp_path / "vendor/other"


def test_a_repository_in_a_folder_named_like_a_top_level_one(tmp_path):
    """The free layouts look like the example's: contracts/<layer>/<domain>/<table>.yaml
    with `contracts` as the layer, or governance/<kind>/<file> with `contracts` as the kind."""
    clone = tmp_path / "contracts"
    assert find_repo_root(_touch(clone / "contracts/sales/orders.yaml")) == clone
    other = tmp_path / "x" / "governance"
    assert find_repo_root(_touch(other / "contracts/orders.yaml")) == other
    assert find_repo_root(_touch(clone / "contracts/bronze/sales/cars.yaml")) == clone  # the example's layout, as ever


def test_a_source_that_doesnt_resolve_says_where_it_looked(tmp_path):
    repo = _repo(tmp_path, {"contracts/a.yaml": _contract("a", reads="contracts/gone.yaml")})
    with pytest.raises(UnresolvableSourceError, match="fluvv.yaml"):
        load_extension(repo / "contracts/a.yaml")


@pytest.mark.parametrize("folder", ["../elsewhere", "/absolute", ".", "a/../b"])
def test_a_project_folder_must_be_inside_the_repository(tmp_path, folder):
    path = tmp_path / "fluvv.yaml"
    path.write_text(f"apiVersion: fluvv/v1\nkind: Project\ncontracts: {folder}\n")
    assert not lint(path).passed
    _repo(tmp_path, {"contracts/a.yaml": _contract("a")})
    assert _codes(run_project_checks(tmp_path)) == [("fluvv.yaml", "fails-lint")]


def test_a_mistyped_project_folder_is_refused_instead_of_checking_nothing(tmp_path, monkeypatch):
    """A folder that isn't there would otherwise compile as zero contracts,
    and CI would go green having looked at none of them."""
    _repo(tmp_path, {"contracts/a.yaml": _contract("a", domain="nowhere"), "governance/g.yaml": _glossary(["A"])})
    (tmp_path / "fluvv.yaml").write_text("apiVersion: fluvv/v1\nkind: Project\ncontracts: contract\n")
    issues = run_project_checks(tmp_path)
    assert _codes(issues) == [("fluvv.yaml", "invalid-project-folder")]
    assert "'contract'" in issues[0].message

    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(cli.build_app(), ["compile"]).exit_code == 1


@pytest.mark.parametrize("folders", ["contracts: shared\ngovernance: shared", "contracts: shared\ngovernance: shared/sub"])
def test_project_folders_cant_overlap(tmp_path, folders):
    """Each folder's YAML is read as that kind of file."""
    (tmp_path / "shared/sub").mkdir(parents=True)
    (tmp_path / "fluvv.yaml").write_text(f"apiVersion: fluvv/v1\nkind: Project\n{folders}\n")
    assert _codes(run_project_checks(tmp_path)) == [("fluvv.yaml", "invalid-project-folder")]


def test_changing_the_project_file_makes_a_manifest_stale(tmp_path):
    repo = _moved_repo(tmp_path)
    compiled = manifest.compile_manifest(repo)
    (repo / "fluvv.yaml").write_text((repo / "fluvv.yaml").read_text() + "# moved\n")
    with pytest.raises(manifest.StaleManifestError):
        manifest.check_fresh(compiled, repo)


def test_a_repository_without_a_project_file_keeps_its_hash():
    """Upgrading fluvv must not make a prebuilt manifest stale."""
    root = EXAMPLE.resolve()
    assert not (root / "fluvv.yaml").exists()
    digest = hashlib.sha256()
    for path in sorted([*root.glob("contracts/**/*.yaml"), *(root / "governance").glob("**/*.yaml")]):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    assert manifest.source_hash(root) == f"sha256:{digest.hexdigest()}"


@pytest.mark.parametrize(
    "content, problem",
    [
        ("contracts: [unclosed\n", "invalid YAML"),
        ("- a\n", "isn't a YAML mapping"),
        ("apiVersion: fluvv/v1\nkind: Domain\n", "isn't one of ['Project']"),
        ("apiVersion: fluvv/v1\nkind: Project\ncontracts: 3\n", "contracts: 3 is not of type 'string'"),
    ],
)
def test_a_broken_project_file_is_reported_first_and_alone(tmp_path, content, problem):
    repo = _repo(tmp_path, {"contracts/a.yaml": _contract("a", domain="nowhere")})
    (repo / "fluvv.yaml").write_text(content)
    issues = run_project_checks(repo)
    assert _codes(issues) == [("fluvv.yaml", "fails-lint")]
    assert problem in issues[0].message


def test_lint_reports_a_broken_project_file_instead_of_crashing(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"contracts/a.yaml": _contract("a")})
    (repo / "fluvv.yaml").write_text("contracts: [unclosed\n")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(cli.build_app(), ["lint"])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "fluvv.yaml: FAILED" in result.output and "contracts/a.yaml: PASSED" in result.output


# --- export --------------------------------------------------------------------------------


def test_export_with_a_broken_project_file_says_so_in_one_line(tmp_path):
    repo = _repo(tmp_path, {"contracts/a.yaml": _contract("a")})
    (repo / "fluvv.yaml").write_text("contracts: [unclosed\n")
    result = CliRunner().invoke(cli.build_app(), ["export", str(repo / "contracts/a.yaml")])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "fluvv.yaml: invalid YAML" in result.output


def test_export_defaults_to_the_contracts_folder_and_takes_another(tmp_path, monkeypatch):
    repo = _moved_repo(tmp_path / "repo")
    contract = repo / "data_contracts/tables/silver/sales/cars.yaml"
    default = export_artifacts(contract)
    assert default.spark_schema_path == repo / "data_contracts/tables/spark_schemas/silver/sales/cars.py"
    assert default.ingestion_config_path.is_file()

    monkeypatch.chdir(repo)
    result = CliRunner().invoke(cli.build_app(), ["export", str(contract), "--out", "generated"])
    assert result.exit_code == 0, result.output
    assert (repo / "generated/spark_schemas/silver/sales/cars.py").is_file()
    assert (repo / "generated/ingestion_config/silver/sales/cars.json").is_file()
