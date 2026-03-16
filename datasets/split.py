#!/usr/bin/env python3
"""
split.py — 将 all_classes.json 按 complexity_bucket 分层拆分为 train/test 集。

用法:
    python3 datasets/split.py [--ratio 0.7] [--seed 42]
    python3 datasets/split.py [--train-count 10 --test-count 30 --seed 42]
"""

import json
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict


def stratified_split(classes, train_ratio, seed):
    """按 complexity_bucket 分层，确保 train/test 各含各复杂度的样本"""
    rng = random.Random(seed)
    by_bucket = defaultdict(list)
    for cls in classes:
        by_bucket[cls["complexity_bucket"]].append(cls)

    train, test = [], []
    for bucket, items in by_bucket.items():
        shuffled = items[:]
        rng.shuffle(shuffled)
        n_train = max(1, round(len(shuffled) * train_ratio))
        # 若只有 1 个，优先放 train
        if len(shuffled) == 1:
            train.extend(shuffled)
        else:
            train.extend(shuffled[:n_train])
            test.extend(shuffled[n_train:])

    # 保持原始顺序（按 class_name 排序）
    train.sort(key=lambda x: x["class_name"])
    test.sort(key=lambda x: x["class_name"])
    return train, test


def split_by_count(classes, train_count, test_count, seed):
    """按数量拆分（随机可复现）"""
    total = len(classes)

    if train_count is None and test_count is None:
        raise ValueError("train_count and test_count cannot both be None")
    if train_count is not None and train_count < 0:
        raise ValueError("train_count must be >= 0")
    if test_count is not None and test_count < 0:
        raise ValueError("test_count must be >= 0")

    if train_count is None:
        train_count = total - test_count
    if test_count is None:
        test_count = total - train_count

    if train_count + test_count > total:
        raise ValueError(
            f"train_count + test_count exceeds total classes: "
            f"{train_count} + {test_count} > {total}"
        )

    rng = random.Random(seed)
    shuffled = classes[:]
    rng.shuffle(shuffled)

    train = shuffled[:train_count]
    test = shuffled[train_count:train_count + test_count]

    train.sort(key=lambda x: x["class_name"])
    test.sort(key=lambda x: x["class_name"])
    return train, test


def write_split(classes, split_name, train_ratio, seed, output_path):
    data = {
        "split": split_name,
        "split_ratio": train_ratio,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(classes),
        "classes": classes,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  default="datasets/all_classes.json")
    parser.add_argument("--train",  default="datasets/train.json")
    parser.add_argument("--test",   default="datasets/test.json")
    parser.add_argument("--ratio",  type=float, default=0.7)
    parser.add_argument("--train-count", type=int, default=None)
    parser.add_argument("--test-count", type=int, default=None)
    parser.add_argument("--seed",   type=int,   default=42)
    args = parser.parse_args()

    with open(args.input) as f:
        dataset = json.load(f)

    classes = dataset["classes"]
    use_count_mode = args.train_count is not None or args.test_count is not None
    if use_count_mode:
        train_classes, test_classes = split_by_count(
            classes, args.train_count, args.test_count, args.seed
        )
    else:
        train_classes, test_classes = stratified_split(classes, args.ratio, args.seed)

    write_split(train_classes, "train", args.ratio, args.seed, args.train)
    write_split(test_classes,  "test",  args.ratio, args.seed, args.test)

    if use_count_mode:
        print(
            f"✓ Split by count {len(classes)} classes  →  "
            f"train: {len(train_classes)}, test: {len(test_classes)}"
        )
    else:
        print(f"✓ Split {len(classes)} classes  →  train: {len(train_classes)}, test: {len(test_classes)}")
    print(f"  train → {args.train}")
    for c in train_classes:
        print(f"    [{c['complexity_bucket']:6s}] {c['class_name']}")
    print(f"  test  → {args.test}")
    for c in test_classes:
        print(f"    [{c['complexity_bucket']:6s}] {c['class_name']}")


if __name__ == "__main__":
    main()
