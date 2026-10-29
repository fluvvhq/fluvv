"""The open `fluvv` package must install and run without Fluvv Engine.

Anything that reads or writes data through Spark, or talks to a catalog,
lives in `fluvv_engine`. These
tests keep that line from eroding one import at a time.
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

# Top-level modules only the engine may import.
ENGINE_ONLY = {"fluvv_engine", "pyspark", "delta", "unitycatalog", "httpx"}


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
  - property: medallionLayer
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


def test_engine_commands_name_fluvv_engine_when_it_is_missing(monkeypatch):
    from typer.testing import CliRunner

    from fluvv import cli

    monkeypatch.setattr(cli, "_plugin_registrars", lambda: [])
    result = CliRunner().invoke(cli.build_app(), ["ingest", "contracts/silver/sales/cars.yaml"])
    assert result.exit_code == 1
    assert "Fluvv Engine" in result.output


def test_version_flag_prints_the_installed_version():
    from importlib.metadata import version

    from typer.testing import CliRunner

    from fluvv import cli

    result = CliRunner().invoke(cli.build_app(), ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"fluvv {version('fluvv')}"
