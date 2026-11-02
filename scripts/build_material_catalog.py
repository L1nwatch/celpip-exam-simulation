#!/usr/bin/env python3
"""Index installed private packs without bundling their questions into the app."""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("listening", "reading", "writing", "speaking")
PACK_ID = re.compile(r"local_[a-z0-9]+(?:_[a-z0-9]+)*\Z")


def build_catalog(root):
    tests = []
    for pack in sorted(root.iterdir()):
        if not pack.is_dir() or pack.is_symlink() or len(pack.name) > 100 or not PACK_ID.fullmatch(pack.name):
            continue
        questions = pack / "questions.json"
        if not questions.is_file():
            continue
        data = json.loads(questions.read_text())
        metadata = json.loads((pack / "material.json").read_text()) if (pack / "material.json").is_file() else {}
        counts = {section: sum(q.get("section") == section for q in data["questions"]) for section in SECTIONS}
        if not any(counts.values()):
            continue
        tests.append({"id": pack.name, "label": metadata.get("label") or pack.name, "sections": counts})
    result = {"version": 1, "tests": tests}
    temporary = root / ".catalog.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(root / "catalog.json")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "materials/private/packs")
    args = parser.parse_args()
    print(f"Indexed {len(build_catalog(args.root)['tests'])} installed material packs")
