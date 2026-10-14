import functools
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
if Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
    CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for layout UI tests")
class PracticeLayoutUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preview_tmp = tempfile.TemporaryDirectory()
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_pages_preview.py"),
             "--output", cls.preview_tmp.name],
            check=True, capture_output=True, text=True,
        )
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(PreviewHandler, directory=cls.preview_tmp.name),
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        cls.driver = webdriver.Chrome(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.preview_tmp.cleanup()

    def setUp(self):
        self.set_viewport(1920)
        self.driver.get(
            f"http://127.0.0.1:{self.server.server_port}/webapp/index.html"
            "?test=celpip1-test1&section=speaking&intro=1"
        )
        WebDriverWait(self.driver, 10).until(
            lambda driver: driver.find_elements("css selector", ".section-start-panel")
        )

    def set_viewport(self, width, height=1080):
        # Chrome's minimum window width exceeds a phone viewport.
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False,
        })

    def render_section(self, section="speaking", intro=False, review=False):
        error = self.driver.execute_async_script("""
            const [section, intro, review, done] = arguments;
            stopPracticePlayback();
            stopTimer();
            state.section = section;
            state.index = 1;
            state.sectionIntro = intro;
            state.submissions = review ? {[section]: {db_attempt_id: 1}} : {};
            renderSections();
            render().then(() => { stopPracticePlayback(); stopTimer(); done(null); })
                .catch(error => done(error.message));
        """, section, intro, review)
        self.assertIsNone(error)

    def layout(self):
        return self.driver.execute_script("""
            const rect = selector => {
                const {left, right, width} = document.querySelector(selector).getBoundingClientRect();
                return {left, right, width};
            };
            return {
                grid: rect('.practice-grid'), panel: rect('.question-panel'),
                overflow: document.documentElement.scrollWidth > innerWidth,
                speaking: document.querySelector('.practice-grid').classList.contains('speaking-layout'),
                sourceVisible: !document.querySelector('.source-panel').hidden,
            };
        """)

    def assert_full_width(self):
        layout = self.layout()
        self.assertAlmostEqual(layout["grid"]["left"], layout["panel"]["left"], delta=1)
        self.assertAlmostEqual(layout["grid"]["right"], layout["panel"]["right"], delta=1)
        self.assertFalse(layout["overflow"])

    def test_speaking_fills_desktop_workspace_for_intro_attempt_and_review(self):
        for width in (1440, 1920, 2560):
            self.set_viewport(width)
            for intro, review in ((True, False), (False, False), (False, True)):
                self.render_section(intro=intro, review=review)
                for collapsed in (True, False):
                    with self.subTest(width=width, intro=intro, review=review, collapsed=collapsed):
                        self.driver.execute_script("setSidebarCollapsed(arguments[0])", collapsed)
                        # Wait for the sidebar's width transition before measuring.
                        WebDriverWait(self.driver, 3).until(
                            lambda driver: driver.execute_script(
                                "return !document.getElementById('practiceView').getAnimations().length"
                            )
                        )
                        self.assertTrue(self.layout()["speaking"])
                        self.assert_full_width()

    def test_speaking_fits_phone_and_tablet_without_horizontal_scroll(self):
        self.render_section()
        for width in (390, 768, 1040):
            self.set_viewport(width, 1000)
            for collapsed in (True, False):
                with self.subTest(width=width, collapsed=collapsed):
                    self.driver.execute_script("setSidebarCollapsed(arguments[0])", collapsed)
                    self.assert_full_width()

    def test_switching_sections_keeps_writing_and_reading_layouts(self):
        self.render_section(review=True)
        self.assert_full_width()
        self.render_section("writing")
        layout = self.layout()
        self.assertFalse(layout["speaking"])
        self.assertAlmostEqual(900, layout["panel"]["width"], delta=1)
        self.assertFalse(layout["sourceVisible"])
        self.render_section("reading")
        layout = self.layout()
        self.assertFalse(layout["speaking"])
        self.assertTrue(layout["sourceVisible"])
        self.assertLess(layout["panel"]["width"], layout["grid"]["width"])
        self.assertFalse(layout["overflow"])


if __name__ == "__main__":
    unittest.main()
