"""The open `fluvv` package must install and run without Fluvv Engine.

Anything that reads or writes data through Spark lives in `fluvv_engine`,
and anything that talks to a metadata catalog in that catalog's adapter,
such as `fluvv_openmetadata`. These tests keep that line from eroding one
import at a time.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import fluvv
from fluvv import product

OPEN_PACKAGE = Path(fluvv.__file__).resolve().parent
# The open package's own tests too: they must pass where only fluvv is installed.
OPEN_TESTS = Path(__file__).resolve().parent
EXAMPLE = OPEN_TESTS / "fixtures" / "sales"

# Top-level modules only the engine and the catalog adapters may import.
ENGINE_ONLY = {"fluvv_engine", "fluvv_openmetadata", "pyspark", "delta", "unitycatalog", "httpx"}


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module an import statement names, skipping `if TYPE_CHECKING:`
    blocks: annotations may name engine types without importing them. Only
    the bare name is recognised; `if typing.TYPE_CHECKING:` is scanned like
    any other code, which errs on the strict side."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def test_open_package_never_imports_the_engine():
    offenders = [
        f"{path}:{line} imports {module}"
        for path in sorted([*OPEN_PACKAGE.rglob("*.py"), *OPEN_TESTS.rglob("*.py")])
        for line, module in _imported_modules(ast.parse(path.read_text(), str(path)))
        if module.split(".")[0] in ENGINE_ONLY
    ]
    assert offenders == []


@pytest.fixture
def without_engine(monkeypatch):
    """Make `import fluvv_engine` fail, as it does where only `fluvv` is installed."""
    monkeypatch.setitem(sys.modules, "fluvv_engine", None)


def test_engine_methods_name_fluvv_engine_when_it_is_missing(without_engine):
    contract = fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml")
    with pytest.raises(product.EngineRequiredError, match="Fluvv Engine") as excinfo:
        contract.ingest(spark=None)
    assert "Contract.ingest()" in str(excinfo.value)


def test_test_checks_the_table_without_spark_or_the_engine(without_engine, tmp_path):
    """`.test()` with no Spark session is datacontract-cli's schema and
    quality checks, which read the table themselves."""
    table = tmp_path / "t.csv"
    table.write_text("k,v\na,1\nb,2\n")
    path = tmp_path / "t.yaml"
    path.write_text(f"""\
apiVersion: v3.1.0
kind: DataContract
id: bronze.d.t
name: d_t_bronze
version: 0.1.0
status: active
servers:
  - server: local
    type: local
    path: {table}
    format: csv
schema:
  - name: t
    properties:
      - name: k
        logicalType: string
        required: true
        unique: true
customProperties:
  - property: layer
    value: bronze
  - property: sources
    value:
      - id: landing
        contract: null
        location: {tmp_path}/landing
        format: csv
        read: spark_read_file
  - property: ingestion_config
    value:
      write: spark_write_file
""")
    contract = fluvv.Contract(path)
    assert contract.test().has_passed()
    table.write_text("k,v\na,1\na,2\n")
    failed = [c.name for c in contract.test().checks if c.result == "failed"]
    assert failed == ["Check that unique field k has no duplicate values"]
    with pytest.raises(product.EngineRequiredError, match="Fluvv Engine"):
        contract.test(spark=object())  # the recording, freshness-checking test is the engine's


def test_engine_commands_name_fluvv_engine_when_it_is_missing(monkeypatch, without_engine):
    from typer.testing import CliRunner

    from fluvv import cli

    monkeypatch.setattr(cli, "_plugin_registrars", lambda: [])
    result = CliRunner().invoke(cli.build_app(), ["ingest", "contracts/silver/sales/cars.yaml"])
    assert result.exit_code == 1
    assert "part of Fluvv Engine, which isn't installed" in result.output


@pytest.fixture
def without_openmetadata(monkeypatch):
    """Make `import fluvv_openmetadata` fail, as it does where only the engine
    is installed. Its modules an earlier test imported go too: Python would
    find a cached `fluvv_openmetadata.publisher` without its package."""
    for name in [name for name in sys.modules if name.startswith("fluvv_openmetadata.")] + ["fluvv_openmetadata"]:
        monkeypatch.setitem(sys.modules, name, None)


def test_publishing_to_openmetadata_names_its_adapter_when_it_is_missing(monkeypatch, without_openmetadata):
    """It's a package of its own beside the engine, so it's named, not the engine."""
    from typer.testing import CliRunner

    from fluvv import cli

    contract = fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml")
    with pytest.raises(product.EngineRequiredError, match="part of fluvv-openmetadata") as excinfo:
        contract.publish(spark=None, to="openmetadata")
    assert "Contract.publish(to='openmetadata')" in str(excinfo.value)
    with pytest.warns(DeprecationWarning, match="publish"), pytest.raises(product.EngineRequiredError, match="fluvv-openmetadata"):
        contract.push_to_openmetadata(spark=None)
    monkeypatch.setattr(cli, "_plugin_registrars", lambda: [])
    for command in (["publish", "openmetadata"], ["prune", "openmetadata"], *([name] for name in cli.OPENMETADATA_COMMANDS)):
        result = CliRunner().invoke(cli.build_app(), command)
        assert result.exit_code == 1, (command, result.output)
        assert "part of fluvv-openmetadata, which isn't installed" in result.output, command


class _Publisher:
    """An adapter's entry point, as importlib.metadata lists it."""

    def __init__(self, name, module, loaded=None, error=None):
        self.name, self.module, self.loaded, self.error = name, module, loaded, error

    def load(self):
        if self.error is not None:
            raise self.error
        return self.loaded


