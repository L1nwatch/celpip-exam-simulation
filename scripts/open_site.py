import os
import re
import ssl
import time
from getpass import getpass
from hashlib import sha1
from html.parser import HTMLParser
from html import escape
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


URL = "https://celpip.top/"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output"
OUT_DIR.mkdir(exist_ok=True)
ENV_PATH = ROOT / ".env"
CELPIP_SET = os.getenv("CELPIP_SET", "1").strip() or "1"
CELPIP_TEST = os.getenv("CELPIP_TEST", "1").strip() or "1"
TARGET_LABEL = f"CELPIP-{CELPIP_SET} Test{CELPIP_TEST}"
REPORT_PREFIX = f"celpip{CELPIP_SET}_test{CELPIP_TEST}"
FIRST_TEST_PARTS = [
    ("Listening", f"/celpip-1-13/celpip-{CELPIP_SET}/test{CELPIP_TEST}/listening"),
    ("Reading", f"/celpip-1-13/celpip-{CELPIP_SET}/test{CELPIP_TEST}/reading"),
    ("Writing", f"/celpip-1-13/celpip-{CELPIP_SET}/test{CELPIP_TEST}/writing"),
    ("Speaking", f"/celpip-1-13/celpip-{CELPIP_SET}/test{CELPIP_TEST}/speaking"),
]
FIRST_TEST_PREFIX = f"/celpip-1-13/celpip-{CELPIP_SET}/test{CELPIP_TEST}"
LOCAL_EXPORT_DIR = OUT_DIR / f"local_celpip{CELPIP_SET}_test{CELPIP_TEST}"
SSL_CONTEXT = ssl._create_unverified_context()
ASSET_EXTENSIONS = {
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".m4a",
    ".wav",
    ".ogg",
    ".webm",
}


def should_save_screenshots():
    return os.getenv("CELPIP_SAVE_SCREENSHOTS") == "1"


def should_refresh_pages():
    return os.getenv("CELPIP_FORCE_REFRESH") == "1"


def save_page(driver, stem):
    html_path = OUT_DIR / f"{stem}.html"
    html_path.write_text(driver.page_source, encoding="utf-8")
    print(f"Saved {stem} HTML:", html_path, flush=True)

    if should_save_screenshots():
        screenshot_path = OUT_DIR / f"{stem}.png"
        driver.save_screenshot(str(screenshot_path))
        print(f"Saved {stem} screenshot:", screenshot_path, flush=True)

    return html_path


def with_online_base(page_source):
    if "<head" not in page_source.lower():
        return page_source

    lower_source = page_source.lower()
    head_start = lower_source.find("<head")
    head_end = lower_source.find(">", head_start)
    if head_end == -1:
        return page_source

    base_tag = f'\n  <base href="{URL}">\n'
    return page_source[: head_end + 1] + base_tag + page_source[head_end + 1 :]


class AssetCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = set()

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        rel = " ".join(attrs_dict.get("rel", "").split()).lower()

        for attr in ["src", "poster", "data-src"]:
            value = attrs_dict.get(attr)
            if value:
                self.assets.add(value)

        href = attrs_dict.get("href")
        if href and (tag == "link" or Path(urlparse(href).path).suffix.lower() in ASSET_EXTENSIONS):
            if tag != "link" or any(token in rel for token in ["stylesheet", "icon", "preload", "prefetch"]):
                self.assets.add(href)


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return

        href = dict(attrs).get("href")
        if href:
            self.links.add(href)


class IframeCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag != "iframe":
            return
        src = dict(attrs).get("src")
        if src:
            self.sources.append(src)


def collect_html_assets(html):
    parser = AssetCollector()
    parser.feed(html)
    return parser.assets


def collect_iframe_sources(html):
    parser = IframeCollector()
    parser.feed(html)
    return parser.sources


def collect_html_links(html):
    parser = LinkCollector()
    parser.feed(html)
    return parser.links


def looks_like_asset(raw):
    parsed = urlparse(raw)
    suffix = Path(parsed.path).suffix.lower()
    return suffix in ASSET_EXTENSIONS


def collect_css_assets(css):
    assets = set()
    for match in re.finditer(r"url\(([^)]+)\)", css):
        raw = match.group(1).strip().strip("'\"")
        if raw and not raw.startswith(("data:", "about:", "#")):
            assets.add(raw)
    return assets


def is_downloadable_asset(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc and parsed.netloc != urlparse(URL).netloc:
        return False
    return Path(parsed.path).suffix.lower() in ASSET_EXTENSIONS


def asset_local_path(export_dir, absolute_url):
    parsed = urlparse(absolute_url)
    path = Path(parsed.path.lstrip("/"))
    suffix = path.suffix
    if not suffix:
        suffix = ".asset"

    stem = path.stem or "asset"
    if parsed.query:
        stem = f"{stem}_{sha1(parsed.query.encode('utf-8')).hexdigest()[:10]}"

    parent = export_dir / "assets" / path.parent
    return parent / f"{stem}{suffix}"


def cookie_header(driver):
    cookies = []
    try:
        browser_cookies = driver.get_cookies() or []
    except WebDriverException:
        browser_cookies = []

    for cookie in browser_cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value:
            cookies.append(f"{name}={value}")
    return "; ".join(cookies)


def download_asset(driver, export_dir, absolute_url, asset_map, base_url=None):
    absolute_url = urljoin(base_url or URL, absolute_url)
    if absolute_url in asset_map:
        return asset_map[absolute_url]
    if not is_downloadable_asset(absolute_url):
        return None

    local_path = asset_local_path(export_dir, absolute_url)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    asset_map[absolute_url] = local_path

    if local_path.exists() and local_path.stat().st_size > 0:
        print("Using cached asset:", local_path, flush=True)
        return local_path

    headers = {"User-Agent": "Mozilla/5.0"}
    cookies = cookie_header(driver)
    if cookies:
        headers["Cookie"] = cookies

    try:
        request = Request(quote_request_url(absolute_url), headers=headers)
        with urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            content = response.read()
        local_path.write_bytes(content)
        print("Saved asset:", local_path, flush=True)
    except URLError as exc:
        print(f"Skipped asset {absolute_url}: {exc}", flush=True)
        return None

    if local_path.suffix.lower() == ".css":
        localize_css(driver, export_dir, local_path, absolute_url, asset_map)

    return local_path


def quote_request_url(raw_url):
    parsed = urlparse(raw_url)
    path = quote(parsed.path, safe="/:%")
    query = quote(parsed.query, safe="=&?/:;+,%")
    fragment = quote(parsed.fragment, safe="")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, fragment))


