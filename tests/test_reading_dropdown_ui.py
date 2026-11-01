import functools
import json
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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
TEST_ID = "local_celpip1_test1"
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
if Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
    CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def reading_fixture():
    titles = ["Reading Correspondence", "Reading to Apply a Diagram",
              "Reading for Information", "Reading for Viewpoints"]
    counts = [11, 8, 9, 10]
    groups, questions, pages = [], [], {}
    for part, (title, count) in enumerate(zip(titles, counts), 1):
        page = f"pages/reading/dropdown-part-{part}.html"
        keys = []
        for number in range(1, count + 1):
            key = f"reading-{part}-{number}"
            keys.append(key)
            choices = list("ABCDE") if part == 3 else ["the creative workshop", "the sports club",
                                                        "the afternoon class", "the weekend course"]
            questions.append({
                "key": key, "id": key, "section": "reading", "number": number,
                "question_type": "multiple_choice_single",
                "question_text": f"_______ - {number}. The statement is supported by the passage."
                if part == 3 else f"{number}. The message recommends __________.",
                "options": [{"id": f"{key}-o{i}", "value": f"{key}-v{i}", "label": str(i),
                             "text": text, "is_correct": i == 1, "media": []}
                            for i, text in enumerate(choices, 1)],
                "source_pages": [{"file": page, "title": title}],
            })
        groups.append({"id": f"reading-{part}", "section": "reading", "title": title,
                       "source_file": page, "question_keys": keys, "media": []})
        if part == 2:
            source = '<p class="reading2-img"><img src="../../assets/images/demo-community-board.svg" alt="Community workshop schedule"></p>'
            message = ('<p class="hidden-article">Subject: Weekend workshops<br><br>Hi Morgan,<br><br>'
                       'I recommend 1. __________ for Avery. The brochure also lists 2. __________ for Sam.'
                       '<br><br>Please book 3. __________ before Friday. We can attend 4. __________ together.'
                       ' You can find us at 5. __________ afterwards.<br><br>Best,<br>Riley</p>')
        elif part in (1, 4):
            source = '<div class="reading-article"><p>Original source passage stays on the left.</p></div>'
            start = 7 if part == 1 else 6
            message = '<h2>Complete the response by filling in the blanks.</h2><div class="reading-answer"><p>Dear Taylor,</p><p>'
            message += ' '.join(f'We could choose {number}. __________ for our next visit.'
                                for number in range(start, count + 1))
            message += '</p><p>Kind regards,<br>Jordan</p></div>'
        else:
            source = '<div class="reading-article"><p>A. First paragraph.</p><p>B. Second paragraph.</p><p>C. Third paragraph.</p><p>D. Fourth paragraph.</p></div>'
            message = ''
        pages[page] = '<main itemprop="articleBody"><div class="reading-left">' + source + message + '</div><div class="reading-right">Instructions and old question controls.</div></main>'
    return groups, questions, pages


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for reading dropdown UI tests")
class ReadingDropdownUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.preview = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts/build_pages_preview.py"),
                        "--output", cls.tmp.name], check=True, capture_output=True)
        cls.groups, cls.questions, cls.pages = reading_fixture()
        pack = cls.preview / "output" / TEST_ID
        data = json.loads((pack / "questions.json").read_text())
        data["questions"] = [q for q in data["questions"] if q["section"] != "reading"] + cls.questions
        data["question_groups"]["reading"] = cls.groups
        (pack / "questions.json").write_text(json.dumps(data))
        for page, html in cls.pages.items():
            (pack / page).write_text(html)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         functools.partial(QuietHandler, directory=cls.tmp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_window_size(1440, 1000)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/webapp/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def setUp(self):
        self.driver.get(self.url + "?view=overview")
        self.driver.execute_script("localStorage.clear()")
        self.open_part(2)

    def open_part(self, part):
        self.driver.get(self.url + f"?test=celpip1-test1&section=reading&part={part}")
        WebDriverWait(self.driver, 10).until(lambda d: len(d.find_elements(
            "css selector", ".reading-select")) == len(self.groups[part - 1]["question_keys"]))

    def open_answers(self, key):
        control = self.driver.find_element("css selector", f'[data-reading-key="{key}"]')
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", control)
        if control.get_attribute("aria-expanded") != "true":
            control.click()
        WebDriverWait(self.driver, 5).until(lambda d: d.find_element("id", "readingAnswerOptions").is_displayed())
        return control

    def choose(self, key, option=1):
        control = self.open_answers(key)
        self.driver.find_element("css selector", f'[role="option"][data-option-id="{key}-o{option}"]').click()
        return control

    def test_diagram_email_is_one_passage_with_inline_dropdowns(self):
        self.assertEqual(5, len(self.driver.find_elements("css selector", ".reading-passage .reading-select")))
        self.assertEqual(3, len(self.driver.find_elements("css selector", ".reading-question")))
        self.assertFalse(self.driver.find_elements("css selector", '#answerArea input[type="radio"]'))
        self.assertTrue(self.driver.find_elements("css selector", "#sourceContent img"))
        self.assertNotIn("Hi Morgan", self.driver.find_element("id", "sourceContent").text)
        self.assertIn("Hi Morgan", self.driver.find_element("css selector", ".reading-passage-body").text)
        self.open_answers("reading-2-1")
        self.assertEqual(4, len(self.driver.find_elements("css selector", '[role="option"]')))
        self.assertFalse(self.driver.find_elements("css selector", "#answerArea select"))

    def test_answers_persist_without_replacing_the_paragraph(self):
        passage = self.driver.find_element("css selector", ".reading-passage-body")
        self.assertTrue(self.driver.find_element("id", "nextBtn").is_enabled())
        for key in self.groups[1]["question_keys"]:
            self.choose(key)
        self.assertTrue(passage.is_displayed())  # Selecting preserves the live DOM and reading position.
        self.assertTrue(self.driver.find_element("id", "nextBtn").is_enabled())
        self.driver.refresh()
        WebDriverWait(self.driver, 10).until(lambda d: d.find_elements("css selector", ".reading-passage"))
        self.assertEqual("reading-2-1-o1", self.driver.find_element("css selector", ".reading-select").get_attribute("data-value"))
        self.choose("reading-2-1", 2)
        self.assertTrue(self.driver.find_element("id", "nextBtn").is_enabled())
        answers = json.loads(self.driver.execute_script("return localStorage.getItem('celpip-practice:local_celpip1_test1')"))
        self.assertEqual("reading-2-1-o2", answers["reading-2-1"])

    def test_unanswered_reading_can_advance_or_cancel_without_unlocking_previous_parts(self):
        self.driver.find_element("id", "nextBtn").click()
        self.assertIn("8 unanswered", self.driver.switch_to.alert.text)
        self.driver.switch_to.alert.dismiss()
        self.assertEqual(1, self.driver.execute_script("return state.index"))
        self.driver.find_element("id", "nextBtn").click()
        self.driver.switch_to.alert.accept()
        WebDriverWait(self.driver, 5).until(lambda d: d.execute_script("return state.index === 2 && !!document.querySelector('.part-dot.active[data-index=\"2\"]')"))
        self.assertFalse(self.driver.find_element("id", "prevBtn").is_displayed())
        self.assertFalse(self.driver.find_element("css selector", '.part-dot[data-index="1"]').is_enabled())
        self.open_part(4)
        self.driver.find_element("id", "nextBtn").click()
        self.assertIn("10 unanswered", self.driver.switch_to.alert.text)
        self.driver.switch_to.alert.dismiss()
        self.assertFalse(self.driver.execute_script("return !!state.submissions.reading"))

    def test_reading_expiry_advances_without_an_unanswered_confirmation(self):
        error = self.driver.execute_async_script("""
            const done = arguments[0];
            state.timer.remaining = 0;
            handleTimerExpired().then(() => done(null)).catch(e => done(e.message));
        """)
        self.assertIsNone(error)
        self.assertEqual(2, self.driver.execute_script("return state.index"))
        self.assertGreater(self.driver.execute_script("return state.timer.remaining"), 595)

    def test_correspondence_and_viewpoints_keep_source_and_reply_separate(self):
        for part in (1, 4):
            with self.subTest(part=part):
                self.open_part(part)
                self.assertEqual(5, len(self.driver.find_elements("css selector", ".reading-passage .reading-select")))
                self.assertIn("Original source passage", self.driver.find_element("id", "sourceContent").text)
                self.assertNotIn("Dear Taylor", self.driver.find_element("id", "sourceContent").text)
                self.assertEqual(3, len(self.driver.find_elements("css selector", ".reading-passage-body p")))

    def test_information_matching_keeps_all_five_paragraph_options(self):
        self.open_part(3)
        self.open_answers("reading-3-1")
        options = self.driver.find_elements("css selector", '[role="option"]')
        self.assertEqual(list("ABCDE"), [option.text for option in options])
        self.assertIn("Select E if the information is not given", self.driver.find_element("id", "questionText").text)
        self.choose("reading-3-1", 5)
        self.assertEqual("reading-3-1-o5", self.driver.execute_script("return state.answers['reading-3-1']"))

    def test_dropdown_choices_are_scored_using_the_original_option_ids(self):
        for part in range(1, 5):
            self.open_part(part)
            for key in self.groups[part - 1]["question_keys"]:
                self.choose(key)
        self.driver.find_element("id", "nextBtn").click()
        WebDriverWait(self.driver, 5).until(lambda d: d.execute_script(
            "return state.submissions.reading?.correct === 38"))
        self.assertEqual(38, self.driver.execute_script("return state.submissions.reading.total"))
        WebDriverWait(self.driver, 5).until(lambda d: all(select.get_attribute("aria-disabled") == "true"
            for select in d.find_elements("css selector", ".reading-select")))

    def test_unwrapped_diagram_email_paragraphs_are_kept_together(self):
        html = self.pages[self.groups[1]["source_file"]].replace(' class="hidden-article"', '')
        layout = self.driver.execute_script("return readingLayoutFromHtml(arguments[0], currentGroup())", html)
        self.assertEqual(self.groups[1]["question_keys"][:5], layout["passages"][0]["keys"])
        self.assertIn("Hi Morgan", layout["passages"][0]["html"])
        self.assertNotIn("Hi Morgan", layout["sourceHtml"])
        invalid = html.replace('5. __________', '99. __________')
        layout = self.driver.execute_script("return readingLayoutFromHtml(arguments[0], currentGroup())", invalid)
        self.assertEqual([], layout["passages"])
        self.assertIn("Hi Morgan", layout["sourceHtml"])

    def test_unnumbered_and_linebreak_separated_blanks_keep_the_question_order(self):
        for part in (1, 2, 4):
            self.open_part(part)
            html = self.pages[self.groups[part - 1]["source_file"]]
            html = re.sub(r"\d+\. _+", "______", html).replace("recommend ______", "recommend______")
            layout = self.driver.execute_script("return readingLayoutFromHtml(arguments[0], currentGroup())", html)
            expected = self.groups[part - 1]["question_keys"][:5] if part == 2 else self.groups[part - 1]["question_keys"][-5:]
            self.assertEqual(expected, layout["passages"][0]["keys"])
        self.open_part(2)
        html = self.pages[self.groups[1]["source_file"]].replace("at 5.", "at<br>5.")
        layout = self.driver.execute_script("return readingLayoutFromHtml(arguments[0], currentGroup())", html)
        self.assertEqual(self.groups[1]["question_keys"][:5], layout["passages"][0]["keys"])

    def test_mock_dropdown_answers_save_separately_without_showing_scores(self):
        self.driver.execute_script("state.exam = {testId: state.testId, status: 'active'}; persistMockExam()")
        self.choose("reading-2-1", 2)
        exam = json.loads(self.driver.execute_script("return localStorage.getItem('celpip-mock:local_celpip1_test1')"))
        self.assertEqual("reading-2-1-o2", exam["answers"]["reading-2-1"])
        self.assertFalse(self.driver.find_elements("css selector", ".inline-feedback"))
        self.assertTrue(self.driver.find_element("id", "nextBtn").is_enabled())

    def test_review_restores_legacy_values_and_keeps_answers_hidden_until_reveal(self):
        error = self.driver.execute_async_script("""
            const done = arguments[0];
            state.data.questions.filter(q => q.section === 'reading').forEach(q => {
                state.answers[q.key] = q.options[1].value; state.checked[q.key] = false;
            });
            state.submissions.reading = {correct: 0, total: 38, elapsed_seconds: 70};
            render().then(() => {stopTimer(); done(null)}).catch(e => done(e.message));
        """)
        self.assertIsNone(error)
        selects = self.driver.find_elements("css selector", ".reading-select")
        self.assertTrue(all(select.get_attribute("aria-disabled") == "true" for select in selects))
        self.assertEqual("reading-2-1-o2", selects[0].get_attribute("data-value"))
        selects[0].click()
        self.assertFalse(self.driver.find_element("id", "readingAnswerOptions").is_displayed())
        card = self.driver.find_element("css selector", '[data-key="reading-2-1"]')
        self.assertNotIn("Correct answer:", card.text)
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", card)
        card.find_element("css selector", ".reveal-answer").click()
        WebDriverWait(self.driver, 5).until(lambda d: 'Correct answer:' in d.find_element(
            "css selector", '[data-key="reading-2-1"]').text)
        note = self.driver.find_element("css selector", '[data-key="reading-2-1"] .review-note-input')
        note.send_keys("Check the opening sentence.")
        self.assertEqual("Check the opening sentence.", self.driver.execute_script("return state.notes['reading-2-1']"))

    def test_selects_fit_phone_and_desktop_after_long_answers(self):
        self.driver.execute_script("""
            state.data.questions.filter(q => q.section === 'reading').forEach(q => {
                q.options[0].text = 'the practical workshop for students and their families on Saturday afternoon';
                state.answers[q.key] = q.options[0].id;
            });
            renderReadingQuestionSet(currentGroup(), [...state.sourceCache.values()].find(v => v?.passages));
        """)
        for width in (390, 768, 1440):
            self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
            with self.subTest(width=width):
                self.assertFalse(self.driver.execute_script("return document.documentElement.scrollWidth > innerWidth"))
                self.assertTrue(self.driver.execute_script("""
                    return [...document.querySelectorAll('.reading-select')].every(s =>
                        s.getBoundingClientRect().width <= s.closest('.reading-passage, .reading-question').clientWidth);
                """))
        self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})

    def test_selected_answer_reflows_as_text_and_keeps_the_blank_number(self):
        long_answer = "the practical workshop for students and their families on Saturday afternoon"
        self.driver.execute_script("state.data.questions.find(q => q.key === 'reading-2-1').options[0].text = arguments[0]", long_answer)
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        try:
            control = self.choose("reading-2-1")
            self.assertEqual(long_answer, control.text)
            self.assertEqual("inline", control.value_of_css_property("display"))
            self.assertEqual("700", control.value_of_css_property("font-weight"))
            self.assertGreater(self.driver.execute_script("return arguments[0].getClientRects().length", control), 1)
            self.assertIn("1.", self.driver.find_element("css selector", ".reading-passage-body").text)
            self.assertEqual("false", control.get_attribute("aria-expanded"))
        finally:
            self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})

    def test_keyboard_selects_answers_and_escape_dismisses_without_changes(self):
        control = self.driver.find_element("css selector", '[data-reading-key="reading-2-1"]')
        control.send_keys(Keys.ARROW_DOWN)
        self.assertEqual("true", control.get_attribute("aria-expanded"))
        control.send_keys(Keys.ARROW_DOWN, Keys.ENTER)
        self.assertEqual("reading-2-1-o2", control.get_attribute("data-value"))
        self.assertEqual(control, self.driver.switch_to.active_element)
        control.send_keys(Keys.ENTER, Keys.ARROW_DOWN, Keys.ESCAPE)
        self.assertEqual("reading-2-1-o2", control.get_attribute("data-value"))
        self.assertEqual("false", control.get_attribute("aria-expanded"))

    def test_answer_menu_stays_on_screen_and_is_removed_when_leaving_reading(self):
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        try:
            self.open_answers("reading-2-1")
            self.assertTrue(self.driver.execute_script("""
                const r = document.getElementById('readingAnswerOptions').getBoundingClientRect();
                return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
            """))
            self.driver.find_element("id", "overviewBtn").click()
            WebDriverWait(self.driver, 5).until(lambda d: d.find_element("id", "overviewView").is_displayed())
            self.assertFalse(self.driver.find_elements("id", "readingAnswerOptions"))
        finally:
            self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})


if __name__ == "__main__":
    unittest.main()