def _plain(output: str) -> str:
    """Typer's boxed error as one line of text."""
    import re

    return " ".join(re.sub(r"\x1b\[[0-9;]*m|[│╭╮╰╯─]", " ", output).split())


@pytest.mark.parametrize("installed, named", [([], "none"), (["datahub"], "datahub")])
def test_a_catalog_no_adapter_is_installed_for_is_refused_naming_those_that_are(monkeypatch, installed, named):
    import types

    from typer.testing import CliRunner

    from fluvv import cli, publishers

    def plugins(group):
        if group != publishers.GROUP:
            return []
        return [_Publisher(name, f"fluvv_{name}.publisher", types.SimpleNamespace(register=lambda publish, prune: None)) for name in installed]

    monkeypatch.setattr(publishers, "entry_points", plugins)
    monkeypatch.setattr(cli, "entry_points", plugins)
    refusal = f"no adapter for the metadata catalog 'nowhere'; installed: {named}"
    with pytest.raises(ValueError, match=refusal):
        fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml").publish(spark=None, to="nowhere")
    for verb in ("publish", "prune"):
        result = CliRunner().invoke(cli.build_app(), [verb, "nowhere"])
        assert result.exit_code == 2 and refusal in _plain(result.output), result.output


def test_an_adapter_missing_a_module_of_its_own_keeps_its_traceback(monkeypatch):
    """Its package imports, so it's broken, not missing: neither "isn't
    installed" nor "no adapter" would say what's wrong."""
    import types

    from fluvv import publishers

    monkeypatch.setitem(sys.modules, "fluvv_datahub", types.ModuleType("fluvv_datahub"))
    broken = _Publisher("datahub", "fluvv_datahub.publisher", error=ModuleNotFoundError("no fluvv_datahub.client", name="fluvv_datahub.client"))
    monkeypatch.setattr(publishers, "entry_points", lambda group: [broken])
    with pytest.raises(ModuleNotFoundError, match="fluvv_datahub.client"):
        fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml").publish(spark=None, to="datahub")


def test_the_deprecated_push_works_with_an_adapter_from_before_publishers(monkeypatch):
    """fluvv-openmetadata 1.0 has the name fluvv 3.0 hands over to and no
    publisher, so push_to_openmetadata() keeps working with it, while
    publish() says a newer one is needed."""
    import types

    from fluvv import publishers

    calls = []
    adapter = types.ModuleType("fluvv_openmetadata.contract")
    adapter.push_to_openmetadata = lambda contract, spark, repo_root: calls.append((contract.id, spark, repo_root)) or "report"
    monkeypatch.setitem(sys.modules, "fluvv_openmetadata", types.ModuleType("fluvv_openmetadata"))
    monkeypatch.setitem(sys.modules, "fluvv_openmetadata.contract", adapter)
    monkeypatch.setattr(publishers, "entry_points", lambda group: [])
    contract = fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml")
    with pytest.warns(DeprecationWarning, match="publish"):
        assert contract.push_to_openmetadata(spark="session", repo_root="repo") == "report"
    assert calls == [("silver.sales.cars", "session", "repo")]
    with pytest.raises(product.EngineRequiredError, match="needs a newer fluvv-openmetadata"):
        contract.publish(spark=None, to="openmetadata")


