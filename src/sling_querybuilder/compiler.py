"""
QueryBuilder to JCR-SQL2 Compiler
=================================
Compiles AEM QueryBuilder predicate dictionaries and query strings into
standard JCR-SQL2 statements compatible with Apache Sling & Jackrabbit Oak.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _escape(val: Any) -> str:
    """Escapes single quotes for JCR-SQL2 literal."""
    return str(val).replace("'", "''")


@dataclass
class CompiledQuery:
    """Represents a compiled JCR-SQL2 query and pagination metadata."""
    sql2: str
    limit: Optional[int] = 10
    offset: int = 0
    selector: str = "n"
    parameters: Dict[str, Any] = field(default_factory=dict)


class QueryBuilderCompiler:
    """
    Translates AEM QueryBuilder predicate parameters into standard JCR-SQL2 queries.
    """

    DEFAULT_SELECTOR = "n"

    def __init__(self, predicates: Dict[str, Any], selector: str = DEFAULT_SELECTOR):
        self.raw_predicates = predicates
        self.selector = selector
        # Flatten and normalize parameters
        self.params: Dict[str, Any] = {}
        for k, v in predicates.items():
            if isinstance(v, list) and len(v) == 1:
                self.params[str(k).strip()] = v[0]
            else:
                self.params[str(k).strip()] = v

    @classmethod
    def from_query_string(cls, query_string: str, selector: str = DEFAULT_SELECTOR) -> QueryBuilderCompiler:
        """Parses a standard URL query string into predicate dictionary."""
        parsed = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        # Flatten single item lists
        predicates: Dict[str, Any] = {}
        for k, v in parsed.items():
            predicates[k] = v[0] if len(v) == 1 else v
        return cls(predicates, selector=selector)

    def compile(self) -> CompiledQuery:
        """Executes compilation from QueryBuilder predicates to JCR-SQL2."""
        selector = self.selector
        
        # 1. Determine node type
        node_type = self.params.get("type", "nt:base").strip()

        # 2. Gather WHERE clauses
        where_clauses: List[str] = []

        # Path predicates
        self._compile_path(where_clauses)

        # Property predicates (including numbered: 1_property, 2_property, etc.)
        self._compile_properties(where_clauses)

        # Fulltext search
        self._compile_fulltext(where_clauses)

        # Date ranges
        self._compile_daterange(where_clauses)

        # Tag predicates
        self._compile_tags(where_clauses)

        # Build base SQL2 SELECT
        sql = f"SELECT [{selector}].* FROM [{node_type}] AS [{selector}]"

        if where_clauses:
            sql += f" WHERE {' AND '.join(where_clauses)}"

        # Ordering
        order_clause = self._compile_orderby()
        if order_clause:
            sql += f" {order_clause}"

        # Pagination metadata
        limit, offset = self._extract_pagination()

        return CompiledQuery(
            sql2=sql,
            limit=limit,
            offset=offset,
            selector=selector,
            parameters=self.params
        )

    def _compile_path(self, where_clauses: List[str]) -> None:
        """Compiles path predicate and modifiers (path.self, path.flat, path.exact)."""
        path = self.params.get("path")
        if not path or not isinstance(path, str):
            return

        clean_path = path.strip().rstrip("/")
        if not clean_path:
            clean_path = "/"

        exact = str(self.params.get("path.exact", "false")).lower() in ("true", "1")
        flat = str(self.params.get("path.flat", "false")).lower() in ("true", "1")
        include_self = str(self.params.get("path.self", "false")).lower() in ("true", "1")

        s = self.selector
        if exact:
            where_clauses.append(f"ISSAMENODE([{s}], '{clean_path}')")
        elif flat:
            where_clauses.append(f"ISCHILDNODE([{s}], '{clean_path}')")
        elif include_self:
            where_clauses.append(
                f"(ISDESCENDANTNODE([{s}], '{clean_path}') OR ISSAMENODE([{s}], '{clean_path}'))"
            )
        else:
            where_clauses.append(f"ISDESCENDANTNODE([{s}], '{clean_path}')")

    def _compile_properties(self, where_clauses: List[str]) -> None:
        """Finds and compiles all property predicate groups (e.g. 'property', '1_property')."""
        prefixes = set()
        for k in self.params:
            match = re.match(r"^(\d+_)?property$", k)
            if match:
                prefixes.add(match.group(1) or "")

        # Sort prefixes deterministically (empty prefix first, then numbered)
        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))

        s = self.selector
        for prefix in sorted_prefixes:
            prop_key = f"{prefix}property"
            prop_name = str(self.params.get(prop_key, "")).strip().lstrip("@")
            if not prop_name:
                continue

            op = str(self.params.get(f"{prefix}property.operation", "equals")).lower().strip()
            is_and = str(self.params.get(f"{prefix}property.and", "false")).lower() in ("true", "1")

            # Collect values
            values: List[str] = []
            direct_val = self.params.get(f"{prefix}property.value")
            if direct_val is not None:
                if isinstance(direct_val, list):
                    values.extend([str(v) for v in direct_val])
                else:
                    values.append(str(direct_val))

            # Numbered values: property.1_value, property.2_value
            numbered_vals = []
            for k, v in self.params.items():
                val_match = re.match(rf"^{re.escape(prefix)}property\.(\d+)_value$", k)
                if val_match:
                    numbered_vals.append((int(val_match.group(1)), str(v)))
            numbered_vals.sort(key=lambda x: x[0])
            for _, v in numbered_vals:
                values.append(v)

            # Build condition for this property group
            if op == "exists":
                where_clauses.append(f"[{s}].[{prop_name}] IS NOT NULL")
            elif op == "not":
                where_clauses.append(f"[{s}].[{prop_name}] IS NULL")
            elif op == "unequals":
                if values:
                    val = values[0].replace("'", "''")
                    where_clauses.append(f"[{s}].[{prop_name}] <> '{val}'")
            elif op == "like":
                if values:
                    val = values[0].replace("'", "''")
                    where_clauses.append(f"[{s}].[{prop_name}] LIKE '{val}'")
            else:  # default 'equals'
                if len(values) == 1:
                    val = _escape(values[0])
                    where_clauses.append(f"[{s}].[{prop_name}] = '{val}'")
                elif len(values) > 1:
                    connector = " AND " if is_and else " OR "
                    sub = [f"[{s}].[{prop_name}] = '{_escape(v)}'" for v in values]
                    where_clauses.append(f"({connector.join(sub)})")

    def _compile_fulltext(self, where_clauses: List[str]) -> None:
        """Compiles fulltext search predicate."""
        fulltext = self.params.get("fulltext")
        if not fulltext:
            return
        
        rel_path = self.params.get("fulltext.relPath")
        s = self.selector
        escaped_text = str(fulltext).replace("'", "''")
        if rel_path:
            clean_rel = str(rel_path).strip().lstrip("@")
            where_clauses.append(f"CONTAINS([{s}].[{clean_rel}], '{escaped_text}')")
        else:
            where_clauses.append(f"CONTAINS([{s}].*, '{escaped_text}')")

    def _compile_daterange(self, where_clauses: List[str]) -> None:
        """Compiles date range predicates."""
        prop = self.params.get("daterange.property")
        if not prop:
            return
        
        clean_prop = str(prop).strip().lstrip("@")
        s = self.selector
        lower = self.params.get("daterange.lowerBound")
        upper = self.params.get("daterange.upperBound")

        if lower:
            escaped_lower = str(lower).replace("'", "''")
            where_clauses.append(f"[{s}].[{clean_prop}] >= CAST('{escaped_lower}' AS DATE)")
        if upper:
            escaped_upper = str(upper).replace("'", "''")
            where_clauses.append(f"[{s}].[{clean_prop}] <= CAST('{escaped_upper}' AS DATE)")

    def _compile_tags(self, where_clauses: List[str]) -> None:
        """Compiles tagid predicate."""
        tagid = self.params.get("tagid")
        if not tagid:
            return
        
        tag_prop = self.params.get("tagid.property", "jcr:content/cq:tags")
        clean_prop = str(tag_prop).strip().lstrip("@")
        s = self.selector
        escaped_tag = str(tagid).replace("'", "''")
        where_clauses.append(f"[{s}].[{clean_prop}] = '{escaped_tag}'")

    def _compile_orderby(self) -> Optional[str]:
        """Compiles ORDER BY clause."""
        orderby = self.params.get("orderby")
        if not orderby:
            return None

        clean_prop = str(orderby).strip().lstrip("@")
        sort_dir = str(self.params.get("orderby.sort", "asc")).upper().strip()
        if sort_dir not in ("ASC", "DESC"):
            sort_dir = "ASC"

        s = self.selector
        return f"ORDER BY [{s}].[{clean_prop}] {sort_dir}"

    def _extract_pagination(self) -> tuple[Optional[int], int]:
        """Extracts p.limit and p.offset parameters."""
        raw_limit = self.params.get("p.limit", "10")
        try:
            limit_val = int(raw_limit)
            limit = None if limit_val < 0 else limit_val
        except (ValueError, TypeError):
            limit = 10

        raw_offset = self.params.get("p.offset", "0")
        try:
            offset = max(0, int(raw_offset))
        except (ValueError, TypeError):
            offset = 0

        return limit, offset


def compile_query(predicates: Dict[str, Any], selector: str = "n") -> CompiledQuery:
    """Convenience helper function to compile a predicate map directly."""
    return QueryBuilderCompiler(predicates, selector=selector).compile()
