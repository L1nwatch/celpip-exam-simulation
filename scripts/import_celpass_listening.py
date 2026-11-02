#!/usr/bin/env python3
"""Convert a manually saved public Listening test into a private study pack.

Input is visible question/answer/transcript data saved from the source page and
the publisher's unchanged personal-practice PDF. Audio stays on the source host.
No scraping, generated questions, or generated speech is performed by this tool.
"""

import argparse
import hashlib
import html
import json
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
COUNTS = (2, 3, 3, 5, 6, 5, 8, 6)
TITLES = ("Listening to Problem Solving Section 1", "Listening to Problem Solving Section 2",
          "Listening to Problem Solving Section 3", "Listening to a Daily Life Conversation",
          "Listening for Information", "Listening to a News Item", "Listening to a Discussion",
          "Listening for Viewpoints")


def audio_url(path):
    url = urljoin("https://celpass.com", path or "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "celpass.com" or not parsed.path.startswith("/api/public/sample-audio/") or parsed.query:
        raise ValueError("Expected a stable public source audio URL")
    return url


def checked_option_images(option):
    images = []
    for image in option.get("media", []):
        url = urljoin("https://celpass.com", image.get("path", ""))
        parsed = urlsplit(url)
        if (image.get("type") != "image" or parsed.scheme != "https" or parsed.netloc != "celpass.com"
                or not parsed.path.startswith("/celpip-sample-tests/") or parsed.query
                or image.get("loaded") is not True or not image.get("width") or not image.get("height")):
            raise ValueError("Source option image has not passed the browser loading check")
        images.append({"type": "image", "path": url, "alt": image.get("alt") or option["text"]})
    return images


def convert(source, original_pdf, destination):
    if not re.fullmatch(r"local_[a-z0-9]+(?:_[a-z0-9]+)*", destination.name) or len(destination.name) > 100:
        raise ValueError("Invalid destination pack ID")
    if destination.exists():
        raise ValueError("Destination already exists; existing packs are never replaced")
    number = re.fullmatch(r"https://celpass.com/celpip/listening/practice-tests/(\d+)", source.get("url", ""))
    if not number:
        raise ValueError("Unrecognized source page")
    number = int(number[1])
    groups = source.get("sections", [])
    counts = tuple(len(g.get("questions", [])) for g in groups)
    if len(counts) != 8 or not all(counts[:3]) or sum(counts[:3]) != 8 or counts[3:] != COUNTS[3:]:
        raise ValueError("Expected eight recordings and 38 questions in CELPIP order")
    answer_rows = BeautifulSoup(source["answerHtml"], "html.parser").find_all("li")
    if len(answer_rows) != 38:
        raise ValueError("Expected 38 published answer-key entries")
    media = {audio_url(m["path"]): m for m in source.get("media_metadata", []) if m.get("path")}

    def checked_audio(path):
        url = audio_url(path)
        item = media.get(url)
        if not item or not isinstance(item.get("duration"), (int, float)) or item["duration"] <= 0 or item.get("error"):
            raise ValueError("Source recording has not passed the browser playback check")
        return {"type": "audio", "path": url}

    questions, built_groups, pages, seen = [], [], {}, set()
    pack_id = destination.name
    for index, (group, title) in enumerate(zip(groups, TITLES)):
        if not group.get("transcript", "").strip():
            raise ValueError("Every recording needs its published transcript")
        page = f"pages/listening/part-{index + 1}.html"
        group_id = f"{pack_id}_group{index + 1}"
        keys = []
        for local_number, q in enumerate(group["questions"], 1):
            source_number = q["number"]
            if not isinstance(source_number, int) or not 1 <= source_number <= 38 or source_number in seen:
                raise ValueError("Source question numbers must be unique and in range")
            seen.add(source_number)
            row = answer_rows[source_number - 1]
            strong = row.find("strong")
            answer = strong.get_text(strip=True) if strong else ""
            match = re.fullmatch(r"([A-D])\.\s+(.+)", answer)
            if not match or len(q["options"]) != 4 or [o["value"] for o in q["options"]] != list("ABCD"):
                raise ValueError("Every question needs four choices and a published answer")
            correct_index = ord(match[1]) - ord("A")
            if q["options"][correct_index]["text"].strip() != match[2].strip():
                raise ValueError("Published answer does not match its option")
            strong.extract()
            text = re.sub(r"\.{3,}", "__________", row.get_text(" ", strip=True))
            key = f"{pack_id}_listening_q{source_number}"
            keys.append(key)
            options = [{"id": f"{key}_{o['value']}", "value": f"{key}_{o['value']}",
                        "label": o["value"], "text": o["text"], "is_correct": i == correct_index,
                        "media": checked_option_images(o)} for i, o in enumerate(q["options"])]
            questions.append({"key": key, "id": key, "section": "listening", "number": local_number,
                              "source_question_number": source_number, "question_type": "multiple_choice_single",
                              "question_text": text, "options": options, "media": [checked_audio(q.get("questionAudio"))] if index < 5 else [],
                              "correct_option_ids": [options[correct_index]["id"]], "correct_answers": [options[correct_index]["text"]],
                              "source_pages": [{"file": page, "title": title}], "group_id": group_id})
        built_groups.append({"id": group_id, "section": "listening", "title": title, "source_file": page,
                             "media": [checked_audio(group["mainAudio"])], "question_keys": keys})
        paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in group["transcript"].split("\n") if p.strip())
        pages[page] = (f'<!doctype html><meta charset="utf-8"><main itemprop="articleBody"><h1>{html.escape(title)}</h1>'
                       f'<p>CELPass.com · Listening Practice Test {number}. <a href="{source["url"]}">Original test</a></p>'
                       f'{paragraphs}<p>Personal study copy. Questions, answer key, transcript and recordings are from CELPass; '
                       'recordings play from the original provider. Part 5 is an audio discussion.</p></main>')
    if seen != set(range(1, 39)):
        raise ValueError("A complete form needs questions 1 through 38")
    pdf_bytes = original_pdf.read_bytes()
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("An unchanged original practice PDF is required")
    counts = {"listening": 38, "reading": 0, "writing": 0, "speaking": 0}
    attribution = {"label": f"CELPass Listening Practice Test {number}", "url": source["url"],
                   "note": "Original-source audio requires internet access. Part 5 is an audio discussion."}
    data = {"question_count": 38, "sections": counts, "source": attribution,
            "question_groups": {"listening": built_groups, "reading": [], "writing": [], "speaking": []}, "questions": questions}
    metadata = {"id": pack_id, "label": f"CELPass Listening {number}", "kind": "private-imported-listening-practice",
                "sections": counts, "source": attribution, "entry": "questions.json",
                "usage": "Private personal study; retain CELPass attribution and original PDF. Not for redistribution.",
                "download_page": "https://celpass.com/celpip/practice-tests-pdf", "original_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "media_storage": "original-provider streaming", "media_checks": list(media.values())}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=destination.parent) as temp:
        staged = Path(temp) / pack_id
        staged.mkdir()
        for page, content in pages.items():
            path = staged / page
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (staged / "questions.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        (staged / "material.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        (staged / "source").mkdir()
        shutil.copy2(original_pdf, staged / "source" / f"celpass-listening-{number}.pdf")
        staged.rename(destination)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_json", type=Path)
    parser.add_argument("original_pdf", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert(json.loads(args.source_json.read_text()), args.original_pdf, args.destination)
    print(f"Imported 38 Listening questions into {args.destination}")
