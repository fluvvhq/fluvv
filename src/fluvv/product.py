"""The `Contract` class: one Fluvv contract file, wrapped with the methods
that make it a Data Product.

`.lint()` and `.lineage()` run here, in the open package. The methods that
read or write data run in Fluvv Engine (`fluvv_engine.contract`); here they
only hand over to it, imported on first use, so the open package installs
and runs without Spark.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Iterable

from fluvv.contract import load_odcs_document, parse_extension
from fluvv.lineage import build_lineage_graph
from fluvv.lint import LintResult, lint
from fluvv.models import FluvvExtension

if TYPE_CHECKING:
    from datacontract.model.run import Run
    from fluvv_engine.openmetadata import SyncReport
    from pyspark.sql import DataFrame, SparkSession


class EngineRequiredError(ImportError):
    """A method that reads or writes data was called where only the open
    `fluvv` package is installed."""


def _engine(method: str) -> ModuleType:
    try:
        importlib.import_module("fluvv_engine")
    except ModuleNotFoundError as exc:
        # Only a missing engine becomes this error. An engine that is
        # installed but can't import its own dependencies keeps its
        # traceback.
        if exc.name != "fluvv_engine":
            raise
        raise EngineRequiredError(
            f"Contract.{method}() is part of Fluvv Engine, which isn't installed. "
            "The open fluvv package lints, compiles and exports contracts; "
            "see https://fluvv.se/engine to run them."
        ) from None
    return importlib.import_module("fluvv_engine.contract")


class Contract:
    """One Fluvv contract file, exposed as a Data Product."""

    def __init__(self, contract_path: Path | str):
        self.path = Path(contract_path)
        self.doc = load_odcs_document(self.path)
        self.extension: FluvvExtension = parse_extension(self.doc, self.path)

    @property
    def id(self) -> str:
        return self.doc["id"]

    def lint(self) -> LintResult:
        return lint(self.path)

    def lineage(self, repo_root: Path | str = ".") -> dict[str, list[str]]:
        """Upstream/downstream contract ids, derived on demand from every
        contract's own `sources[].contract` field -- no local or
        OpenMetadata round trip needed (fluvv/lineage.py)."""
        graph = build_lineage_graph(repo_root)
        return graph.get(self.id, {"upstream": [], "downstream": []})

    # Fluvv Engine. Each method's behaviour is documented on its
    # counterpart in fluvv_engine.contract.

    def ingest(self, spark: SparkSession, register: Iterable[str] = ()) -> DataFrame:
        """Builds the target table from its sources and transforms, then runs
        each catalog registration strategy in `register`."""
        return _engine("ingest").ingest(self, spark, register)

    def test(self, spark: SparkSession) -> Run:
        """Checks the target table against the contract and records the results."""
        return _engine("test").test(self, spark)

    def read(self, spark: SparkSession) -> DataFrame:
        return _engine("read").read(self, spark)

    def logs(self, spark: SparkSession) -> DataFrame:
        return _engine("logs").logs(self, spark)

    def metrics_history(self, spark: SparkSession) -> DataFrame:
        return _engine("metrics_history").metrics_history(self, spark)

    def push_to_openmetadata(self, spark: SparkSession, repo_root: Path | str = ".") -> SyncReport:
        """Pushes this contract's current state to OpenMetadata."""
        return _engine("push_to_openmetadata").push_to_openmetadata(self, spark, repo_root)
