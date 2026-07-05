#!/usr/bin/env python3
"""Convert legacy output snapshots into compact private material packs."""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "output"
DEFAULT_DESTINATION = ROOT / "materials" / "private" / "packs"
MEDIA_EXTENSIONS = {
    ".aac",
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".svg",
    ".wav",
    ".webm",
    ".webp",
}
HTML_REF_RE = re.compile(
    r"""(?:src|poster|data-src)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^"')]+)["']?\s*\)""",
    re.IGNORECASE,
)
ATTR_REF_RE = re.compile(
    r"""(?P<attr>src|poster|data-src)\s*=\s*(?P<quote>["'])(?P<ref>[^"']+)(?P=quote)""",
    re.IGNORECASE,
)
IMG_TAG_RE = re.compile(r"""<img\b[^>]*>""", re.IGNORECASE)
DECORATIVE_MEDIA_RE = re.compile(r"""(?:/gpt/|gptsmall|/timer/|gif-\d+)""", re.IGNORECASE)
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
DROP_TAGS = {"script", "style", "iframe", "form", "nav", "header", "footer", "noscript"}
MEDIA_FOLDERS = {
    ".aac": "audio",
    ".m4a": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".wav": "audio",
    ".avif": "images",
    ".gif": "images",
    ".jpeg": "images",
    ".jpg": "images",
    ".png": "images",
    ".svg": "images",
    ".webp": "images",
    ".mp4": "video",
    ".webm": "video",
}


class RefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(attrs)

    def _collect(self, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value:
                continue
            if name.lower() in {"src", "poster", "data-src"}:
                self.refs.add(value)


class ContentExtractor(HTMLParser):
    def __init__(self, target: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target = target
        self.recording = False
        self.record_depth = 0
        self.skip_depth = 0
        self.parts: list[str] = []
        self.found = False

    def target_matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        attr_map = dict(attrs)
        if self.target == "article":
            return attr_map.get("itemprop") == "articleBody"
        return tag == self.target

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            self.skip_depth += 1
            return
        if not self.recording and self.target_matches(tag, attrs):
            self.recording = True
            self.record_depth = 1
            self.found = True
            return
        if not self.recording:
            return
        if tag in DROP_TAGS:
            self.skip_depth = 1
            return
        self.record_depth += 1
        self.parts.append(format_start_tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.recording and not self.skip_depth and tag not in DROP_TAGS:
            self.parts.append(format_start_tag(tag, attrs, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.recording:
            return
        if self.record_depth == 1:
            self.recording = False
            self.record_depth = 0
            return
        if tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")
        self.record_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.recording and not self.skip_depth:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.recording and not self.skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.recording and not self.skip_depth:
            self.parts.append(f"&#{name};")

    def content(self) -> str:
        return "".join(self.parts).strip()


def format_start_tag(tag: str, attrs: list[tuple[str, str | None]], self_closing: bool = False) -> str:
    safe_attrs = []
    for name, value in attrs:
        lower = name.lower()
        if lower.startswith("on") or lower in {"id"}:
            continue
        if value is None:
            safe_attrs.append(html.escape(name, quote=True))
        else:
            safe_attrs.append(f'{html.escape(name, quote=True)}="{html.escape(value, quote=True)}"')
    attr_text = f" {' '.join(safe_attrs)}" if safe_attrs else ""
    if self_closing or tag in VOID_TAGS:
        return f"<{tag}{attr_text}>"
    return f"<{tag}{attr_text}>"


def clean_relative_path(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path or path.startswith("#") or path.startswith("data:"):
        return None
    normalized = posixpath.normpath(path.lstrip("/"))
    if normalized in {"", "."} or normalized.startswith("../"):
        return None
    return normalized


def resolve_relative_path(from_file: str, relative_path: str) -> str | None:
    if relative_path.startswith("/"):
        return clean_relative_path(relative_path)
    base = posixpath.dirname(from_file)
    return clean_relative_path(posixpath.join(base, relative_path))


def relative_path(from_file: str, target_file: str) -> str:
    return posixpath.relpath(target_file, posixpath.dirname(from_file))


def material_label(test_id: str) -> str:
    match = re.fullmatch(r"local_celpip(\d+)_test(\d+)", test_id)
    if not match:
        return test_id.replace("_", " ").title()
    return f"CELPIP {match.group(1)} - Test {match.group(2)}"


def source_files(data: dict) -> set[str]:
    refs: set[str] = set()
    for question in data.get("questions", []):
        for key in ("source_file",):
            value = question.get(key)
            if isinstance(value, str):
                path = clean_relative_path(value)
                if path:
                    refs.add(path)
        for page in question.get("source_pages", []) or []:
            value = page.get("file") if isinstance(page, dict) else None
            if isinstance(value, str):
                path = clean_relative_path(value)
                if path:
                    refs.add(path)
    for groups in (data.get("question_groups") or {}).values():
        for group in groups or []:
            value = group.get("source_file") if isinstance(group, dict) else None
            if isinstance(value, str):
                path = clean_relative_path(value)
                if path:
                    refs.add(path)
    return refs


def path_refs_from_obj(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            cleaned = clean_relative_path(path)
            if cleaned:
                refs.add(cleaned)
        for key, child in value.items():
            if key == "timer_media":
                continue
            refs.update(path_refs_from_obj(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(path_refs_from_obj(child))
    return refs


def html_refs(html: str, source_file: str) -> set[str]:
    parser = RefParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    refs = set(parser.refs)
    refs.update(match.group(1) or match.group(2) for match in HTML_REF_RE.finditer(html))

    resolved: set[str] = set()
    for ref in refs:
        path = resolve_relative_path(source_file, ref)
        if path and Path(path).suffix.lower() in MEDIA_EXTENSIONS:
            resolved.add(path)
    return resolved


def strip_decorative_media(html_text: str) -> str:
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        return "" if DECORATIVE_MEDIA_RE.search(tag) else tag

    return IMG_TAG_RE.sub(replace, html_text)


def useful_page_html(source_root: Path, page: str) -> str:
    raw = (source_root / page).read_text(encoding="utf-8", errors="ignore")
    for target in ("article", "main", "body"):
        extractor = ContentExtractor(target)
        try:
            extractor.feed(raw)
        except Exception:
            continue
        content = extractor.content()
        if extractor.found and content:
            break
    else:
        content = ""
    title = html.escape(posixpath.basename(page).replace("-", " ").replace(".html", "").title())
    content = strip_decorative_media(content)
    return (
        "<!doctype html>\n"
        "<html><head>"
        '<meta charset="utf-8">'
        f"<title>{title}</title>"
        "</head><body>"
        f'<main itemprop="articleBody" data-source-file="{html.escape(page, quote=True)}">'
        f"{content}"
        "</main></body></html>\n"
    )


def referenced_assets(data: dict, cleaned_pages: dict[str, str]) -> set[str]:
    refs = {
        path
        for path in path_refs_from_obj(data)
        if Path(path).suffix.lower() in MEDIA_EXTENSIONS
    }

    for question in data.get("questions", []):
        source_file = question.get("source_file") or (question.get("source_pages") or [{}])[0].get("file")
        if isinstance(source_file, str) and isinstance(question.get("question_html"), str):
            refs.update(html_refs(strip_decorative_media(question["question_html"]), source_file))
        for sample in question.get("response_samples", []) or []:
            if isinstance(sample, dict) and isinstance(sample.get("html"), str) and isinstance(source_file, str):
                refs.update(html_refs(strip_decorative_media(sample["html"]), source_file))

    for page, page_html in cleaned_pages.items():
        refs.update(html_refs(page_html, page))

    return refs


def media_folder(path: str) -> str:
    return MEDIA_FOLDERS.get(Path(path).suffix.lower(), "assets")


def build_asset_map(refs: set[str]) -> dict[str, str]:
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for ref in sorted(refs):
        folder = media_folder(ref)
        name = Path(ref).name
        candidate = f"{folder}/{name}"
        if candidate in used:
            stem = Path(ref).stem
            suffix = Path(ref).suffix
            prefix = re.sub(r"[^A-Za-z0-9]+", "-", str(Path(ref).parent)).strip("-").lower()
            candidate = f"{folder}/{prefix}-{stem}{suffix}"
        counter = 2
        base = candidate
        while candidate in used:
            path = Path(base)
            candidate = f"{path.parent.as_posix()}/{path.stem}-{counter}{path.suffix}"
            counter += 1
        used.add(candidate)
        mapping[ref] = candidate
    return mapping


def copy_assets(source_root: Path, destination_root: Path, asset_map: dict[str, str]) -> list[str]:
    copied: list[str] = []
    for ref, mapped in sorted(asset_map.items(), key=lambda item: item[1]):
        source = source_root / ref
        target = destination_root / mapped
        if not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(mapped)
    return copied


def rewrite_html_refs(html_text: str, source_file: str, asset_map: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        original = match.group("ref")
        resolved = resolve_relative_path(source_file, original)
        if not resolved or resolved not in asset_map:
            return match.group(0)
        rewritten = relative_path(source_file, asset_map[resolved])
        return f'{match.group("attr")}={match.group("quote")}{rewritten}{match.group("quote")}'

    return ATTR_REF_RE.sub(replace, html_text)


def rewrite_paths(value: object, asset_map: dict[str, str]) -> None:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            cleaned = clean_relative_path(path)
            if cleaned in asset_map:
                value["path"] = asset_map[cleaned]
        value.pop("timer_media", None)
        value.pop("source_iframe", None)
        value.pop("answer_source_iframe", None)
        value.pop("answer_extraction_status", None)
        value.pop("is_result_source", None)
        value.pop("explanation", None)
        for child in value.values():
            rewrite_paths(child, asset_map)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                cleaned = clean_relative_path(child)
                if cleaned in asset_map:
                    value[index] = asset_map[cleaned]
            else:
                rewrite_paths(child, asset_map)


def rewrite_question_html(data: dict, asset_map: dict[str, str]) -> None:
    questions = list(data.get("questions", []))
    questions.extend((data.get("questions_by_key") or {}).values())
    for question in questions:
        if not isinstance(question, dict):
            continue
        source_file = question.get("source_file") or (question.get("source_pages") or [{}])[0].get("file")
        if not isinstance(source_file, str):
            continue
        if isinstance(question.get("question_html"), str):
            question["question_html"] = rewrite_html_refs(strip_decorative_media(question["question_html"]), source_file, asset_map)
        for sample in question.get("response_samples", []) or []:
            if isinstance(sample, dict) and isinstance(sample.get("html"), str):
                sample["html"] = rewrite_html_refs(strip_decorative_media(sample["html"]), source_file, asset_map)


def write_pages(destination_root: Path, cleaned_pages: dict[str, str], asset_map: dict[str, str]) -> list[str]:
    written: list[str] = []
    for page, page_html in sorted(cleaned_pages.items()):
        target = destination_root / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewrite_html_refs(page_html, page, asset_map), encoding="utf-8")
        written.append(page)
    return written


def convert_pack(source_pack: Path, destination_root: Path, clean: bool) -> dict:
    questions_path = source_pack / "questions.json"
    if not questions_path.exists():
        raise ValueError(f"{source_pack} does not contain questions.json")

    data = json.loads(questions_path.read_text(encoding="utf-8"))
    if not isinstance(data.get("questions"), list):
        raise ValueError(f"{questions_path} has no questions list")

    test_id = source_pack.name
    destination_pack = destination_root / test_id
    if clean and destination_pack.exists():
        shutil.rmtree(destination_pack)
    destination_pack.mkdir(parents=True, exist_ok=True)

    pages = {
        page
        for page in source_files(data)
        if (source_pack / page).is_file() and Path(page).suffix.lower() in {".html", ".htm"}
    }
    cleaned_pages = {page: useful_page_html(source_pack, page) for page in pages}
    assets = {path for path in referenced_assets(data, cleaned_pages) if (source_pack / path).is_file()}
    asset_map = build_asset_map(assets)
    copied_pages = write_pages(destination_pack, cleaned_pages, asset_map)
    copied_assets = copy_assets(source_pack, destination_pack, asset_map)
    rewrite_question_html(data, asset_map)
    rewrite_paths(data, asset_map)
    data.pop("questions_by_key", None)
    data.pop("source_root", None)
    data.pop("notes", None)
    (destination_pack / "questions.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sections = data.get("sections") or {}
    if not sections:
        for question in data.get("questions", []):
            section = question.get("section")
            if section:
                sections[section] = sections.get(section, 0) + 1

    manifest = {
        "id": test_id,
        "label": material_label(test_id),
        "kind": "private-local-material-pack",
        "source": str(source_pack),
        "generated_from": data.get("generated_from", "legacy output snapshot"),
        "question_count": len(data.get("questions", [])),
        "sections": sections,
        "files": {
            "questions": "questions.json",
            "pages": copied_pages,
            "audio": [path for path in copied_assets if path.startswith("audio/")],
            "images": [path for path in copied_assets if path.startswith("images/")],
            "video": [path for path in copied_assets if path.startswith("video/")],
        },
        "converted_at": datetime.now(timezone.utc).isoformat(),
    }
    (destination_pack / "material.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def convert_all(source_root: Path, destination_root: Path, clean: bool) -> list[dict]:
    packs = sorted(path for path in source_root.glob("local_celpip*_test*") if path.is_dir())
    manifests = [convert_pack(pack, destination_root, clean) for pack in packs]
    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "material_root": str(destination_root),
        "packs": [
            {
                "id": manifest["id"],
                "label": manifest["label"],
                "question_count": manifest["question_count"],
                "sections": manifest["sections"],
            }
            for manifest in manifests
        ],
    }
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    (destination_root.parent / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--clean", action="store_true", help="Replace existing converted packs")
    args = parser.parse_args()

    manifests = convert_all(args.source, args.destination, args.clean)
    print(f"Converted {len(manifests)} material pack(s) to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
