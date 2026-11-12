"""One contract for every environment: `${VAR}` variables and the server a
run picks, by datacontract-cli's rules (SPEC.md Section 1.3)."""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from fluvv import Contract, Project, cli, export_artifacts
from fluvv.contract import load_extension
from fluvv.lint import lint
from fluvv.manifest import compile_manifest
from fluvv.product import EngineRequiredError
from fluvv.schema import SCHEMAS_DIR
from fluvv.semantic import ProjectCheckError, run_project_checks
from fluvv.variables import (
    UnknownServerError,
    UnresolvedVariableError,
    choose_server,
    environment,
    resolve_contract,
    resolve_variables,
)

LAKE = "abfss://lake@${STORAGE_ACCOUNT}.dfs.core.windows.net"
PROD_LAKE = "abfss://lake@stprod.dfs.core.windows.net"


def _contract(contract_id: str, servers: list[dict], source: dict, mode: str = "overwrite") -> dict:
    return {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": contract_id,
        "version": "0.1.0",
        "status": "active",
        "servers": servers,
        "schema": [{"name": contract_id.replace(".", "_"), "logicalType": "object", "properties": [{"name": "k", "logicalType": "string"}]}],
        "customProperties": [
            {"property": "sources", "value": [{"read": "spark_read_file", **source}]},
            {"property": "ingestion_config", "value": {"write": "spark_write_file", "mode": mode}},
        ],
    }


def _servers(table: str) -> list[dict]:
    """A laptop server first, so it's the default, and a cloud one."""
    return [
        {"server": "local", "type": "local", "path": f"./data/{table}", "format": "delta"},
        {"server": "lakehouse", "type": "azure", "location": f"{LAKE}/{table}", "format": "delta"},
    ]


def _bronze(servers: list[dict] | None = None) -> dict:
    source = {"id": "raw", "contract": None, "location": "${LANDING_ROOT:-./data/landing}/cars", "format": "csv"}
    return _contract("bronze.cars", servers or _servers("bronze/cars"), source, mode="append")


def _silver(servers: list[dict] | None = None) -> dict:
    return _contract("silver.cars", servers or _servers("silver/cars"), {"id": "bronze", "contract": "contracts/bronze/cars.yaml"})


def _repo(root: Path, files: dict[str, dict]) -> Path:
    for rel, doc in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for name in ("STORAGE_ACCOUNT", "LANDING_ROOT", "MAX"):
        monkeypatch.delenv(name, raising=False)
    root = _repo(tmp_path / "repo", {"contracts/bronze/cars.yaml": _bronze(), "contracts/silver/cars.yaml": _silver()})
    monkeypatch.chdir(root)
    return root


def _contract_entry(manifest: dict, contract_id: str) -> dict:
    return next(c for c in manifest["contracts"] if c["id"] == contract_id)


# --- the rules ------------------------------------------------------------------------------


def test_a_variable_takes_its_value_then_its_default_and_otherwise_fails():
    values = {"A": "x", "EMPTY": ""}
    assert resolve_variables("${A}/${B:-d}", variables=values) == "x/d"
    assert resolve_variables("${EMPTY:-d}", variables=values) == "d"  # empty counts as unset
    with pytest.raises(UnresolvedVariableError, match=r"Variable B referenced in here is not set") as raised:
        resolve_variables("${B}", "here", values)
    assert raised.value.name == "B"
    with pytest.raises(UnresolvedVariableError):
        resolve_variables("${EMPTY}", variables=values)
    assert resolve_variables(5, variables={}) == 5


def test_dotenv_fills_in_under_the_process_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("A=from-file\nB=from-file\n")
    (tmp_path / "below").mkdir()
    monkeypatch.chdir(tmp_path / "below")  # the nearest .env above the current directory
    monkeypatch.setenv("A", "from-process")
    monkeypatch.delenv("B", raising=False)
    values = environment()
    assert values["A"] == "from-process" and values["B"] == "from-file"
    assert "B" not in os.environ  # read, not loaded into the process


