import functools
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from test_practice_layout_ui import CHROME, PreviewHandler


ROOT = Path(__file__).resolve().parents[1]
TEST_ID = "local_celpip1_test1"


class MockHandler(PreviewHandler):
    submissions = []
    drafts = []
    fail_section = None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/submissions":
            self.send_json(200, {"attempts": []})
        elif path == "/api/drafts":
            self.send_json(200, {"drafts": []})
        elif path == "/api/reviews":
            self.send_json(200, {"reviews": []})
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/submissions", "/api/drafts"):
            super().do_POST()
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if path == "/api/drafts":
            self.drafts.append(payload)
            self.send_json(200, {"ok": True})
        elif payload["section"] == self.fail_section:
            self.send_json(503, {"error": "Temporary storage failure"})
        else:
            self.submissions.append(payload)
            self.send_json(201, {"attempt_id": len(self.submissions), "created_at": "2026-10-25"})


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for mock exam UI tests")
class MockExamUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, str(ROOT / "scripts/build_pages_preview.py"), "--output", cls.tmp.name],
                       check=True, capture_output=True)
        app = Path(cls.tmp.name) / "webapp/app.js"
        app.write_text(app.read_text().replace("const SERVER_API_ENABLED = false;", "const SERVER_API_ENABLED = true;"))
        questions = Path(cls.tmp.name) / f"output/{TEST_ID}/questions.json"
        data = json.loads(questions.read_text())
        for question in data["questions"]:
            if question["section"] == "speaking":
                question["timing"] = {"preparation_seconds": 1, "recording_seconds": 1}
        questions.write_text(json.dumps(data))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(MockHandler, directory=cls.tmp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        for flag in ("--headless=new", "--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"):
            options.add_argument(flag)
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_script_timeout(15)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def setUp(self):
        self.url = f"http://127.0.0.1:{self.server.server_port}/webapp/index.html"
        self.driver.get(self.url + "?view=overview")
        self.driver.execute_script("localStorage.clear()")
        self.driver.refresh()
        self.wait(lambda d: d.find_elements("css selector", "[data-mock-test]"))
        MockHandler.submissions.clear()
        MockHandler.drafts.clear()
        MockHandler.recordings.clear()
        MockHandler.fail_section = None

    def wait(self, condition, timeout=15):
        return WebDriverWait(self.driver, timeout).until(condition)

    def section(self, name):
        self.wait(lambda d: d.execute_script("return state.section === arguments[0] && !state.submittingSection && "
                                           "document.getElementById('partLabel').textContent.endsWith(' · ' + arguments[0])", name))

    def start(self):
        self.driver.find_element("css selector", "[data-mock-test]").click()
        self.wait(lambda d: d.find_elements("css selector", "#partPassageMedia"))

    def call(self, code):
        result = self.driver.execute_async_script(
            "const done = arguments[arguments.length - 1]; (async () => {" + code +
            "})().then(() => done(null)).catch(e => done(e.stack));")
        self.assertIsNone(result)

    def assert_scores_hidden(self):
        self.assertFalse(self.driver.find_element("id", "historyBtn").is_displayed())
        self.assertFalse(self.driver.find_element("id", "overviewBtn").is_displayed())
        self.assertFalse(self.driver.find_element("id", "sectionResult").is_displayed())
        self.assertFalse(self.driver.find_element("id", "scoreCount").is_displayed())
        self.assertFalse(self.driver.find_elements("css selector", ".inline-feedback, .option.correct, .option.wrong, .response-samples"))
        self.assertTrue(self.driver.execute_script("return [...document.querySelectorAll('#sectionTabs button')].every(b => b.disabled)"))
        self.assertEqual([], MockHandler.submissions)
        self.assertEqual([], MockHandler.drafts)

    def test_full_exam_auto_transitions_records_and_reveals_results_only_at_end(self):
        practice = {"answer": "preserve me"}
        self.driver.execute_script("""
            localStorage.setItem('celpip-practice:local_celpip1_test1', arguments[0]);
            localStorage.setItem('celpip-practice:local_celpip1_test1:timings', '{"old":42}');
            localStorage.setItem('celpip-practice:local_celpip1_test1:submissions', '{"listening":{"correct":38}}');
        """, json.dumps(practice))
        self.start()
        self.wait(lambda d: d.find_elements("id", "nextListeningQuestion"))
        for index in range(2):
            self.driver.find_element("css selector", ".option input").click()
            self.driver.find_element("id", "nextListeningQuestion").click()
            if index == 0:
                self.wait(lambda d: "Question 2" in d.find_element("id", "mediaArea").text)
        self.section("reading")
        self.assert_scores_hidden()
        self.assertTrue(self.driver.execute_script("return state.timer.running"))
        # A stale/manually edited URL cannot reveal a finished section mid-exam.
        self.driver.get(self.driver.current_url.replace("section=reading", "section=listening"))
        self.section("reading")
        self.assert_scores_hidden()
        self.driver.find_element("id", "nextBtn").click()
        self.section("writing")
        for index in range(2):
            self.assert_scores_hidden()
            self.assertTrue(self.driver.execute_script("return state.timer.running"))
            self.driver.find_element("css selector", ".long-response").send_keys("My completed writing response.")
            self.driver.find_element("id", "nextBtn").click()
            if index == 0:
                self.wait(lambda d: d.execute_script("return state.index === 1"))
        self.section("speaking")
        self.assert_scores_hidden()
        self.wait(lambda d: d.find_element("id", "mockResultsView").is_displayed()
                  and d.execute_script("return !state.mockSaving"), 25)
        self.assertEqual(["listening", "reading", "writing", "speaking"], [p["section"] for p in MockHandler.submissions])
        self.assertTrue(all(p["defer_assessment"] for p in MockHandler.submissions))
        self.assertEqual(3, len(MockHandler.recordings))
        self.assertEqual(4, len(self.driver.find_elements("css selector", ".mock-result-card")))
        self.assertEqual(practice, json.loads(self.driver.execute_script("return localStorage.getItem('celpip-practice:local_celpip1_test1')")))
        self.assertEqual({"old": 42}, json.loads(self.driver.execute_script("return localStorage.getItem('celpip-practice:local_celpip1_test1:timings')")))
        self.driver.refresh()
        self.wait(lambda d: d.find_element("id", "mockResultsView").is_displayed())
        self.assertEqual(4, len(MockHandler.submissions), "Refreshing results must not duplicate successful submissions")
        self.driver.find_element("css selector", '[data-mock-review="listening"]').click()
        self.wait(lambda d: d.find_elements("css selector", ".inline-feedback"))
        self.assertTrue(self.driver.find_element("id", "mockResultsBtn").is_displayed())

    def test_timers_advance_unanswered_parts_and_resume_saved_position(self):
        self.start()
        self.call("await submitSection();")
        self.section("reading")
        self.call("state.timer.remaining = 0; await handleTimerExpired();")
        self.section("writing")
        self.assert_scores_hidden()
        self.call("state.timer.remaining = 0; await handleTimerExpired();")
        self.assertEqual(1, self.driver.execute_script("return state.index"))
        self.assertEqual(26 * 60, self.driver.execute_script("return state.timer.remaining"))
        self.driver.find_element("css selector", ".long-response").send_keys("Saved writing draft")
        self.driver.find_element("id", "exitMockBtn").click()
        self.wait(lambda d: d.find_elements("css selector", "[data-mock-test]"))
        self.assertIn("Resume mock exam", self.driver.find_element("css selector", "[data-mock-test]").text)
        self.driver.find_element("css selector", "[data-mock-test]").click()
        self.section("writing")
        self.assertEqual(1, self.driver.execute_script("return state.index"))
        self.wait(lambda d: d.execute_script("""
            return !document.getElementById('practiceView').hidden
                && document.querySelector('.long-response')?.value === 'Saved writing draft'
                && currentGroup()?.questions.some(q => state.answers[q.key] === 'Saved writing draft');
        """))
        self.assert_scores_hidden()

    def test_failed_result_sync_can_retry_only_the_missing_section(self):
        MockHandler.fail_section = "reading"
        self.start()
        for section in ("listening", "reading", "writing", "speaking"):
            self.section(section)
            self.call("await submitSection();")
        self.wait(lambda d: d.find_element("id", "retryMockSaveBtn").is_displayed())
        self.assertEqual(["listening", "writing", "speaking"], [p["section"] for p in MockHandler.submissions])
        MockHandler.fail_section = None
        self.driver.find_element("id", "retryMockSaveBtn").click()
        self.wait(lambda d: not d.find_element("id", "retryMockSaveBtn").is_displayed()
                  and d.execute_script("return !state.mockSaving"))
        self.assertEqual(["listening", "writing", "speaking", "reading"], [p["section"] for p in MockHandler.submissions])

    def test_plain_writing_prompt_recovers_paragraphs_lists_and_excludes_answers(self):
        self.start()
        self.call("""
            const question = state.data.questions.find(q => q.section === 'writing');
            delete question.question_html;
            const originalFetch = window.fetch;
            window.fetch = async (...args) => args[0] === sourceUrl(question.source_file)
                ? new Response(`<main itemprop="articleBody"><p><strong>Read the information.</strong></p>
                    <p>A fictional workplace is asking for feedback.</p><p>&nbsp;</p>
                    <p>Write an email covering these points:</p><ul><li>Describe a problem.</li><li>Suggest a solution.</li></ul>
                    <div>26 Minutes</div><div data-rlta-element="container"><h3>CELPIP 10-12 Response</h3>
                    <p>SECRET SAMPLE ANSWER</p></div><div class="mod-custom"><textarea>OLD EDITOR</textarea></div></main>`)
                : originalFetch(...args);
            await loadWritingPrompt(question);
            window.fetch = originalFetch;
            state.section = 'writing'; state.index = 0; state.sectionIntro = false;
            await render();
        """)
        prompt = self.driver.find_element("css selector", ".structured-prompt")
        self.assertEqual(3, len(prompt.find_elements("css selector", "p")))
        self.assertEqual(2, len(prompt.find_elements("css selector", "li")))
        self.assertNotIn("Minutes", prompt.text)
        self.assertNotIn("SECRET", prompt.text)
        self.assertNotIn("OLD EDITOR", prompt.text)
        self.assertEqual(1, len(self.driver.find_elements("css selector", "textarea.long-response")))


if __name__ == "__main__":
    unittest.main()
