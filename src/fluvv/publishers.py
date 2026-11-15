"""Metadata catalog adapters, found by the catalog's name.

A package that publishes contracts to a metadata catalog registers a module
under the `fluvv.publishers` entry point, named for its catalog:
`publish(contract, spark, repo_root)` does what
`Contract.publish(spark, to=<name>)` asks, and
`register(publish_app, prune_app)` adds its `fluvv publish <name>` and
`fluvv prune <name>` commands. So the open package never imports an
adapter, and a new one needs no release of it.
"""

from __future__ import annotations

import importlib
from importlib.metadata import EntryPoint, entry_points
from types import ModuleType

from fluvv.product import EngineRequiredError

GROUP = "fluvv.publishers"

KNOWN = {
    "openmetadata": ("fluvv_openmetadata", "fluvv-openmetadata", "https://fluvv.se/docs/openmetadata-integration"),
}
"""The adapters Fluvv makes, by catalog: their module, package and page.
Known only so that, without one, its catalog's name says what to
install."""


def installed() -> dict[str, EntryPoint]:
    """Every adapter registered, by its catalog's name."""
    return {ep.name: ep for ep in entry_points(group=GROUP)}


def unknown(name: str) -> str:
    """Why there's nothing to publish to `name` with: no adapter for it."""
    return f"no adapter for the metadata catalog '{name}'; installed: {', '.join(sorted(installed())) or 'none'}"


def _imports(package: str) -> bool:
    try:
        importlib.import_module(package)
    except ImportError:
        return False
    return True


def load(name: str, caller: str) -> ModuleType:
    """The adapter for the catalog `name`, or the error that says why it
    can't be had, naming `caller`."""
    found = installed()
    if name in found:
        try:
            return found[name].load()
        except ModuleNotFoundError as exc:
            # The adapter's own package missing means it isn't installed,
            # whatever its entry point says. Anything else it needs, such
            # as Fluvv Engine's Spark, keeps its own message, and so does a
            # module of its own missing from a package that imports: a bug.
            package = found[name].module.split(".")[0]
            if (exc.name or "").split(".")[0] != package or _imports(package):
                raise
    if name in KNOWN:
        module, package, page = KNOWN[name]
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
            raise EngineRequiredError(f"{caller} is part of {package}, which isn't installed; see {page}.") from None
        # Installed, but from before it registered itself here.
        raise EngineRequiredError(f"{caller} needs a newer {package} than the one installed; see {page}.")
    raise ValueError(unknown(name))
