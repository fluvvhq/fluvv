from __future__ import annotations

from pathlib import Path

import pytest

from fluvv import governance

# A copy of the sales example project, so these tests pass from a checkout
# of this package alone.
EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "sales"


@pytest.fixture(scope="module")
def sales_docs():
    return [d for d in governance.load_contracts(EXAMPLE).values() if d["domain"] == "sales"]


@pytest.fixture(scope="module")
def gov():
    return governance.load_governance(EXAMPLE)


def _doc(layer: str, columns: list[dict], **extra) -> dict:
    return {
        "id": f"{layer}.d.t",
        "domain": "d",
        "schema": [{"name": f"t_{layer}", "properties": columns}],
        "customProperties": [{"property": "medallionLayer", "value": layer}],
        **extra,
    }


def _gov(terms: dict[str, list[str]] | None = None, domains=("d",)) -> governance.Governance:
    return governance.Governance(
        domains={d: governance.Domain(d, None, "team-a", []) for d in domains},
        glossaries={
            "d": governance.Glossary("d", None, {n: governance.GlossaryTerm(n, f"{n} def", related=r) for n, r in (terms or {}).items()})
        },
    )


def test_repo_contracts_and_governance_files_have_no_issues(sales_docs, gov):
    contract_ids = {d["id"] for d in sales_docs}
    for doc in sales_docs:
        assert governance.contract_issues(doc) == []
        assert governance.contract_reference_issues(doc, gov) == []
    assert governance.glossary_issues(gov.glossaries["sales"], gov) == []
    for product in gov.products.values():
        assert governance.data_product_issues(product, gov, contract_ids) == []


def test_glossary_definitions_come_from_the_glossary_file_with_derived_synonyms(sales_docs, gov):
    terms = governance.glossary(gov, "sales", sales_docs)
    model_year = terms["ModelYear"]
    assert model_year.definition == "The model year of the vehicle, as assigned by the manufacturer."  # no column-specific rename note
    assert model_year.synonyms == ["year"]  # bronze's name; `model_year` is just the snake_case form
    assert model_year.related == ["Make", "Mileage", "Model"]
    assert terms["SalePrice"].synonyms == ["price"]


def test_explicit_synonyms_merge_with_derived_ones():
    gov = _gov({"Thing": []})
    gov.glossaries["d"].terms["Thing"].synonyms = ["widget"]
    docs = [_doc("gold", [{"name": "thing_name", "businessName": "Thing"}])]
    assert governance.glossary(gov, "d", docs)["Thing"].synonyms == ["thing_name", "widget"]


def test_unknown_business_term_suggests_a_stub():
    doc = _doc("gold", [{"name": "a", "businessName": "Known"}, {"name": "b", "businessName": "Unknown"}])
    issues = governance.contract_reference_issues(doc, _gov({"Known": []}))
    assert [i.code for i in issues] == ["unknown-business-term"]
    assert "governance/glossaries/d.yaml" in issues[0].message
    assert "  - name: Unknown\n    definition:" in issues[0].message


def test_contract_must_reference_a_known_domain_and_not_define_its_product():
    doc = {**_doc("gold", []), "domain": "nowhere", "dataProduct": "p"}
    codes = sorted(i.code for i in governance.contract_reference_issues(doc, _gov()))
    assert codes == ["data-product-in-contract", "unknown-domain"]


def test_contract_issues_flag_bad_classification_retention_and_leftover_related_terms():
    doc = _doc(
        "gold",
        [{"name": "a", "classification": "secret"}, {"name": "b", "customProperties": [{"property": "relatedTerms", "value": ["X"]}]}],
        slaProperties=[{"property": "retention", "value": "90", "unit": "d"}],
    )
    codes = sorted(i.code for i in governance.contract_issues(doc))
    assert codes == ["invalid-retention", "related-terms-in-contract", "unknown-classification"]


def test_glossary_related_terms_must_exist_in_the_glossary():
    gov = _gov({"Thing": ["Nope"], "Other": []})
    issues = governance.glossary_issues(gov.glossaries["d"], gov)
    assert [(i.code, "Nope" in i.message) for i in issues] == [("unknown-related-term", True)]


def test_data_product_ports_must_name_real_contracts():
    product = governance.DataProduct("p", "d", {}, "team-a", [], ["gold.d.t", "gold.d.gone"])
    issues = governance.data_product_issues(product, _gov(), {"gold.d.t"})
    assert [i.code for i in issues] == ["unknown-product-contract"]


def test_domain_owner_and_experts_come_from_the_domain_file(gov):
    assert governance.domain_owner(gov, "sales") == "sales-analytics-team"
    assert governance.experts(gov, "sales") == ["jane.doe"]
    assert governance.domain_owner(gov, "nowhere") is None


def test_data_products_come_from_odps_output_ports(gov):
    product = governance.data_products(gov, "sales")["sales_reporting"]
    assert product.owner == "sales-analytics-team"
    assert product.experts == ["jane.doe"]
    assert sorted(product.contract_ids) == [
        "gold.sales.cars", "gold.sales.dealership_performance", "gold.sales.employees", "gold.sales.locations",
    ]
    assert governance.products_of(gov, "gold.sales.cars") == ["sales_reporting"]
    assert governance.products_of(gov, "silver.sales.cars") == []


def test_retention_period_and_primary_key():
    doc = _doc(
        "gold",
        [{"name": "b", "primaryKey": True, "primaryKeyPosition": 2}, {"name": "a", "primaryKey": True, "primaryKeyPosition": 1}],
        slaProperties=[{"property": "retention", "value": 2, "unit": "years"}],
    )
    assert governance.retention_period(doc) == "P2Y"
    assert governance.primary_key(doc) == ["a", "b"]
