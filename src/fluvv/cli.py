from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Callable

import typer

from fluvv.contract import RepoRootNotFoundError
from fluvv.export import export_artifacts
from fluvv.lint import lint
from fluvv.manifest import DEFAULT_PATH as DEFAULT_MANIFEST_PATH, write_manifest
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
    their references resolve. Exits non-zero, writing nothing, if any
    don't. Run `lint` first: compile doesn't re-check each file's shape."""
    try:
        typer.echo(write_manifest(Path("."), out))
    except ProjectCheckError as exc:
        typer.echo(exc.report())
        raise typer.Exit(code=1)


def _engine_required(name: str) -> Callable[[], None]:
    def command() -> None:
        typer.echo(
            f"`fluvv {name}` is part of Fluvv Engine, which isn't installed. "
            "See https://fluvv.se/engine.",
            err=True,
        )
        raise typer.Exit(code=1)

    command.__doc__ = "Part of Fluvv Engine (not installed)."
    return command


def _plugin_registrars() -> list[Callable[[typer.Typer], None]]:
    return [ep.load() for ep in entry_points(group=PLUGIN_GROUP)]


def build_app() -> typer.Typer:
    app = typer.Typer(help="Fluvv tooling: lint, export and compile Fluvv contracts and governance files.")

    @app.callback()
    def _main() -> None:
        """(Callback present so `lint` stays an explicit subcommand as more get added.)"""

    app.command(name="lint")(lint_command)
    app.command(name="export")(export_command)
    app.command(name="compile")(compile_command)
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
