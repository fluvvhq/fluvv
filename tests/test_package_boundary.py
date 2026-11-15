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
    """Make `import fluvv_openmetadata` fail, as it does where only the engine is installed."""
    monkeypatch.setitem(sys.modules, "fluvv_openmetadata", None)


def test_the_openmetadata_push_names_its_adapter_when_it_is_missing(monkeypatch, without_openmetadata):
    """It's a package of its own beside the engine, so it's named, not the engine."""
    from typer.testing import CliRunner

    from fluvv import cli

    contract = fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml")
    with pytest.raises(product.EngineRequiredError, match="part of fluvv-openmetadata") as excinfo:
        contract.push_to_openmetadata(spark=None)
    assert "Contract.push_to_openmetadata()" in str(excinfo.value)
    monkeypatch.setattr(cli, "_plugin_registrars", lambda: [])
    for command in cli.OPENMETADATA_COMMANDS:
        result = CliRunner().invoke(cli.build_app(), [command])
        assert result.exit_code == 1
        assert "part of fluvv-openmetadata, which isn't installed" in result.output, command


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
        def __init__(self, module):
            self.module = module

        def load(self):
            raise ModuleNotFoundError(missing, name="pyspark")

    real_import = importlib.import_module

    def import_module(name, *args, **kwargs):
        # The OpenMetadata adapter imports the engine, so it can't load either.
        if name in ("fluvv_engine", "fluvv_openmetadata"):
            raise ModuleNotFoundError(missing, name="pyspark")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(cli, "entry_points", lambda group: [_EntryPoint("fluvv_engine.cli"), _EntryPoint("fluvv_openmetadata.cli")])
    monkeypatch.setattr(importlib, "import_module", import_module)
    linted = CliRunner().invoke(cli.build_app(), ["lint", str(EXAMPLE / "contracts/silver/sales/cars.yaml")])
    assert linted.exit_code == 0, linted.output
    for command in (["ingest", "contracts/silver/sales/cars.yaml"], ["license", "report"], ["push-openmetadata"]):
        result = CliRunner().invoke(cli.build_app(), command)
        assert result.exit_code == 1 and "fluvv-engine[local]" in result.output, result.output
        assert ".." not in result.output
    # Not "isn't installed": the adapter is, and the engine's message says what to fix.
    with pytest.raises(ModuleNotFoundError, match=r"fluvv-engine\[local\]"):
        fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml").push_to_openmetadata(spark=None)


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
