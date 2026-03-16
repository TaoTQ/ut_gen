#!/usr/bin/env python3
"""Parse JaCoCo XML and emit a unified coverage JSON contract."""

import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "ut_coverage_v1"


def _counter(element, ctype: str) -> dict:
    for counter in element.findall("counter"):
        if counter.get("type") == ctype:
            missed = int(counter.get("missed", 0))
            covered = int(counter.get("covered", 0))
            total = covered + missed
            pct = round(covered / total * 100, 2) if total else 0.0
            return {"covered": covered, "missed": missed, "total": total, "pct": pct}
    return {"covered": 0, "missed": 0, "total": 0, "pct": 0.0}


def parse(xml_path: str) -> dict:
    xml_file = Path(xml_path)
    if not xml_file.exists():
        return {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "jacoco_xml": str(xml_file),
            },
            "summary": {"error": f"JaCoCo XML not found: {xml_path}"},
            "coverage": {
                "line": {"covered": 0, "missed": 0, "total": 0, "pct": 0.0},
                "branch": {"covered": 0, "missed": 0, "total": 0, "pct": 0.0},
                "method": {"covered": 0, "missed": 0, "total": 0, "pct": 0.0},
            },
            "packages": [],
            "classes": {},
            "_total": {"line": 0.0, "branch": 0.0, "method": 0.0},
        }

    tree = ET.parse(str(xml_file))
    root = tree.getroot()

    classes = {}
    packages = []
    for package in root.findall("package"):
        package_name = package.get("name", "").replace("/", ".")
        package_entry = {
            "package": package_name,
            "line": _counter(package, "LINE"),
            "branch": _counter(package, "BRANCH"),
            "method": _counter(package, "METHOD"),
            "classes": [],
        }

        for cls in package.findall("class"):
            raw_name = cls.get("name", "").split("/")[-1]
            if "$" in raw_name:
                continue

            methods = {}
            for method in cls.findall("method"):
                method_name = method.get("name")
                descriptor = method.get("desc", "")
                if method_name in ("<init>", "<clinit>"):
                    continue
                method_key = method_name if method_name not in methods else f"{method_name}({descriptor})"
                methods[method_key] = {
                    "line": _counter(method, "LINE")["pct"],
                    "branch": _counter(method, "BRANCH")["pct"],
                }

            class_entry = {
                "line": _counter(cls, "LINE")["pct"],
                "branch": _counter(cls, "BRANCH")["pct"],
                "method": _counter(cls, "METHOD")["pct"],
                "methods": methods,
            }
            classes[raw_name] = class_entry
            package_entry["classes"].append({"class": raw_name, **class_entry})

        packages.append(package_entry)

    coverage = {
        "line": _counter(root, "LINE"),
        "branch": _counter(root, "BRANCH"),
        "method": _counter(root, "METHOD"),
    }

    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "jacoco_xml": str(xml_file),
        },
        "coverage": coverage,
        "packages": packages,
        "classes": classes,
        # Backward-compatible totals used by historical scripts.
        "_total": {
            "line": coverage["line"]["pct"],
            "branch": coverage["branch"]["pct"],
            "method": coverage["method"]["pct"],
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_coverage.py <jacoco.xml>", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(parse(sys.argv[1]), indent=2, ensure_ascii=False))
