"""Governance: what each contract says about its own table, plus the
definitions that span tables -- domains, glossaries, Data Products -- each
in its own file under `governance/` (docs/fluvv/SPEC.md Section 5).

A contract only *references* those shared entities (`domain`,
`businessName`); it never defines them. So nothing here is inferred across
contracts: a term's definition is whatever its glossary says, a domain's
owner whatever its domain file says, a Data Product's members whatever its
ODPS output ports list. What *is* derived -- glossary synonyms from
physical column names -- is derived from facts, never a definition.

Shared by lint (checks on one file), compile (references between files)
and the OpenMetadata push. Spark-free
and network-free."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fluvv.contract import (
    LAYER_PROPERTIES,
    contract_files,
    get_custom_property,
    governance_files,
    load_odcs_document,
    project_layout,
)

DEFAULT_CLASSIFICATIONS = {
    "public": "PII.None",
    "internal": "PII.None",
    "pii": "PII.NonSensitive",
    "sensitive-pii": "PII.Sensitive",
}
"""Each allowed value of a column's ODCS `classification` and the tag it
becomes in a catalog, unless the repository defines its own list (SPEC.md
Section 5.4)."""
MEDALLION_LAYERS = ("bronze", "silver", "gold")
"""Not a rule any more, but what 1.x promised for these names keeps holding:
they sort in this order, and `--layer` takes them even where no contract has
them."""

DOMAIN_KIND = "Domain"
GLOSSARY_KIND = "Glossary"
CLASSIFICATIONS_KIND = "Classifications"
DATA_PRODUCT_KIND = "DataProduct"

_ISO_UNIT_BY_SLA_UNIT = {
    "d": "D", "day": "D", "days": "D",
    "w": "W", "week": "W", "weeks": "W",
    "m": "M", "month": "M", "months": "M",
    "y": "Y", "yr": "Y", "year": "Y", "years": "Y",
}
_FRESHNESS_UNITS = {
    "h": "H", "hr": "H", "hour": "H", "hours": "H",
    "d": "D", "day": "D", "days": "D",
    "w": "W", "week": "W", "weeks": "W",
}
"""Freshness is measured in hours to weeks. `m` is deliberately absent:
in retention it means months, and minutes/months must not be confused."""
_HOURS_BY_FRESHNESS_UNIT = {"H": 1, "D": 24, "W": 24 * 7}


# --- contracts (table-scoped) ---------------------------------------------


def load_contracts(repo_root: Path | str = ".") -> dict[Path, dict[str, Any]]:
    """Every contract under `repo_root`, keyed by resolved path."""
    return {path.resolve(): load_odcs_document(path) for path in contract_files(repo_root)}


def medallion_layer(doc: dict[str, Any]) -> str | None:
    """The contract's layer label, if it has one (`fluvv.contract.
    LAYER_PROPERTIES`). Keeps this name because it's supported API."""
    for name in LAYER_PROPERTIES:
        value = get_custom_property(doc, name)
        if value is not None:
            return value
    return None


def schema_object(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["schema"][0]


def columns(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return schema_object(doc).get("properties", [])


def team_members(doc: dict[str, Any], role: str) -> list[str]:
    """Usernames with `role` in the contract's team. ODCS v3.1.0's team
    object (`{name, members}`) is the profile's form; the deprecated v3.0
    array is still read so an unmigrated contract lints instead of crashing."""
    team = doc.get("team")
    members = (team.get("members") or []) if isinstance(team, dict) else (team or [])
    return [m["username"] for m in members if m.get("role") == role]


def owner(doc: dict[str, Any]) -> str | None:
    """The owning team: the team object's `name` (ODCS v3.1.0), the same
    shape as an ODPS Data Product's `team.name`."""
    team = doc.get("team")
    if isinstance(team, dict):
        return team.get("name")
    owners = team_members(doc, "owner")
    return owners[0] if owners else None


def sla_entry(doc: dict[str, Any], property_name: str) -> dict[str, Any] | None:
    return next((p for p in doc.get("slaProperties") or [] if p.get("property") == property_name), None)


def retention_period(doc: dict[str, Any]) -> str | None:
    """ODCS `slaProperties` `retention` entry as an ISO 8601 duration
    (e.g. `{value: 90, unit: d}` -> "P90D"), or None if absent/unmappable."""
    entry = sla_entry(doc, "retention")
    if entry is None:
        return None
    unit = _ISO_UNIT_BY_SLA_UNIT.get(str(entry.get("unit", "")).lower())
    if unit is None or not isinstance(entry.get("value"), int):
        return None
    return f"P{entry['value']}{unit}"


def freshness_hours(doc: dict[str, Any]) -> int | None:
    """ODCS `slaProperties` `freshness` entry as a number of hours: the
    maximum age the table's data may have. None if absent/unmappable."""
    entry = sla_entry(doc, "freshness")
    if entry is None:
        return None
    unit = _FRESHNESS_UNITS.get(str(entry.get("unit", "")).lower())
    value = entry.get("value")
    if unit is None or not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value * _HOURS_BY_FRESHNESS_UNIT[unit]


def freshness_period(doc: dict[str, Any]) -> str | None:
    """The freshness SLA as an ISO 8601 duration (`PT6H`, `P7D`, `P2W`)."""
    entry = sla_entry(doc, "freshness")
    if entry is None or freshness_hours(doc) is None:
        return None
    unit = _FRESHNESS_UNITS[str(entry["unit"]).lower()]
    return f"PT{entry['value']}H" if unit == "H" else f"P{entry['value']}{unit}"


def primary_key(doc: dict[str, Any]) -> list[str]:
    """Primary-key column names, ordered by `primaryKeyPosition`."""
    pk = [p for p in columns(doc) if p.get("primaryKey")]
    return [p["name"] for p in sorted(pk, key=lambda p: p.get("primaryKeyPosition", 0))]


@dataclass(frozen=True)
class Reference:
    """One side of an ODCS v3.1.0 relationship, parsed: `contract` is the
    repo-relative file of another contract, or None for this one."""

    contract: str | None
    schema_object: str
    column: str


class InvalidReferenceError(ValueError):
    pass


def parse_reference(ref: str) -> Reference:
    """ODCS shorthand (`table.column`, within this contract) or fully
    qualified (`[<contract>.yaml#]/schema/<object>/properties/<property>`).
    Fluvv resolves `<object>`/`<property>` by `id`, falling back to `name`."""
    contract, _, fragment = ref.rpartition("#") if "#" in ref else (None, "", ref)
    fragment = fragment.lstrip("/")
    if "/" not in fragment:
        table, dot, column = fragment.partition(".")
        if not (dot and table and column):
            raise InvalidReferenceError(f"'{ref}' is neither `table.column` nor `[file.yaml#]/schema/<object>/properties/<property>`")
        return Reference(contract or None, table, column)
    parts = fragment.split("/")
    if len(parts) != 4 or parts[0] != "schema" or parts[2] != "properties":
        raise InvalidReferenceError(f"'{ref}' must point at a column: `[file.yaml#]/schema/<object>/properties/<property>`")
    return Reference(contract or None, parts[1], parts[3])


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def relationships(doc: dict[str, Any]) -> list[tuple[list[str], list[str]]]:
    """Every ODCS v3.1.0 relationship in the contract as
    `(from columns, to references)`: property-level (`from` is the property
    itself) and schema-level (`from` given as references into this
    contract). Composite keys pair up positionally."""
    found = []
    for obj in doc.get("schema") or []:
        for prop in obj.get("properties") or []:
            for rel in prop.get("relationships") or []:
                found.append(([prop["name"]], _as_list(rel["to"])))
        for rel in obj.get("relationships") or []:
            from_columns = [str(r).replace("/", ".").split(".")[-1] for r in _as_list(rel["from"])]
            found.append((from_columns, _as_list(rel["to"])))
    return found


def _by_id_or_name(items: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((i for i in items if i.get("id") == key), None) or next((i for i in items if i.get("name") == key), None)


def resolve_reference(ref: str, doc: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(target contract, target column) for a relationship's `to`, or
    raises InvalidReferenceError saying what didn't resolve."""
    parsed = parse_reference(ref)
    target_doc = doc
    if parsed.contract is not None:
        path = Path(repo_root) / parsed.contract
        if not path.is_file():
            raise InvalidReferenceError(f"'{ref}': contract file '{parsed.contract}' does not exist")
        target_doc = load_odcs_document(path)
    obj = _by_id_or_name(target_doc.get("schema") or [], parsed.schema_object)
    if obj is None:
        raise InvalidReferenceError(f"'{ref}': '{target_doc.get('id')}' has no schema object '{parsed.schema_object}'")
    prop = _by_id_or_name(obj.get("properties") or [], parsed.column)
    if prop is None:
        raise InvalidReferenceError(f"'{ref}': '{target_doc.get('id')}' has no column '{parsed.column}'")
    return target_doc, prop


def by_domain(docs: list[dict[str, Any]]) -> dict[str | None, list[dict[str, Any]]]:
    grouped: dict[str | None, list[dict[str, Any]]] = {}
    for doc in docs:
        grouped.setdefault(doc.get("domain"), []).append(doc)
    return grouped


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# --- governance files -------------------------------------------------------


@dataclass
class Domain:
    name: str
    description: str | None
    owner: str | None
    experts: list[str]
    path: Path | None = None


@dataclass
class GlossaryTerm:
    name: str
    definition: str
    synonyms: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass
class Glossary:
    domain: str | None
    """None for the shared glossary, which every contract can use."""
    description: str | None
    terms: dict[str, GlossaryTerm]
    path: Path | None = None


@dataclass
class DataProduct:
    """An ODPS v1.1.0 `DataProduct`. The owning team is `team.name`;
    experts are `team.members` with `role: expert`; members are the
    contracts its `outputPorts[].contractId` name."""

    name: str
    domain: str | None
    description: dict[str, Any]
    owner: str | None
    experts: list[str]
    contract_ids: list[str]
    path: Path | None = None


@dataclass
class Governance:
    domains: dict[str, Domain] = field(default_factory=dict)
    glossaries: dict[str | None, Glossary] = field(default_factory=dict)
    """Keyed by domain; None is the shared glossary."""
    products: dict[str, DataProduct] = field(default_factory=dict)
    classifications: dict[str, str | None] = field(default_factory=lambda: dict(DEFAULT_CLASSIFICATIONS))
    """Each allowed `classification` and its catalog tag, if any."""
    directory: str = "governance"
    """The repository's governance folder, for messages that name a file to add."""


def parse_domain(raw: dict[str, Any], path: Path | None = None) -> Domain:
    return Domain(raw["name"], raw.get("description"), raw.get("owner"), list(raw.get("experts") or []), path)


def parse_glossary(raw: dict[str, Any], path: Path | None = None) -> Glossary:
    terms = {
        t["name"]: GlossaryTerm(t["name"], t["definition"], list(t.get("synonyms") or []), list(t.get("relatedTerms") or []))
        for t in raw.get("terms") or []
    }
    return Glossary(raw.get("domain"), raw.get("description"), terms, path)


def parse_classifications(raw: dict[str, Any]) -> dict[str, str | None]:
    return {value["name"]: value.get("catalogTag") for value in raw.get("values") or []}


def parse_data_product(raw: dict[str, Any], path: Path | None = None) -> DataProduct:
    team = raw.get("team") or {}
    return DataProduct(
        name=raw.get("name") or raw["id"],
        domain=raw.get("domain"),
        description=raw.get("description") or {},
        owner=team.get("name"),
        experts=[m["username"] for m in team.get("members") or [] if m.get("role") == "expert"],
        contract_ids=[p["contractId"] for p in raw.get("outputPorts") or [] if p.get("contractId")],
        path=path,
    )


def load_governance_documents(repo_root: Path | str = ".") -> dict[Path, dict[str, Any]]:
    """Every governance file under `repo_root`, keyed by resolved path."""
    return {path.resolve(): load_odcs_document(path) for path in governance_files(repo_root)}


def load_governance(repo_root: Path | str = ".") -> Governance:
    """Domains, glossaries, classifications and Data Products, dispatched on
    each file's `kind` (the `governance/domains|glossaries|products` folders
    are a convention, not a requirement). Assumes the files are schema-valid
    -- lint is where they're validated."""
    gov = Governance(directory=project_layout(repo_root).governance)
    for path, raw in load_governance_documents(repo_root).items():
        kind = (raw or {}).get("kind")
        if kind == DOMAIN_KIND:
            domain = parse_domain(raw, path)
            gov.domains[domain.name] = domain
        elif kind == GLOSSARY_KIND:
            glossary = parse_glossary(raw, path)
            gov.glossaries[glossary.domain] = glossary
        elif kind == CLASSIFICATIONS_KIND:
            gov.classifications = parse_classifications(raw)
        elif kind == DATA_PRODUCT_KIND:
            product = parse_data_product(raw, path)
            gov.products[product.name] = product
    return gov


def resolve_term(gov: Governance, domain: str | None, name: str) -> Glossary | None:
    """The glossary a column's `businessName` resolves in: its contract's
    domain's, then the shared one."""
    for key in (domain, None):
        found = gov.glossaries.get(key)
        if found is not None and name in found.terms:
            return found
    return None


def glossary(gov: Governance, domain: str | None, docs: list[dict[str, Any]]) -> dict[str, GlossaryTerm]:
    """A glossary's terms (`domain` None for the shared one), with synonyms
    extended by derivation: every physical column name a contract in `docs`
    uses for the term, other than the term's own snake_case form (bronze
    `year` -> `ModelYear` gets synonym `year`)."""
    defined = gov.glossaries.get(domain)
    if defined is None:
        return {}
    physical: dict[str, set[str]] = {}
    for doc in docs:
        for prop in columns(doc):
            name = prop.get("businessName")
            if name and resolve_term(gov, doc.get("domain"), name) is defined:
                physical.setdefault(name, set()).add(prop["name"])
    terms = {}
    for name, term in defined.terms.items():
        derived = physical.get(name, set()) - {_snake(name)}
        terms[name] = GlossaryTerm(name, term.definition, sorted(set(term.synonyms) | derived), sorted(term.related))
    return terms


def domain_owner(gov: Governance, domain: str) -> str | None:
    found = gov.domains.get(domain)
    return found.owner if found else None


def experts(gov: Governance, domain: str) -> list[str]:
    found = gov.domains.get(domain)
    return sorted(found.experts) if found else []


def data_products(gov: Governance, domain: str | None = None) -> dict[str, DataProduct]:
    return {n: p for n, p in sorted(gov.products.items()) if domain is None or p.domain == domain}


def products_of(gov: Governance, contract_id: str) -> list[str]:
    """Names of the Data Products whose output ports list `contract_id`."""
    return sorted(n for n, p in gov.products.items() if contract_id in p.contract_ids)


# --- checks -----------------------------------------------------------------


@dataclass(frozen=True)
class GovernanceIssue:
    code: str
    message: str


def term_stub(name: str) -> str:
    """A ready-to-paste glossary entry for an undefined `businessName`."""
    return f"  - name: {name}\n    definition: <what {name} means, independent of any one table>"


def contract_issues(doc: dict[str, Any]) -> list[GovernanceIssue]:
    """Checks on the contract's own (table-scoped) governance fields."""
    issues = []
    if isinstance(doc.get("team"), list):
        issues.append(GovernanceIssue(
            "team-array-deprecated",
            "use the ODCS v3.1.0 team object: `team: {name: <owning team>, members: [{username, role}]}` "
            "instead of a list with a `role: owner` entry",
        ))
    for prop in columns(doc):
        if get_custom_property(prop, "relatedTerms") is not None:
            issues.append(GovernanceIssue(
                "related-terms-in-contract",
                f"column '{prop['name']}': relatedTerms relate glossary terms to each other, not columns; "
                "move them to the term in its glossary",
            ))
    for entry in doc.get("slaProperties") or []:
        if entry.get("property") == "retention" and retention_period(doc) is None:
            issues.append(GovernanceIssue(
                "invalid-retention",
                "slaProperties retention needs an integer value and a unit of d/w/m/y",
            ))
        if entry.get("property") == "freshness" and freshness_hours(doc) is None:
            issues.append(GovernanceIssue(
                "invalid-freshness",
                "slaProperties freshness needs a positive integer value and a unit of h/d/w (hours, days, weeks)",
            ))
    if doc.get("dataProduct"):
        issues.append(GovernanceIssue(
            "data-product-in-contract",
            f"dataProduct '{doc['dataProduct']}': Data Product membership is defined by the product, not the table; "
            f"remove it here and list contract '{doc.get('id')}' under the product's `outputPorts`",
        ))
    return issues


def id_issues(doc: dict[str, Any]) -> list[GovernanceIssue]:
    """An `id` is how everything else names a contract, so one must exist.
    Its shape is free; that it's unique is compile's to check."""
    contract_id = doc.get("id")
    if not isinstance(contract_id, str) or not contract_id:
        return [GovernanceIssue("missing-contract-id", "a contract needs an `id`: a non-empty string that names it")]
    return []


def domain_name_issues(doc: dict[str, Any]) -> list[GovernanceIssue]:
    """A contract's domain can become a folder and a catalog level, like its
    layer. On the contract, not the Domain file: 1.x refused a contract with
    such a domain, through its id and path, but not a Domain file."""
    domain = doc.get("domain")
    if isinstance(domain, str) and re.search(r"[./\\]", domain):
        return [GovernanceIssue("invalid-domain-name", f"domain '{domain}' can't contain `.`, `/` or `\\`: it can become a folder and a catalog level")]
    return []


def relationship_issues(doc: dict[str, Any], repo_root: Path) -> list[GovernanceIssue]:
    """Each relationship's `from` columns exist here, and its `to` resolves
    to a column that can be referenced: unique, or the target's primary key."""
    return [*relationship_shape_issues(doc), *relationship_target_issues(doc, repo_root)]


def relationship_shape_issues(doc: dict[str, Any]) -> list[GovernanceIssue]:
    """The part of `relationship_issues` that needs only the contract itself."""
    own_columns = {p["name"] for p in columns(doc)}
    issues = []
    for from_columns, to_refs in relationships(doc):
        for column in from_columns:
            if column not in own_columns:
                issues.append(GovernanceIssue("invalid-relationship", f"relationship from unknown column '{column}'"))
        if len(from_columns) != len(to_refs):
            issues.append(GovernanceIssue("invalid-relationship", f"relationship from {from_columns} names {len(to_refs)} target column(s)"))
    return issues


def relationship_target_issues(doc: dict[str, Any], repo_root: Path) -> list[GovernanceIssue]:
    """The part of `relationship_issues` that resolves targets, which may
    live in other contracts. Skips relationships whose shape is wrong."""
    issues = []
    for from_columns, to_refs in relationships(doc):
        if len(from_columns) != len(to_refs):
            continue
        targets = []
        for ref in to_refs:
            try:
                targets.append(resolve_reference(ref, doc, repo_root))
            except InvalidReferenceError as exc:
                issues.append(GovernanceIssue("relationship-target-not-found", str(exc)))
        if len(targets) != len(to_refs):
            continue
        target_doc = targets[0][0]
        target_columns = [prop["name"] for _, prop in targets]
        is_key = sorted(target_columns) == sorted(primary_key(target_doc)) or (
            len(targets) == 1 and (targets[0][1].get("unique") or targets[0][1].get("primaryKey"))
        )
        if not is_key:
            issues.append(GovernanceIssue(
                "relationship-target-not-unique",
                f"relationship to '{target_doc.get('id')}' {target_columns}: the target must be unique or the primary key",
            ))
    return issues


def unknown_domain(domain: str, gov: Governance) -> GovernanceIssue:
    """Worded the same for a contract, glossary or product, so a missing
    domain file is reported once with every file that names it."""
    return GovernanceIssue(
        "unknown-domain",
        f"domain '{domain}' has no domain file; add {gov.directory}/domains/{domain}.yaml (kind: {DOMAIN_KIND})",
    )


def contract_reference_issues(doc: dict[str, Any], gov: Governance) -> list[GovernanceIssue]:
    """The contract's references to shared entities must resolve. (That it
    doesn't define them itself is `contract_issues`.) A domain is optional,
    but one that's named must exist. A column's `classification` counts:
    the vocabulary it comes from is the repository's."""
    domain = doc.get("domain")
    issues = []
    if domain is not None and domain not in gov.domains:
        issues.append(unknown_domain(domain, gov))
    where = f"{gov.directory}/glossaries/{domain}.yaml or the shared glossary" if domain else "the shared glossary (a glossary with no `domain`)"
    for prop in columns(doc):
        name = prop.get("businessName")
        if name and resolve_term(gov, domain, name) is None:
            issues.append(GovernanceIssue(
                "unknown-business-term",
                f"column '{prop['name']}': businessName '{name}' is not a term in {where}; "
                f"add it under `terms:`\n{term_stub(name)}",
            ))
        value = prop.get("classification")
        if value is not None and value not in gov.classifications:
            issues.append(GovernanceIssue(
                "unknown-classification",
                f"column '{prop['name']}': classification '{value}' is not one of {list(gov.classifications)}",
            ))
    return issues


def glossary_issues(glossary_: Glossary) -> list[GovernanceIssue]:
    """Checks that need only the glossary file itself."""
    issues = []
    for term in glossary_.terms.values():
        for target in term.related:
            if target not in glossary_.terms:
                issues.append(GovernanceIssue(
                    "unknown-related-term",
                    f"term '{term.name}' relates to '{target}', which is not a term in this glossary",
                ))
    return issues


def glossary_reference_issues(glossary_: Glossary, gov: Governance) -> list[GovernanceIssue]:
    """Checks that need the rest of the repository."""
    if glossary_.domain is not None and glossary_.domain not in gov.domains:
        return [unknown_domain(glossary_.domain, gov)]
    return []


def data_product_issues(product: DataProduct) -> list[GovernanceIssue]:
    """Checks that need only the product file itself."""
    if not product.owner:
        return [GovernanceIssue("missing-product-owner", f"data product '{product.name}': set `team.name` to the owning team")]
    return []


def data_product_reference_issues(product: DataProduct, gov: Governance, contract_ids: set[str]) -> list[GovernanceIssue]:
    """Checks that need the rest of the repository."""
    issues = []
    if product.domain is not None and product.domain not in gov.domains:
        issues.append(unknown_domain(product.domain, gov))
    for contract_id in product.contract_ids:
        if contract_id not in contract_ids:
            issues.append(GovernanceIssue(
                "unknown-product-contract",
                f"data product '{product.name}': output port contractId '{contract_id}' is not the id of any contract",
            ))
    return issues
