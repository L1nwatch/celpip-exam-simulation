import functools
import re
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

    def render_comparison(self, choice_step=False, review=False, long_text=False):
        image = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
        cells = [f'<td><img src="{image}#{i}" style="width:100%;height:600px">'
                 f'<strong>{label}</strong><ul><li>Spend three exciting hours with the team.</li>'
                 '<li>Enjoy food and a range of activities.</li><li>Accommodates up to 50 people.</li></ul></td>'
                 for i, label in enumerate(('Sailing trip', 'Culture night'))]
        choose = '<p>Choose an outing. You do not need to speak for this part.</p><table class="kouyutable"><tr>' + ''.join(cells) + '</tr></table>'
        rival = f'<p>Your Co-worker’s Choice</p><table class="kouyutable"><tr><td><img src="{image}#rival"></td><td><strong>Street food</strong><ul><li>Eat different types of food.</li><li>Stroll downtown or sit in public parks.</li><li>Affordable prices.</li></ul></td></tr></table>'
        persuade = '<p>Persuade your co-worker that your choice is more suitable by comparing the two options.</p><p>Preparation: 60 seconds. Recording: 60 seconds.</p><p>&nbsp;</p>' + rival + '<p>Your Choice</p><table class="kouyutable"><tr>' + '<td>OR</td>'.join(cells) + '</tr></table>'
        if long_text:
            details = '<ul>' + ''.join(f'<li>{text}</li>' for text in (
                'A complete afternoon of guided activities for staff members and their guests.',
                'Refreshments, equipment and a space to relax are included at no additional charge.',
                'Accommodates up to 50 people, including visitors who have never tried the activity.',
                'Available during the summer, with flexible dates to fit the entire group schedule.',
            )) + '</ul>'
            choose = re.sub(r'<ul>.*?</ul>', details, choose)
            persuade = re.sub(r'<ul>.*?</ul>', details, persuade)
            prompt = ('Your team is planning a summer event for staff and their guests. '
                      'Compare the activities, costs and facilities in both options. '
                      'Choose the option you prefer and explain why it would be more suitable for the group.')
            choose = choose.replace('<p>Choose an outing. You do not need to speak for this part.</p>',
                                    f'<p><span style="font-size: 18px; font-weight: bold">{prompt}</span></p>')
            persuade = persuade.replace('Persuade your co-worker that your choice is more suitable by comparing the two options.',
                                        f'<span style="font-size: 18px; font-weight: bold">{prompt}</span>')
        error = self.driver.execute_async_script("""
            const [choose, persuade, choiceStep, review, done] = arguments;
            stopPracticePlayback(); stopTimer();
            const question = (key, html, text) => ({key, section: 'speaking', number: 5,
                question_type: 'speaking_task', source_file: key + '.html', question_html: html,
                question_text: text, timing: {preparation_seconds: 60, recording_seconds: key === 'choose' ? 0 : 60}});
            state.data = {questions: [question('choose', choose, 'You do not need to speak for this part.'),
                question('persuade', persuade, 'Persuade your co-worker.')]};
            state.section = 'speaking'; state.sectionIntro = false; state.index = choiceStep ? 0 : 1;
            state.answers = {choose: 'choice:0:Sailing%20trip', persuade: 'recording:fixture'};
            state.submissions = review ? {speaking: {submitted_at: '2026-10-15'}} : {};
            render().then(() => {stopPracticePlayback(); stopTimer(); done(null);}).catch(e => done(e.message));
        """, choose, persuade, choice_step, review)
        self.assertIsNone(error)

    def comparison_layout(self):
        return self.driver.execute_script("""
            const rect = el => {const {top, bottom, left, right} = el.getBoundingClientRect(); return {top, bottom, left, right};};
            return {options: [...document.querySelectorAll('.speaking-option-card')].map(rect),
                recorder: document.querySelector('.speaking-recorder') && rect(document.querySelector('.speaking-recorder')),
                panel: rect(document.querySelector('.question-panel')), controls: rect(document.querySelector('.controls')),
                overflow: document.documentElement.scrollWidth > innerWidth};
        """)

    def test_task5_options_and_recorder_fit_together_on_laptop(self):
        for width, height in ((1512, 771), (1366, 768), (1920, 1080)):
            self.set_viewport(width, height)
            for choice_step, review in ((True, False), (True, True), (False, False), (False, True)):
                self.render_comparison(choice_step, review)
                self.driver.execute_script('setSidebarCollapsed(false)')
                WebDriverWait(self.driver, 3).until(lambda d: d.execute_script(
                    "return !document.getElementById('practiceView').getAnimations().length"))
                with self.subTest(width=width, choice_step=choice_step, review=review):
                    layout = self.comparison_layout()
                    self.assertEqual(2, len(layout['options']))
                    first, second = layout['options']
                    self.assertAlmostEqual(first['top'], second['top'], delta=1)
                    self.assertLess(first['right'], second['left'])
                    self.assertLessEqual(max(first['bottom'], second['bottom']), layout['controls']['top'])
                    if layout['recorder']:
                        self.assertGreaterEqual(layout['recorder']['top'], layout['panel']['top'])
                        self.assertLess(layout['recorder']['bottom'], first['top'])
                    self.assertFalse(layout['overflow'])

    def test_task5_long_options_fit_with_navigation_and_recorder(self):
        for width in (1366, 1512):
            self.set_viewport(width, 771)
            for choice_step, review in ((True, False), (True, True), (False, False), (False, True)):
                self.render_comparison(choice_step, review, long_text=True)
                self.driver.execute_script('setSidebarCollapsed(false)')
                WebDriverWait(self.driver, 3).until(lambda d: d.execute_script(
                    "return !document.getElementById('practiceView').getAnimations().length"))
                with self.subTest(width=width, choice_step=choice_step, review=review):
                    layout = self.comparison_layout()
                    self.assertEqual(2, len(layout['options']))
                    self.assertLessEqual(max(card['bottom'] for card in layout['options']), layout['controls']['top'])
                    self.assertFalse(layout['overflow'])

    def test_task5_recorder_stays_visible_while_review_scrolls(self):
        self.set_viewport(1512, 771)
        self.render_comparison(review=True)
        self.driver.execute_script("document.querySelector('.question-panel').scrollTop = 250")
        layout = self.comparison_layout()
        self.assertGreaterEqual(layout['recorder']['top'], layout['panel']['top'])
        self.assertLess(layout['recorder']['bottom'], layout['controls']['top'])

    def test_task5_stacks_on_phone_and_fits_tablet(self):
        self.render_comparison()
        for width in (390, 768):
            self.set_viewport(width, 1000)
            with self.subTest(width=width):
                layout = self.comparison_layout()
                first, second = layout['options']
                if width == 390:
                    self.assertGreater(second['top'], first['bottom'])
                else:
                    self.assertAlmostEqual(first['top'], second['top'], delta=1)
                self.assertFalse(layout['overflow'])

    def test_task5_playback_and_live_indicator_fit_without_horizontal_scroll(self):
        self.render_comparison(review=True)
        for width, height in ((390, 1000), (1512, 771)):
            self.set_viewport(width, height)
            self.driver.execute_script("""
                document.querySelector('.recorded-player').hidden = false;
                document.querySelector('.recording-indicator').hidden = false;
                document.querySelector('.speaking-recorder').classList.add('is-recording');
            """)
            with self.subTest(width=width):
                self.assertFalse(self.comparison_layout()['overflow'])
                self.assertTrue(self.driver.find_element('css selector', '.recording-indicator').is_displayed())
                self.assertTrue(self.driver.find_element('css selector', '.recorded-player').is_displayed())

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
