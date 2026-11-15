from __future__ import annotations

import importlib
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import Any, Callable

import typer
from typer.core import TyperGroup

from fluvv import publishers, views
from fluvv.contract import PROJECT_FILE, InvalidProjectFileError, Layout, RepoRootNotFoundError, project_layout
from fluvv.export import export_artifacts
from fluvv.lint import lint
from fluvv.manifest import DEFAULT_PATH as DEFAULT_MANIFEST_PATH, write_manifest
from fluvv.project import Project
from fluvv.semantic import ProjectCheckError
from fluvv.variables import InvalidVariableValueError, UnknownServerError, UnresolvedVariableError

# Installed packages add commands to the `fluvv` CLI by registering a
# `register(app)` function under this entry-point group. Fluvv Engine adds
# ingest and license this way, so the open package never imports it. A
# metadata catalog's adapter adds `fluvv publish <catalog>` and `fluvv
# prune <catalog>` through `fluvv.publishers` (fluvv/publishers.py), and its
# other commands, such as setup-openmetadata, through this group.
PLUGIN_GROUP = "fluvv.commands"

# The commands of the packages that add them, with each package's module,
# name and page. Listed only so that, without the package, they say where
# they come from instead of failing as unknown commands.
ENGINE_COMMANDS = ("ingest", "license")
# push- and prune-openmetadata are `publish openmetadata` and `prune
# openmetadata` by their older names, deprecated until fluvv 4.0.
OPENMETADATA_COMMANDS = ("push-openmetadata", "prune-openmetadata", "setup-openmetadata")
_PROVIDERS = (
    (ENGINE_COMMANDS, "fluvv_engine", "Fluvv Engine", "https://fluvv.se/engine"),
    (OPENMETADATA_COMMANDS, "fluvv_openmetadata", "fluvv-openmetadata", "https://fluvv.se/docs/openmetadata-integration"),
)


