#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

_TIMESTAMP_RE = re.compile(rb"(?m)^(\s*fileCreationTimestamp\s*=\s*)[^\r\n]*")


def _normalized_members(path: Path) -> tuple[dict[str, bytes], int]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError(f"{path}: duplicate ZIP member names are not allowed")

        normalized: dict[str, bytes] = {}
        timestamp_fields = 0
        for name in names:
            payload = archive.read(name)
            payload, count = _TIMESTAMP_RE.subn(rb"\1<normalized>", payload)
            timestamp_fields += count
            normalized[name] = payload

    return normalized, timestamp_fields


def compare_sl1_semantics(left: Path, right: Path) -> tuple[bool, list[str]]:
    left_members, left_timestamps = _normalized_members(left)
    right_members, right_timestamps = _normalized_members(right)

    errors: list[str] = []
    if left_timestamps == 0 or right_timestamps == 0:
        errors.append("expected fileCreationTimestamp metadata was not found in both SL1 archives")
    elif left_timestamps != right_timestamps:
        errors.append(
            f"fileCreationTimestamp field count differs: {left_timestamps} != {right_timestamps}"
        )

    left_names = set(left_members)
    right_names = set(right_members)
    if left_names != right_names:
        missing_right = sorted(left_names - right_names)
        missing_left = sorted(right_names - left_names)
        if missing_right:
            errors.append(f"members missing from right archive: {missing_right}")
        if missing_left:
            errors.append(f"members missing from left archive: {missing_left}")

    for name in sorted(left_names & right_names):
        if left_members[name] == right_members[name]:
            continue
        left_hash = hashlib.sha256(left_members[name]).hexdigest()
        right_hash = hashlib.sha256(right_members[name]).hexdigest()
        errors.append(f"member differs after timestamp normalization: {name}: {left_hash} != {right_hash}")

    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare PrusaSlicer SL1 archives while ignoring only fileCreationTimestamp metadata."
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()

    try:
        matches, errors = compare_sl1_semantics(args.left, args.right)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        print(f"SL1 semantic comparison failed: {exc}", file=sys.stderr)
        return 2

    if not matches:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("SL1 semantic contents match after normalizing fileCreationTimestamp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