def test_the_chosen_server_comes_first_filled_in_and_documentation_stays_as_written():
    doc = _bronze()
    doc["description"] = {"purpose": "Cars as ${NOT_SET} delivers them"}
    doc["schema"][0]["quality"] = [{"type": "sql", "query": "SELECT COUNT(*) FROM t WHERE x = '${NOT_SET}'", "mustBe": "${MAX}"}]
    resolved = resolve_contract(doc, "lakehouse", {"STORAGE_ACCOUNT": "stprod", "MAX": "0"})
    assert resolved["servers"][0] == {"server": "lakehouse", "type": "azure", "location": f"{PROD_LAKE}/bronze/cars", "format": "delta"}
    assert resolved["servers"][1]["server"] == "local"
    assert resolved["customProperties"][0]["value"][0]["location"] == "./data/landing/cars"
    assert resolved["description"] == doc["description"]
    assert resolved["schema"][0]["quality"][0]["query"] == doc["schema"][0]["quality"][0]["query"]
    assert resolved["schema"][0]["quality"][0]["mustBe"] == 0  # a whole reference keeps the type it reads as
    assert doc["servers"][1]["location"] == f"{LAKE}/bronze/cars"  # the contract itself isn't changed


def test_only_the_chosen_server_needs_its_variables():
    resolved = resolve_contract(_bronze(), None, {})
    assert resolved["servers"][0]["path"] == "./data/bronze/cars"
    assert resolved["servers"][1]["location"] == f"{LAKE}/bronze/cars"


def test_a_server_is_the_one_named_the_only_one_or_the_first():
    doc = _bronze()
    assert choose_server(doc)["server"] == "local"
    assert choose_server(doc, "lakehouse")["server"] == "lakehouse"
    with pytest.raises(UnknownServerError, match=r"Server 'prod' not found in data contract. Available servers: lakehouse, local"):
        choose_server(doc, "prod")
    only = _bronze(servers=[_servers("bronze/cars")[0]])
    assert choose_server(only, "prod")["server"] == "local"


# --- compile ------------------------------------------------------------------------------


def test_compile_without_a_server_uses_each_contracts_first(repo):
    manifest = compile_manifest(repo)
    silver = _contract_entry(manifest, "silver.cars")
    assert silver["server"]["server"] == "local"
    assert silver["ingestion"]["sources"][0]["location"] == "./data/bronze/cars"
    bronze = _contract_entry(manifest, "bronze.cars")
    assert bronze["ingestion"]["sources"][0]["location"] == "./data/landing/cars"


def test_compile_on_a_server_holds_its_filled_in_values(repo, monkeypatch):
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    manifest = compile_manifest(repo, server="lakehouse")
    jsonschema.Draft202012Validator(json.loads((SCHEMAS_DIR / "manifest.schema.json").read_text())).validate(manifest)
    silver = _contract_entry(manifest, "silver.cars")
    assert silver["server"]["location"] == f"{PROD_LAKE}/silver/cars"
    assert silver["ingestion"]["sources"][0]["location"] == f"{PROD_LAKE}/bronze/cars"
    assert silver["odcs"] == _silver()  # as written, as datacontract-cli publishes it


def test_a_missing_variable_is_reported_by_name(repo):
    issues = run_project_checks(repo, server="lakehouse")
    assert {(i.path, i.code) for i in issues} == {
        ("contracts/bronze/cars.yaml", "unresolved-variable"),
        ("contracts/silver/cars.yaml", "unresolved-variable"),
    }
    assert all("STORAGE_ACCOUNT" in i.message for i in issues)


def test_a_server_no_contract_has_is_reported(repo):
    issues = run_project_checks(repo, server="prod")
    assert {i.code for i in issues} == {"unknown-server"}


def test_an_upstream_without_the_server_and_more_than_one_is_reported(tmp_path, monkeypatch):
    bronze = _bronze(servers=[{**s, "server": name} for s, name in zip(_servers("bronze/cars"), ("onprem", "lakehouse"))])
    silver = _silver(servers=[_servers("silver/cars")[0]])  # one server: used on any run
    root = _repo(tmp_path, {"contracts/bronze/cars.yaml": bronze, "contracts/silver/cars.yaml": silver})
    monkeypatch.chdir(root)
    with pytest.raises(UnknownServerError, match=r"source 'bronze'.*Server 'local' not found"):
        load_extension("contracts/silver/cars.yaml", server="local")