def relative_asset_path(from_file, asset_path):
    return os.path.relpath(asset_path, start=from_file.parent).replace(os.sep, "/")


def localize_css(driver, export_dir, css_path, css_url, asset_map):
    css = css_path.read_text(encoding="utf-8", errors="replace")
    rewritten = css
    for raw in collect_css_assets(css):
        absolute = urljoin(css_url, raw)
        local_path = download_asset(driver, export_dir, absolute, asset_map, base_url=css_url)
        if local_path:
            rewritten = rewritten.replace(raw, relative_asset_path(css_path, local_path))
    if rewritten != css:
        css_path.write_text(rewritten, encoding="utf-8")


def localize_html_assets(driver, export_dir, html_path, page_url, html, asset_map):
    rewritten = html
    for raw in collect_html_assets(html):
        if not looks_like_asset(raw):
            continue
        absolute = urljoin(page_url, raw)
        local_path = download_asset(driver, export_dir, absolute, asset_map, base_url=page_url)
        if local_path:
            relative = relative_asset_path(html_path, local_path)
            rewritten = rewritten.replace(f'"{raw}"', f'"{relative}"')
            rewritten = rewritten.replace(f"'{raw}'", f"'{relative}'")
    html_path.write_text(rewritten, encoding="utf-8")


def remove_base_href(html):
    return re.sub(r"<base\b[^>]*>", "", html, flags=re.IGNORECASE)


def neutralize_local_popups(html):
    marker = "window.__celpipLocalNoPopups"
    if marker in html:
        return html

    script = (
        "<script>"
        "window.__celpipLocalNoPopups=true;"
        "window.alert=function(){};"
        "window.confirm=function(){return true;};"
        "window.prompt=function(){return null;};"
        "window.onbeforeunload=null;"
        "window.addEventListener('beforeunload',function(event){event.stopImmediatePropagation();},true);"
        "</script>"
    )
    if "</head>" in html.lower():
        return re.sub(r"</head>", script + "</head>", html, count=1, flags=re.IGNORECASE)
    return script + html


def inject_head_html(html, snippet, marker):
    if marker in html:
        return html
    if "</head>" in html.lower():
        return re.sub(r"</head>", snippet + "</head>", html, count=1, flags=re.IGNORECASE)
    return snippet + html


def staticize_quiz_html(html):
    css = (
        "<style id=\"celpip-local-static-quiz\">"
        ".aq-loading-message,.ari-loading,.aq-ic-loading-message{display:none!important;visibility:hidden!important;}"
        ".aq-hidden-onloading{display:block!important;visibility:visible!important;opacity:1!important;}"
        ".aq-question-panel-content{display:block!important;visibility:visible!important;opacity:1!important;}"
        ".aq-question-panel{display:block!important;visibility:visible!important;}"
        "</style>"
    )
    html = inject_head_html(html, css, "celpip-local-static-quiz")
    html = re.sub(r"\s*<div class=\"aq-loading-message\">\s*<div class=\"ari-loading\">Loading</div>\s*</div>", "", html, flags=re.IGNORECASE)
    html = html.replace(" aq-hidden-onloading", "")
    html = html.replace("aq-hidden-onloading ", "")
    html = html.replace(" aq-ic-loading", "")
    html = re.sub(r'<div class="aq-ic-loading-message">.*?</div>', "", html, flags=re.DOTALL)
    return html


def staticize_outer_iframes(html):
    html = html.replace('scrolling="no"', 'scrolling="yes"')
    html = html.replace("scrolling: \"no\"", "scrolling: \"yes\"")
    html = re.sub(
        r'style="([^"]*height:\s*)\d+px([^"]*)"',
        lambda match: f'style="{match.group(1)}1200px{match.group(2)}"',
        html,
        flags=re.IGNORECASE,
    )
    return html


def iframe_local_path(export_dir, absolute_url):
    parsed = urlparse(absolute_url)
    safe_path = Path(parsed.path.lstrip("/"))
    stem = safe_path.stem or "iframe"
    digest_source = f"{parsed.path}?{parsed.query}"
    digest = sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    return export_dir / "iframes" / safe_path.parent / f"{stem}_{digest}.html"


def quiz_step_path(local_path):
    return local_path.with_name(f"{local_path.stem}_test{local_path.suffix}")


def quiz_question_step_path(test_path, question_index):
    return test_path.with_name(f"{test_path.stem}_q{question_index}{test_path.suffix}")


def quiz_results_path(test_path):
    return test_path.with_name(f"{test_path.stem}_results{test_path.suffix}")


def quiz_results_complete(results_path):
    if not results_path.exists() or results_path.stat().st_size == 0:
        return False
    html = results_path.read_text(encoding="utf-8", errors="replace")
    return "ariQuizStatSQ" in html or "aq-answer-result-message" in html or "icon-ok" in html


