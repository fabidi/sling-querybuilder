"""
Command Line Interface for sling-querybuilder
=============================================
Provides CLI tools to compile QueryBuilder queries into JCR-SQL2
and launch the QueryBuilder HTTP gateway proxy.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .compiler import QueryBuilderCompiler, compile_query
from .gateway import QueryBuilderGateway


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sling-querybuilder",
        description="QueryBuilder to JCR-SQL2 compiler and runtime gateway for Apache Sling",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: compile
    compile_p = subparsers.add_parser("compile", help="Compile QueryBuilder predicates to JCR-SQL2")
    compile_p.add_argument(
        "query",
        type=str,
        help="QueryBuilder query string (e.g. 'path=/content&type=cq:Page&p.limit=20')",
    )
    compile_p.add_argument(
        "--selector",
        type=str,
        default="n",
        help="SQL2 selector alias (default: 'n')",
    )

    # Subcommand: explain
    explain_p = subparsers.add_parser("explain", help="Compile QueryBuilder predicates and analyze Oak query plan")
    explain_p.add_argument(
        "query",
        type=str,
        help="QueryBuilder query string (e.g. 'path=/content&type=cq:Page&1_property=hotelId&1_property.value=123')",
    )
    explain_p.add_argument(
        "--selector",
        type=str,
        default="n",
        help="SQL2 selector alias (default: 'n')",
    )

    # Subcommand: index-def
    idx_p = subparsers.add_parser("index-def", help="Generate Apache Jackrabbit Oak index definitions")
    idx_p.add_argument(
        "--name",
        type=str,
        required=True,
        help="Name of the Oak index definition (e.g. 'hotelIdIndex' or 'novariaHotelLucene')",
    )
    idx_p.add_argument(
        "--type",
        type=str,
        choices=["property", "lucene"],
        default="property",
        help="Index type: 'property' or 'lucene' (default: 'property')",
    )
    idx_p.add_argument(
        "--properties",
        type=str,
        required=True,
        help="Comma-separated property names to index (e.g. 'hotelId,brandId,authoredStarRating')",
    )
    idx_p.add_argument(
        "--node-type",
        type=str,
        default="cq:Page",
        help="Target JCR node type for Lucene indexRules (default: 'cq:Page')",
    )
    idx_p.add_argument(
        "--format",
        type=str,
        choices=["json", "filevault", "repoinit"],
        default="json",
        help="Output serialization format: 'json', 'filevault' (.content.xml), or 'repoinit' (default: 'json')",
    )

    # Subcommand: gateway
    gateway_p = subparsers.add_parser("gateway", help="Run HTTP gateway for Apache Sling")
    gateway_p.add_argument(
        "--sling-url",
        type=str,
        default="http://localhost:8080",
        help="Base URL of target Apache Sling instance (e.g. http://localhost:8080 or http://10.0.0.142:8080)",
    )
    gateway_p.add_argument(
        "--auth",
        type=str,
        default="admin:admin",
        help="Basic authentication credentials for Sling in 'user:password' format (default: admin:admin)",
    )
    gateway_p.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host interface to bind gateway HTTP server (default: 0.0.0.0)",
    )
    gateway_p.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Port to bind gateway HTTP server (default: 8081)",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "compile":
        try:
            compiler = QueryBuilderCompiler.from_query_string(args.query, selector=args.selector)
            compiled = compiler.compile()
            print("--- Compiled JCR-SQL2 ---")
            print(compiled.sql2)
            print("--- Metadata ---")
            print(f"Limit:  {compiled.limit if compiled.limit is not None else 'Unlimited'}")
            print(f"Offset: {compiled.offset}")
            return 0
        except Exception as e:
            sys.stderr.write(f"Compilation error: {e}\n")
            return 1

    elif args.command == "explain":
        try:
            compiler = QueryBuilderCompiler.from_query_string(args.query, selector=args.selector)
            compiled = compiler.compile(explain=True)
            gw = QueryBuilderGateway()
            plan = gw._synthesize_oak_plan(compiled)
            analysis = gw.parse_oak_plan(plan, compiled)

            print("--- Compiled Oak EXPLAIN Query ---")
            print(compiled.sql2)
            print("\n--- Oak Query Plan & Index Analysis ---")
            print(f"Index Used:    {analysis['index_used']}")
            print(f"Is Traversal:  {analysis['is_traversal']}")
            print(f"Risk Level:    {analysis['risk_level']}")
            print(f"Advice:        {analysis['recommendation']}")
            print(f"Plan:          {analysis['plan']}")
            return 0
        except Exception as e:
            sys.stderr.write(f"Explain error: {e}\n")
            return 1

    elif args.command == "index-def":
        try:
            from .oak_index import create_lucene_index, create_property_index
            prop_list = [p.strip() for p in args.properties.split(",") if p.strip()]

            if args.type == "lucene":
                idx = create_lucene_index(name=args.name, node_type=args.node_type, properties=prop_list)
            else:
                idx = create_property_index(name=args.name, properties=prop_list)

            if args.format == "filevault":
                print(idx.to_filevault_xml())
            elif args.format == "repoinit":
                print(idx.to_repoinit())
            else:
                print(idx.to_json())
            return 0
        except Exception as e:
            sys.stderr.write(f"Index definition error: {e}\n")
            return 1

    elif args.command == "gateway":
        auth_tuple = None
        if args.auth:
            parts = args.auth.split(":", 1)
            auth_tuple = (parts[0], parts[1] if len(parts) > 1 else "")

        gateway = QueryBuilderGateway(
            sling_base_url=args.sling_url,
            sling_auth=auth_tuple,
            host=args.host,
            port=args.port,
        )
        print(f"Starting sling-querybuilder gateway on http://{args.host}:{args.port}")
        print(f"Targeting Apache Sling at {args.sling_url}")
        print("Press Ctrl+C to stop.")
        try:
            gateway.start(blocking=True)
        except KeyboardInterrupt:
            print("\nShutting down gateway...")
            gateway.stop()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
