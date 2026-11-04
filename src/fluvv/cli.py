from __future__ import annotations

import importlib
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import Any, Callable

import typer

from fluvv import views
from fluvv.contract import RepoRootNotFoundError
from fluvv.export import export_artifacts
from fluvv.governance import LAYERS
from fluvv.lint import lint
from fluvv.manifest import DEFAULT_PATH as DEFAULT_MANIFEST_PATH, read_or_compile, write_manifest
from fluvv.semantic import ProjectCheckError

# Installed packages add commands to the `fluvv` CLI by registering a
# `register(app)` function under this entry-point group. Fluvv Engine adds
# ingest and the OpenMetadata commands this way, so the open package never
# imports it.
PLUGIN_GROUP = "fluvv.commands"

# Fluvv Engine's commands. Listed only so that, without the engine, they
# say where they come from instead of failing as unknown commands.
ENGINE_COMMANDS = ("ingest", "push-openmetadata", "prune-openmetadata", "setup-openmetadata")


def _yaml_files(path: Path) -> list[Path]:
    """What linting `path` means. A file is itself. A repository root (it
    holds contracts/ or governance/) is the YAML in those two folders, so
    `fluvv lint .` lints the project and nothing else in it. Any other
    folder is every YAML file below it, skipping hidden folders like .venv.
    The generated files under contracts/ are .py and .json, so they're
    never picked up."""
    if path.is_file():
        return [path]
    roots = [path / top for top in ("contracts", "governance") if (path / top).is_dir()] or [path]
    return sorted(
        found
        for root in roots
        for found in root.rglob("*.yaml")
        if not any(part.startswith(".") for part in found.relative_to(root).parts[:-1])
    )


def lint_command(
    paths: list[Path] = typer.Argument(
        None,
        exists=True,
        help="Contracts, governance files (Domain, Glossary, ODPS DataProduct), or folders of them. "
        "Default: the repository in the current directory.",
    ),
) -> None:
    """Lint contracts and governance files, each on its own. Exits non-zero
    if any fail. References between files are checked by `compile`."""
    all_passed = True
    for path in paths or [Path(".")]:
        files = _yaml_files(path)
        if not files:
            typer.echo(f"{path}: no .yaml files to lint", err=True)
            all_passed = False
        for contract_path in files:
            result = lint(contract_path)
            typer.echo(result.report())
            all_passed &= result.passed
    raise typer.Exit(code=0 if all_passed else 1)


def export_command(
    contract_paths: list[Path] = typer.Argument(..., help="One or more Fluvv contract YAML files."),
) -> None:
    """Export the Spark schema and ingestion_config JSON for one or more
    contracts. They're written under the repository's contracts/, so each
    contract must be inside one."""
    for contract_path in contract_paths:
        try:
            result = export_artifacts(contract_path)
        except RepoRootNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{contract_path}:")
        typer.echo(f"  {result.spark_schema_path}")
        typer.echo(f"  {result.ingestion_config_path}")


def compile_command(
    out: Path = typer.Option(DEFAULT_MANIFEST_PATH, "--out", help="Where to write the manifest."),
) -> None:
    """Compile every contract and governance file under the current
    directory (the repo root) into one manifest JSON, after checking that
    each file can be read and their references resolve. Exits non-zero,
    writing nothing, if not. `lint` checks each file in full."""
    try:
        typer.echo(write_manifest(Path("."), out))
    except ProjectCheckError as exc:
        typer.echo(exc.report())
        raise typer.Exit(code=1)


def _manifest_option() -> Any:
    return typer.Option(
        None,
        "--manifest",
        exists=True,
        dir_okay=False,
        help="Read this compiled manifest (`fluvv compile`) instead of compiling one from the repo. "
        "Refused if it's stale against the repo's contracts/governance files.",
    )


def _manifest(manifest_path: Path | None) -> dict[str, Any]:
    """What `list` and `show` read: compiled from the repo in the current
    directory, or the prebuilt manifest given with --manifest."""
    try:
        return read_or_compile(manifest_path, Path("."))
    except ProjectCheckError as exc:
        message = exc.report()
    except ValueError as exc:
        if manifest_path is None:
            raise
        message = str(exc)  # stale, or not a manifest this version reads; it names the file
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _check_known(value: str | None, known: list[str], option: str) -> None:
    # A filter naming nothing in the repo is far more likely a typo than a
    # question whose answer is an empty list.
    if value is not None and value not in known:
        choices = ", ".join(known) or "there are none"
        raise typer.BadParameter(f"{value!r} isn't one of: {choices}", param_hint=option)


def list_domains_command(manifest_path: Path = _manifest_option()) -> None:
    """List domains, with each one's owner and how many data products,
    contracts and glossary terms it has."""
    typer.echo(views.domains(_manifest(manifest_path)))