def expected_quiz_results_path_for_iframe(local_path, html):
    if "aq-btn-continue" in html:
        return quiz_results_path(quiz_step_path(local_path))
    if "tdQuestionInfo" in html or "aq-question-panel" in html:
        return quiz_results_path(local_path)
    return None


def cached_iframe_missing_quiz_results(local_path):
    if not local_path.exists() or local_path.stat().st_size == 0:
        return False
    html = local_path.read_text(encoding="utf-8", errors="replace")
    results_path = expected_quiz_results_path_for_iframe(local_path, html)
    return bool(results_path and not quiz_results_complete(results_path))


def local_iframe_paths_from_page(page_path):
    html = page_path.read_text(encoding="utf-8", errors="replace")
    for src in collect_iframe_sources(html):
        iframe_path = (page_path.parent / src).resolve()
        try:
            iframe_path.relative_to((LOCAL_EXPORT_DIR / "iframes").resolve())
        except ValueError:
            continue
        yield iframe_path


def cached_page_missing_quiz_results(page_path):
    if not page_path.exists() or page_path.stat().st_size == 0:
        return False
    for iframe_path in local_iframe_paths_from_page(page_path):
        if cached_iframe_missing_quiz_results(iframe_path):
            return True
    return False


def current_question_position(driver):
    try:
        text = driver.find_element(By.CSS_SELECTOR, "#tdQuestionInfo").text
    except WebDriverException:
        return None

    match = re.search(r"Question\s+(\d+)\s+of\s+(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def has_quiz_results(driver):
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, ".aq-bnt-tryagain, #dtResults, .aq-dt-results"))
    except WebDriverException:
        return False


def choose_placeholder_answers(driver):
    answered = False

    radio_names = set()
    for radio in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
        name = radio.get_attribute("name") or radio.get_attribute("id")
        if not name or name in radio_names:
            continue
        radio_names.add(name)
        try:
            driver.execute_script("arguments[0].click();", radio)
            answered = True
        except WebDriverException:
            pass

    if answered:
        return True

    for checkbox in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
        try:
            driver.execute_script("arguments[0].click();", checkbox)
            return True
        except WebDriverException:
            pass

    for selector in ["textarea", "input[type='text']"]:
        for field in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if field.is_displayed() and field.is_enabled() and not field.get_attribute("value"):
                    field.send_keys("test")
                    answered = True
            except WebDriverException:
                pass

    return answered


def save_current_quiz_question(driver, export_dir, local_path, absolute_url, asset_map):
    html = staticize_quiz_html(neutralize_local_popups(remove_base_href(driver.page_source)))
    local_path.write_text(html, encoding="utf-8")
    localize_html_assets(driver, export_dir, local_path, absolute_url, html, asset_map)
    print("Saved quiz question HTML:", local_path, flush=True)


def save_current_quiz_results(driver, export_dir, local_path, absolute_url, asset_map):
    html = staticize_quiz_html(neutralize_local_popups(remove_base_href(driver.page_source)))
    local_path.write_text(html, encoding="utf-8")
    localize_html_assets(driver, export_dir, local_path, absolute_url, html, asset_map)
    print("Saved quiz results HTML:", local_path, flush=True)


def click_first_visible(driver, selectors):
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", element)
                    return True
            except WebDriverException:
                pass
    return False


def wait_for_quiz_page_change(driver, before_source):
    WebDriverWait(driver, 15).until(
        lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}
        and d.page_source != before_source
    )
    time.sleep(1)


def restart_completed_quiz_if_needed(driver):
    if current_question_position(driver):
        return

    try_again = driver.find_elements(By.CSS_SELECTOR, ".aq-bnt-tryagain")
    if not try_again:
        return

    before_source = driver.page_source
    if not click_first_visible(driver, [".aq-bnt-tryagain"]):
        return

    try:
        wait_for_quiz_page_change(driver, before_source)
    except WebDriverException:
        pass

    if driver.find_elements(By.CSS_SELECTOR, ".aq-btn-continue"):
        before_source = driver.page_source
        if click_first_visible(driver, [".aq-btn-continue"]):
            try:
                wait_for_quiz_page_change(driver, before_source)
            except WebDriverException:
                pass


def wait_for_quiz_dynamic_state(driver):
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script(
                """
                const visibleLoading = [...document.querySelectorAll('.ari-loading, .aq-loading-message')]
                  .some(el => el.offsetParent !== null && el.textContent.trim().toLowerCase().includes('loading'));
                const status = document.querySelector('#tdQuestionInfo');
                const statusReady = !status || status.textContent.trim().length > 0;
                const hasKnownState = Boolean(
                  document.querySelector('.aq-bnt-tryagain, #dtResults, .aq-btn-continue, .aq-question-panel')
                );
                return !visibleLoading && statusReady && hasKnownState;
                """
            )
        )
    except TimeoutException:
        print("Timed out waiting for quiz iframe dynamic state; saving current DOM.", flush=True)


