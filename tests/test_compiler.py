import pytest
from sling_querybuilder.compiler import QueryBuilderCompiler, CompiledQuery, compile_query


class TestQueryBuilderCompiler:
    def test_default_empty_query(self):
        compiled = compile_query({})
        assert isinstance(compiled, CompiledQuery)
        assert compiled.sql2 == "SELECT [n].* FROM [nt:base] AS [n]"
        assert compiled.limit == 10
        assert compiled.offset == 0

    def test_basic_type_query(self):
        compiled = compile_query({"type": "cq:Page"})
        assert compiled.sql2 == "SELECT [n].* FROM [cq:Page] AS [n]"

    def test_path_descendants(self):
        compiled = compile_query({
            "type": "cq:Page",
            "path": "/content/meridian"
        })
        assert compiled.sql2 == (
            "SELECT [n].* FROM [cq:Page] AS [n] "
            "WHERE ISDESCENDANTNODE([n], '/content/meridian')"
        )

    def test_path_self(self):
        compiled = compile_query({
            "type": "cq:Page",
            "path": "/content/meridian",
            "path.self": "true"
        })
        assert "WHERE (ISDESCENDANTNODE([n], '/content/meridian') OR ISSAMENODE([n], '/content/meridian'))" in compiled.sql2

    def test_path_flat_children(self):
        compiled = compile_query({
            "type": "cq:Page",
            "path": "/content/meridian",
            "path.flat": "true"
        })
        assert compiled.sql2 == (
            "SELECT [n].* FROM [cq:Page] AS [n] "
            "WHERE ISCHILDNODE([n], '/content/meridian')"
        )

    def test_path_exact(self):
        compiled = compile_query({
            "type": "cq:Page",
            "path": "/content/meridian",
            "path.exact": "true"
        })
        assert compiled.sql2 == (
            "SELECT [n].* FROM [cq:Page] AS [n] "
            "WHERE ISSAMENODE([n], '/content/meridian')"
        )

    def test_property_equals(self):
        compiled = compile_query({
            "type": "cq:Page",
            "property": "jcr:content/cq:template",
            "property.value": "/conf/meridian/templates/hotel"
        })
        assert compiled.sql2 == (
            "SELECT [n].* FROM [cq:Page] AS [n] "
            "WHERE [n].[jcr:content/cq:template] = '/conf/meridian/templates/hotel'"
        )

    def test_property_operations(self):
        # unequals
        c1 = compile_query({
            "property": "jcr:content/status",
            "property.operation": "unequals",
            "property.value": "archived"
        })
        assert "[n].[jcr:content/status] <> 'archived'" in c1.sql2

        # like
        c2 = compile_query({
            "property": "jcr:title",
            "property.operation": "like",
            "property.value": "%Resort%"
        })
        assert "[n].[jcr:title] LIKE '%Resort%'" in c2.sql2

        # exists
        c3 = compile_query({
            "property": "jcr:content/cq:lastModified",
            "property.operation": "exists"
        })
        assert "[n].[jcr:content/cq:lastModified] IS NOT NULL" in c3.sql2

        # not
        c4 = compile_query({
            "property": "jcr:content/cq:lastModified",
            "property.operation": "not"
        })
        assert "[n].[jcr:content/cq:lastModified] IS NULL" in c4.sql2

    def test_property_multiple_values_or_default(self):
        compiled = compile_query({
            "property": "jcr:content/brand",
            "property.1_value": "Grand",
            "property.2_value": "Regency"
        })
        assert "([n].[jcr:content/brand] = 'Grand' OR [n].[jcr:content/brand] = 'Regency')" in compiled.sql2

    def test_property_multiple_values_and(self):
        compiled = compile_query({
            "property": "jcr:content/features",
            "property.1_value": "pool",
            "property.2_value": "spa",
            "property.and": "true"
        })
        assert "([n].[jcr:content/features] = 'pool' AND [n].[jcr:content/features] = 'spa')" in compiled.sql2

    def test_multiple_numbered_properties(self):
        compiled = compile_query({
            "type": "cq:Page",
            "path": "/content/meridian",
            "1_property": "jcr:content/active",
            "1_property.value": "true",
            "2_property": "jcr:content/tier",
            "2_property.value": "luxury"
        })
        assert "ISDESCENDANTNODE([n], '/content/meridian')" in compiled.sql2
        assert "[n].[jcr:content/active] = 'true'" in compiled.sql2
        assert "[n].[jcr:content/tier] = 'luxury'" in compiled.sql2

    def test_fulltext_search(self):
        compiled = compile_query({
            "fulltext": "luxury suites"
        })
        assert compiled.sql2 == (
            "SELECT [n].* FROM [nt:base] AS [n] "
            "WHERE CONTAINS([n].*, 'luxury suites')"
        )

    def test_fulltext_with_relpath(self):
        compiled = compile_query({
            "type": "cq:Page",
            "fulltext": "penthouse",
            "fulltext.relPath": "jcr:content"
        })
        assert "CONTAINS([n].[jcr:content], 'penthouse')" in compiled.sql2

    def test_date_range(self):
        compiled = compile_query({
            "type": "cq:Page",
            "daterange.property": "jcr:content/cq:lastModified",
            "daterange.lowerBound": "2024-01-01T00:00:00.000Z",
            "daterange.upperBound": "2024-12-31T23:59:59.999Z"
        })
        assert "[n].[jcr:content/cq:lastModified] >= CAST('2024-01-01T00:00:00.000Z' AS DATE)" in compiled.sql2
        assert "[n].[jcr:content/cq:lastModified] <= CAST('2024-12-31T23:59:59.999Z' AS DATE)" in compiled.sql2

    def test_tag_predicate(self):
        compiled = compile_query({
            "type": "cq:Page",
            "tagid": "meridian:destinations/paris",
            "tagid.property": "jcr:content/cq:tags"
        })
        assert "[n].[jcr:content/cq:tags] = 'meridian:destinations/paris'" in compiled.sql2

    def test_ordering(self):
        # Default ASC
        c1 = compile_query({
            "type": "cq:Page",
            "orderby": "@jcr:content/cq:lastModified"
        })
        assert c1.sql2.endswith("ORDER BY [n].[jcr:content/cq:lastModified] ASC")

        # Explicit DESC
        c2 = compile_query({
            "type": "cq:Page",
            "orderby": "jcr:title",
            "orderby.sort": "desc"
        })
        assert c2.sql2.endswith("ORDER BY [n].[jcr:title] DESC")

    def test_pagination_parameters(self):
        compiled = compile_query({
            "p.limit": "50",
            "p.offset": "20"
        })
        assert compiled.limit == 50
        assert compiled.offset == 20

    def test_unlimited_pagination(self):
        compiled = compile_query({
            "p.limit": "-1"
        })
        assert compiled.limit is None

    def test_parse_query_string(self):
        qs = "path=/content/meridian&type=cq:Page&1_property=jcr:content/active&1_property.value=true&p.limit=25&orderby=@jcr:score&orderby.sort=desc"
        compiler = QueryBuilderCompiler.from_query_string(qs)
        compiled = compiler.compile()
        assert compiled.limit == 25
        assert "ISDESCENDANTNODE([n], '/content/meridian')" in compiled.sql2
        assert "[n].[jcr:content/active] = 'true'" in compiled.sql2
        assert "ORDER BY [n].[jcr:score] DESC" in compiled.sql2
