"""
Unit tests for Apache Jackrabbit Oak Index Definition Generator.
"""

import json
from sling_querybuilder.oak_index import (
    create_property_index,
    create_lucene_index,
    OakPropertyIndex,
    OakLuceneIndex,
)


def test_property_index_generation():
    idx = create_property_index(
        name="hotelIdIndex",
        properties=["hotelId"],
        declaring_node_types=["cq:PageContent"],
        unique=True
    )
    assert idx.name == "hotelIdIndex"
    d = idx.to_dict()
    assert d["type"] == "property"
    assert d["propertyNames"] == ["hotelId"]
    assert d["declaringNodeTypes"] == ["cq:PageContent"]
    assert d["unique"] is True

    # JSON export
    j = json.loads(idx.to_json())
    assert j["type"] == "property"

    # FileVault XML export
    xml = idx.to_filevault_xml()
    assert 'type="property"' in xml
    assert 'unique="{Boolean}true"' in xml
    assert 'propertyNames="[hotelId]"' in xml

    # Repoinit export
    repoinit = idx.to_repoinit()
    assert "create path (oak:QueryIndexDefinition) /oak:index/hotelIdIndex" in repoinit
    assert 'set propertyNames to "hotelId"' in repoinit


def test_lucene_index_generation():
    idx = create_lucene_index(
        name="novariaHotelLucene",
        node_type="cq:Page",
        properties=["jcr:content/hotelId", "jcr:content/brandId", "jcr:content/authoredStarRating"]
    )
    assert idx.name == "novariaHotelLucene"
    d = idx.to_dict()
    assert d["type"] == "lucene"
    assert d["async"] == ["async", "nrt"]
    assert "cq:Page" in d["indexRules"]
    props = d["indexRules"]["cq:Page"]["properties"]
    assert "jcr_content_hotelId" in props
    assert props["jcr_content_hotelId"]["name"] == "jcr:content/hotelId"
    assert props["jcr_content_hotelId"]["propertyIndex"] is True

    # JSON export
    j = json.loads(idx.to_json())
    assert j["compatVersion"] == 2

    # FileVault XML export
    xml = idx.to_filevault_xml()
    assert 'type="lucene"' in xml
    assert '<cq:Page jcr:primaryType="nt:unstructured">' in xml
    assert 'name="jcr:content/hotelId"' in xml

    # Repoinit export
    repoinit = idx.to_repoinit()
    assert "create path (oak:QueryIndexDefinition) /oak:index/novariaHotelLucene" in repoinit
    assert 'set type to "lucene"' in repoinit