def wait_for_quiz_results(driver):
    try:
        WebDriverWait(driver, 20).until(
            lambda d: has_quiz_results(d)
            and d.execute_script(
                """
                const loading = [...document.querySelectorAll('.ari-loading, .yui-dt-loading')]
                  .some(el => el.offsetParent !== null && el.textContent.trim().toLowerCase().includes('loading'));
                const rows = [...document.querySelectorAll('#dtResults .yui-dt-data tr, .aq-dt-results .yui-dt-data tr')];
                const hasFilledRow = rows.some(row => {
                  const questionText = row.querySelector('.aq-question-content')?.textContent.trim();
                  const answerCount = row.querySelectorAll('.aq-answer-container').length;
                  const explanationText = row.querySelector('.aq-question-explanation')?.textContent.trim();
                  return questionText || answerCount > 0 || explanationText;
                });
                return !loading && rows.length > 0 && hasFilledRow;
                """
            )
        )
        expand_quiz_results_page_size(driver)
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script(
                """
                const rows = [...document.querySelectorAll('#dtResults .yui-dt-data tr, .aq-dt-results .yui-dt-data tr')];
                if (!rows.length) return false;
                return rows.every(row => {
                  const panel = row.querySelector('.aq-question-panel');
                  if (!panel) return false;
                  const questionText = panel.querySelector('.aq-question-content')?.textContent.trim();
                  const answerCount = panel.querySelectorAll('.aq-answer-container').length;
                  const explanationText = panel.querySelector('.aq-question-explanation')?.textContent.trim();
                  return questionText || answerCount > 0 || explanationText;
                });
                """
            )
        )
        time.sleep(1)
    except TimeoutException:
        print("Timed out waiting for quiz results table; saving current DOM.", flush=True)


def expand_quiz_results_page_size(driver):
    try:
        for select_el in driver.find_elements(By.CSS_SELECTOR, ".yui-pg-container select"):
            options = [
                option.get_attribute("value")
                for option in select_el.find_elements(By.CSS_SELECTOR, "option")
            ]
            numeric_options = [int(value) for value in options if value and value.isdigit()]
            if not numeric_options:
                continue

            target = str(max(numeric_options))
            before_rows = len(driver.find_elements(By.CSS_SELECTOR, "#dtResults .yui-dt-data tr"))
            Select(select_el).select_by_value(target)
            WebDriverWait(driver, 10).until(
                lambda d: (
                    len(d.find_elements(By.CSS_SELECTOR, "#dtResults .yui-dt-data tr")) >= int(target)
                    or len(d.find_elements(By.CSS_SELECTOR, "#dtResults .yui-dt-data tr")) > before_rows
                )
                and d.execute_script(
                    """
                    return [...document.querySelectorAll('#dtResults .yui-dt-data tr')].some(row => {
                      const questionText = row.querySelector('.aq-question-content')?.textContent.trim();
                      const answerCount = row.querySelectorAll('.aq-answer-container').length;
                      const explanationText = row.querySelector('.aq-question-explanation')?.textContent.trim();
                      return questionText || answerCount > 0 || explanationText;
                    });
                    """
                )
            )
    except (TimeoutException, WebDriverException) as exc:
        print(f"Could not expand quiz results page size before saving: {exc}", flush=True)


def submit_current_quiz_page(driver):
    if not choose_placeholder_answers(driver):
        return False

    before_position = current_question_position(driver)
    submit_buttons = driver.find_elements(By.CSS_SELECTOR, ".aq-button-panel .btn-primary.disable-onsubmit")
    if not submit_buttons:
        return False

    try:
        submitted = driver.execute_script(
            """
            if (window.ariQuizQueManager) {
              if (!ariQuizQueManager.validate || ariQuizQueManager.validate()) {
                ariQuizQueManager.savePage();
                return true;
              }
            }
            return false;
            """
        )
        if not submitted:
            submit = submit_buttons[0]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", submit)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", submit)
        WebDriverWait(driver, 20).until(
            lambda d: (
                current_question_position(d) is not None
                and current_question_position(d) != before_position
            )
            or has_quiz_results(d)
        )
        time.sleep(1)
        return True
    except (TimeoutException, WebDriverException) as exc:
        print(f"Skipped quiz submit capture: {exc}", flush=True)
        return False


def capture_quiz_results(driver, export_dir, first_step_path, absolute_url, asset_map):
    results_path = quiz_results_path(first_step_path)
    if results_path.exists() and results_path.stat().st_size > 0 and not should_refresh_pages():
        print("Using cached quiz results HTML:", results_path, flush=True)
        return results_path

    if not submit_current_quiz_page(driver):
        print("Could not submit quiz page for results capture.", flush=True)
        return None

    wait_for_quiz_results(driver)
    save_current_quiz_results(driver, export_dir, results_path, absolute_url, asset_map)
    return results_path


