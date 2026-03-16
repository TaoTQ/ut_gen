#!/usr/bin/env python3
"""
collect.py — 扫描 Java 项目源码，生成 all_classes.json 数据集。

用法:
    python3 datasets/collect.py [--config project.json]
"""

import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from fnmatch import fnmatch


def load_config(config_path="project.json"):
    with open(config_path) as f:
        return json.load(f)


def should_exclude(filename, exclude_patterns):
    return any(fnmatch(filename, p) for p in exclude_patterns)


def extract_package(content):
    m = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
    return m.group(1) if m else ""


def extract_public_methods(content):
    """提取 public 方法名（排除构造器、关键字）"""
    # 匹配 public [static] [final] <returnType> <methodName>(
    pattern = re.compile(
        r'^\s*public\s+(?:(?:static|final|synchronized|abstract)\s+)*'
        r'(?:[\w<>\[\],\s]+?)\s+(\w+)\s*\(',
        re.MULTILINE
    )
    reserved = {'class', 'interface', 'enum', 'if', 'while', 'for', 'switch'}
    methods = []
    for m in pattern.finditer(content):
        name = m.group(1)
        if name not in reserved and not name[0].isupper():
            methods.append(name)
    return list(dict.fromkeys(methods))  # 去重保序


def complexity_bucket(line_count):
    if line_count < 150:
        return "low"
    elif line_count < 500:
        return "medium"
    else:
        return "high"


def collect_classes(config):
    source_base = Path(config["source_base"])
    test_base = Path(config["test_base"])
    exclude_patterns = config.get("exclude_patterns", [])

    classes = []
    for java_file in sorted(source_base.rglob("*.java")):
        if should_exclude(java_file.name, exclude_patterns):
            continue

        content = java_file.read_text(encoding="utf-8", errors="replace")
        package = extract_package(content)
        class_name = java_file.stem
        lines = content.splitlines()
        public_methods = extract_public_methods(content)

        # 推断测试文件路径
        pkg_path = package.replace(".", "/") if package else ""
        test_file = test_base / pkg_path / f"{class_name}Test.java"

        classes.append({
            "id": class_name,
            "class_name": class_name,
            "fqn": f"{package}.{class_name}" if package else class_name,
            "package": package,
            "source_path": str(java_file),
            "test_output_path": str(test_file),
            "test_class_name": f"{class_name}Test",
            "line_count": len(lines),
            "public_method_count": len(public_methods),
            "public_methods": public_methods,
            "complexity_bucket": complexity_bucket(len(lines)),
        })

    return classes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="project.json")
    parser.add_argument("--output", default="datasets/all_classes.json")
    args = parser.parse_args()

    config = load_config(args.config)
    classes = collect_classes(config)

    dataset = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "project": config.get("name", "unknown"),
        "source_base": config["source_base"],
        "total_classes": len(classes),
        "classes": classes,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"✓ Collected {len(classes)} classes → {args.output}")
    for cls in classes:
        print(f"  [{cls['complexity_bucket']:6s}] {cls['class_name']} "
              f"({cls['line_count']} lines, {cls['public_method_count']} public methods)")


if __name__ == "__main__":
    main()
