import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []
        self.stylesheets = []
        self.buttons = []
        self.mains = set()
        self.aria_labels = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.stylesheets.append(attrs["href"])
        if tag == "button":
            self.buttons.append(attrs)
        if tag == "main" and attrs.get("id"):
            self.mains.add(attrs["id"])
        if "aria-label" in attrs:
            self.aria_labels.add(attrs["aria-label"])


def parse_html(path):
    parser = AssetParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def js_ids(js_text):
    return set(re.findall(r"\bid=[\"']([^\"']+)[\"']", js_text))


class StaticWebUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preview_tmp = tempfile.TemporaryDirectory()
        cls.preview_root = Path(cls.preview_tmp.name) / "pages-preview"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_pages_preview.py"), "--output", str(cls.preview_root)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.preview_tmp.cleanup()

    def test_local_and_public_html_reference_existing_assets(self):
        for html_path in (ROOT / "webapp" / "index.html", self.preview_root / "webapp" / "index.html"):
            with self.subTest(html=str(html_path)):
                parser = parse_html(html_path)
                for script in parser.scripts:
                    self.assertTrue((html_path.parent / script).exists(), script)
                for stylesheet in parser.stylesheets:
                    self.assertTrue((html_path.parent / stylesheet).exists(), stylesheet)

    def test_core_views_and_controls_are_present(self):
        parser = parse_html(ROOT / "webapp" / "index.html")
        required_ids = {
            "overviewView",
            "historyView",
            "practiceView",
            "overviewBtn",
            "historyBtn",
            "sectionTabs",
            "questionNav",
            "submitSectionBtn",
            "sourceContent",
            "answerArea",
            "timerBtn",
            "prevBtn",
            "nextBtn",
        }
        self.assertTrue(required_ids.issubset(parser.ids))
        self.assertEqual({"overviewView", "historyView", "practiceView"}, parser.mains)
        self.assertIn("Sections", parser.aria_labels)
        self.assertIn("Question list", parser.aria_labels)

    def test_all_dollar_id_lookups_have_a_declared_or_rendered_id(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        parser = parse_html(ROOT / "webapp" / "index.html")
        available_ids = parser.ids | js_ids(app_js)
        lookups = set(re.findall(r"\$\([\"']([A-Za-z0-9_-]+)[\"']\)", app_js))
        missing = sorted(lookups - available_ids)
        self.assertEqual([], missing)

    def test_web_ui_uses_server_endpoints_that_server_defines(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        server_py = (ROOT / "server.py").read_text(encoding="utf-8")
        for endpoint in (
            "/api/submissions",
            "/api/drafts",
            "/api/recordings",
            "/api/writing-assessments",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, app_js)
                self.assertIn(endpoint, server_py)

    def test_public_preview_does_not_reference_private_materials(self):
        preview_js = (self.preview_root / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("../output/", preview_js)
        self.assertIn("const SERVER_API_ENABLED = false;", preview_js)
        self.assertNotIn("materials/private", preview_js)
        self.assertNotIn("MATERIAL_ROOT", preview_js)

    def test_local_app_reads_private_material_packs_not_legacy_output(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const MATERIAL_ROOT = "../materials/private/packs";', app_js)
        self.assertIn("const SERVER_API_ENABLED = true;", app_js)
        self.assertIn("materialUrl(testId", app_js)
        self.assertIn("Object.fromEntries", app_js)
        self.assertNotIn("../output/", app_js)

    def test_practice_title_uses_clean_display_group_title(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function displayGroupTitle", app_js)
        self.assertIn(".replace(/^\\d+\\s+/, \"\")", app_js)
        self.assertIn('`Part ${state.index + 1}: ${displayTitle}', app_js)
        self.assertNotIn("`${group.title} · ${group.questions.length}", app_js)

    def test_short_demo_sections_do_not_show_celpip_level_estimates(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function hasOfficialScoreTotal", app_js)
        self.assertIn("function displayLevelForResult", app_js)
        self.assertIn("const level = displayLevelForResult(section.id, result)", app_js)
        self.assertIn("estimateLevel(state.section, correct, choiceQuestions.length)", app_js)
        self.assertIn('level: level?.level || null', app_js)
        self.assertIn('"Practice Score"', app_js)
        self.assertIn("Raw practice score only. This section is too short for a CELPIP level estimate.", app_js)
        self.assertNotIn('level: level?.level || "M"', app_js)

    def test_writing_uses_start_screen_before_timer(self):
        app_js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderWritingIntro", app_js)
        self.assertIn('if (state.section === "reading" && !state.submissions[state.section] && !state.timer.running) toggleTimer();', app_js)
        self.assertIn('if (section === "writing") return params.get("intro") !== "0";', app_js)
        self.assertIn('else if (state.section === "writing" && !state.submissions[state.section]) params.set("intro", "0");', app_js)
        self.assertIn('"Begin Writing"', app_js)
        self.assertIn('if (!state.submissions.writing && !state.timer.running) toggleTimer();', app_js)

    def test_javascript_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        for js_path in (ROOT / "webapp" / "app.js", self.preview_root / "webapp" / "app.js"):
            with self.subTest(js=js_path.name):
                result = subprocess.run(
                    [node, "--check", str(js_path)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