def rewrite_quiz_submit_link(local_path, next_path):
    html = local_path.read_text(encoding="utf-8", errors="replace")
    relative = relative_asset_path(local_path, next_path)

    def replace_submit_anchor(match):
        attrs = match.group(1)
        attrs = re.sub(r"\s+href=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        attrs = re.sub(r"\s+onclick=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        return f'<a{attrs} href="{relative}" onclick="window.location.href=this.href; return false;">'

    rewritten = re.sub(
        r'<a\b([^>]*class="[^"]*\bbtn-primary\b[^"]*\bdisable-onsubmit\b[^"]*"[^>]*)>',
        replace_submit_anchor,
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if rewritten != html:
        local_path.write_text(rewritten, encoding="utf-8")


def rewrite_quiz_submit_noop(local_path):
    html = local_path.read_text(encoding="utf-8", errors="replace")

    def replace_submit_anchor(match):
        attrs = match.group(1)
        attrs = re.sub(r"\s+href=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        attrs = re.sub(r"\s+onclick=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        return f'<a{attrs} href="#" onclick="return false;">'

    rewritten = re.sub(
        r'<a\b([^>]*class="[^"]*\bbtn-primary\b[^"]*\bdisable-onsubmit\b[^"]*"[^>]*)>',
        replace_submit_anchor,
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if rewritten != html:
        local_path.write_text(rewritten, encoding="utf-8")


def capture_quiz_question_flow(driver, export_dir, first_step_path, absolute_url, asset_map):
    first_position = current_question_position(driver)
    if not first_position:
        return []

    saved_paths = []
    results_path = None
    current_path = first_step_path
    seen_positions = set()
    max_steps = int(os.getenv("CELPIP_MAX_QUIZ_STEPS", "40"))

    for _ in range(max_steps):
        position = current_question_position(driver)
        if not position:
            break

        question_index, question_total = position
        if position in seen_positions:
            break
        seen_positions.add(position)

        if question_index == 1:
            current_path = first_step_path
        else:
            current_path = quiz_question_step_path(first_step_path, question_index)

        save_current_quiz_question(driver, export_dir, current_path, absolute_url, asset_map)
        saved_paths.append(current_path)

        if question_index >= question_total:
            results_path = capture_quiz_results(driver, export_dir, first_step_path, absolute_url, asset_map)
            break

        if not submit_current_quiz_page(driver):
            break

    if saved_paths and saved_paths[0] != first_step_path and first_step_path.exists():
        rewrite_quiz_submit_link(first_step_path, saved_paths[0])

    for current_path, next_path in zip(saved_paths, saved_paths[1:]):
        rewrite_quiz_submit_link(current_path, next_path)

    if saved_paths and results_path:
        rewrite_quiz_submit_link(saved_paths[-1], results_path)
    elif saved_paths:
        rewrite_quiz_submit_noop(saved_paths[-1])

    return saved_paths


def save_quiz_next_step(driver, export_dir, local_path, absolute_url, asset_map):
    buttons = driver.find_elements(By.CSS_SELECTOR, ".aq-btn-continue")
    if not buttons:
        return None

    next_path = quiz_step_path(local_path)
    next_results_path = quiz_results_path(next_path)
    if (
        next_path.exists()
        and next_path.stat().st_size > 0
        and quiz_results_complete(next_results_path)
        and not should_refresh_pages()
    ):
        print("Using cached quiz next-step HTML:", next_path, flush=True)
        return next_path
    if next_path.exists() and next_path.stat().st_size > 0 and not quiz_results_complete(next_results_path):
        print("Refreshing quiz next-step with missing results:", next_path, flush=True)

    try:
        button = buttons[0]
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", button)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", button)
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}
        )
        time.sleep(1)
        restart_completed_quiz_if_needed(driver)
        next_html = staticize_quiz_html(neutralize_local_popups(remove_base_href(driver.page_source)))
        position = current_question_position(driver)
        current_path = next_path
        if position and position[0] != 1 and next_path.exists() and next_path.stat().st_size > 0:
            current_path = quiz_question_step_path(next_path, position[0])
        current_path.write_text(next_html, encoding="utf-8")
        localize_html_assets(driver, export_dir, current_path, absolute_url, next_html, asset_map)
        capture_quiz_question_flow(driver, export_dir, next_path, absolute_url, asset_map)
        print("Saved quiz next-step HTML:", next_path, flush=True)
        return next_path
    except WebDriverException as exc:
        print(f"Skipped quiz next-step capture for {absolute_url}: {exc}", flush=True)
        return None


def rewrite_quiz_continue_link(local_path, next_path):
    html = local_path.read_text(encoding="utf-8", errors="replace")
    relative = relative_asset_path(local_path, next_path)

    def replace_continue_anchor(match):
        attrs = match.group(1)
        attrs = re.sub(r"\s+href=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        attrs = re.sub(r"\s+onclick=(['\"]).*?\1", "", attrs, flags=re.IGNORECASE | re.DOTALL)
        return f'<a{attrs} href="{relative}" onclick="window.location.href=this.href; return false;">'

    rewritten = re.sub(
        r'<a\b([^>]*class="[^"]*\baq-btn-continue\b[^"]*"[^>]*)>',
        replace_continue_anchor,
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if rewritten != html:
        local_path.write_text(rewritten, encoding="utf-8")


def wait_for_dynamic_content(driver):
    time.sleep(1)
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script(
                """
                const loading = [...document.querySelectorAll('.aq-ic-loading-message')]
                  .some(el => el.offsetParent !== null && el.textContent.trim().toLowerCase().includes('loading'));
                return !loading;
                """
            )
        )
    except TimeoutException:
        print("Timed out waiting for visible Loading... to disappear; saving current DOM.", flush=True)

    frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src]")
    for frame in frames:
        try:
            driver.switch_to.frame(frame)
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}
            )
        except WebDriverException:
            pass
        finally:
            driver.switch_to.default_content()


def localize_iframes(driver, export_dir, html_path, page_url, html, asset_map):
    rewritten = html_path.read_text(encoding="utf-8", errors="replace")
    frame_sources = collect_iframe_sources(html)
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src]")

    for index, raw_src in enumerate(frame_sources):
        absolute = urljoin(page_url, raw_src)
        parsed = urlparse(absolute)
        if parsed.netloc and parsed.netloc != urlparse(URL).netloc:
            continue
        if index >= len(frames):
            continue

        local_path = iframe_local_path(export_dir, absolute)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        iframe_missing_results = cached_iframe_missing_quiz_results(local_path)
        if local_path.exists() and local_path.stat().st_size > 0 and not should_refresh_pages() and not iframe_missing_results:
            print("Using cached iframe HTML:", local_path, flush=True)
            next_path = quiz_step_path(local_path)
            cached_html = local_path.read_text(encoding="utf-8", errors="replace")
            if "aq-btn-continue" in cached_html and next_path.exists() and next_path.stat().st_size > 0:
                rewrite_quiz_continue_link(local_path, next_path)
        else:
            if iframe_missing_results:
                print("Refreshing iframe with missing quiz results:", local_path, flush=True)
            try:
                driver.switch_to.frame(frames[index])
                WebDriverWait(driver, 15).until(
                    lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}
                )
                wait_for_quiz_dynamic_state(driver)
                iframe_html = staticize_quiz_html(neutralize_local_popups(remove_base_href(driver.page_source)))
                local_path.write_text(iframe_html, encoding="utf-8")
                localize_html_assets(driver, export_dir, local_path, absolute, iframe_html, asset_map)
                restart_completed_quiz_if_needed(driver)
                wait_for_quiz_dynamic_state(driver)
                if current_question_position(driver):
                    capture_quiz_question_flow(driver, export_dir, local_path, absolute, asset_map)
                else:
                    next_path = save_quiz_next_step(driver, export_dir, local_path, absolute, asset_map)
                    if next_path:
                        rewrite_quiz_continue_link(local_path, next_path)
                print("Saved iframe HTML:", local_path, flush=True)
            except WebDriverException as exc:
                print(f"Skipped iframe {absolute}: {exc}", flush=True)
            finally:
                driver.switch_to.default_content()

        relative = relative_asset_path(html_path, local_path)
        escaped_src = escape(raw_src, quote=True)
        rewritten = rewritten.replace(f'src="{raw_src}"', f'src="{relative}"')
        rewritten = rewritten.replace(f"src='{raw_src}'", f"src='{relative}'")
        rewritten = rewritten.replace(f'src="{escaped_src}"', f'src="{relative}"')
        rewritten = rewritten.replace(f"src='{escaped_src}'", f"src='{relative}'")
        rewritten = rewritten.replace(f'src: "{raw_src}"', f'src: "{relative}"')
        rewritten = rewritten.replace(f"src: '{raw_src}'", f"src: '{relative}'")
        rewritten = rewritten.replace(f'src: "{escaped_src}"', f'src: "{relative}"')
        rewritten = rewritten.replace(f"src: '{escaped_src}'", f"src: '{relative}'")
        rewritten = staticize_outer_iframes(rewritten)
        rewritten = rewritten.replace(" aq-ic-loading", "")
        rewritten = re.sub(r'<div class="aq-ic-loading-message">.*?</div>', "", rewritten, flags=re.DOTALL)

    html_path.write_text(rewritten, encoding="utf-8")


