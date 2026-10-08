"""
QueryBuilder to JCR-SQL2 Compiler
=================================
Compiles AEM QueryBuilder predicate dictionaries and query strings into
standard JCR-SQL2 statements compatible with Apache Sling & Jackrabbit Oak.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def _escape(val: Any) -> str:
    """Escapes single quotes for JCR-SQL2 literal."""
    return str(val).replace("'", "''")


def _parse_relative_date(date_str: str) -> str:
    """
    Parses relative date strings (e.g., -1d, -7d, -1w, -1M, -1y, +2d)
    into an ISO-8601 UTC timestamp.
    """
    date_str = date_str.strip()
    match = re.match(r"^([+-]?\d+)([dDwWmMyY])$", date_str)
    if not match:
        return date_str

    amount = int(match.group(1))
    unit = match.group(2).lower()
    now = datetime.now(timezone.utc)

    if unit == "d":
        target = now + timedelta(days=amount)
    elif unit == "w":
        target = now + timedelta(weeks=amount)
    elif unit == "m":
        target = now + timedelta(days=amount * 30)
    elif unit == "y":
        target = now + timedelta(days=amount * 365)
    else:
        target = now

    return target.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass
class CompiledQuery:
    """Represents a compiled JCR-SQL2 query and pagination metadata."""
    sql2: str
    limit: Optional[int] = 10
    offset: int = 0
    selector: str = "n"
    parameters: Dict[str, Any] = field(default_factory=dict)
    is_explain: bool = False


class QueryBuilderCompiler:
    """
    Translates AEM QueryBuilder predicate parameters into standard JCR-SQL2 queries.
    Supports path filters, property matching, fulltext search, date ranges,
    tags, ordering, pagination, and recursive boolean groups (1_group.p.or).
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
        predicates: Dict[str, Any] = {}
        for k, v in parsed.items():
            predicates[k] = v[0] if len(v) == 1 else v
        return cls(predicates, selector=selector)

    def compile(self, explain: bool = False) -> CompiledQuery:
        """Executes compilation from QueryBuilder predicates to JCR-SQL2."""
        selector = self.selector
        
        # 1. Determine node type
        node_type = self.params.get("type", "nt:base").strip()

        # 2. Gather WHERE clauses
        where_clauses: List[str] = self._compile_params(self.params)

        # Check if explain is requested via argument or p.explain=true
        is_explain = explain or str(self.params.get("p.explain", "false")).lower() in ("true", "1")
        prefix = "EXPLAIN " if is_explain else ""

        # Build base SQL2 SELECT
        sql = f"{prefix}SELECT [{selector}].* FROM [{node_type}] AS [{selector}]"

        if where_clauses:
            sql += f" WHERE {' AND '.join(where_clauses)}"

        # Ordering
        order_clause = self._compile_orderby(self.params)
        if order_clause:
            sql += f" {order_clause}"

        # Pagination metadata
        limit, offset = self._extract_pagination(self.params)

        return CompiledQuery(
            sql2=sql,
            limit=limit,
            offset=offset,
            selector=selector,
            parameters=self.params,
            is_explain=is_explain
        )

    def _compile_params(self, params: Dict[str, Any]) -> List[str]:
        """Compiles a flat dictionary of predicates into a list of SQL2 WHERE expressions."""
        clauses: List[str] = []

        # 1. Path predicates (including 1_path, 2_path)
        path_clauses = self._compile_path(params)
        clauses.extend(path_clauses)

        # 2. Node name predicates (nodename, 1_nodename, etc.)
        nodename_clauses = self._compile_nodenames(params)
        clauses.extend(nodename_clauses)

        # 3. Direct property predicates
        prop_clauses = self._compile_properties(params)
        clauses.extend(prop_clauses)

        # 4. Fulltext search (fulltext, 1_fulltext, etc.)
        ft_clauses = self._compile_fulltext(params)
        clauses.extend(ft_clauses)

        # 5. Date ranges (daterange, 1_daterange, etc.)
        dr_clauses = self._compile_daterange(params)
        clauses.extend(dr_clauses)

        # 6. Tag predicates
        tag_clauses = self._compile_tags(params)
        clauses.extend(tag_clauses)

        # 7. Group predicates (e.g. 1_group, 2_group, group)
        group_clauses = self._compile_groups(params)
        clauses.extend(group_clauses)

        return clauses

    def _compile_path(self, params: Dict[str, Any]) -> List[str]:
        """Compiles path predicates and numbered paths (1_path, 2_path) with modifiers."""
        prefixes = set()
        for k in params:
            match = re.match(r"^(\d+_)?path$", k)
            if match:
                prefixes.add(match.group(1) or "")

        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))
        s = self.selector
        
        path_clauses: List[str] = []
        for prefix in sorted_prefixes:
            path_val = params.get(f"{prefix}path")
            if not path_val or not isinstance(path_val, str):
                continue

            clean_path = path_val.strip().rstrip("/") or "/"
            exact = str(params.get(f"{prefix}path.exact", params.get("path.exact", "false"))).lower() in ("true", "1")
            flat = str(params.get(f"{prefix}path.flat", params.get("path.flat", "false"))).lower() in ("true", "1")
            include_self = str(params.get(f"{prefix}path.self", params.get("path.self", "false"))).lower() in ("true", "1")

            if exact:
                path_clauses.append(f"ISSAMENODE([{s}], '{clean_path}')")
            elif flat:
                path_clauses.append(f"ISCHILDNODE([{s}], '{clean_path}')")
            elif include_self:
                path_clauses.append(f"(ISDESCENDANTNODE([{s}], '{clean_path}') OR ISSAMENODE([{s}], '{clean_path}'))")
            else:
                path_clauses.append(f"ISDESCENDANTNODE([{s}], '{clean_path}')")

        if len(path_clauses) == 1:
            return [path_clauses[0]]
        elif len(path_clauses) > 1:
            return [f"({' OR '.join(path_clauses)})"]
        return []

    def _compile_nodenames(self, params: Dict[str, Any]) -> List[str]:
        """Compiles nodename predicates (e.g. 'nodename', '1_nodename')."""
        clauses: List[str] = []
        prefixes = set()
        for k in params:
            match = re.match(r"^(\d+_)?nodename$", k)
            if match:
                prefixes.add(match.group(1) or "")

        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))
        s = self.selector
        for prefix in sorted_prefixes:
            name_val = str(params.get(f"{prefix}nodename", "")).strip()
            if not name_val:
                continue
            if "*" in name_val:
                like_val = name_val.replace("*", "%")
                clauses.append(f"NAME([{s}]) LIKE '{_escape(like_val)}'")
            else:
                clauses.append(f"NAME([{s}]) = '{_escape(name_val)}'")
        return clauses

    def _compile_properties(self, params: Dict[str, Any]) -> List[str]:
        """Finds and compiles all direct property predicate groups (e.g. 'property', '1_property')."""
        clauses: List[str] = []
        prefixes = set()
        for k in params:
            match = re.match(r"^(\d+_)?property$", k)
            if match:
                prefixes.add(match.group(1) or "")

        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))
        s = self.selector
        for prefix in sorted_prefixes:
            prop_key = f"{prefix}property"
            prop_name = str(params.get(prop_key, "")).strip().lstrip("@")
            if not prop_name:
                continue

            op = str(params.get(f"{prefix}property.operation", "equals")).lower().strip()
            is_and = str(params.get(f"{prefix}property.and", "false")).lower() in ("true", "1")

            # Collect values
            values: List[str] = []
            direct_val = params.get(f"{prefix}property.value")
            if direct_val is not None:
                if isinstance(direct_val, list):
                    values.extend([str(v) for v in direct_val])
                else:
                    values.append(str(direct_val))

            # Numbered values: property.1_value, property.2_value
            numbered_vals = []
            for k, v in params.items():
                val_match = re.match(rf"^{re.escape(prefix)}property\.(\d+)_value$", k)
                if val_match:
                    numbered_vals.append((int(val_match.group(1)), str(v)))
            numbered_vals.sort(key=lambda x: x[0])
            for _, v in numbered_vals:
                values.append(v)

            # Build condition for this property group
            if op == "exists":
                clauses.append(f"[{s}].[{prop_name}] IS NOT NULL")
            elif op == "not":
                clauses.append(f"[{s}].[{prop_name}] IS NULL")
            elif op == "unequals":
                if len(values) == 1:
                    clauses.append(f"[{s}].[{prop_name}] <> '{_escape(values[0])}'")
                elif len(values) > 1:
                    sub = [f"[{s}].[{prop_name}] <> '{_escape(v)}'" for v in values]
                    clauses.append(f"({' AND '.join(sub)})")
            elif op == "like":
                if len(values) == 1:
                    clauses.append(f"[{s}].[{prop_name}] LIKE '{_escape(values[0])}'")
                elif len(values) > 1:
                    connector = " AND " if is_and else " OR "
                    sub = [f"[{s}].[{prop_name}] LIKE '{_escape(v)}'" for v in values]
                    clauses.append(f"({connector.join(sub)})")
            elif op == "contains":
                formatted_vals = [v if ("%" in v or "_" in v) else f"%{v}%" for v in values]
                if len(formatted_vals) == 1:
                    clauses.append(f"[{s}].[{prop_name}] LIKE '{_escape(formatted_vals[0])}'")
                elif len(formatted_vals) > 1:
                    connector = " AND " if is_and else " OR "
                    sub = [f"[{s}].[{prop_name}] LIKE '{_escape(v)}'" for v in formatted_vals]
                    clauses.append(f"({connector.join(sub)})")
            elif op in (">", "greater"):
                if values:
                    clauses.append(f"[{s}].[{prop_name}] > '{_escape(values[0])}'")
            elif op in (">=", "greater_equal"):
                if values:
                    clauses.append(f"[{s}].[{prop_name}] >= '{_escape(values[0])}'")
            elif op in ("<", "lower"):
                if values:
                    clauses.append(f"[{s}].[{prop_name}] < '{_escape(values[0])}'")
            elif op in ("<=", "lower_equal"):
                if values:
                    clauses.append(f"[{s}].[{prop_name}] <= '{_escape(values[0])}'")
            else:  # default 'equals'
                if len(values) == 1:
                    val = _escape(values[0])
                    clauses.append(f"[{s}].[{prop_name}] = '{val}'")
                elif len(values) > 1:
                    connector = " AND " if is_and else " OR "
                    sub = [f"[{s}].[{prop_name}] = '{_escape(v)}'" for v in values]
                    clauses.append(f"({connector.join(sub)})")

        return clauses

    def _compile_groups(self, params: Dict[str, Any]) -> List[str]:
        """
        Extracts and recursively compiles boolean groups (e.g. '1_group.property', '1_group.p.or').
        """
        group_keys: Dict[str, Dict[str, Any]] = {}
        for k, v in params.items():
            match = re.match(r"^(\d+_)?group\.(.+)$", k)
            if match:
                grp_prefix = match.group(1) or ""
                sub_key = match.group(2)
                if grp_prefix not in group_keys:
                    group_keys[grp_prefix] = {}
                group_keys[grp_prefix][sub_key] = v

        sorted_groups = sorted(list(group_keys.keys()), key=lambda x: (int(x.rstrip("_")) if x else -1))
        clauses: List[str] = []

        for grp in sorted_groups:
            sub_params = group_keys[grp]
            is_or = str(sub_params.get("p.or", "false")).lower() in ("true", "1")
            is_not = str(sub_params.get("p.not", "false")).lower() in ("true", "1")

            sub_clauses = self._compile_params(sub_params)
            if not sub_clauses:
                continue

            connector = " OR " if is_or else " AND "
            if len(sub_clauses) == 1 and not is_not:
                clauses.append(sub_clauses[0])
            else:
                combined = f"({connector.join(sub_clauses)})"
                if is_not:
                    combined = f"NOT {combined}"
                clauses.append(combined)

        return clauses

    def _compile_fulltext(self, params: Dict[str, Any]) -> List[str]:
        """Compiles fulltext search predicates (fulltext, 1_fulltext, etc.)."""
        clauses: List[str] = []
        prefixes = set()
        for k in params:
            match = re.match(r"^(\d+_)?fulltext$", k)
            if match:
                prefixes.add(match.group(1) or "")

        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))
        s = self.selector
        for prefix in sorted_prefixes:
            text = params.get(f"{prefix}fulltext")
            if not text:
                continue
            rel_path = params.get(f"{prefix}fulltext.relPath")
            escaped_text = _escape(text)
            if rel_path:
                clean_rel = str(rel_path).strip().lstrip("@")
                clauses.append(f"CONTAINS([{s}].[{clean_rel}], '{escaped_text}')")
            else:
                clauses.append(f"CONTAINS([{s}].*, '{escaped_text}')")
        return clauses

    def _compile_daterange(self, params: Dict[str, Any]) -> List[str]:
        """Compiles date range predicates (daterange, 1_daterange, etc.) with relative date math."""
        clauses: List[str] = []
        prefixes = set()
        for k in params:
            match = re.match(r"^(\d+_)?daterange(\..+)?$", k)
            if match:
                prefixes.add(match.group(1) or "")

        sorted_prefixes = sorted(list(prefixes), key=lambda x: (int(x.rstrip("_")) if x else -1))
        s = self.selector

        for prefix in sorted_prefixes:
            prop = params.get(f"{prefix}daterange.property", "jcr:content/cq:lastModified")
            clean_prop = str(prop).strip().lstrip("@")
            lower = params.get(f"{prefix}daterange.lowerBound")
            upper = params.get(f"{prefix}daterange.upperBound")
            lower_op = str(params.get(f"{prefix}daterange.lowerOperation", ">=")).strip()
            upper_op = str(params.get(f"{prefix}daterange.upperOperation", "<=")).strip()

            if lower:
                parsed_lower = _parse_relative_date(str(lower))
                escaped_lower = _escape(parsed_lower)
                clauses.append(f"[{s}].[{clean_prop}] {lower_op} CAST('{escaped_lower}' AS DATE)")
            if upper:
                parsed_upper = _parse_relative_date(str(upper))
                escaped_upper = _escape(parsed_upper)
                clauses.append(f"[{s}].[{clean_prop}] {upper_op} CAST('{escaped_upper}' AS DATE)")

        return clauses

    def _compile_tags(self, params: Dict[str, Any]) -> List[str]:
        """Compiles tagid predicates with support for multiple numbered tags and AND/OR."""
        clauses: List[str] = []
        tag_prop = params.get("tagid.property", "jcr:content/cq:tags")
        clean_prop = str(tag_prop).strip().lstrip("@")
        is_and = str(params.get("tagid.and", "false")).lower() in ("true", "1")
        s = self.selector

        tag_values: List[str] = []
        if "tagid" in params and params["tagid"]:
            val = params["tagid"]
            if isinstance(val, list):
                tag_values.extend([str(v) for v in val])
            else:
                tag_values.append(str(val))

        # Check numbered tags: 1_tagid, 2_tagid
        numbered_tags = []
        for k, v in params.items():
            match = re.match(r"^(\d+)_tagid$", k)
            if match and v:
                numbered_tags.append((int(match.group(1)), str(v)))
        numbered_tags.sort(key=lambda x: x[0])
        for _, v in numbered_tags:
            tag_values.append(v)

        if len(tag_values) == 1:
            clauses.append(f"[{s}].[{clean_prop}] = '{_escape(tag_values[0])}'")
        elif len(tag_values) > 1:
            connector = " AND " if is_and else " OR "
            sub = [f"[{s}].[{clean_prop}] = '{_escape(v)}'" for v in tag_values]
            clauses.append(f"({connector.join(sub)})")

        return clauses

    def _compile_orderby(self, params: Dict[str, Any]) -> Optional[str]:
        """Compiles ORDER BY clause."""
        orderby = params.get("orderby")
        if not orderby:
            return None

        clean_prop = str(orderby).strip().lstrip("@")
        sort_dir = str(params.get("orderby.sort", "asc")).upper().strip()
        if sort_dir not in ("ASC", "DESC"):
            sort_dir = "ASC"

        s = self.selector
        return f"ORDER BY [{s}].[{clean_prop}] {sort_dir}"

    def _extract_pagination(self, params: Dict[str, Any]) -> Tuple[Optional[int], int]:
        """Extracts p.limit and p.offset parameters."""
        raw_limit = params.get("p.limit", "10")
        try:
            limit_val = int(raw_limit)
            limit = None if limit_val < 0 else limit_val
        except (ValueError, TypeError):
            limit = 10

        raw_offset = params.get("p.offset", "0")
        try:
            offset = max(0, int(raw_offset))
        except (ValueError, TypeError):
            offset = 0

        return limit, offset


def compile_query(predicates: Dict[str, Any], selector: str = "n", explain: bool = False) -> CompiledQuery:
    """Convenience helper function to compile a predicate map directly."""
    return QueryBuilderCompiler(predicates, selector=selector).compile(explain=explain)
