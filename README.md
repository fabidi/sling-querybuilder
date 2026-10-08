# sling-querybuilder

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![CI](https://github.com/fabidi/sling-querybuilder/actions/workflows/ci.yml/badge.svg)](https://github.com/fabidi/sling-querybuilder/actions/workflows/ci.yml)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0%20runtime-brightgreen.svg)]()

> **The first open-source QueryBuilder to JCR-SQL2 compiler and runtime gateway for Apache Sling & Jackrabbit Oak.**

---

## The Missing Link in the Sling / AEM Ecosystem

For over 14 years, developers, QA engineers, and automated tools working in the Adobe Experience Manager (AEM) ecosystem have faced a fundamental divide:

1. **AEM QueryBuilder (`/bin/querybuilder.json`)** is proprietary (`com.day.cq.search.QueryBuilder`).
2. **Apache Sling & Jackrabbit Oak** are 100% open-source, but only expose `/bin/query.json` using **JCR-SQL2** or XPath.

Because of this gap, anyone building integration tests, automated migration tools, or Agentic AI MCP servers had only two choices: pay for full enterprise AEM licenses, or write complex custom Java OSGi bundles.

**`sling-querybuilder` solves this problem with zero runtime dependencies.** It compiles standard AEM QueryBuilder predicate maps into valid JCR-SQL2 and provides a drop-in HTTP gateway proxy that brings `/bin/querybuilder.json` to vanilla Apache Sling instances.

---

## Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Client as AI Agent / Client
    participant GW as sling-querybuilder Gateway
    participant C as QueryBuilderCompiler
    participant Sling as Apache Sling 12 / Oak

    Client->>GW: GET /bin/querybuilder.json (predicates)
    GW->>C: Compile predicates
    C-->>GW: JCR-SQL2 query string
    GW->>Sling: GET /bin/query.json (JCR-SQL2)
    Sling-->>GW: Oak Lucene / Node results
    GW-->>Client: AEM-format JSON (hits, total)
```

---

## Features

- **Pure Python Compiler:** Zero third-party runtime dependencies. Uses standard Python 3.11+ library.
- **Full Predicate Support:**
  - Node types (`type=cq:Page`, `type=dam:Asset`, etc.)
  - Hierarchy & path filters (`path`, `path.flat`, `path.self`, `path.exact`)
  - Property evaluation (`property`, `1_property`, `2_property` with `equals`, `unequals`, `like`, `exists`, `not`)
  - Fulltext search (`fulltext`, `fulltext.relPath`)
  - Date ranges (`daterange.property`, `lowerBound`, `upperBound`)
  - Tag queries (`tagid`, `tagid.property`)
  - Sorting & ordering (`orderby`, `orderby.sort=asc|desc`)
  - Pagination (`p.limit`, `p.offset`, unlimited `-1`)
- **Drop-in HTTP Gateway:** Listens on `/bin/querybuilder.json`, forwards queries to Sling `/bin/query.json`, and reshapes responses to match AEM's exact payload structure (`success`, `results`, `total`, `offset`, `hits`).
- **CLI Utility:** Instantly compile query strings in terminal or launch the gateway proxy.
- **Docker Compose Ready:** Out-of-the-box harness to launch `apache/sling:12`.

---

## Quickstart

### 1. Installation

```bash
pip install sling-querybuilder
```

Or using `uv`:
```bash
uv pip install sling-querybuilder
```

### 2. CLI Usage

#### Compile QueryBuilder to JCR-SQL2:
```bash
sling-querybuilder compile "path=/content/novaria&type=cq:Page&1_property=jcr:content/active&1_property.value=true&p.limit=20"
```

**Output:**
```sql
--- Compiled JCR-SQL2 ---
SELECT [n].* FROM [cq:Page] AS [n] WHERE ISDESCENDANTNODE([n], '/content/novaria') AND [n].[jcr:content/active] = 'true'
--- Metadata ---
Limit:  20
Offset: 0
```

#### Launch the HTTP Gateway:
```bash
# Target local Apache Sling
sling-querybuilder gateway --sling-url http://localhost:8080 --port 8081

# Or target a remote/secondary Docker host
sling-querybuilder gateway --sling-url http://10.0.0.142:8080 --port 8081
```

Now you can send standard QueryBuilder requests directly to `http://localhost:8081/bin/querybuilder.json`!

### 3. Python Library Usage

```python
from sling_querybuilder import QueryBuilderCompiler, compile_query

# Option A: Convenience function
compiled = compile_query({
    "path": "/content/dam",
    "type": "dam:Asset",
    "1_property": "jcr:content/metadata/dc:format",
    "1_property.value": "image/jpeg",
    "orderby": "@jcr:content/jcr:lastModified",
    "orderby.sort": "desc",
    "p.limit": "50"
})

print(compiled.sql2)
# SELECT [n].* FROM [dam:Asset] AS [n] WHERE ISDESCENDANTNODE([n], '/content/dam') AND [n].[jcr:content/metadata/dc:format] = 'image/jpeg' ORDER BY [n].[jcr:content/jcr:lastModified] DESC

# Option B: Parse from URL query string
compiler = QueryBuilderCompiler.from_query_string("path=/content/site&type=cq:Page")
compiled = compiler.compile()
```

---

## Predicate Mapping Matrix

| AEM QueryBuilder Predicate | JCR-SQL2 Translation |
|---|---|
| `type=cq:Page` | `SELECT [n].* FROM [cq:Page] AS [n]` |
| *(no type specified)* | `SELECT [n].* FROM [nt:base] AS [n]` |
| `path=/content/site` | `ISDESCENDANTNODE([n], '/content/site')` |
| `path=/content/site&path.flat=true` | `ISCHILDNODE([n], '/content/site')` |
| `path=/content/site&path.self=true` | `(ISDESCENDANTNODE([n], '/content/site') OR ISSAMENODE([n], '/content/site'))` |
| `path=/content/site&path.exact=true` | `ISSAMENODE([n], '/content/site')` |
| `property=jcr:title&property.value=Home` | `[n].[jcr:title] = 'Home'` |
| `property=status&property.operation=unequals&property.value=archived` | `[n].[status] <> 'archived'` |
| `property=jcr:title&property.operation=like&property.value=%Grand%` | `[n].[jcr:title] LIKE '%Grand%'` |
| `property=cq:lastModified&property.operation=exists` | `[n].[cq:lastModified] IS NOT NULL` |
| `property=cq:lastModified&property.operation=not` | `[n].[cq:lastModified] IS NULL` |
| `fulltext=luxury resort` | `CONTAINS([n].*, 'luxury resort')` |
| `fulltext=spa&fulltext.relPath=jcr:content` | `CONTAINS([n].[jcr:content], 'spa')` |
| `orderby=@jcr:content/cq:lastModified&orderby.sort=desc` | `ORDER BY [n].[jcr:content/cq:lastModified] DESC` |

---

## Running with Docker Compose

To test with a live Apache Sling 12 container:

```bash
docker compose up -d
```

> **Tip (Secondary Docker Host Offloading):** If you run Docker on a secondary desktop or server (e.g. `desktop-server` / `10.0.0.142`) to preserve your laptop's memory:
> ```bash
> docker --context desktop-server compose up -d
> sling-querybuilder gateway --sling-url http://10.0.0.142:8080 --port 8081
> ```

Verify Sling product info:
```bash
curl -u admin:admin http://localhost:8080/system/console/status-productinfo.json
```

---

## Integration with `aem-mcp-starter-kit`

When developing Agentic AI workflows with [`aem-mcp-starter-kit`](https://github.com/fabidi/aem-mcp-starter-kit):

1. Spin up Apache Sling 12 in Docker (`docker compose up -d`).
2. Start `sling-querybuilder gateway --sling-url http://localhost:8080 --port 8081`.
3. Configure `AEM_BASE_URL=http://localhost:8081` and `AEM_MODE=onprem` in your MCP client.
4. Your AI agent can now issue standard QueryBuilder queries against open-source Apache Sling without modifying any MCP tool code!

---

## Testing

Run the full pytest suite:

```bash
pytest tests/ -v
```

---

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