def test_without_a_server_every_contract_and_upstream_uses_its_first(tmp_path):
    # As before servers could be chosen: an engine from then runs this repository as compile describes it.
    bronze = _bronze(servers=[{**s, "server": name} for s, name in zip(_servers("bronze/cars"), ("onprem", "lakehouse"))])
    silver = _silver(servers=[_servers("silver/cars")[0]])
    root = _repo(tmp_path, {"contracts/bronze/cars.yaml": bronze, "contracts/silver/cars.yaml": silver})
    manifest = compile_manifest(root, variables={})
    assert _contract_entry(manifest, "silver.cars")["ingestion"]["sources"][0]["location"] == "./data/bronze/cars"


def test_an_upstream_with_one_server_is_read_on_any(tmp_path):
    root = _repo(tmp_path, {"contracts/bronze/cars.yaml": _bronze(servers=[_servers("bronze/cars")[0]]), "contracts/silver/cars.yaml": _silver()})
    manifest = compile_manifest(root, server="lakehouse", variables={"STORAGE_ACCOUNT": "stprod"})
    assert _contract_entry(manifest, "silver.cars")["ingestion"]["sources"][0]["location"] == "./data/bronze/cars"


def test_two_contracts_whose_filled_in_locations_meet_are_one_target(tmp_path):
    first = _contract("a", [{"server": "local", "type": "local", "path": "${ROOT_A}/t", "format": "delta"}], {"id": "raw", "contract": None, "location": "./in", "format": "csv"})
    second = _contract("b", [{"server": "local", "type": "local", "path": "${ROOT_B}/t", "format": "delta"}], {"id": "raw", "contract": None, "location": "./in", "format": "csv"})
    root = _repo(tmp_path, {"contracts/a.yaml": first, "contracts/b.yaml": second})
    assert not run_project_checks(root, variables={"ROOT_A": "./x", "ROOT_B": "./y"})
    issues = run_project_checks(root, variables={"ROOT_A": "./x", "ROOT_B": "./x"})
    assert {i.code for i in issues} == {"duplicate-target"}


def test_compile_takes_a_server_on_the_command_line(repo, monkeypatch):
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    result = CliRunner().invoke(cli.build_app(), ["compile", "--server", "lakehouse"])
    assert result.exit_code == 0, result.output
    written = json.loads((repo / "target/manifest.json").read_text())
    assert _contract_entry(written, "bronze.cars")["server"]["location"] == f"{PROD_LAKE}/bronze/cars"


# --- export, Contract and Project ---------------------------------------------------------------


def test_export_writes_the_filled_in_values(repo, monkeypatch):
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    result = export_artifacts(repo / "contracts/silver/cars.yaml", server="lakehouse")
    config = json.loads(result.ingestion_config_path.read_text())
    assert config["sources"][0]["location"] == f"{PROD_LAKE}/bronze/cars"
    assert result.spark_schema_path.is_file()

    cli_result = CliRunner().invoke(cli.build_app(), ["export", "contracts/bronze/cars.yaml", "--server", "lakehouse"])
    assert cli_result.exit_code == 0, cli_result.output


def test_export_with_a_missing_variable_says_which(repo):
    result = CliRunner().invoke(cli.build_app(), ["export", "contracts/bronze/cars.yaml", "--server", "lakehouse"])
    assert result.exit_code == 1 and "Variable STORAGE_ACCOUNT" in result.output


def test_contract_and_project_take_a_server(repo, monkeypatch):
    assert Contract("contracts/silver/cars.yaml").doc["servers"][0]["server"] == "local"
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    silver = Contract("contracts/silver/cars.yaml", server="lakehouse")
    assert silver.server == "lakehouse"
    assert silver.doc["servers"][0]["location"] == f"{PROD_LAKE}/silver/cars"
    assert load_extension("contracts/silver/cars.yaml", server="lakehouse").sources[0].location == f"{PROD_LAKE}/bronze/cars"
    assert Project(server="lakehouse").contract("silver.cars")["server"]["location"] == f"{PROD_LAKE}/silver/cars"


