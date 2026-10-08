import json
import time
import urllib.request
import pytest
from unittest.mock import patch

from sling_querybuilder.gateway import QueryBuilderGateway


class TestQueryBuilderGateway:
    def test_normalize_hits(self):
        gw = QueryBuilderGateway()
        raw = [
            {"path": "/content/site/en", "title": "English Home"},
            {"jcr:path": "/content/site/fr", "title": "French Home"},
            {"path": "/content/dam/logo.png", "name": "custom-logo"},
        ]
        hits = gw._normalize_hits(raw)
        assert len(hits) == 3
        assert hits[0]["path"] == "/content/site/en"
        assert hits[0]["name"] == "en"
        assert hits[1]["path"] == "/content/site/fr"
        assert hits[1]["name"] == "fr"
        assert hits[2]["name"] == "custom-logo"

    @patch.object(QueryBuilderGateway, "_query_sling_sql2")
    def test_execute_querybuilder_pagination(self, mock_query):
        mock_query.return_value = [
            {"path": f"/content/item{i}"} for i in range(15)
        ]
        gw = QueryBuilderGateway()
        res = gw.execute_querybuilder({
            "type": "cq:Page",
            "p.offset": "5",
            "p.limit": "5"
        })
        assert res["success"] is True
        assert res["total"] == 15
        assert res["results"] == 5
        assert res["offset"] == 5
        assert len(res["hits"]) == 5
        assert res["hits"][0]["path"] == "/content/item5"
        assert res["hits"][-1]["path"] == "/content/item9"
        assert "SELECT [n].* FROM [cq:Page] AS [n]" in res["_compiled_sql2"]

    @patch.object(QueryBuilderGateway, "_query_sling_sql2")
    def test_http_get_querybuilder(self, mock_query):
        mock_query.return_value = [
            {"path": "/content/novaria/en/hotels/paris-grand", "title": "Paris Grand"}
        ]
        gw = QueryBuilderGateway(port=18081)
        gw.start(blocking=False)
        time.sleep(0.1)  # allow server to bind

        try:
            # Test /healthz
            with urllib.request.urlopen("http://127.0.0.1:18081/healthz") as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["status"] == "healthy"

            # Test /bin/querybuilder.json GET
            url = "http://127.0.0.1:18081/bin/querybuilder.json?path=/content/novaria&type=cq:Page"
            with urllib.request.urlopen(url) as resp:
                assert resp.status == 200
                res = json.loads(resp.read().decode("utf-8"))
                assert res["success"] is True
                assert res["results"] == 1
                assert res["hits"][0]["path"] == "/content/novaria/en/hotels/paris-grand"
        finally:
            gw.stop()

    @patch.object(QueryBuilderGateway, "_query_sling_sql2")
    def test_http_post_json(self, mock_query):
        mock_query.return_value = [
            {"path": "/content/novaria/en/hotels/london-house"}
        ]
        gw = QueryBuilderGateway(port=18082)
        gw.start(blocking=False)
        time.sleep(0.1)

        try:
            req = urllib.request.Request(
                "http://127.0.0.1:18082/bin/querybuilder.json",
                data=json.dumps({"path": "/content/novaria", "type": "cq:Page"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                res = json.loads(resp.read().decode("utf-8"))
                assert res["success"] is True
                assert len(res["hits"]) == 1
        finally:
            gw.stop()

    def test_http_connection_failure(self):
        # Point to closed port to verify error handling
        gw = QueryBuilderGateway(sling_base_url="http://127.0.0.1:19999", port=18083)
        gw.start(blocking=False)
        time.sleep(0.1)

        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen("http://127.0.0.1:18083/bin/querybuilder.json?type=cq:Page")
            assert exc_info.value.code == 502
        finally:
            gw.stop()

    def test_gateway_explain_execution(self):
        gw = QueryBuilderGateway()
        res = gw.execute_querybuilder({
            "path": "/content/novaria",
            "type": "cq:Page",
            "property": "hotelId",
            "property.value": "NVR-NYC-0001",
            "p.explain": "true"
        })
        assert res["success"] is True
        assert res["explain"] is True
        assert res["_compiled_sql2"].startswith("EXPLAIN SELECT")
        assert "plan" in res
        assert "is_traversal" in res
        assert res["is_traversal"] is True
        assert res["risk_level"] == "CRITICAL"
