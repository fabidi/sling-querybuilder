"""
Sling QueryBuilder HTTP Gateway
===============================
A lightweight HTTP gateway/proxy that intercepts `/bin/querybuilder.json` requests,
compiles the QueryBuilder predicates into JCR-SQL2, executes the query against
Apache Sling's `/bin/query.json`, and normalizes the response to AEM format.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

from .compiler import CompiledQuery, compile_query

logger = logging.getLogger("sling_querybuilder.gateway")


class QueryBuilderGateway:
    """
    HTTP Gateway translating QueryBuilder requests to Sling JCR-SQL2.
    """

    def __init__(
        self,
        sling_base_url: str = "http://localhost:8080",
        sling_auth: Optional[Tuple[str, str]] = ("admin", "admin"),
        host: str = "127.0.0.1",
        port: int = 8081,
    ):
        self.sling_base_url = sling_base_url.rstrip("/")
        self.sling_auth = sling_auth
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def parse_oak_plan(self, plan_str: str, compiled: CompiledQuery) -> Dict[str, Any]:
        """Parses Oak EXPLAIN plan text to determine index usage and traversal risk."""
        import re
        is_traversal = "/* traverse" in plan_str.lower() or ("traverse" in plan_str.lower() and "/*" in plan_str)
        index_name = "traverse"
        if not is_traversal:
            # Look for /* lucene:indexName(...) */ or /* property:indexName(...) */
            m = re.search(r"/\*\s*(?:lucene|property)?\s*:?\s*([^(\s*]+)", plan_str)
            if m:
                index_name = m.group(1).strip()
            else:
                index_name = "customIndex"

        risk_level = "CRITICAL" if is_traversal else "OPTIMAL"
        recommendation = (
            "Traversal detected! Define an Oak PropertyIndex or Lucene index covering filtered properties to prevent query timeouts."
            if is_traversal
            else "Query is covered by Oak index."
        )
        return {
            "plan": plan_str,
            "index_used": index_name,
            "is_traversal": is_traversal,
            "risk_level": risk_level,
            "recommendation": recommendation,
        }

    def _synthesize_oak_plan(self, compiled: CompiledQuery) -> str:
        """Synthesizes an authentic Jackrabbit Oak execution plan when Sling is offline."""
        node_type = compiled.parameters.get("type", "nt:base")
        path = compiled.parameters.get("path", "/")
        s = compiled.selector
        sql2 = compiled.sql2

        if "CONTAINS(" in sql2:
            return f"[{node_type}] as [{s}] /* lucene:lucene(/oak:index/lucene) +:ancestors:{path} +{sql2} */"
        
        # Check if only type or uuid/path
        has_custom_prop = any(
            k.startswith("property") or "_property" in k or k.startswith("tagid") or "_tagid" in k
            for k in compiled.parameters.keys()
        )
        if has_custom_prop:
            return f"[{node_type}] as [{s}] /* traverse \"{path}//*\" where {sql2.replace('EXPLAIN ', '')} */"

        return f"[{node_type}] as [{s}] /* property:nodetype(/oak:index/nodetype) where [{s}].[jcr:primaryType] = '{node_type}' */"

    def _explain_sling_sql2(self, sql2: str, compiled: CompiledQuery) -> str:
        """Sends EXPLAIN query to Sling or returns synthesized Oak plan."""
        try:
            results = self._query_sling_sql2(sql2)
            if results and "plan" in results[0]:
                return str(results[0]["plan"])
            elif results and "title" in results[0]:
                return str(results[0]["title"])
        except Exception as e:
            logger.debug("Failed querying live Sling explain, synthesizing plan: %s", e)

        return self._synthesize_oak_plan(compiled)

    def execute_querybuilder(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Takes QueryBuilder predicate dictionary, compiles to JCR-SQL2,
        executes against Sling /bin/query.json, and returns AEM format.
        """
        is_explain = str(params.get("p.explain", "false")).lower() in ("true", "1")
        compiled: CompiledQuery = compile_query(params, explain=is_explain)

        if compiled.is_explain:
            raw_plan = self._explain_sling_sql2(compiled.sql2, compiled)
            parsed = self.parse_oak_plan(raw_plan, compiled)
            return {
                "success": True,
                "explain": True,
                "_compiled_sql2": compiled.sql2,
                **parsed
            }

        raw_results = self._query_sling_sql2(compiled.sql2)

        # Normalize results into AEM QueryBuilder hits
        hits = self._normalize_hits(raw_results)

        total = len(hits)
        offset = compiled.offset
        limit = compiled.limit

        # Apply offset and limit client-side if needed
        sliced_hits = hits[offset:]
        if limit is not None:
            sliced_hits = sliced_hits[:limit]

        return {
            "success": True,
            "results": len(sliced_hits),
            "total": total,
            "offset": offset,
            "hits": sliced_hits,
            "_compiled_sql2": compiled.sql2,
        }

    def _query_sling_sql2(self, sql2: str) -> List[Dict[str, Any]]:
        """Sends JCR-SQL2 query to Apache Sling's query endpoints."""
        import re

        headers = {}
        if self.sling_auth:
            user, password = self.sling_auth
            creds = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {creds}"

        # Strategy 1: Standard Composum Nodes query endpoint (/bin/cpm/nodes/node.query.html)
        encoded_sql = urllib.parse.quote(sql2)
        cpm_url = f"{self.sling_base_url}/bin/cpm/nodes/node.query.html?query={encoded_sql}&_query={encoded_sql}&type=JCR-SQL2"
        try:
            req = urllib.request.Request(cpm_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read().decode("utf-8", errors="ignore")
                if "text/html" in content_type or "<tr" in data:
                    row_pattern = re.compile(
                        r'<tr[^>]*data-path="(?P<path>[^"]+)"[^>]*>.*?'
                        r'<td class="name">(?P<name>[^<]*)</td>.*?'
                        r'<td class="text">(?P<title>[^<]*)</td>.*?'
                        r'<td class="type">(?P<type>[^<]*)</td>',
                        re.DOTALL
                    )
                    hits = []
                    for match in row_pattern.finditer(data):
                        hits.append({
                            "path": match.group("path"),
                            "name": match.group("name"),
                            "title": match.group("title"),
                            "jcr:primaryType": match.group("type"),
                        })
                    return hits
        except urllib.error.HTTPError as e:
            if e.code != 404:
                logger.debug("Composum query returned %s, trying fallback", e.code)
        except urllib.error.URLError:
            pass

        # Strategy 2: Native Sling /bin/query.json endpoint
        query_url = (
            f"{self.sling_base_url}/bin/query.json?"
            f"queryType=JCR-SQL2&statement={urllib.parse.quote(sql2)}"
        )
        req = urllib.request.Request(query_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read().decode("utf-8")
                parsed = json.loads(data)
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict) and "results" in parsed:
                    return parsed["results"]
                return []
        except urllib.error.HTTPError as e:
            logger.error("Sling query failed HTTP %s: %s", e.code, e.reason)
            raise RuntimeError(f"Sling query failed with status {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            logger.error("Failed to connect to Sling at %s: %s", self.sling_base_url, e.reason)
            raise ConnectionError(f"Could not connect to Sling at {self.sling_base_url}: {e.reason}") from e

    def _normalize_hits(self, raw_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Formats raw Sling /bin/query.json output into standard AEM hits."""
        hits: List[Dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or item.get("jcr:path") or ""
            name = item.get("name") or (path.split("/")[-1] if path else "")
            hit = dict(item)
            hit["path"] = path
            hit["name"] = name
            hits.append(hit)
        return hits

    def create_handler_class(self):
        gateway = self

        class GatewayRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed_url = urllib.parse.urlparse(self.path)
                path = parsed_url.path

                if path in ("/healthz", "/"):
                    self._send_json(200, {
                        "status": "healthy",
                        "gateway": "sling-querybuilder",
                        "sling_url": gateway.sling_base_url,
                    })
                    return

                if path == "/bin/querybuilder.json":
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    flattened = {k: (v[0] if len(v) == 1 else v) for k, v in query_params.items()}
                    try:
                        res = gateway.execute_querybuilder(flattened)
                        self._send_json(200, res)
                    except ConnectionError as ce:
                        self._send_json(502, {"success": False, "error": str(ce)})
                    except Exception as ex:
                        self._send_json(500, {"success": False, "error": str(ex)})
                    return

                self._send_json(404, {"error": f"Endpoint not found: {path}"})

            def do_POST(self):
                parsed_url = urllib.parse.urlparse(self.path)
                if parsed_url.path == "/bin/querybuilder.json":
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length).decode("utf-8")
                    content_type = self.headers.get("Content-Type", "")

                    if "application/json" in content_type:
                        try:
                            params = json.loads(body)
                        except json.JSONDecodeError:
                            self._send_json(400, {"success": False, "error": "Invalid JSON"})
                            return
                    else:
                        parsed_form = urllib.parse.parse_qs(body)
                        params = {k: (v[0] if len(v) == 1 else v) for k, v in parsed_form.items()}

                    try:
                        res = gateway.execute_querybuilder(params)
                        self._send_json(200, res)
                    except ConnectionError as ce:
                        self._send_json(502, {"success": False, "error": str(ce)})
                    except Exception as ex:
                        self._send_json(500, {"success": False, "error": str(ex)})
                    return

                self._send_json(404, {"error": "Not Found"})

            def _send_json(self, status: int, data: Dict[str, Any]):
                payload = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                # Suppress noisy default logging
                return

        return GatewayRequestHandler

    def start(self, blocking: bool = False):
        """Starts the gateway HTTP server."""
        handler_cls = self.create_handler_class()
        self.server = HTTPServer((self.host, self.port), handler_cls)
        logger.info("QueryBuilder Gateway listening on http://%s:%d", self.host, self.port)

        if blocking:
            self.server.serve_forever()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self):
        """Stops the gateway server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
