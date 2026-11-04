"""Plain-text views of a repository's governance: the tables `fluvv list`
and `fluvv impact` print and the detail `fluvv show` prints, rendered from
what `fluvv.project.Project` returns. Everything shown comes from the
manifest, so it's what any integration reading the same manifest sees.
Plain aligned text rather than a rich-text table, so the output stays
greppable and reads the same in a pipe or a CI log. See docs/fluvv/SPEC.md
Section 7.1.
"""

from __future__ import annotations

from typing import Any, Iterable

from fluvv.governance import LAYERS
from fluvv.project import Project

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


def domains(project: Project) -> str:
    rows = [
        [
            d["name"],
            d["owner"],
            len(project.products(domain=d["name"])),
            len(project.contracts(domain=d["name"])),
            len(project.terms(domain=d["name"])),
        ]
        for d in project.domains()
    ]
    return table(["DOMAIN", "OWNER", "PRODUCTS", "CONTRACTS", "TERMS"], rows)


def products(project: Project, domain: str | None = None, owner: str | None = None) -> str:
    rows = [[p["name"], p["domain"], p["owner"], len(p["contracts"])] for p in project.products(domain, owner)]
    return table(["PRODUCT", "DOMAIN", "OWNER", "CONTRACTS"], rows)


def contracts(
    project: Project, domain: str | None = None, layer: str | None = None, product: str | None = None, owner: str | None = None
) -> str:
    rows = [[c["id"], c["owner"], c["dataProducts"], c["freshness"]] for c in project.contracts(domain, layer, product, owner)]
    return table(["CONTRACT", "OWNER", "PRODUCTS", "FRESHNESS"], rows)


def terms(project: Project, domain: str | None = None) -> str:
    return table(["TERM", "DOMAIN", "DEFINITION"], [[t["name"], t["domain"], t["definition"]] for t in project.terms(domain)])


def columns(
    project: Project, domain: str | None = None, contract: str | None = None, classification: Iterable[str] | None = None
) -> str:
    rows = [
        [col["contract"], col["name"], col.get("logicalType"), col.get("businessName"), col.get("classification")]
        for col in project.columns(domain, contract, classification)
    ]
    return table(["CONTRACT", "COLUMN", "TYPE", "TERM", "CLASSIFICATION"], rows)


def impact(project: Project, ref: str) -> str | None:
    found = project.impact(ref)
    if found is None:
        return None
    by_id = {c["id"]: c for c in project.contracts()}
    rows = [
        [f"{e['contract']}.{e['column']}" if e["column"] else e["contract"], e["via"], by_id[e["contract"]]["owner"], by_id[e["contract"]]["dataProducts"]]
        for e in found
    ]
    return table(["DOWNSTREAM", "VIA", "OWNER", "PRODUCTS"], rows)


def _domain(d: dict[str, Any], project: Project) -> str:
    members = project.contracts(domain=d["name"])
    by_layer = ", ".join(f"{layer} {n}" for layer in LAYERS if (n := sum(c["layer"] == layer for c in members)))
    fields = [
        ("owner", d["owner"]),
        ("experts", d["experts"]),
        ("data products", [p["name"] for p in project.products(domain=d["name"])]),
        ("contracts", f"{len(members)} ({by_layer})" if members else None),
        ("glossary terms", len(project.terms(domain=d["name"]))),
    ]
    return _block(f"{d['name']} (domain)", d["description"], fields)


def _product(p: dict[str, Any], project: Project) -> str:
    members = project.contracts(product=p["name"])
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


def _term(t: dict[str, Any], project: Project) -> str:
    # A column's businessName names a term in its own domain's glossary.
    used_by = [[col["contract"], col["name"]] for col in project.columns(domain=t["domain"]) if col.get("businessName") == t["name"]]
    fields = [("domain", t["domain"]), ("synonyms", t["synonyms"]), ("related terms", t["relatedTerms"])]
    detail = table(["USED BY", "COLUMN"], used_by) if used_by else None
    return _block(f"{t['name']} (glossary term)", t["definition"], fields, detail)


def show(project: Project, name: str) -> str | None:
    """Every domain, data product, contract and glossary term called
    `name` (a contract also by its file path), each in full, or None if
    nothing is. Names can repeat across kinds, e.g. a data product named
    after its domain, so each match is shown."""
    domain, product, contract = project.domain(name), project.product(name), project.contract(name)
    shown = [
        *([_domain(domain, project)] if domain else []),
        *([_product(product, project)] if product else []),
        *([_contract(contract)] if contract else []),
        *(_term(t, project) for t in project.terms() if t["name"] == name),
    ]
    return "\n\n".join(shown) or None
