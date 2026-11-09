"""The supported API's docstrings, which are its reference: `help()` shows
them, and the website's Python API pages are generated from them. Each
member says what it does in its first sentence and documents the
parameters its signature has, and every example runs, in a copy of the
example repository."""

from __future__ import annotations

import dataclasses
import doctest
import importlib
import inspect
import re
import shutil
from pathlib import Path

import pytest

import fluvv

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"

# The supported API (README, Versioning) that callers use directly; the
# functions only packages building on fluvv use are documented in place.
ERRORS = [
    "fluvv.product.EngineRequiredError",
    "fluvv.semantic.ProjectCheckError",
    "fluvv.manifest.StaleManifestError",
    "fluvv.contract.RepoRootNotFoundError",
]
EXAMPLE_MODULES = ["fluvv.product", "fluvv.project", "fluvv.lint", "fluvv.export", "fluvv.contract"]


def _resolve(path: str):
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def _members():
    """(name, object) for each documented member, methods under their class."""
    found = [(name, getattr(fluvv, name)) for name in fluvv.__all__] + [(path, _resolve(path)) for path in ERRORS]
    for name, obj in list(found):
        if inspect.isclass(obj):
            for attr, value in vars(obj).items():
                if not attr.startswith("_") and (inspect.isfunction(value) or isinstance(value, property)):
                    found.append((f"{name}.{attr}", value))
    return found


def _sections(doc: str) -> dict[str, list[str]]:
    """A numpydoc docstring's sections by heading: a line underlined with dashes."""
    lines = inspect.cleandoc(doc).splitlines()
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for i, line in enumerate(lines):
        if i + 1 < len(lines) and lines[i + 1].strip() and set(lines[i + 1].strip()) == {"-"}:
            current = line.strip()
            sections[current] = []
        elif not (line.strip() and set(line.strip()) == {"-"}):
            sections[current].append(line)
    return sections


MEMBERS = _members()


@pytest.mark.parametrize("name, obj", MEMBERS, ids=[name for name, _ in MEMBERS])
def test_member_says_what_it_does_in_its_first_sentence(name, obj):
    doc = inspect.getdoc(obj)
    assert doc, f"{name} has no docstring"
    summary = doc.split("\n\n")[0]
    assert summary.endswith(".") and not re.search(r"\.\s+[A-Z]", summary), f"{name}'s summary isn't one sentence: {summary!r}"


def _constructed(obj) -> bool:
    """A class callers create, so its parameters are theirs to know. Results
    and errors are only received: they document their attributes."""
    return "__init__" in vars(obj) and not dataclasses.is_dataclass(obj) and not issubclass(obj, BaseException)


@pytest.mark.parametrize(
    "name, obj",
    [(name, obj) for name, obj in MEMBERS if inspect.isfunction(obj) or inspect.isclass(obj) and _constructed(obj)],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_parameters_section_matches_the_signature(name, obj):
    signature = inspect.signature(obj.__init__ if inspect.isclass(obj) else obj)
    expected = [p for p in signature.parameters if p != "self"]
    documented = [line.split(" : ")[0] for line in _sections(inspect.getdoc(obj)).get("Parameters", []) if line and not line[0].isspace()]
    assert documented == expected


@pytest.mark.parametrize("module", EXAMPLE_MODULES)
def test_examples_run(module, tmp_path, monkeypatch):
    # A copy, since an example may write (export_artifacts).
    repo = tmp_path / "sales"
    shutil.copytree(EXAMPLE, repo)
    monkeypatch.chdir(repo)
    finder = doctest.DocTestFinder()
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS)
    tests = [t for t in finder.find(importlib.import_module(module)) if t.examples]
    for test in tests:
        runner.run(test)
    assert tests and runner.summarize(verbose=False).failed == 0
