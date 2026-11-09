"""The `Contract` class: one Fluvv contract file, wrapped with the methods
that make it a Data Product.

`.lint()`, `.lineage()` and `.test()` without Spark run here, in the open
package. The methods that read or write data through Spark run in Fluvv
Engine (`fluvv_engine.contract`); here they only hand over to it, imported
on first use, so the open package installs and runs without Spark.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Iterable

from datacontract.data_contract import DataContract

from fluvv.contract import load_odcs_document, parse_extension
from fluvv.lineage import build_lineage_graph
from fluvv.lint import LintResult, lint
from fluvv.models import FluvvExtension

if TYPE_CHECKING:
    from datacontract.model.run import Run
    from fluvv_engine.openmetadata import SyncReport
    from pyspark.sql import DataFrame, SparkSession


class EngineRequiredError(ImportError):
    """A method that needs Fluvv Engine was called without it.

    Raised by the `Contract` methods that read or write data when only the
    open `fluvv` package is installed, or when the installed Fluvv Engine
    is too old to have the method. The message says which.

    See Also
    --------
    Contract : Says which of its methods need Fluvv Engine.
    """


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
    module = importlib.import_module("fluvv_engine.contract")
    # The engine accepts any fluvv of its major version, so this fluvv may
    # be newer than the installed engine and hand over a method it lacks.
    if not hasattr(module, method):
        raise EngineRequiredError(
            f"Contract.{method}() needs a newer Fluvv Engine than the one installed; see https://fluvv.se/engine."
        )
    return module


class Contract:
    """One contract file, with the methods that build, check and publish its table.

    Linting, lineage and checks without Spark run in the open `fluvv`
    package. The methods that read or write data need Fluvv Engine, and
    raise `EngineRequiredError` without it.

    Parameters
    ----------
    contract_path : Path or str
        The contract's YAML file.

    Attributes
    ----------
    id : str
        The contract's `id`.
    path : Path
        `contract_path`, as given.
    doc : dict
        The contract's parsed YAML.

    Raises
    ------
    FileNotFoundError
        If there's no file at `contract_path`.
    ValueError
        If its Fluvv properties are missing or malformed.

    See Also
    --------
    lint : Lints a file that may not load as a `Contract`.
    Project : A whole repository's contracts and governance.

    Examples
    --------
    >>> from fluvv import Contract
    >>> cars = Contract("contracts/silver/sales/cars.yaml")
    >>> cars.id
    'silver.sales.cars'
    >>> cars.doc["name"]
    'sales_cars_silver'
    """

    def __init__(self, contract_path: Path | str):
        self.path = Path(contract_path)
        self.doc = load_odcs_document(self.path)
        self.extension: FluvvExtension = parse_extension(self.doc, self.path)

    @property
    def id(self) -> str:
        """The contract's `id`, e.g. `silver.sales.cars`."""
        return self.doc["id"]

    def lint(self) -> LintResult:
        """Lints this contract on its own, like `fluvv lint <file>`.

        Checks the file against ODCS and Fluvv's properties without reading
        any other file. Whether its references resolve is checked when the
        repository compiles.

        Returns
        -------
        LintResult
            What lint found. `passed` says whether the file passed.

        See Also
        --------
        lint : The same check, given a path.
        Project : Compiles the repository, which checks references.

        Examples
        --------
        >>> from fluvv import Contract
        >>> result = Contract("contracts/silver/sales/cars.yaml").lint()
        >>> result.passed
        True
        >>> print(result.report())
        contracts/silver/sales/cars.yaml: PASSED
        """
        return lint(self.path)

    def lineage(self, repo_root: Path | str = ".") -> dict[str, list[str]]:
        """The contracts directly upstream and downstream of this one.

        Read from every contract's `sources[].contract` in the repository,
        so nothing has to have run and no catalog is asked.

        Parameters
        ----------
        repo_root : Path or str, default "."
            The repository whose contracts are read.

        Returns
        -------
        dict
            `{"upstream": [...], "downstream": [...]}`, each a list of
            contract ids.

        See Also
        --------
        Project.impact : Everything downstream, transitively, down to columns.

        Examples
        --------
        >>> from fluvv import Contract
        >>> Contract("contracts/silver/sales/cars.yaml").lineage()
        {'upstream': ['bronze.sales.cars'], 'downstream': ['gold.sales.cars', 'gold.sales.dealership_performance']}
        """
        graph = build_lineage_graph(repo_root)
        return graph.get(self.id, {"upstream": [], "downstream": []})

    # Fluvv Engine. Each method's implementation is its counterpart in
    # fluvv_engine.contract; what a caller can rely on is documented here.

    def ingest(self, spark: SparkSession, register: Iterable[str] = ()) -> DataFrame:
        """Builds the contract's table from its sources, like `fluvv ingest`.

        Needs Fluvv Engine. Reads each source, runs the transforms and
        writes the result the way `ingestion_config` says, then runs each
        catalog registration strategy in `register`. Every run, successful
        or not, adds a row to the contract's `ingestion_logs`, and a
        successful one adds its business metrics to `business_metrics`.

        Upstream contracts aren't run first: a source is read as its table
        is now. The steps are in
        [Running a contract](https://fluvv.se/docs/running-a-contract).

        Parameters
        ----------
        spark : SparkSession
            The session to run in, e.g. from `fluvv_engine.get_spark_session`.
        register : iterable of str, optional
            Catalog registration strategies to run after the write, by name,
            e.g. `"metastore_register"`.

        Returns
        -------
        DataFrame
            The result that was written.

        Raises
        ------
        EngineRequiredError
            If Fluvv Engine isn't installed.
        RuntimeError
            If this contract hasn't run before and the license refuses new
            ones. See
            [Installing Fluvv Engine](https://fluvv.se/docs/installing-fluvv-engine).

        Examples
        --------
        >>> from fluvv import Contract
        >>> from fluvv_engine import get_spark_session  # doctest: +SKIP
        >>> spark = get_spark_session()  # doctest: +SKIP
        >>> for path in ["contracts/bronze/sales/cars.yaml", "contracts/silver/sales/cars.yaml"]:
        ...     Contract(path).ingest(spark)  # doctest: +SKIP
        """
        return _engine("ingest").ingest(self, spark, register)

    def test(self, spark: SparkSession | None = None) -> Run:
        """Checks the contract's table against its schema and quality rules.

        Without `spark`, datacontract-cli runs the checks, with no Spark and
        nothing recorded. Given a session, Fluvv Engine runs the same
        checks, adds the freshness SLA, and records every check in the
        contract's `quality_results`. What the checks cover is in
        [Testing contracts](https://fluvv.se/docs/testing-contracts).

        Parameters
        ----------
        spark : SparkSession, optional
            A session for Fluvv Engine to run the checks in and record them.

        Returns
        -------
        datacontract.model.run.Run
            datacontract-cli's result: `checks` holds each check, and
            `has_passed()` says whether they all passed.

        Raises
        ------
        EngineRequiredError
            If `spark` is given and Fluvv Engine isn't installed.

        Examples
        --------
        >>> from fluvv import Contract
        >>> run = Contract("contracts/silver/sales/cars.yaml").test()  # doctest: +SKIP
        >>> run.has_passed()  # doctest: +SKIP
        True
        """
        if spark is None:
            return DataContract(data_contract_file=str(self.path)).test()
        return _engine("test").test(self, spark)

    def read(self, spark: SparkSession) -> DataFrame:
        """The contract's table, read from its `servers` location.

        Needs Fluvv Engine.

        Parameters
        ----------
        spark : SparkSession
            The session to read with.

        Returns
        -------
        DataFrame
            The table as it is now.

        Raises
        ------
        EngineRequiredError
            If Fluvv Engine isn't installed.

        Examples
        --------
        >>> from fluvv import Contract
        >>> Contract("contracts/gold/sales/cars.yaml").read(spark).show()  # doctest: +SKIP
        """
        return _engine("read").read(self, spark)

    def logs(self, spark: SparkSession) -> DataFrame:
        """The contract's `ingestion_logs`: one row per ingest, successful or not.

        Needs Fluvv Engine. The columns are in
        [Run record tables](https://fluvv.se/docs/run-record-tables).

        Parameters
        ----------
        spark : SparkSession
            The session to read with.

        Returns
        -------
        DataFrame
            Every run of this contract.

        Raises
        ------
        EngineRequiredError
            If Fluvv Engine isn't installed.

        See Also
        --------
        Contract.metrics_history : The business metrics each run computed.

        Examples
        --------
        >>> from fluvv import Contract
        >>> logs = Contract("contracts/gold/sales/cars.yaml").logs(spark)  # doctest: +SKIP
        >>> logs.orderBy("started_at").select("status", "rows_written").show()  # doctest: +SKIP
        """
        return _engine("logs").logs(self, spark)

    def metrics_history(self, spark: SparkSession) -> DataFrame:
        """The contract's `business_metrics`: each metric's value after every successful ingest.

        Needs Fluvv Engine. The columns are in
        [Run record tables](https://fluvv.se/docs/run-record-tables).

        Parameters
        ----------
        spark : SparkSession
            The session to read with.

        Returns
        -------
        DataFrame
            One row per metric per successful run.

        Raises
        ------
        EngineRequiredError
            If Fluvv Engine isn't installed.

        See Also
        --------
        Contract.logs : Every run, successful or not.

        Examples
        --------
        >>> from fluvv import Contract
        >>> Contract("contracts/gold/sales/dealership_performance.yaml").metrics_history(spark).show()  # doctest: +SKIP
        """
        return _engine("metrics_history").metrics_history(self, spark)

    def push_to_openmetadata(self, spark: SparkSession, repo_root: Path | str = ".") -> SyncReport:
        """Publishes this contract to OpenMetadata, like `fluvv push-openmetadata <file>`.

        Needs Fluvv Engine. Pushes the table's catalog entry, its lineage,
        the contract, its run history, profile, sample data and latest
        quality results, from the repository compiled on the spot and the
        contract's run records.
        A step that fails is reported rather than raised, so the others
        still run. What lands where is in
        [Pushing contracts](https://fluvv.se/docs/pushing-contracts).

        Parameters
        ----------
        spark : SparkSession
            The session to read the run records with.
        repo_root : Path or str, default "."
            The repository to compile.

        Returns
        -------
        SyncReport
            What was pushed: `ok` says whether every step succeeded, and
            `render()` returns the text `fluvv push-openmetadata` prints.

        Raises
        ------
        EngineRequiredError
            If Fluvv Engine isn't installed.
        ProjectCheckError
            If the repository doesn't compile.

        Examples
        --------
        >>> from fluvv import Contract
        >>> report = Contract("contracts/gold/sales/cars.yaml").push_to_openmetadata(spark)  # doctest: +SKIP
        >>> report.ok  # doctest: +SKIP
        True
        """
        return _engine("push_to_openmetadata").push_to_openmetadata(self, spark, repo_root)
