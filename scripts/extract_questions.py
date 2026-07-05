import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup


CELPIP_SET = os.getenv("CELPIP_SET", "1").strip() or "1"
CELPIP_TEST = os.getenv("CELPIP_TEST", "1").strip() or "1"
REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(
    os.getenv(
        "CELPIP_OUTPUT_DIR",
        REPO_ROOT / "output" / f"local_celpip{CELPIP_SET}_test{CELPIP_TEST}",
    )
)
PAGES_DIR = ROOT / "pages"
IFRAMES_DIR = ROOT / "iframes"
OUT_PATH = ROOT / "questions.json"
TASK_SECTIONS = {"writing", "speaking"}


def clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def slug(value):
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def rel(path):
    return path.relative_to(ROOT).as_posix()


def normalize_asset(src, base_file):
    if not src:
        return None
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https", "data"}:
        return src
    if src.startswith("/"):
        local_asset = ROOT / "assets" / src.lstrip("/")
        if local_asset.exists():
            return local_asset.relative_to(ROOT).as_posix()
        return src
    return (base_file.parent / src).resolve().relative_to(ROOT.resolve()).as_posix()


def infer_section(page_path):
    parts = page_path.relative_to(PAGES_DIR).parts
    return parts[0] if parts else "unknown"


def page_title(page_path):
    soup = BeautifulSoup(page_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    title = soup.find("title")
    if title:
        return clean_text(title.get_text(" "))
    return page_path.stem.replace("-", " ")


def iframe_sources_from_page(page_path):
    soup = BeautifulSoup(page_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src")
        if not src:
            continue
        iframe_path = (page_path.parent / src).resolve()
        try:
            iframe_path.relative_to(IFRAMES_DIR.resolve())
        except ValueError:
            continue
        yield iframe_path


def build_iframe_context():
    context = {}
    for page_path in sorted(PAGES_DIR.rglob("*.html")):
        section = infer_section(page_path)
        if section not in {"listening", "reading"}:
            continue
        title = page_path.stem.replace("-", " ")
        for iframe_path in iframe_sources_from_page(page_path):
            context.setdefault(
                iframe_path.name,
                {
                    "section": section,
                    "source_pages": [],
                },
            )
            context[iframe_path.name]["source_pages"].append(
                {
                    "file": rel(page_path),
                    "title": title,
                }
            )
    return context


def question_number(panel):
    index = panel.select_one(".aq-question-index")
    if index:
        text = clean_text(index.get_text(" "))
        if text.isdigit():
            return int(text)
    match = re.search(r"question(\d+)", panel.get("id", ""), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_media(node, base_file):
    media = []
    for tag_name, media_type, attr in [
        ("audio", "audio", "src"),
        ("video", "video", "src"),
        ("source", "source", "src"),
        ("img", "image", "src"),
    ]:
        for tag in node.select(f"{tag_name}[{attr}]"):
            path = normalize_asset(tag.get(attr), base_file)
            if path:
                media.append({"type": media_type, "path": path})
    return media


def split_media(media):
    prompt_media = []
    timer_media = []
    for item in media:
        if "/timer/" in item["path"]:
            timer_media.append(item)
        else:
            prompt_media.append(item)
    return prompt_media, timer_media


def unique_media(media):
    seen = set()
    result = []
    for item in media:
        key = (item.get("type"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def extract_part_media(source_file, section):
    if section != "listening" or not source_file:
        return []

    page_path = ROOT / source_file
    if not page_path.exists():
        return []

    iframe_paths = list(iframe_sources_from_page(page_path))
    if not iframe_paths:
        return []

    # The source page iframe points to the quiz entry page. Its media is the
    # full listening material; question-level iframe pages contain per-question clips.
    media = []
    soup = BeautifulSoup(iframe_paths[0].read_text(encoding="utf-8", errors="replace"), "html.parser")
    for item in extract_media(soup, iframe_paths[0]):
        if item["type"] == "source":
            item = {
                **item,
                "type": "video" if re.search(r"\.(mp4|webm)$", item["path"], flags=re.IGNORECASE) else "audio",
            }
        if item["type"] in {"audio", "video"}:
            media.append({**item, "source_iframe": rel(iframe_paths[0])})
    return unique_media(media)


def parse_timing(text):
    timing = {}
    prep = re.search(r"Preparation:\s*(\d+)\s*seconds", text, flags=re.IGNORECASE)
    if prep:
        timing["preparation_seconds"] = int(prep.group(1))
    recording = re.search(r"Recording:\s*(\d+)\s*seconds", text, flags=re.IGNORECASE)
    if recording:
        timing["recording_seconds"] = int(recording.group(1))
    minutes = re.search(r"\b(\d+)\s*Minutes\b", text, flags=re.IGNORECASE)
    if minutes:
        timing["time_limit_minutes"] = int(minutes.group(1))
    word_count = re.search(r"about\s*(\d+)\s*-\s*(\d+)\s*words", text, flags=re.IGNORECASE)
    if word_count:
        timing["word_count_target"] = {
            "min": int(word_count.group(1)),
            "max": int(word_count.group(2)),
        }
    return timing


def option_is_correct(container):
    return bool(container.select(".icon-ok"))


def normalized_answer_text(value):
    return clean_text(value).casefold()


def extract_correct_answer_texts(panel):
    correct = []
    for row in panel.select(".aq-question-explanation table tr, table.ariQuizStatSQ tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 3:
            continue
        if not cells[2].select(".icon-ok"):
            continue
        text = clean_text(cells[1].get_text(" "))
        if text:
            correct.append(text)
    return correct


def extract_correct_answer_labels(panel):
    labels = []
    for row in panel.select(".aq-question-explanation table tr, table.ariQuizStatSQ tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 3:
            continue
        if not cells[2].select(".icon-ok"):
            continue
        label = clean_text(cells[0].get_text(" ")).rstrip(".")
        if label:
            labels.append(label)
    return labels


def extract_explanation_text(panel):
    explanation = panel.select_one(".aq-question-explanation")
    if not explanation:
        return ""
    clone = BeautifulSoup(str(explanation), "html.parser")
    for table in clone.select("table"):
        table.decompose()
    return clean_text(clone.get_text(" "))


def extract_options(panel, base_file, correct_answer_texts=None, correct_answer_labels=None):
    correct_lookup = {normalized_answer_text(text) for text in correct_answer_texts or []}
    correct_label_lookup = {clean_text(label).rstrip(".") for label in correct_answer_labels or []}
    options = []
    answer_containers = panel.select(".aq-answer-container")
    for idx, container in enumerate(answer_containers, start=1):
        control = container.select_one("input, select, textarea")
        answer = container.select_one(".aq-answer")
        label = container.select_one("label")
        raw_label = clean_text(label.get_text(" ")) if label else str(idx)
        option_id = control.get("id") if control else None
        input_type = (control.get("type") if control and control.name == "input" else control.name if control else None) or "unknown"
        text = clean_text(answer.get_text(" ")) if answer else clean_text(container.get_text(" "))
        options.append(
            {
                "id": option_id,
                "label": raw_label,
                "text": text,
                "value": control.get("value") if control else None,
                "input_type": input_type,
                "checked": bool(control and control.has_attr("checked")),
                "disabled": bool(control and control.has_attr("disabled")),
                "media": extract_media(container, base_file),
                "is_correct": (
                    normalized_answer_text(text) in correct_lookup
                    or clean_text(raw_label).rstrip(".") in correct_label_lookup
                    or option_is_correct(container)
                ),
            }
        )
    return options


def infer_question_type(options):
    types = {opt["input_type"] for opt in options}
    if "checkbox" in types:
        return "multiple_choice_multi"
    if "radio" in types:
        return "multiple_choice_single"
    if {"text", "textarea"} & types:
        return "free_text"
    if options:
        return "choice_unknown"
    return "unknown"


def is_results_iframe(iframe_path, soup):
    if "_results" in iframe_path.stem:
        return True
    return bool(soup.select(".aq-answer-result-message, .ariQuizStatSQ, #dtResults"))


def extract_question(panel, iframe_path, context, is_result_source=False):
    content = panel.select_one(".aq-question-content")
    correct_answer_texts = extract_correct_answer_texts(panel)
    correct_answer_labels = extract_correct_answer_labels(panel)
    options = extract_options(panel, iframe_path, correct_answer_texts, correct_answer_labels)
    correct_options = [opt["id"] or opt["label"] for opt in options if opt["is_correct"]]
    media = extract_media(panel, iframe_path)
    qnum = question_number(panel)
    qid = panel.get("id")
    question_text = clean_text(content.get_text(" ")) if content else ""

    source_pages = context.get("source_pages", [])
    source_slug = slug(Path(source_pages[0]["file"]).stem) if source_pages else slug(iframe_path.stem)
    stable_key = f"celpip{CELPIP_SET}_test{CELPIP_TEST}_{context.get('section', 'unknown')}_{source_slug}_q{qnum or qid}"

    return {
        "key": stable_key,
        "id": qid,
        "number": qnum,
        "question_text": question_text,
        "question_type": infer_question_type(options),
        "options": options,
        "correct_option_ids": correct_options,
        "correct_answers": [
            opt["text"] or (opt["media"][0]["path"] if opt["media"] else opt["label"])
            for opt in options
            if opt["is_correct"]
        ],
        "answer_extraction_status": "found" if correct_options else "not_found_in_saved_html",
        "answer_source_iframe": rel(iframe_path) if correct_options and is_result_source else None,
        "explanation": extract_explanation_text(panel),
        "media": media,
        "source_iframe": rel(iframe_path),
        "is_result_source": is_result_source,
        "section": context.get("section", "unknown"),
        "source_pages": source_pages,
    }


def extract_iframe_questions(iframe_path, context):
    soup = BeautifulSoup(iframe_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    result_source = is_results_iframe(iframe_path, soup)
    questions = []
    for panel in soup.select(".aq-question-panel[id]"):
        question = extract_question(panel, iframe_path, context, result_source)
        if question["question_text"] or question["options"] or question["media"]:
            questions.append(question)
    return questions


def is_task_page(page_path):
    section = infer_section(page_path)
    if section not in TASK_SECTIONS:
        return False
    if page_path.parent == PAGES_DIR:
        return False
    name = page_path.name.lower()
    if "performance-standards" in name or "result-page" in name:
        return False
    return True


def article_prompt_fragment(page_path):
    soup = BeautifulSoup(page_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    article = soup.select_one(".com-content-article")
    body = soup.select_one(".com-content-article__body")
    if not article or not body:
        return None, None, None

    title_node = article.select_one(".page-header h1, .page-header h2, .page-header")
    title = clean_text(title_node.get_text(" ")) if title_node else page_title(page_path)
    fragment = BeautifulSoup("", "html.parser")

    for child in body.children:
        if not getattr(child, "name", None):
            continue
        text = clean_text(child.get_text(" "))
        child_id = child.get("id") or ""
        child_classes = set(child.get("class") or [])
        if "CELPIP 4-5 Response" in text or "CELPIP 7-8 Response" in text or "CELPIP 10-12 Response" in text:
            break
        if "mod-custom" in child_classes or child_id.startswith("mod-custom"):
            break
        fragment.append(BeautifulSoup(str(child), "html.parser"))

    return title, body, fragment


def extract_response_samples(body):
    samples = []
    if not body:
        return samples

    for panel in body.select('[id^="rlta-panel-celpip-"][role="tabpanel"]'):
        match = re.search(
            r"rlta-panel-celpip-(\d+)-(\d+)-response",
            panel.get("id", ""),
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        content = panel.select_one('[data-rlta-element="panel-content"]') or panel
        clone = BeautifulSoup(str(content), "html.parser")
        for node in clone.select("script, style, iframe, form"):
            node.decompose()
        level = f"{match.group(1)}-{match.group(2)}"
        samples.append(
            {
                "level": level,
                "title": f"CELPIP {level} Response",
                "html": str(clone),
                "text": clean_text(clone.get_text(" ")),
            }
        )
    return samples


def extract_task_question(page_path):
    section = infer_section(page_path)
    title, body, fragment = article_prompt_fragment(page_path)
    if not fragment:
        return None

    question_text = clean_text(fragment.get_text(" "))
    if not question_text:
        return None

    media, timer_media = split_media(extract_media(fragment, page_path))
    source_pages = [{"file": rel(page_path), "title": title}]
    number_match = re.search(r"\bTask\s+(\d+)\b", title, flags=re.IGNORECASE)
    number = int(number_match.group(1)) if number_match else None
    stable_key = f"celpip{CELPIP_SET}_test{CELPIP_TEST}_{section}_{slug(page_path.stem)}"

    return {
        "key": stable_key,
        "id": page_path.stem,
        "number": number,
        "question_text": question_text,
        "question_html": str(fragment),
        "response_samples": extract_response_samples(body) if section == "writing" else [],
        "question_type": f"{section}_task",
        "options": [],
        "correct_option_ids": [],
        "correct_answers": [],
        "answer_extraction_status": "no_answer_expected",
        "answer_source_iframe": None,
        "explanation": "",
        "media": media,
        "timer_media": timer_media,
        "timing": parse_timing(question_text),
        "source_iframe": None,
        "source_file": rel(page_path),
        "is_result_source": False,
        "section": section,
        "source_pages": source_pages,
    }


def extract_task_questions():
    questions = []
    for page_path in sorted(PAGES_DIR.rglob("*.html")):
        if not is_task_page(page_path):
            continue
        question = extract_task_question(page_path)
        if question:
            questions.append(question)
    return questions


def dedupe_questions(questions):
    by_key = {}
    for question in questions:
        key = (
            question["section"],
            tuple(page["file"] for page in question["source_pages"]),
            question["id"],
            question["question_text"],
        )
        existing = by_key.get(key)
        if not existing:
            by_key[key] = question
            continue

        if question["correct_option_ids"] and not existing["correct_option_ids"]:
            correct_by_text = {
                normalized_answer_text(opt["text"])
                for opt in question["options"]
                if opt["is_correct"] and opt["text"]
            }
            correct_by_label = {
                clean_text(opt["label"]).rstrip(".")
                for opt in question["options"]
                if opt["is_correct"] and opt["label"]
            }
            correct_by_media = {
                media["path"]
                for opt in question["options"]
                if opt["is_correct"]
                for media in opt.get("media", [])
            }
            for option in existing["options"]:
                option_media = {media["path"] for media in option.get("media", [])}
                if (
                    normalized_answer_text(option["text"]) in correct_by_text
                    or clean_text(option["label"]).rstrip(".") in correct_by_label
                    or bool(option_media & correct_by_media)
                ):
                    option["is_correct"] = True
            existing["correct_option_ids"] = [
                opt["id"] or opt["label"]
                for opt in existing["options"]
                if opt["is_correct"]
            ]
            existing["correct_answers"] = [
                opt["text"] or (opt["media"][0]["path"] if opt.get("media") else opt["label"])
                for opt in existing["options"]
                if opt["is_correct"]
            ]
            existing["answer_extraction_status"] = "found"
            existing["answer_source_iframe"] = question["answer_source_iframe"]
            existing["explanation"] = question["explanation"]
        elif question["is_result_source"] and question["explanation"] and not existing["explanation"]:
            existing["explanation"] = question["explanation"]

        existing_media = {(item["type"], item["path"]) for item in existing["media"]}
        for media in question["media"]:
            media_key = (media["type"], media["path"])
            if media_key not in existing_media:
                existing["media"].append(media)
                existing_media.add(media_key)

    result = list(by_key.values())
    result.sort(key=lambda q: (q["section"], q["source_pages"][0]["file"] if q["source_pages"] else "", q["number"] or 9999, q["id"] or ""))
    return result


def build_question_groups(questions):
    groups_by_section = {}
    group_by_key = {}

    for question in questions:
        section = question["section"]
        source_file = (
            question["source_pages"][0]["file"]
            if question.get("source_pages")
            else question.get("source_file")
            or question["key"]
        )
        group_key = (section, source_file)

        group = group_by_key.get(group_key)
        if not group:
            title = (
                question["source_pages"][0]["title"]
                if question.get("source_pages")
                else source_file
            )
            group = {
                "id": slug(source_file),
                "section": section,
                "source_file": source_file,
                "title": title,
                "media": extract_part_media(source_file, section),
                "question_keys": [],
            }
            group_by_key[group_key] = group
            groups_by_section.setdefault(section, []).append(group)

        question["group_id"] = group["id"]
        group["question_keys"].append(question["key"])

    return groups_by_section


def inherited_context(iframe_path, iframe_context):
    context = iframe_context.get(iframe_path.name, {"section": "unknown", "source_pages": []})
    if context["section"] != "unknown":
        return context

    candidates = [
        re.sub(r"_test_q\d+\.html$", ".html", iframe_path.name),
        re.sub(r"_test_results\.html$", ".html", iframe_path.name),
        re.sub(r"_test\.html$", ".html", iframe_path.name),
        re.sub(r"_results\.html$", ".html", iframe_path.name),
    ]
    for base_name in candidates:
        if base_name != iframe_path.name and base_name in iframe_context:
            return iframe_context[base_name]
    return context


def main():
    iframe_context = build_iframe_context()
    questions = []
    for iframe_path in sorted(IFRAMES_DIR.glob("*.html")):
        context = inherited_context(iframe_path, iframe_context)
        questions.extend(extract_iframe_questions(iframe_path, context))
    questions.extend(extract_task_questions())

    questions = dedupe_questions(questions)
    question_groups = build_question_groups(questions)
    payload = {
        "source_root": str(ROOT),
        "generated_from": f"local CELPIP-{CELPIP_SET} Test{CELPIP_TEST} HTML snapshot",
        "question_count": len(questions),
        "sections": {
            section: sum(1 for q in questions if q["section"] == section)
            for section in sorted({q["section"] for q in questions})
        },
        "question_groups": question_groups,
        "questions": questions,
        "questions_by_key": {question["key"]: question for question in questions},
        "notes": [
            "correct_option_ids and correct_answers are populated from saved result/explanation pages when available.",
            "media paths are relative to source_root.",
            "question_groups groups questions by original source page; listening group media is the full part audio/video from the saved iframe entry page.",
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(f"question_count={payload['question_count']}")
    print(f"sections={payload['sections']}")


if __name__ == "__main__":
    main()
