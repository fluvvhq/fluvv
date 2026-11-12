"""Variables in a contract, and the server a run picks, so one contract
describes its table in every environment (SPEC.md Section 1.3).

The rules are datacontract-cli's (ODCS v3.2.0's variables), so a contract
means the same to both: any string value may hold `${VAR}` or
`${VAR:-default}`; a value comes from the process environment or a `.env`
file in the current directory or the nearest one above it, and a variable
already set wins over `.env`; one that's unset or empty, with no default,
is an error. Values are filled in where a contract is used and never
written back, so lint checks the file as written. Only the chosen server is
filled in, and fields that document the contract, rather than build or test
its table, stay as written.
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Mapping

from dotenv import dotenv_values, find_dotenv

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
"""A reference: its name, and its default if it has one."""

_DEFERRED_FIELDS = frozenset({
    "authoritativeDefinitions", "businessName", "context", "description", "examples", "implementation",
    "query", "servers", "synonyms", "transformLogic", "transformSourceObjects",
})
"""Left as written, as datacontract-cli leaves them: documentation, not what
builds or tests a table, and a description holding `${SOME_TOKEN}` must not
turn into its value in a catalog. A quality `query` is datacontract-cli's to
fill in, after its own `${model}` tokens (from its 1.2; the version fluvv
pins runs it as written); servers are filled in once one is chosen."""

_TYPED_FIELDS = frozenset({"mustBe", "mustNotBe"})
"""Compared with what a query returns, so a value that's one whole reference
keeps the type its text reads as."""


_PLAIN_NUMBER = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")


class UnresolvedVariableError(ValueError):
    """A `${VAR}` reference names a variable that's unset or empty and has no default.

    Raised where a contract is used: compiled, exported, or a `Contract`'s
    `doc` read. Compile reports it as `unresolved-variable`. The message is
    datacontract-cli's.

    Parameters
    ----------
    name : str
        The variable.
    source : str, default ""
        Where the reference is, for the message.

    Attributes
    ----------
    name : str
        The variable.
    """

    def __init__(self, name: str, source: str = ""):
        self.name = name
        where = f" in {source}" if source else ""
        super().__init__(f"Variable {name} referenced{where} is not set. Set it in the environment or a .env file, or use ${{{name}:-default}}.")


class InvalidVariableValueError(ValueError):
    """A value a variable filled in isn't one its field allows.

    Raised by `export_artifacts`; compile reports it as
    `invalid-variable-value`. The message says where, never the value,
    which may be a secret, as datacontract-cli's does.
    """


class UnknownServerError(ValueError):
    """A run asked for a server that a contract with more than one doesn't have.

    Compile reports it as `unknown-server`. The message is datacontract-cli's.
    """


def environment() -> dict[str, str]:
    """The values variables take: the process environment, over a `.env` file.

    The `.env` file is the one in the current directory or the nearest one
    above it, as datacontract-cli finds it. It only fills in variables the
    environment doesn't set, and neither is changed.

    Returns
    -------
    dict
        Each variable's value.
    """
    path = find_dotenv(usecwd=True)
    from_file = {name: value for name, value in dotenv_values(path).items() if value is not None} if path else {}
    return {**from_file, **os.environ}


def contains_variables(value: Any) -> bool:
    """Whether `value` is a string holding at least one `${VAR}` reference."""
    return isinstance(value, str) and VARIABLE.search(value) is not None


def resolve_variables(value: Any, source: str = "", variables: Mapping[str, str] | None = None) -> Any:
    """`value` with every `${VAR}` reference replaced.

    Parameters
    ----------
    value : Any
        Returned unchanged unless it's a string.
    source : str, default ""
        Where the value is, for the error message.
    variables : Mapping, optional
        The values to use. `environment()` if not given.

    Returns
    -------
    Any
        The value with its references filled in.

    Raises
    ------
    UnresolvedVariableError
        If a reference has no value and no default.
    """
    if not isinstance(value, str):
        return value
    values = environment() if variables is None else variables

    def replace(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        if found := values.get(name):
            return found
        if default is not None:
            return default
        raise UnresolvedVariableError(name, source)

    return VARIABLE.sub(replace, value)


def choose_server(doc: dict[str, Any], name: str | None = None) -> dict[str, Any] | None:
    """The server a run uses, as written.

    The one called `name`, as datacontract-cli's `--server` picks it, or
    the contract's only server whatever it's called: a table with one place
    is there in every environment (datacontract-cli would refuse the name).
    Without a name, the first, since a table is written to one place. None
    if the contract has no servers and no name is asked for.

    Parameters
    ----------
    doc : dict
        The contract.
    name : str, optional
        The server's `server` name, as datacontract-cli's `--server` takes it.

    Returns
    -------
    dict or None
        The server.

    Raises
    ------
    UnknownServerError
        If `name` is given and the contract has more than one server, none
        of them called `name`.
    """
    servers = doc.get("servers") or []
    if name is None:
        return servers[0] if servers else None
    named = next((server for server in servers if server.get("server") == name), None)
    if named is not None:
        return named
    if len(servers) == 1:
        return servers[0]
    available = ", ".join(sorted(server["server"] for server in servers if server.get("server")))
    raise UnknownServerError(f"Server '{name}' not found in data contract. Available servers: {available}")


def resolve_server_variables(
    server: dict[str, Any], variables: Mapping[str, str] | None = None, custom_properties: bool = True
) -> dict[str, Any]:
    """A copy of `server` with the references in its values filled in.

    Its string fields and its custom properties' values, as datacontract-cli
    fills them in to connect. A `port` that fills in as digits becomes a
    number.

    Parameters
    ----------
    server : dict
        One of a contract's `servers`.
    variables : Mapping, optional
        The values to use. `environment()` if not given.
    custom_properties : bool, default True
        Fill in its custom properties too. datacontract-cli keeps API
        headers and the like there, which only a connection needs.

    Returns
    -------
    dict
        The server, filled in.

    Raises
    ------
    UnresolvedVariableError
        If a reference has no value and no default.
    """
    values = environment() if variables is None else variables
    label = f"server '{server.get('server')}'"
    filled = {
        key: resolve_variables(value, f"{label} {key}", values) if contains_variables(value) else copy.deepcopy(value)
        for key, value in server.items()
    }
    if isinstance(filled.get("port"), str) and filled["port"].isdigit():
        filled["port"] = int(filled["port"])
    if custom_properties and isinstance(filled.get("customProperties"), list):
        filled["customProperties"] = [
            {**prop, "value": _resolve(prop["value"], f"{label} custom property '{prop.get('property')}'", values, model=False)}
            if isinstance(prop, dict) and "value" in prop else prop
            for prop in filled["customProperties"]
        ]
    return filled


def resolve_contract(doc: dict[str, Any], server: str | None = None, variables: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A copy of a contract as a run on `server` uses it.

    The chosen server (see `choose_server`) comes first, filled in apart
    from its custom properties, which only datacontract-cli's connection
    needs (`resolve_server_variables` fills them in); the other servers
    follow as written, so their variables aren't needed. Every other value
    is filled in, except the fields that document the contract
    (`description`, `businessName` and the like) and quality queries, which
    stay as written, as datacontract-cli leaves them. Keys are never filled
    in. `doc` itself isn't changed.

    Parameters
    ----------
    doc : dict
        The contract as written.
    server : str, optional
        The server to use, by its `server` name. See `choose_server`.
    variables : Mapping, optional
        The values to use. `environment()` if not given.

    Returns
    -------
    dict
        The contract, filled in.

    Raises
    ------
    UnresolvedVariableError
        If a reference has no value and no default.
    UnknownServerError
        If the contract has more than one server and none called `server`.
    """
    values = environment() if variables is None else variables
    chosen = choose_server(doc, server)
    filled = _resolve(doc, "contract", values)
    if chosen is not None:
        others = [copy.deepcopy(other) for other in doc.get("servers") or [] if other is not chosen]
        filled["servers"] = [resolve_server_variables(chosen, values, custom_properties=False), *others]
    return filled


