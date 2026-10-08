"""
Apache Jackrabbit Oak Index Definition Generator
=================================================
Generates enterprise-grade Oak index configurations for Apache Sling & Adobe AEM:
1. Oak PropertyIndex (exact-value lookups on single or multiple properties).
2. Oak LuceneIndex (fulltext, analyzed text, propertyIndex, ordered, and async indexing).

Supports serialization to:
- JSON (for Sling Post Servlet or HTTP PUT)
- Adobe FileVault XML (.content.xml for /oak:index/... content packages)
- Apache Sling Repoinit scripts (declarative OSGi configuration)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OakPropertyIndex:
    """Represents an Oak PropertyIndex definition."""
    name: str
    property_names: List[str]
    declaring_node_types: List[str] = field(default_factory=list)
    unique: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "jcr:primaryType": "oak:QueryIndexDefinition",
            "type": "property",
            "propertyNames": self.property_names,
            "reindex": False,
        }
        if self.declaring_node_types:
            d["declaringNodeTypes"] = self.declaring_node_types
        if self.unique:
            d["unique"] = True
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_filevault_xml(self) -> str:
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<jcr:root xmlns:jcr="http://www.jcp.org/jcr/1.0" xmlns:oak="http://jackrabbit.apache.org/oak/ns/1.0"',
            '    jcr:primaryType="oak:QueryIndexDefinition"',
            '    type="property"',
            f'    reindex="{{Boolean}}false"',
        ]
        if self.unique:
            lines.append('    unique="{Boolean}true"')
        if self.declaring_node_types:
            types_str = f"[{','.join(self.declaring_node_types)}]"
            lines.append(f'    declaringNodeTypes="{types_str}"')
        props_str = f"[{','.join(self.property_names)}]"
        lines.append(f'    propertyNames="{props_str}"/>')
        return "\n".join(lines)

    def to_repoinit(self) -> str:
        idx_path = f"/oak:index/{self.name}"
        lines = [
            f"# Oak Property Index: {self.name}",
            f"create path (oak:QueryIndexDefinition) {idx_path}",
            f"set properties on {idx_path}",
            '  set type to "property"',
            '  set reindex to false',
        ]
        props_list = ", ".join(f'"{p}"' for p in self.property_names)
        lines.append(f"  set propertyNames to {props_list}")
        if self.declaring_node_types:
            types_list = ", ".join(f'"{t}"' for t in self.declaring_node_types)
            lines.append(f"  set declaringNodeTypes to {types_list}")
        if self.unique:
            lines.append("  set unique to true")
        lines.append("end")
        return "\n".join(lines)


@dataclass
class OakLuceneIndex:
    """Represents an Oak LuceneIndex definition with indexRules and property mappings."""
    name: str
    node_type: str = "cq:Page"
    properties: List[str] = field(default_factory=list)
    async_mode: List[str] = field(default_factory=lambda: ["async", "nrt"])
    compat_version: int = 2
    fulltext: bool = True

    def to_dict(self) -> Dict[str, Any]:
        prop_defs: Dict[str, Any] = {}
        for p in self.properties:
            clean_name = p.replace(":", "_").replace("/", "_")
            prop_defs[clean_name] = {
                "jcr:primaryType": "nt:unstructured",
                "name": p,
                "propertyIndex": True,
                "ordered": True,
            }

        rule: Dict[str, Any] = {
            "jcr:primaryType": "nt:unstructured",
            "properties": {
                "jcr:primaryType": "nt:unstructured",
                **prop_defs
            }
        }

        d: Dict[str, Any] = {
            "jcr:primaryType": "oak:QueryIndexDefinition",
            "type": "lucene",
            "async": self.async_mode,
            "compatVersion": self.compat_version,
            "evaluatePathRestrictions": True,
            "reindex": False,
            "indexRules": {
                "jcr:primaryType": "nt:unstructured",
                self.node_type: rule
            }
        }
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_filevault_xml(self) -> str:
        async_str = f"[{','.join(self.async_mode)}]"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<jcr:root xmlns:jcr="http://www.jcp.org/jcr/1.0" xmlns:nt="http://www.jcp.org/jcr/nt/1.0" xmlns:oak="http://jackrabbit.apache.org/oak/ns/1.0"',
            '    jcr:primaryType="oak:QueryIndexDefinition"',
            f'    async="{async_str}"',
            f'    compatVersion="{{Long}}{self.compat_version}"',
            '    evaluatePathRestrictions="{Boolean}true"',
            '    reindex="{Boolean}false"',
            '    type="lucene">',
            '    <indexRules jcr:primaryType="nt:unstructured">',
            f'        <{self.node_type} jcr:primaryType="nt:unstructured">',
            '            <properties jcr:primaryType="nt:unstructured">',
        ]
        for p in self.properties:
            clean_name = p.replace(":", "_").replace("/", "_")
            lines.append(
                f'                <{clean_name} jcr:primaryType="nt:unstructured" name="{p}" ordered="{{Boolean}}true" propertyIndex="{{Boolean}}true"/>'
            )
        lines.extend([
            '            </properties>',
            f'        </{self.node_type}>',
            '    </indexRules>',
            '</jcr:root>',
        ])
        return "\n".join(lines)

    def to_repoinit(self) -> str:
        idx_path = f"/oak:index/{self.name}"
        async_list = ", ".join(f'"{a}"' for a in self.async_mode)
        lines = [
            f"# Oak Lucene Index: {self.name}",
            f"create path (oak:QueryIndexDefinition) {idx_path}",
            f"set properties on {idx_path}",
            '  set type to "lucene"',
            f"  set async to {async_list}",
            f"  set compatVersion to {self.compat_version}",
            "  set evaluatePathRestrictions to true",
            "  set reindex to false",
            "end",
        ]
        return "\n".join(lines)


def create_property_index(
    name: str,
    properties: List[str],
    declaring_node_types: Optional[List[str]] = None,
    unique: bool = False
) -> OakPropertyIndex:
    """Helper to instantiate an OakPropertyIndex."""
    return OakPropertyIndex(
        name=name,
        property_names=properties,
        declaring_node_types=declaring_node_types or [],
        unique=unique
    )


def create_lucene_index(
    name: str,
    node_type: str = "cq:Page",
    properties: Optional[List[str]] = None,
    fulltext: bool = True,
    async_mode: Optional[List[str]] = None
) -> OakLuceneIndex:
    """Helper to instantiate an OakLuceneIndex."""
    return OakLuceneIndex(
        name=name,
        node_type=node_type,
        properties=properties or [],
        fulltext=fulltext,
        async_mode=async_mode or ["async", "nrt"]
    )