@pytest.fixture
def older_engine(monkeypatch):
    """An installed engine from before this fluvv: it has none of the
    methods or commands fluvv hands over."""
    import types

    monkeypatch.setitem(sys.modules, "fluvv_engine", types.ModuleType("fluvv_engine"))
    monkeypatch.setitem(sys.modules, "fluvv_engine.contract", types.ModuleType("fluvv_engine.contract"))


def test_an_older_engine_is_named_as_too_old_not_missing(monkeypatch, older_engine):
    from typer.testing import CliRunner

    from fluvv import cli

    with pytest.raises(product.EngineRequiredError, match="needs a newer Fluvv Engine"):
        fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml").read(spark=object())
    monkeypatch.setattr(cli, "_plugin_registrars", lambda: [])
    monkeypatch.setattr(cli, "_publisher_registrars", lambda: [])  # an adapter needs a newer engine than this
    result = CliRunner().invoke(cli.build_app(), ["ingest", "contracts/silver/sales/cars.yaml"])
    assert result.exit_code == 1
    assert "needs a newer Fluvv Engine" in result.output


def test_version_flag_prints_the_installed_version():
    from importlib.metadata import version

    from typer.testing import CliRunner

    from fluvv import cli

    result = CliRunner().invoke(cli.build_app(), ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"fluvv {version('fluvv')}"


def test_an_engine_that_cant_load_leaves_the_open_commands_working(monkeypatch):
    """Fluvv Engine installed without the Spark it needs: the open commands
    still run, and the engine's say what's missing."""
    import importlib

    from typer.testing import CliRunner

    from fluvv import cli

    missing = "Fluvv Engine runs on Spark, which isn't installed here: install fluvv-engine[local]"

    class _EntryPoint:
        def __init__(self, module, name=""):
            self.module, self.name = module, name

        def load(self):
            raise ModuleNotFoundError(missing, name="pyspark")

    real_import = importlib.import_module

    def import_module(name, *args, **kwargs):
        # The OpenMetadata adapter imports the engine, so it can't load either.
        if name in ("fluvv_engine", "fluvv_openmetadata"):
            raise ModuleNotFoundError(missing, name="pyspark")
        return real_import(name, *args, **kwargs)

    from fluvv import publishers

    def plugins(group):
        if group == publishers.GROUP:
            return [_EntryPoint("fluvv_openmetadata.publisher", "openmetadata")]
        return [_EntryPoint("fluvv_engine.cli"), _EntryPoint("fluvv_openmetadata.cli")]

    monkeypatch.setattr(cli, "entry_points", plugins)
    monkeypatch.setattr(publishers, "entry_points", plugins)
    monkeypatch.setattr(importlib, "import_module", import_module)
    linted = CliRunner().invoke(cli.build_app(), ["lint", str(EXAMPLE / "contracts/silver/sales/cars.yaml")])
    assert linted.exit_code == 0, linted.output
    for command in (
        ["ingest", "contracts/silver/sales/cars.yaml"], ["license", "report"], ["publish", "openmetadata"], ["push-openmetadata"],
    ):
        result = CliRunner().invoke(cli.build_app(), command)
        assert result.exit_code == 1 and "fluvv-engine[local]" in result.output, result.output
        assert ".." not in result.output
    # Not "isn't installed": the adapter is, and the engine's message says what to fix.
    with pytest.raises(ModuleNotFoundError, match=r"fluvv-engine\[local\]"):
        fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml").publish(spark=None, to="openmetadata")


def test_a_plugin_missing_a_module_of_its_own_keeps_its_traceback(monkeypatch):
    """A plugin whose package loads but lacks a module of its own is broken,
    not missing a dependency. `fluvv` stands in for its package, since it
    always loads here, as Fluvv Engine may not."""
    from fluvv import cli

    class _EntryPoint:
        module = "fluvv.plugin"

        def load(self):
            raise ModuleNotFoundError("no module named fluvv.plugin_missing", name="fluvv.plugin_missing")

    monkeypatch.setattr(cli, "entry_points", lambda group: [_EntryPoint()])
    with pytest.raises(ModuleNotFoundError):
        cli.build_app()