class _Recorder(dict):
    """Values that fill in anything, noting what was asked for."""

    def get(self, name, default=None):
        self[name] = "x"
        return "x"


def uses_variables(doc: dict[str, Any], server: str | None = None) -> bool:
    """Whether filling in `doc` for `server` would replace anything: a
    `${VAR}` outside the fields that stay as written, or in the chosen
    server."""
    recorder = _Recorder()
    resolve_contract(doc, server, recorder)
    return bool(recorder)


def with_defaults(node: Any) -> Any:
    """`node` with each `${VAR:-default}` read as its default, what the file
    says when nothing overrides it, for judging it as written. A `${VAR}`
    without a default stays."""
    if isinstance(node, dict):
        return {key: with_defaults(value) for key, value in node.items()}
    if isinstance(node, list):
        return [with_defaults(item) for item in node]
    if isinstance(node, str):
        return VARIABLE.sub(lambda match: match.group(0) if match.group(2) is None else match.group(2), node)
    return node


def _custom_property(prop: Any, source: str, values: Mapping[str, str]) -> Any:
    """An entry of `customProperties`: ODCS structure, apart from its `value`."""
    if not isinstance(prop, dict):
        return prop
    filled = _resolve({key: value for key, value in prop.items() if key != "value"}, source, values)
    if "value" in prop:
        filled["value"] = _resolve(prop["value"], f"{source}.value", values, model=False)
    return {key: filled[key] for key in prop}


def _resolve(node: Any, source: str, values: Mapping[str, str], model: bool = True, typed: bool = False) -> Any:
    """`node` filled in. `model` is the ODCS structure, where documentation
    fields stay as written; a custom property's value is any data, all of it
    filled in, as datacontract-cli does."""
    if isinstance(node, dict):
        filled = {}
        for key, value in node.items():
            where = f"{source}.{key}"
            if model and key == "implementation" and node.get("engine") == "datacontract-cli" and isinstance(value, dict) and "arguments" in value:
                # A custom check's arguments are inputs to the test, like a library metric's.
                filled[key] = {**copy.deepcopy(value), "arguments": _resolve(value["arguments"], f"{where}.arguments", values, model=False)}
            elif model and key in _DEFERRED_FIELDS:
                filled[key] = copy.deepcopy(value)
            elif model and key == "customProperties" and isinstance(value, list):
                filled[key] = [_custom_property(prop, f"{where}.{prop.get('property')}" if isinstance(prop, dict) else where, values) for prop in value]
            else:
                filled[key] = _resolve(value, where, values, model, typed=model and key in _TYPED_FIELDS)
        return filled
    if isinstance(node, list):
        return [_resolve(item, f"{source}[{index}]", values, model, typed) for index, item in enumerate(node)]
    resolved = resolve_variables(node, source, values)
    if not (typed and isinstance(node, str) and VARIABLE.fullmatch(node)):
        return resolved
    if _PLAIN_NUMBER.fullmatch(resolved):
        return float(resolved) if "." in resolved else int(resolved)
    if resolved in ("true", "false"):
        return resolved == "true"
    return resolved
