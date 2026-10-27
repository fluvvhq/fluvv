"""Generate the artifacts derived from a contract:
- contracts/spark_schemas/<mirrored path>.py  -- datacontract-cli export("spark")
- contracts/ingestion_config/<mirrored path>.json -- the sources/transform/
  ingestion_config customProperties, consumed by the ingestion framework
  at runtime. Fully resolved: a contract-backed source's location/format
  are filled in from its upstream contract, so the engine never needs to
  read another contract.

Each output path mirrors the contract's own path relative to contracts/,
e.g. contracts/bronze/sales/cars.yaml -> contracts/spark_schemas/bronze/sales/cars.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datacontract.data_contract import DataContract

from fluvv.contract import find_repo_root, load_odcs_document, parse_extension, resolve_extension


@dataclass
class ExportResult:
    contract_path: Path
    spark_schema_path: Path
    ingestion_config_path: Path


def _relative_stem(contract_path: Path, repo_root: Path) -> Path:
    return contract_path.resolve().relative_to((repo_root / "contracts").resolve()).with_suffix("")


def export_artifacts(contract_path: Path | str, repo_root: Path | str | None = None) -> ExportResult:
    contract_path = Path(contract_path)
    repo_root = Path(repo_root) if repo_root is not None else find_repo_root(contract_path)
    rel_stem = _relative_stem(contract_path, repo_root)

    data_contract = DataContract(data_contract_file=str(contract_path))

    spark_schema_path = (repo_root / "contracts" / "spark_schemas" / rel_stem).with_suffix(".py")
    spark_schema_path.parent.mkdir(parents=True, exist_ok=True)
    spark_schema_path.write_text(data_contract.export("spark"))

    doc = load_odcs_document(contract_path)
    extension = resolve_extension(parse_extension(doc, contract_path), repo_root)
    ingestion_config_path = (repo_root / "contracts" / "ingestion_config" / rel_stem).with_suffix(".json")
    ingestion_config_path.parent.mkdir(parents=True, exist_ok=True)
    ingestion_config_path.write_text(extension.model_dump_json(by_alias=True, indent=2) + "\n")

    return ExportResult(
        contract_path=contract_path,
        spark_schema_path=spark_schema_path,
        ingestion_config_path=ingestion_config_path,
    )
