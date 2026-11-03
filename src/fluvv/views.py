"""Plain-text views of a compiled manifest (fluvv.manifest): the tables
`fluvv list` prints and the detail `fluvv show` prints. Everything shown
comes from the manifest, so it's what any integration reading the same
manifest sees. Plain aligned text rather than a rich-text table, so the
output stays greppable and reads the same in a pipe or a CI log. See
docs/fluvv/SPEC.md Section 7.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fluvv.governance import LAYERS

EMPTY = "-"


def _cell(value: Any) -> str:
    if value is None or value == "" or value == []:
        return EMPTY
    text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    # One line per row, even for a definition written as a `|` or `>` YAML block.
    return " ".join(text.split())


def table(headers: list[str], rows: list[list[Any]]) -> str:
    """Columns padded to their widest cell. The last column isn't padded,
    so a long definition doesn't push the others apart."""
    cells = [headers, *([_cell(v) for v in row] for row in rows)]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers) - 1)]
    return "\n".join("  ".join([*(c.ljust(w) for c, w in zip(row, widths)), row[-1]]) for row in cells)


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else line for line in text.splitlines())


def _purpose(description: Any) -> str | None:
    # ODCS and ODPS descriptions are objects (purpose, usage, limitations);
    # a domain's is a plain string.
    return description.get("purpose") if isinstance(description, dict) else description


def _block(title: str, description: str | None, fields: list[tuple[str, Any]], detail: str | None = None) -> str:
    width = max(len(name) for name, _ in fields)
    lines = [title, *([_indent(description)] if description else []), ""]
    lines += [f"  {name.ljust(width)}  {_cell(value)}" for name, value in fields]
    if detail:
        lines += ["", _indent(detail)]
    return "\n".join(lines)


def _in_layer_order(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Bronze, silver, gold, the order data flows through them, rather than
    # the manifest's alphabetical bronze, gold, silver.
    return sorted(contracts, key=lambda c: (LAYERS.index(c["layer"]), c["id"]))


def domains(manifest: dict[str, Any]) -> str:
    rows = [
        [
            d["name"],
            d["owner"],
            sum(p["domain"] == d["name"] for p in manifest["dataProducts"]),
            sum(c["domain"] == d["name"] for c in manifest["contracts"]),
            sum(len(g["terms"]) for g in manifest["glossaries"] if g["domain"] == d["name"]),
        ]
        for d in manifest["domains"]
    ]
    return table(["DOMAIN", "OWNER", "PRODUCTS", "CONTRACTS", "TERMS"], rows)


def products(manifest: dict[str, Any], domain: str | None = None) -> str:
    rows = [
        [p["name"], p["domain"], p["owner"], len(p["contracts"])]
        for p in sorted(manifest["dataProducts"], key=lambda p: p["name"])
        if domain in (None, p["domain"])
    ]
    return table(["PRODUCT", "DOMAIN", "OWNER", "CONTRACTS"], rows)


def contracts(manifest: dict[str, Any], domain: str | None = None, layer: str | None = None, product: str | None = None) -> str:
    rows = [
        [c["id"], c["owner"], c["dataProducts"], c["freshness"]]
        for c in _in_layer_order(manifest["contracts"])
        if domain in (None, c["domain"]) and layer in (None, c["layer"]) and (product is None or product in c["dataProducts"])
    ]
    return table(["CONTRACT", "OWNER", "PRODUCTS", "FRESHNESS"], rows)


def terms(manifest: dict[str, Any], domain: str | None = None) -> str:
    rows = [
        [t["name"], g["domain"], t["definition"]]
        for g in manifest["glossaries"]
        if domain in (None, g["domain"])
        for t in g["terms"]
    ]
    return table(["TERM", "DOMAIN", "DEFINITION"], rows)


def _domain(d: dict[str, Any], manifest: dict[str, Any]) -> str:
    members = [c for c in manifest["contracts"] if c["domain"] == d["name"]]
    by_layer = ", ".join(f"{layer} {n}" for layer in LAYERS if (n := sum(c["layer"] == layer for c in members)))
    fields = [
        ("owner", d["owner"]),
        ("experts", d["experts"]),
        ("data products", sorted(p["name"] for p in manifest["dataProducts"] if p["domain"] == d["name"])),
        ("contracts", f"{len(members)} ({by_layer})" if members else None),
        ("glossary terms", sum(len(g["terms"]) for g in manifest["glossaries"] if g["domain"] == d["name"])),
    ]
    return _block(f"{d['name']} (domain)", d["description"], fields)


def _product(p: dict[str, Any], manifest: dict[str, Any]) -> str:
    members = _in_layer_order([c for c in manifest["contracts"] if c["id"] in p["contracts"]])
    fields = [("domain", p["domain"]), ("owner", p["owner"]), ("experts", p["experts"])]
    detail = table(["CONTRACT", "OWNER", "FRESHNESS"], [[c["id"], c["owner"], c["freshness"]] for c in members])
    return _block(f"{p['name']} (data product)", _purpose(p["description"]), fields, detail)


def _contract(c: dict[str, Any]) -> str:
    references = [
        f"{', '.join(r['from'])} -> {r['to']['contract']} ({', '.join(r['to']['columns'])})" for r in c["relationships"]
    ]
    fields = [
        ("file", c["path"]),
        ("domain", c["domain"]),
        ("layer", c["layer"]),
        ("owner", c["owner"]),
        ("experts", c["experts"]),
        ("data products", c["dataProducts"]),
        ("tags", c["tags"]),
        ("freshness", c["freshness"]),
        ("retention", c["retention"]),
        ("primary key", c["primaryKey"]),
        ("references", references),
        ("upstream", c["upstream"]),
        ("downstream", c["downstream"]),
    ]
    detail = table(
        ["COLUMN", "TYPE", "TERM", "CLASSIFICATION"],
        [[col["name"], col.get("logicalType"), col.get("businessName"), col.get("classification")] for col in c["columns"]],
    )
    return _block(f"{c['id']} (contract)", _purpose(c["description"]), fields, detail)


def _term(t: dict[str, Any], domain: str, manifest: dict[str, Any]) -> str:
    # A column's businessName names a term in its own domain's glossary.
    used_by = [
        [c["id"], col["name"]]
        for c in _in_layer_order(manifest["contracts"])
        if c["domain"] == domain
        for col in c["columns"]
        if col.get("businessName") == t["name"]
    ]
    fields = [("domain", domain), ("synonyms", t["synonyms"]), ("related terms", t["relatedTerms"])]
    detail = table(["USED BY", "COLUMN"], used_by) if used_by else None
    return _block(f"{t['name']} (glossary term)", t["definition"], fields, detail)


def show(manifest: dict[str, Any], name: str) -> str | None:
    """Every domain, data product, contract and glossary term called
    `name` (a contract also by its file path), each in full, or None if
    nothing is. Names can repeat across kinds, e.g. a data product named
    after its domain, so each match is shown."""
    path = Path(name).as_posix()
    shown = [
        *(_domain(d, manifest) for d in manifest["domains"] if d["name"] == name),
        *(_product(p, manifest) for p in manifest["dataProducts"] if p["name"] == name),
        *(_contract(c) for c in manifest["contracts"] if name == c["id"] or path == c["path"]),
        *(_term(t, g["domain"], manifest) for g in manifest["glossaries"] for t in g["terms"] if t["name"] == name),
    ]
    return "\n\n".join(shown) or None