# --- lint -------------------------------------------------------------------------------------


def test_lint_checks_the_file_as_written(repo):
    doc = _bronze()
    doc["customProperties"][1]["value"]["mode"] = "${MODE:-append}"  # Fluvv's own enum, checked once filled in
    doc["customProperties"].append({
        "property": "transform",
        "value": {"engine": "pyspark-sql", "sql": "SELECT r.* FROM raw AS r JOIN ${LOOKUP} AS l ON r.k = l.k LIMIT ${N:-10}"},
    })
    (repo / "contracts/bronze/cars.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    result = lint(repo / "contracts/bronze/cars.yaml")
    assert result.passed, result.report()


def test_a_value_lint_defers_is_judged_by_compile_once_filled_in(repo):
    doc = _bronze()
    doc["customProperties"][1]["value"]["mode"] = "${MODE:-append}"
    (repo / "contracts/bronze/cars.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    assert lint(repo / "contracts/bronze/cars.yaml").passed
    assert not run_project_checks(repo, variables={})
    issues = run_project_checks(repo, variables={"MODE": "sometimes"})
    assert [(i.path, i.code) for i in issues] == [("contracts/bronze/cars.yaml", "invalid-variable-value")]
    assert "sometimes" not in issues[0].message  # a filled-in value may be a secret


def test_a_server_type_filled_in_wrong_is_reported(tmp_path):
    doc = _bronze(servers=[{"server": "local", "type": "${STYPE:-local}", "path": "./data/b", "format": "delta"}])
    root = _repo(tmp_path, {"contracts/bronze/cars.yaml": doc})
    assert not run_project_checks(root, variables={})
    assert {i.code for i in run_project_checks(root, variables={"STYPE": "bogus"})} == {"invalid-variable-value"}


def test_an_sla_holding_a_variable_is_judged_once_filled_in(repo):
    doc = _bronze()
    doc["slaProperties"] = [
        {"property": "freshness", "value": "${FRESH:-24}", "unit": "h"},
        {"property": "retention", "value": "${KEEP}", "unit": "d"},
    ]
    path = repo / "contracts/bronze/cars.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert lint(path).passed, lint(path).report()
    manifest = compile_manifest(repo, variables={"KEEP": "90"})
    assert _contract_entry(manifest, "bronze.cars")["retention"] == "P90D"  # a whole reference reads as a number
    assert {i.code for i in run_project_checks(repo, variables={"KEEP": "ninety"})} == {"invalid-variable-value"}


def test_a_contract_lints_and_says_its_id_without_its_variables(repo):
    contract = Contract("contracts/bronze/cars.yaml", server="lakehouse")
    assert contract.lint().passed and contract.id == "bronze.cars"
    with pytest.raises(UnresolvedVariableError, match="STORAGE_ACCOUNT"):
        contract.doc


def test_the_open_test_checks_the_table_on_its_filled_in_server(tmp_path, monkeypatch):
    (tmp_path / "landed").mkdir()
    (tmp_path / "landed/cars.csv").write_text("k\na\nb\n")
    servers = [{"server": "local", "type": "local", "path": "${LANDED}/cars.csv", "format": "csv"}]
    root = _repo(tmp_path / "repo", {"contracts/bronze/cars.yaml": _bronze(servers=servers)})
    monkeypatch.chdir(root)
    monkeypatch.setenv("LANDED", str(tmp_path / "landed"))
    run = Contract("contracts/bronze/cars.yaml").test()
    assert run.has_passed(), [(c.name, c.result, c.reason) for c in run.checks]


def test_an_engine_from_before_servers_refuses_a_contract_that_needs_them(repo, monkeypatch):
    engine = types.ModuleType("fluvv_engine.contract")
    engine.ingest = lambda contract, spark, register: "ingested"
    monkeypatch.setitem(sys.modules, "fluvv_engine", types.ModuleType("fluvv_engine"))
    monkeypatch.setitem(sys.modules, "fluvv_engine.contract", engine)
    with pytest.raises(EngineRequiredError, match="newer Fluvv Engine"):
        Contract("contracts/bronze/cars.yaml").ingest(spark=None)  # two servers: it would write to the first, as written
    engine.RUNS_ON_CHOSEN_SERVER = True
    assert Contract("contracts/bronze/cars.yaml").ingest(spark=None) == "ingested"


def test_list_show_and_impact_take_a_server(repo, monkeypatch):
    runner = CliRunner()
    assert runner.invoke(cli.build_app(), ["list", "contracts"]).exit_code == 0
    failed = runner.invoke(cli.build_app(), ["list", "contracts", "--server", "lakehouse"])
    assert failed.exit_code == 1 and "STORAGE_ACCOUNT" in failed.output
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    for command in (["list", "contracts"], ["show", "silver.cars"], ["impact", "bronze.cars"]):
        result = runner.invoke(cli.build_app(), [*command, "--server", "lakehouse"])
        assert result.exit_code == 0, result.output


def test_a_servers_custom_properties_stay_as_written_in_the_manifest(repo, monkeypatch):
    doc = _bronze()
    doc["servers"][1]["customProperties"] = [{"property": "header", "value": "Bearer ${TOKEN}"}]
    (repo / "contracts/bronze/cars.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    monkeypatch.setenv("STORAGE_ACCOUNT", "stprod")
    monkeypatch.delenv("TOKEN", raising=False)  # only datacontract-cli's connection needs it
    manifest = compile_manifest(repo, server="lakehouse")
    server = _contract_entry(manifest, "bronze.cars")["server"]
    assert server["location"] == f"{PROD_LAKE}/bronze/cars"
    assert server["customProperties"] == [{"property": "header", "value": "Bearer ${TOKEN}"}]
    export_artifacts(repo / "contracts/silver/cars.yaml", server="lakehouse")


def test_an_engine_from_before_servers_still_runs_a_contract_with_two_servers_and_no_variables(tmp_path, monkeypatch):
    servers = [{"server": "local", "type": "local", "path": "./data/b", "format": "delta"},
               {"server": "lake", "type": "local", "path": "/mnt/lake/b", "format": "delta"}]
    root = _repo(tmp_path, {"contracts/bronze/cars.yaml": _contract("bronze.cars", servers, {"id": "raw", "contract": None, "location": "./in", "format": "csv"})})
    monkeypatch.chdir(root)
    engine = types.ModuleType("fluvv_engine.contract")
    engine.ingest = lambda contract, spark, register: "ingested"
    monkeypatch.setitem(sys.modules, "fluvv_engine", types.ModuleType("fluvv_engine"))
    monkeypatch.setitem(sys.modules, "fluvv_engine.contract", engine)
    assert Contract("contracts/bronze/cars.yaml").ingest(spark=None) == "ingested"  # it writes the first, which a run uses
    with pytest.raises(EngineRequiredError):
        Contract("contracts/bronze/cars.yaml", server="lake").ingest(spark=None)


def test_export_says_where_a_filled_in_value_is_wrong_without_saying_it(repo, monkeypatch):
    doc = _bronze()
    doc["customProperties"][1]["value"]["mode"] = "${MODE:-append}"
    (repo / "contracts/bronze/cars.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    monkeypatch.setenv("MODE", "sometimes")
    result = CliRunner().invoke(cli.build_app(), ["export", "contracts/bronze/cars.yaml"])
    assert result.exit_code == 1 and "ingestion_config/mode" in result.output and "sometimes" not in result.output


def test_lineage_needs_no_environment_and_finds_a_contract_whose_id_is_a_variable(repo):
    doc = _bronze()
    doc["id"] = "${LAYER:-bronze}.cars"
    (repo / "contracts/bronze/cars.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    assert Contract("contracts/bronze/cars.yaml").lineage(repo)["downstream"] == ["silver.cars"]
