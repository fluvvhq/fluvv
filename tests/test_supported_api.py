"""The API code building on fluvv may rely on within a major version
(README, Versioning). Fluvv Engine accepts any fluvv 1.x on the strength
of it, so a change these tests catch breaks installed engines: it waits for
2.0, unless it only adds (a parameter with a default), in which case
update the expected parameters here."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

import fluvv
from fluvv import cli, manifest

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"

# Each supported function's parameters as a caller sees them, without
# annotations: those don't change what a call can pass.
FUNCTIONS = {
    "fluvv.contract": {
        "find_repo_root": "(start)",
        "load_odcs_document": "(contract_path)",
        "parse_extension": "(doc, contract_path)",
        "resolve_extension": "(extension, repo_root)",
        "get_custom_property": "(doc, property_name)",
    },
    "fluvv.governance": {
        "load_contracts": "(repo_root='.')",
        "schema_object": "(doc)",
        "columns": "(doc)",
        "primary_key": "(doc)",
        "owner": "(doc)",
        "medallion_layer": "(doc)",
        "sla_entry": "(doc, property_name)",
        "retention_period": "(doc)",
        "freshness_period": "(doc)",
        "freshness_hours": "(doc)",
    },
    "fluvv.manifest": {
        "compile_manifest": "(repo_root='.')",
        "write_manifest": "(repo_root='.', out=None)",
        "load_manifest": "(path)",
        "check_fresh": "(manifest, repo_root='.')",
        "read_or_compile": "(path=None, repo_root='.')",
    },
    "fluvv.lineage": {"combine_sql": "(sources, transform)"},
}

PROJECT_METHODS = {
    "__init__": "(self, repo_root='.', manifest=None)",
    "domains": "(self)",
    "products": "(self, domain=None, owner=None)",
    "contracts": "(self, domain=None, layer=None, product=None, owner=None)",
    "terms": "(self, domain=None)",
    "classifications": "(self)",
    "columns": "(self, domain=None, contract=None, classification=None)",
    "domain": "(self, name)",
    "product": "(self, name)",
    "contract": "(self, ref)",
    "term": "(self, name, domain=None)",
    "impact": "(self, ref)",
}

EXCEPTIONS = {
    "fluvv.contract.RepoRootNotFoundError": FileNotFoundError,
    "fluvv.manifest.StaleManifestError": ValueError,
    "fluvv.semantic.ProjectCheckError": ValueError,
    "fluvv.product.EngineRequiredError": ImportError,
}

EXPORTS = {
    "Contract", "ExportResult", "IngestionConfig", "LintResult", "FluvvExtension",
    "Project", "Source", "Transform", "export_artifacts", "lint", "load_extension",
}


def _parameters(function) -> str:
    signature = inspect.signature(function)
    return str(signature.replace(
        parameters=[p.replace(annotation=p.empty) for p in signature.parameters.values()],
        return_annotation=signature.empty,
    ))


@pytest.mark.parametrize(
    "module, name, expected",
    [(module, name, expected) for module, functions in FUNCTIONS.items() for name, expected in functions.items()],
)
def test_supported_function_keeps_its_parameters(module, name, expected):
    assert _parameters(getattr(importlib.import_module(module), name)) == expected


@pytest.mark.parametrize("name, expected", PROJECT_METHODS.items())
def test_project_method_keeps_its_parameters(name, expected):
    assert _parameters(getattr(fluvv.Project, name)) == expected


@pytest.mark.parametrize("path, base", EXCEPTIONS.items())
def test_supported_exception_keeps_its_base(path, base):
    module, name = path.rsplit(".", 1)
    assert issubclass(getattr(importlib.import_module(module), name), base)


def test_top_level_exports_are_kept():
    assert EXPORTS <= set(fluvv.__all__)


def test_contract_keeps_path_doc_and_id():
    contract = fluvv.Contract(EXAMPLE / "contracts/silver/sales/cars.yaml")
    assert contract.path == EXAMPLE / "contracts/silver/sales/cars.yaml"
    assert contract.doc["id"] == contract.id == "silver.sales.cars"


def test_manifest_version_and_plugin_group_stay_put():
    # A new manifestVersion means a key removed or changed: a major release.
    assert manifest.MANIFEST_VERSION == "2"
    assert cli.PLUGIN_GROUP == "fluvv.commands"
