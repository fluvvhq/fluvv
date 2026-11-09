"""Generate the artifacts derived from a contract:
- <out>/spark_schemas/<mirrored path>.py  -- datacontract-cli export("spark")
- <out>/ingestion_config/<mirrored path>.json -- the sources/transform/
  ingestion_config customProperties, consumed by the ingestion framework
  at runtime. Fully resolved: a contract-backed source's location/format
  are filled in from its upstream contract, so the engine never needs to
  read another contract.

`<out>` defaults to the repository's contracts folder, and each output path
mirrors the contract's own path relative to that folder, e.g.
contracts/bronze/sales/cars.yaml -> contracts/spark_schemas/bronze/sales/cars.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datacontract.data_contract import DataContract

from fluvv.contract import find_repo_root, load_odcs_document, parse_extension, project_layout, resolve_extension


@dataclass
class ExportResult:
    """The files `export_artifacts` wrote for one contract.

    Attributes
    ----------
    contract_path : Path
        The contract they were generated from.
    spark_schema_path : Path
        The contract's schema as a PySpark `StructType`.
    ingestion_config_path : Path
        Its sources, transforms and `ingestion_config` as JSON, with each
        upstream contract's location and format filled in.
    """

    contract_path: Path
    spark_schema_path: Path
    ingestion_config_path: Path


def _relative_stem(contract_path: Path, contracts_dir: Path) -> Path:
    return contract_path.resolve().relative_to(contracts_dir.resolve()).with_suffix("")


def export_artifacts(
    contract_path: Path | str, repo_root: Path | str | None = None, out_dir: Path | str | None = None
) -> ExportResult:
    """Writes a contract's generated files, like `fluvv export <file>`.

    A Spark schema under `spark_schemas/` and a resolved ingestion config
    under `ingestion_config/`, each at the contract's own path relative to
    the contracts folder, e.g. `contracts/silver/sales/cars.yaml` gives
    `spark_schemas/silver/sales/cars.py`. Existing files are overwritten.

    Parameters
    ----------
    contract_path : Path or str
        The contract.
    repo_root : Path or str, optional
        The repository the contract is in. Found from `contract_path` if
        not given.
    out_dir : Path or str, optional
        Where the two folders go. The repository's contracts folder if not
        given.

    Returns
    -------
    ExportResult
        The paths written.

    Raises
    ------
    RepoRootNotFoundError
        If `repo_root` isn't given and `contract_path` isn't in a
        repository.

    Examples
    --------
    >>> from fluvv import export_artifacts
    >>> result = export_artifacts("contracts/silver/sales/cars.yaml")
    >>> result.spark_schema_path
    PosixPath('.../contracts/spark_schemas/silver/sales/cars.py')
    >>> result.ingestion_config_path
    PosixPath('.../contracts/ingestion_config/silver/sales/cars.json')
    """
    contract_path = Path(contract_path)
    repo_root = Path(repo_root) if repo_root is not None else find_repo_root(contract_path)
    contracts_dir = repo_root / project_layout(repo_root).contracts
    rel_stem = _relative_stem(contract_path, contracts_dir)
    out = Path(out_dir) if out_dir is not None else contracts_dir

    data_contract = DataContract(data_contract_file=str(contract_path))

    spark_schema_path = (out / "spark_schemas" / rel_stem).with_suffix(".py")
    spark_schema_path.parent.mkdir(parents=True, exist_ok=True)
    spark_schema_path.write_text(data_contract.export("spark"))

    doc = load_odcs_document(contract_path)
    extension = resolve_extension(parse_extension(doc, contract_path), repo_root)
    ingestion_config_path = (out / "ingestion_config" / rel_stem).with_suffix(".json")
    ingestion_config_path.parent.mkdir(parents=True, exist_ok=True)
    ingestion_config_path.write_text(extension.model_dump_json(by_alias=True, indent=2) + "\n")

    return ExportResult(
        contract_path=contract_path,
        spark_schema_path=spark_schema_path,
        ingestion_config_path=ingestion_config_path,
    )
