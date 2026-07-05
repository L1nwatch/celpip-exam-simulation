#!/usr/bin/env python3
"""Import a material pack into the exam app output structure."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    try:
      return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
      raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def validate_pack(pack: Path) -> dict:
    if not pack.is_dir():
        raise SystemExit(f"Material pack does not exist: {pack}")

    questions_path = pack / "questions.json"
    if not questions_path.exists():
        raise SystemExit(f"Missing required file: {questions_path}")

    data = load_json(questions_path)
    required = {"sections", "question_groups", "questions", "questions_by_key"}
    missing = sorted(required - set(data))
    if missing:
        raise SystemExit(f"{questions_path} missing required key(s): {', '.join(missing)}")

    if not isinstance(data["questions"], list) or not data["questions"]:
        raise SystemExit(f"{questions_path} must contain a non-empty questions list")

    for question in data["questions"]:
        for key in ("key", "section", "question_type"):
            if key not in question:
                raise SystemExit(f"Question is missing {key}: {question}")

    return data


def copy_pack(pack: Path, target_root: Path, test_id: str, clean: bool) -> Path:
    destination = target_root / test_id
    if clean and destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SystemExit(f"Destination already exists. Use --clean to replace it: {destination}")
    shutil.copytree(pack, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path, help="Path to a material pack directory")
    parser.add_argument(
        "--target",
        type=Path,
        default=ROOT / "output",
        help="Target root that will contain <test_id>/, for example output",
    )
    parser.add_argument("--test-id", help="Override destination test id; defaults to pack folder name")
    parser.add_argument("--clean", action="store_true", help="Replace an existing destination directory")
    args = parser.parse_args()

    pack = args.pack.resolve()
    validate_pack(pack)
    test_id = args.test_id or pack.name
    target = args.target if args.target.is_absolute() else ROOT / args.target
    destination = copy_pack(pack, target, test_id, args.clean)
    print(f"Imported {pack} -> {destination}")


if __name__ == "__main__":
    main()