def list_products_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's data products."),
    manifest_path: Path = _manifest_option(),
) -> None:
    """List data products, with each one's domain, owner and number of
    contracts (its output ports)."""
    manifest = _manifest(manifest_path)
    _check_known(domain, [d["name"] for d in manifest["domains"]], "--domain")
    typer.echo(views.products(manifest, domain))


def list_contracts_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's contracts."),
    layer: str = typer.Option(None, "--layer", help="Only this layer's contracts: bronze, silver or gold."),
    product: str = typer.Option(None, "--product", help="Only this data product's contracts."),
    manifest_path: Path = _manifest_option(),
) -> None:
    """List contracts, bronze to gold, with each one's owner, data products
    and freshness SLA."""
    _check_known(layer, list(LAYERS), "--layer")
    manifest = _manifest(manifest_path)
    _check_known(domain, [d["name"] for d in manifest["domains"]], "--domain")
    _check_known(product, sorted(p["name"] for p in manifest["dataProducts"]), "--product")
    typer.echo(views.contracts(manifest, domain, layer, product))


def list_terms_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's glossary."),
    manifest_path: Path = _manifest_option(),
) -> None:
    """List glossary terms, with each one's domain and definition."""
    manifest = _manifest(manifest_path)
    _check_known(domain, [d["name"] for d in manifest["domains"]], "--domain")
    typer.echo(views.terms(manifest, domain))


def show_command(
    name: str = typer.Argument(..., help="A contract id or file, data product, domain or glossary term."),
    manifest_path: Path = _manifest_option(),
) -> None:
    """Show everything the repository says about one contract, data
    product, domain or glossary term."""
    manifest = _manifest(manifest_path)
    # A contract file by any path, as the other commands take one; the
    # manifest knows it by its path from the repo root, here.
    candidate, root = Path(name).resolve(), Path(".").resolve()
    if candidate.is_file() and candidate.is_relative_to(root):
        name = candidate.relative_to(root).as_posix()
    shown = views.show(manifest, name)
    if shown is None:
        typer.echo(f"nothing named {name!r}: no contract, data product, domain or glossary term has that name", err=True)
        raise typer.Exit(code=1)
    typer.echo(shown)


def _engine_installed() -> bool:
    try:
        importlib.import_module("fluvv_engine")
    except ModuleNotFoundError as exc:
        if exc.name != "fluvv_engine":
            raise  # installed, but broken: keep the traceback
        return False
    return True


def _engine_required(name: str) -> Callable[[], None]:
    def command() -> None:
        # The engine accepts any fluvv of its major version, so an installed
        # engine may predate a command this fluvv knows it for.
        reason = "needs a newer Fluvv Engine than the one installed" if _engine_installed() else "is part of Fluvv Engine, which isn't installed"
        typer.echo(f"`fluvv {name}` {reason}. See https://fluvv.se/engine.", err=True)
        raise typer.Exit(code=1)

    command.__doc__ = "Part of Fluvv Engine (not installed)."
    return command


def _print_version(show: bool) -> None:
    # An option callback, so it runs before typer asks for a subcommand.
    if show:
        typer.echo(f"fluvv {version('fluvv')}")
        raise typer.Exit()


def _plugin_registrars() -> list[Callable[[typer.Typer], None]]:
    return [ep.load() for ep in entry_points(group=PLUGIN_GROUP)]


def build_app() -> typer.Typer:
    app = typer.Typer(
        help="Fluvv tooling: lint, export and compile Fluvv contracts and governance files, and list what they define."
    )

    @app.callback()
    def _main(
        show_version: bool = typer.Option(
            False, "--version", is_eager=True, callback=_print_version, help="Print the installed fluvv version and exit."
        ),
    ) -> None:
        """(Callback present so `lint` stays an explicit subcommand as more get added.)"""

    app.command(name="lint")(lint_command)
    app.command(name="export")(export_command)
    app.command(name="compile")(compile_command)
    list_app = typer.Typer(help="List the repository's domains, data products, contracts or glossary terms.", no_args_is_help=True)
    list_app.command(name="domains")(list_domains_command)
    list_app.command(name="products")(list_products_command)
    list_app.command(name="contracts")(list_contracts_command)
    list_app.command(name="terms")(list_terms_command)
    app.add_typer(list_app, name="list")
    app.command(name="show")(show_command)
    for register in _plugin_registrars():
        register(app)

    registered = {info.name for info in app.registered_commands}
    for name in ENGINE_COMMANDS:
        if name not in registered:
            app.command(
                name=name,
                context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
            )(_engine_required(name))
    return app


app = build_app()

if __name__ == "__main__":
    app()