def rewrite_links(html_path, link_map):
    html = html_path.read_text(encoding="utf-8")
    rewritten = html
    for source, target in link_map.items():
        rewritten = rewritten.replace(f'href="{source}"', f'href="{target}"')
        rewritten = rewritten.replace(f"href='{source}'", f"href='{target}'")
        absolute = URL.rstrip("/") + source
        rewritten = rewritten.replace(f'href="{absolute}"', f'href="{target}"')
        rewritten = rewritten.replace(f"href='{absolute}'", f"href='{target}'")
    if rewritten != html:
        html_path.write_text(rewritten, encoding="utf-8")


def rewrite_page_links(html_path, path_to_page):
    link_map = {
        path: relative_asset_path(html_path, page_path)
        for path, page_path in path_to_page.items()
        if page_path != html_path
    }
    rewrite_links(html_path, link_map)


def write_status_report(stem, rows):
    report_path = OUT_DIR / f"{stem}.txt"
    lines = [f"{TARGET_LABEL} Open Check", ""]
    for row in rows:
        lines.extend(
            [
                f"Part: {row['part']}",
                f"URL: {row['url']}",
                f"Title: {row['title']}",
                f"Status: {row['status']}",
                "",
            ]
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {stem} status report:", report_path, flush=True)
    return report_path


def title_from_saved_html(html_path):
    html = html_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return html_path.stem
    return re.sub(r"\s+", " ", match.group(1)).strip()


def has_stale_dynamic_loading(html_path):
    if not html_path.exists():
        return False
    html = html_path.read_text(encoding="utf-8", errors="replace")
    return "aq-ic-loading-message" in html or 'src="/index.php?option=com_ariquiz' in html


def save_or_reuse_html(driver, wait, export_dir, page_path, url, asset_map, label):
    stale_dynamic = has_stale_dynamic_loading(page_path)
    missing_quiz_results = cached_page_missing_quiz_results(page_path)
    if (
        page_path.exists()
        and page_path.stat().st_size > 0
        and not should_refresh_pages()
        and not stale_dynamic
        and not missing_quiz_results
    ):
        print(f"Using cached {label} HTML:", page_path, flush=True)
        return {
            "url": url,
            "title": title_from_saved_html(page_path),
            "status": "OK: cached local HTML",
        }
    if stale_dynamic:
        print(f"Refreshing stale dynamic {label} HTML:", page_path, flush=True)
    if missing_quiz_results:
        print(f"Refreshing {label} with missing quiz results:", page_path, flush=True)

    print(f"Saving {label}: {url}", flush=True)
    driver.get(url)
    wait.until(lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"})
    wait_for_dynamic_content(driver)

    current_url = driver.current_url
    title = driver.title
    is_login_page = bool(driver.find_elements(By.CSS_SELECTOR, "form.mod-login"))
    status = "FAILED: redirected to login" if is_login_page else "OK: saved local HTML"

    page_path.parent.mkdir(parents=True, exist_ok=True)
    html = driver.page_source
    page_path.write_text(html, encoding="utf-8")
    localize_html_assets(driver, export_dir, page_path, current_url, html, asset_map)
    localize_iframes(driver, export_dir, page_path, current_url, html, asset_map)
    print(f"Saved {label} local HTML:", page_path, flush=True)

    return {"url": current_url, "title": title, "status": status}


def normalize_first_test_path(raw_href, base_url):
    raw_path = urlparse(raw_href).path
    if not urlparse(raw_href).scheme and not raw_href.startswith("/") and raw_path.endswith(".html"):
        local_path = raw_path.removesuffix(".html").strip("/")
        first_segment = local_path.split("/", 1)[0]
        if "/" in local_path and first_segment in {"listening", "reading", "writing", "speaking"}:
            return f"{FIRST_TEST_PREFIX}/{local_path}".rstrip("/")
        return None

    absolute = urljoin(base_url, raw_href)
    parsed = urlparse(absolute)
    site_host = urlparse(URL).netloc

    if parsed.netloc and parsed.netloc != site_host:
        return None
    if parsed.query or parsed.fragment:
        return None
    if Path(parsed.path).suffix.lower() in ASSET_EXTENSIONS:
        return None

    path = parsed.path.rstrip("/")
    if path == FIRST_TEST_PREFIX or path.startswith(FIRST_TEST_PREFIX + "/"):
        return path
    return None


def local_page_path_for_test_path(export_dir, path):
    relative = path.removeprefix(FIRST_TEST_PREFIX).strip("/")
    if not relative:
        relative = "index"

    parts = relative.split("/")
    if len(parts) == 1:
        return export_dir / "pages" / f"{parts[0]}.html"

    return export_dir / "pages" / Path(*parts[:-1]) / f"{parts[-1]}.html"


def label_for_test_path(path):
    relative = path.removeprefix(FIRST_TEST_PREFIX).strip("/")
    if not relative:
        return f"Test{CELPIP_TEST}"
    return relative.replace("/", " - ").replace("-", " ").title()


def write_local_index(export_dir, rows):
    row_by_file = {row["file"].removeprefix("./"): row for row in rows}
    rows = []
    for part, _ in FIRST_TEST_PARTS:
        filename = f"{part.lower()}.html"
        page_path = export_dir / "pages" / filename
        row = row_by_file.get(filename)
        rows.append(
            {
                "part": part,
                "file": filename,
                "title": row["title"] if row else title_from_saved_html(page_path) if page_path.exists() else part,
                "status": row["status"] if row else "OK: cached local HTML" if page_path.exists() else "MISSING",
            }
        )
    cards = "\n".join(
        f"""
        <li>
          <a href="pages/{escape(row['file'])}">{escape(row['part'])}</a>
          <span>{escape(row['status'])}</span>
          <small>{escape(row['title'])}</small>
        </li>
        """
        for row in rows
    )
    index_path = export_dir / "index.html"
    index_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(TARGET_LABEL)} Local Debug Copy</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; line-height: 1.5; }}
    h1 {{ margin-bottom: 8px; }}
    p {{ color: #555; }}
    ul {{ list-style: none; padding: 0; max-width: 720px; }}
    li {{ display: grid; grid-template-columns: 160px 1fr; gap: 8px 16px; padding: 14px 0; border-bottom: 1px solid #ddd; }}
    a {{ font-weight: 600; color: #2d5c46; }}
    span {{ color: #333; }}
    small {{ grid-column: 2; color: #666; }}
  </style>
</head>
<body>
  <h1>{escape(TARGET_LABEL)} Local Debug Copy</h1>
  <p>Generated from the current logged-in debug pages. Same-origin page assets are saved under <code>assets/</code> when possible.</p>
  <ul>
    {cards}
  </ul>
</body>
</html>
""",
        encoding="utf-8",
    )
    print("Saved local debug index:", index_path, flush=True)
    return index_path


def load_dotenv(path):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def first_present(driver, selectors, timeout=8):
    wait = WebDriverWait(driver, timeout)
    for by, selector in selectors:
        try:
            return wait.until(EC.presence_of_element_located((by, selector)))
        except TimeoutException:
            continue
    return None


def build_driver():
    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    service = Service(executable_path=chromedriver_path) if chromedriver_path else Service()
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


def open_home(driver, wait):
    print(f"Opening {URL}", flush=True)
    driver.get(URL)
    wait.until(lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"})
    print("Opened:", driver.current_url, flush=True)
    print("Title:", driver.title, flush=True)


def login(driver, wait):
    open_home(driver, wait)

    username = os.getenv("CELPIP_USERNAME")
    password = os.getenv("CELPIP_PASSWORD")

    if os.getenv("CELPIP_PROMPT_LOGIN") == "1" and not (username and password):
        username = input("CELPIP username: ").strip()
        password = getpass("CELPIP password: ")

    if username and password:
        user_input = first_present(
            driver,
            [
                (By.CSS_SELECTOR, "input[name='username']"),
                (By.CSS_SELECTOR, "input[name='user']"),
                (By.CSS_SELECTOR, "input[name='email']"),
                (By.CSS_SELECTOR, "input[type='text']"),
            ],
        )
        pass_input = first_present(
            driver,
            [
                (By.CSS_SELECTOR, "input[name='password']"),
                (By.CSS_SELECTOR, "input[type='password']"),
            ],
        )

        if user_input and pass_input:
            user_input.clear()
            user_input.send_keys(username)
            pass_input.clear()
            pass_input.send_keys(password)
            print("Credentials filled.", flush=True)

            submit = first_present(
                driver,
                [
                    (By.CSS_SELECTOR, "button[name='Submit']"),
                    (By.CSS_SELECTOR, "button[type='submit']"),
                    (By.CSS_SELECTOR, "input[type='submit']"),
                ],
                timeout=3,
            )

            old_url = driver.current_url
            if submit:
                submit.click()
            else:
                pass_input.send_keys(Keys.ENTER)

            print("Submitted login form.", flush=True)
            try:
                wait.until(
                    lambda d: d.current_url != old_url
                    or not d.find_elements(By.CSS_SELECTOR, "form.mod-login")
                    or d.find_elements(By.CSS_SELECTOR, ".alert-danger, .alert-message, .alert-error")
                )
            except TimeoutException:
                print("Login submit completed, but no page change was detected before timeout.", flush=True)

            save_page(driver, "after_login")
        else:
            print("Login fields were not found with the common selectors.", flush=True)
    else:
        print(
            "CELPIP_USERNAME/CELPIP_PASSWORD were not set; leaving login manual. "
            "Set CELPIP_PROMPT_LOGIN=1 to type them securely at runtime.",
            flush=True,
        )


def check_first_test_menu(driver, wait):
    driver.execute_script(
        """
        const modal = document.getElementById('myModal');
        if (modal) modal.style.display = 'none';
        localStorage.setItem('modalShown', 'true');
        """
    )

    celpip_menu = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "li.itemid424 > a.dj-up_a")))
    celpip1_menu = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.itemid232 > a.dj-more")))
    test1_menu = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.itemid233 > a.dj-more")))
    listening_link = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "a[href='/celpip-1-13/celpip-1/test1/listening']"))
    )

    driver.execute_script(
        """
        const anchors = [arguments[0], arguments[1], arguments[2]];
        for (const anchor of anchors) {
          const item = anchor.closest('li');
          if (!item) continue;
          item.classList.add('hover', 'active');
          const wrap = item.querySelector(':scope > .dj-subwrap');
          if (wrap) {
            wrap.style.display = 'block';
            wrap.style.visibility = 'visible';
            wrap.style.opacity = '1';
            wrap.style.height = 'auto';
            wrap.style.overflow = 'visible';
            wrap.style.zIndex = '9999';
          }
        }
        """,
        celpip_menu,
        celpip1_menu,
        test1_menu,
    )
    time.sleep(0.5)

    menu_html = save_page(driver, "menu_celpip1_test1")
    print("Menu exists: Celpip 1-13 > CELPIP-1 > Test1", flush=True)
    print("First part link:", listening_link.get_attribute("href"), flush=True)
    print("Saved menu HTML:", menu_html, flush=True)


def check_first_test_parts(driver, wait):
    rows = []

    for part, path in FIRST_TEST_PARTS:
        url = URL.rstrip("/") + path
        print(f"Opening {TARGET_LABEL} {part}: {url}", flush=True)
        driver.get(url)
        wait.until(lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"})
        time.sleep(0.5)

        current_url = driver.current_url
        title = driver.title
        is_login_page = bool(driver.find_elements(By.CSS_SELECTOR, "form.mod-login"))
        alert_texts = [
            alert.text.strip().lower()
            for alert in driver.find_elements(By.CSS_SELECTOR, ".alert-danger, .alert-error, .alert-warning")
            if alert.text.strip()
        ]
        has_error = (
            title.strip() in {"403", "404", "Error", "Not Found"}
            or "not found" in title.lower()
            or any(("error" in text or "unauthorized" in text or "not found" in text) for text in alert_texts)
        )

        if is_login_page:
            status = "FAILED: redirected to login"
        elif has_error:
            status = "CHECK: page loaded but contains an error keyword"
        else:
            status = "OK: page opened"

        if should_save_screenshots():
            screenshot_path = OUT_DIR / f"{REPORT_PREFIX}_{part.lower()}.png"
            driver.save_screenshot(str(screenshot_path))
            print(f"Saved {part} screenshot:", screenshot_path, flush=True)

        rows.append({"part": part, "url": current_url, "title": title, "status": status})
        print(f"{part}: {status}", flush=True)

    report_path = write_status_report(f"{REPORT_PREFIX}_open_check", rows)
    print("Saved first-test open-check report:", report_path, flush=True)
    open_home(driver, wait)


def save_first_test_local_copy(driver, wait):
    export_dir = LOCAL_EXPORT_DIR
    pages_dir = export_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    asset_map = {}
    rows = []
    queued = [path for _, path in FIRST_TEST_PARTS]
    seen = set()
    path_to_page = {}

    while queued:
        path = queued.pop(0)
        if path in seen:
            continue
        seen.add(path)

        url = URL.rstrip("/") + path
        page_path = local_page_path_for_test_path(export_dir, path)
        saved = save_or_reuse_html(
            driver,
            wait,
            export_dir,
            page_path,
            url,
            asset_map,
            f"{TARGET_LABEL} {label_for_test_path(path)}",
        )
        path_to_page[path] = page_path
        rows.append(
            {
                "part": label_for_test_path(path),
                "url": saved["url"],
                "title": saved["title"],
                "status": saved["status"],
                "file": relative_asset_path(pages_dir, page_path),
            }
        )

        html = page_path.read_text(encoding="utf-8", errors="replace")
        for href in sorted(collect_html_links(html)):
            next_path = normalize_first_test_path(href, url)
            if next_path and next_path not in seen and next_path not in queued:
                queued.append(next_path)

    for page_path in path_to_page.values():
        rewrite_page_links(page_path, path_to_page)

    write_local_index(export_dir, rows)
    write_status_report(f"{REPORT_PREFIX}_local_copy", rows)
    open_home(driver, wait)


def main():
    load_dotenv(ENV_PATH)

    driver = build_driver()
    wait = WebDriverWait(driver, 20)

    try:
        login(driver, wait)

        if os.getenv("CELPIP_CHECK_MENU") == "1":
            check_first_test_menu(driver, wait)

        checked_first_test = os.getenv("CELPIP_CHECK_FIRST_TEST") == "1"
        if checked_first_test:
            check_first_test_parts(driver, wait)

        if os.getenv("CELPIP_SAVE_FIRST_TEST_LOCAL") == "1":
            save_first_test_local_copy(driver, wait)

        save_page(driver, "page")

        hold_seconds = int(os.getenv("CELPIP_HOLD_SECONDS", "600"))
        print(f"Browser will stay open for {hold_seconds} seconds for manual inspection.", flush=True)
        time.sleep(hold_seconds)
    except WebDriverException as exc:
        print("Selenium failed:", exc, flush=True)
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
