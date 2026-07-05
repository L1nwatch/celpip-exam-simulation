#!/usr/bin/env python3
"""Build the static GitHub Pages preview artifact."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from import_materials import copy_pack, validate_pack


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "pages-preview"
DEMO_PACK = ROOT / "materials" / "demo" / "local_celpip1_test1"


PUBLIC_TESTS = """const TESTS = [
  { id: "local_celpip1_test1", label: "Demo CELPIP-Style Test" },
];"""


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=webapp/index.html?view=overview">
  <title>CELPIP Practice Lab Preview</title>
</head>
<body>
  <p><a href="webapp/index.html?view=overview">Open the preview</a></p>
</body>
</html>
"""


def public_app_js(source: str) -> str:
    source = re.sub(
        r"const TESTS = Array\.from\(\{ length: 13 \}[\s\S]+?\}\)\.flat\(\);\n\n"
        r"const MATERIAL_ROOT = \"\.\./materials/private/packs\";",
        PUBLIC_TESTS,
        source,
        count=1,
    )
    source = source.replace(
        """function assetUrl(path) {
  if (!path) return "";
  return materialUrl(state.testId, path);
}

function sourceUrl(path) {
  return materialUrl(state.testId, path);
}

function materialUrl(testId, path) {
  return `${MATERIAL_ROOT}/${testId}/${path}`;
}""",
        """function assetUrl(path) {
  if (!path) return "";
  return `../output/${state.testId}/${path}`;
}

function sourceUrl(path) {
  return `../output/${state.testId}/${path}`;
}""",
    )
    source = source.replace(
        'const response = await fetch(materialUrl(testId, "questions.json"));',
        'const response = await fetch(`../output/${testId}/questions.json`);',
    )
    if "../materials/private" in source or "MATERIAL_ROOT" in source or "materialUrl(" in source:
        raise SystemExit("Public preview JavaScript still references private material paths")
    return source


def build(output_dir: Path, clean: bool = True) -> Path:
    output_dir = output_dir.resolve()
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    webapp_dir = output_dir / "webapp"
    shutil.copytree(ROOT / "webapp", webapp_dir, ignore=shutil.ignore_patterns("celpip_practice.db", "logs", "recordings"))
    (webapp_dir / "app.js").write_text(public_app_js((ROOT / "webapp" / "app.js").read_text(encoding="utf-8")), encoding="utf-8")
    (output_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    validate_pack(DEMO_PACK)
    copy_pack(DEMO_PACK, output_dir / "output", DEMO_PACK.name, clean=True)
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directory to write the Pages artifact")
    parser.add_argument("--no-clean", action="store_true", help="Do not remove the output directory before building")
    args = parser.parse_args()

    artifact = build(args.output, clean=not args.no_clean)
    print(f"Built GitHub Pages preview at {artifact}")


if __name__ == "__main__":
    main()