def _yaml_files(path: Path) -> list[Path]:
    """What linting `path` means. A file is itself. A repository root (it
    holds its contracts or governance folder) is the YAML in those two
    folders plus its project file, so `fluvv lint .` lints the project and
    nothing else in it. Any other folder is every YAML file below it,
    skipping hidden folders like .venv. Generated files are .py and .json,
    so they're never picked up, wherever `export --out` puts them."""
    if path.is_file():
        return [path]
    try:
        layout = project_layout(path)
    except InvalidProjectFileError:
        layout = Layout()  # linting the project file below is what reports this
    roots = [path / top for top in (layout.contracts, layout.governance) if (path / top).is_dir()]
    project_file = [path / PROJECT_FILE] if roots and (path / PROJECT_FILE).is_file() else []
    return sorted(
        project_file + [
            found
            for root in roots or [path]
            for found in root.rglob("*.yaml")
            if not any(part.startswith(".") for part in found.relative_to(root).parts[:-1])
        ]
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


_SERVER_OPTION_HELP = (
    "The server to use, by its `server` name, as datacontract-cli's --server takes it; "
    "its variables are filled in from the environment and .env. Default: each contract's first."
)


def export_command(
    contract_paths: list[Path] = typer.Argument(..., help="One or more Fluvv contract YAML files."),
    out: Path = typer.Option(
        None, "--out", help="Where to write the spark_schemas/ and ingestion_config/ folders. Default: the repository's target/ folder."
    ),
    server: str = typer.Option(None, "--server", help=_SERVER_OPTION_HELP),
) -> None:
    """Export the Spark schema and ingestion_config JSON for one or more
    contracts to the repository's target/ folder, at paths mirroring each
    contract's own under its contracts folder, so each contract must be
    inside one."""
    for contract_path in contract_paths:
        try:
            result = export_artifacts(contract_path, out_dir=out, server=server)
        except (RepoRootNotFoundError, UnresolvedVariableError, UnknownServerError, InvalidVariableValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{contract_path}:")
        typer.echo(f"  {result.spark_schema_path}")
        typer.echo(f"  {result.ingestion_config_path}")


def compile_command(
    out: Path = typer.Option(DEFAULT_MANIFEST_PATH, "--out", help="Where to write the manifest."),
    server: str = typer.Option(None, "--server", help=_SERVER_OPTION_HELP),
) -> None:
    """Compile every contract and governance file under the current
    directory (the repo root) into one manifest JSON, after checking that
    each file can be read and their references resolve. Exits non-zero,
    writing nothing, if not. `lint` checks each file in full."""
    try:
        typer.echo(write_manifest(Path("."), out, server))
    except ProjectCheckError as exc:
        typer.echo(exc.report())
        raise typer.Exit(code=1)


def _server_option() -> Any:
    return typer.Option(None, "--server", help=_SERVER_OPTION_HELP + " Not used with --manifest.")


def _manifest_option() -> Any:
    return typer.Option(
        None,
        "--manifest",
        exists=True,
        dir_okay=False,
        help="Read this compiled manifest (`fluvv compile`) instead of compiling one from the repo. "
        "Refused if it's stale against the repo's contracts/governance files.",
    )


def _project(manifest_path: Path | None, server: str | None = None) -> Project:
    """What `list`, `show` and `impact` read: compiled from the repo in the
    current directory, or the prebuilt manifest given with --manifest."""
    try:
        return Project(".", manifest_path, server)
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


def _owners(entries: list[dict[str, Any]]) -> list[str]:
    return sorted({e["owner"] for e in entries if e["owner"]})


def _repo_relative(name: str) -> str:
    # A contract file by any path, as the other commands take one; the
    # manifest knows it by its path from the repo root, here.
    candidate, root = Path(name).resolve(), Path(".").resolve()
    if candidate.is_file() and candidate.is_relative_to(root):
        return candidate.relative_to(root).as_posix()
    return name


def list_domains_command(manifest_path: Path = _manifest_option(), server: str = _server_option()) -> None:
    """List domains, with each one's owner and how many data products,
    contracts and glossary terms it has."""
    typer.echo(views.domains(_project(manifest_path, server)))


def list_products_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's data products."),
    owner: str = typer.Option(None, "--owner", help="Only data products this team owns."),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """List data products, with each one's domain, owner and number of
    contracts (its output ports)."""
    project = _project(manifest_path, server)
    _check_known(domain, [d["name"] for d in project.domains()], "--domain")
    _check_known(owner, _owners(project.products()), "--owner")
    typer.echo(views.products(project, domain, owner))


def list_contracts_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's contracts."),
    layer: str = typer.Option(None, "--layer", help="Only this layer's contracts."),
    product: str = typer.Option(None, "--product", help="Only this data product's contracts."),
    owner: str = typer.Option(None, "--owner", help="Only contracts this team owns."),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """List contracts, upstream first, with each one's owner, data products
    and freshness SLA."""
    project = _project(manifest_path, server)
    _check_known(layer, list(dict.fromkeys(c["layer"] for c in project.contracts() if c["layer"])), "--layer")
    _check_known(domain, [d["name"] for d in project.domains()], "--domain")
    _check_known(product, [p["name"] for p in project.products()], "--product")
    _check_known(owner, _owners(project.contracts()), "--owner")
    typer.echo(views.contracts(project, domain, layer, product, owner))


def list_terms_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's glossary."),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """List glossary terms, with each one's domain and definition."""
    project = _project(manifest_path, server)
    _check_known(domain, [d["name"] for d in project.domains()], "--domain")
    typer.echo(views.terms(project, domain))


def list_columns_command(
    domain: str = typer.Option(None, "--domain", help="Only this domain's columns."),
    contract: str = typer.Option(None, "--contract", help="Only this contract's columns, by id."),
    classification: list[str] = typer.Option(
        None, "--classification", help="Only columns classified so. Repeatable."
    ),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """List columns, upstream first, with each one's type, glossary term
    and classification: e.g. where personal data is, with --classification
    pii --classification sensitive-pii."""
    project = _project(manifest_path, server)
    for value in classification or []:
        _check_known(value, [c["name"] for c in project.classifications()], "--classification")
    _check_known(domain, [d["name"] for d in project.domains()], "--domain")
    _check_known(contract, [c["id"] for c in project.contracts()], "--contract")
    typer.echo(views.columns(project, domain, contract, classification or None))


def show_command(
    name: str = typer.Argument(..., help="A contract id or file, data product, domain or glossary term."),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """Show everything the repository says about one contract, data
    product, domain or glossary term."""
    project = _project(manifest_path, server)
    name = _repo_relative(name)
    shown = views.show(project, name)
    if shown is None:
        typer.echo(f"nothing named {name!r}: no contract, data product, domain or glossary term has that name", err=True)
        raise typer.Exit(code=1)
    typer.echo(shown)


def impact_command(
    ref: str = typer.Argument(..., help="A contract (id or file), or one of its columns: <contract id>.<column>."),
    manifest_path: Path = _manifest_option(), server: str = _server_option(),
) -> None:
    """List everything downstream of a contract or column, nearest first,
    with who owns it: who to tell before changing it."""
    project = _project(manifest_path, server)
    ref = _repo_relative(ref)
    shown = views.impact(project, ref)
    if shown is None:
        typer.echo(f"{ref!r} is neither a contract nor a column of one", err=True)
        raise typer.Exit(code=1)
    typer.echo(shown)


def _unavailable(module: str, package: str) -> str:
    """Why a command another package adds isn't here."""
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name == module:
            return f"is part of {package}, which isn't installed"
        # Installed, but something it needs isn't, such as Spark: an install
        # to fix, whose message says how. Other errors keep their traceback.
        return f"can't run: {exc}"
    # The package accepts any fluvv of its major version, so an installed
    # one may predate a command this fluvv knows it for.
    return f"needs a newer {package} than the one installed"


def _required(name: str, module: str, package: str, page: str) -> Callable[[], None]:
    def command() -> None:
        typer.echo(f"`fluvv {name}` {_unavailable(module, package).rstrip('.')}. See {page}.", err=True)
        raise typer.Exit(code=1)

    command.__doc__ = f"Part of {package}, which can't run here."
    return command


def _print_version(show: bool) -> None:
    # An option callback, so it runs before typer asks for a subcommand.
    if show:
        typer.echo(f"fluvv {version('fluvv')}")
        raise typer.Exit()


def _loads(package: str) -> bool:
    try:
        importlib.import_module(package)
    except ImportError:
        return False
    return True


def _loaded(group: str) -> list[Any]:
    loaded = []
    for ep in entry_points(group=group):
        try:
            loaded.append(ep.load())
        except ModuleNotFoundError as exc:
            # A plugin installed without something it needs, such as Fluvv
            # Engine without Spark, mustn't take the open commands down with
            # it; its own commands say what's missing (_required). A
            # module of its own, or of fluvv, missing from a package that
            # loads is a bug: its traceback stays.
            if (exc.name or "").split(".")[0] in (ep.module.split(".")[0], "fluvv") and _loads(ep.module.split(".")[0]):
                raise
            continue
    return loaded


def _plugin_registrars() -> list[Callable[[typer.Typer], None]]:
    return _loaded(PLUGIN_GROUP)


def _publisher_registrars() -> list[Callable[[typer.Typer, typer.Typer], None]]:
    return [publisher.register for publisher in _loaded(publishers.GROUP)]


# A stand-in takes any arguments, so it can say what's missing instead of
# complaining about them.
_STAND_IN = {"allow_extra_args": True, "ignore_unknown_options": True}


class _Catalogs(TyperGroup):
    """`fluvv publish` and `fluvv prune`: a catalog no adapter is installed
    for is refused as `Contract.publish` refuses it, naming those that are."""

    def resolve_command(self, ctx: typer.Context, args: list[str]):
        if args and not args[0].startswith("-") and self.get_command(ctx, args[0]) is None:
            raise typer.BadParameter(publishers.unknown(args[0]), ctx=ctx, param_hint="CATALOG")
        return super().resolve_command(ctx, args)


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
    list_app = typer.Typer(
        help="List the repository's domains, data products, contracts, glossary terms or columns.", no_args_is_help=True
    )
    list_app.command(name="domains")(list_domains_command)
    list_app.command(name="products")(list_products_command)
    list_app.command(name="contracts")(list_contracts_command)
    list_app.command(name="terms")(list_terms_command)
    list_app.command(name="columns")(list_columns_command)
    app.add_typer(list_app, name="list")
    app.command(name="show")(show_command)
    app.command(name="impact")(impact_command)
    publish_app = typer.Typer(
        cls=_Catalogs,
        help="Publish contracts and their run records to a metadata catalog, through its adapter.",
        no_args_is_help=True,
    )
    prune_app = typer.Typer(
        cls=_Catalogs,
        help="Remove from a metadata catalog what no contract produces anymore, through its adapter.",
        no_args_is_help=True,
    )
    for register in _publisher_registrars():
        register(publish_app, prune_app)
    for catalog, (module, package, page) in publishers.KNOWN.items():
        for verb, group in (("publish", publish_app), ("prune", prune_app)):
            if catalog not in {info.name for info in group.registered_commands}:
                group.command(name=catalog, context_settings=_STAND_IN)(_required(f"{verb} {catalog}", module, package, page))
    app.add_typer(publish_app, name="publish")
    app.add_typer(prune_app, name="prune")
    for register in _plugin_registrars():
        register(app)

    registered = {info.name for info in app.registered_commands} | {info.name for info in app.registered_groups}
    for names, module, package, page in _PROVIDERS:
        for name in names:
            if name not in registered:
                app.command(name=name, context_settings=_STAND_IN)(_required(name, module, package, page))
    return app


app = build_app()

if __name__ == "__main__":
    app()
