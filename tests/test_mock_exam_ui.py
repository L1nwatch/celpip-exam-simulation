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
from urllib.parse import parse_qs, urlparse

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
    attempts = {}
    saved_drafts = {}
    history_unavailable = False
    playback_recordings = {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/submissions":
            if self.history_unavailable:
                self.send_json(503, {"error": "History temporarily unavailable"})
                return
            attempt_id = parse_qs(urlparse(self.path).query).get('attempt_id', [None])[0]
            if attempt_id:
                attempt = self.attempts.get(int(attempt_id))
                self.send_json(200 if attempt else 404, {"attempt": attempt} if attempt else {"error": "Not found"})
            else:
                self.send_json(200, {"attempts": list(reversed(list(self.attempts.values())))})
        elif path == "/api/drafts":
            self.send_json(200, {"drafts": list(self.saved_drafts.values())})
        elif path == "/api/reviews":
            self.send_json(200, {"reviews": []})
        elif path == "/api/recordings" and self.playback_recordings:
            key = parse_qs(urlparse(self.path).query).get('question_key', [''])[0]
            self.send_json(200, {"recordings": self.playback_recordings.get(key, [])})
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
            if payload.get('notes_only'):
                draft = self.saved_drafts.setdefault(payload['test_id'], {'test_id': payload['test_id']})
                draft.setdefault('notes', {}).update(payload['notes'])
            else:
                self.saved_drafts[payload['test_id']] = payload
            self.send_json(200, {"ok": True})
        elif payload["section"] == self.fail_section:
            self.send_json(503, {"error": "Temporary storage failure"})
        else:
            self.submissions.append(payload)
            self.attempts[len(self.submissions)] = {
                **payload, 'id': len(self.submissions), 'created_at': '2026-10-31T18:00:00Z',
            }
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
        MockHandler.attempts.clear()
        MockHandler.saved_drafts.clear()
        MockHandler.playback_recordings.clear()
        MockHandler.history_unavailable = False
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

    def complete_mock(self):
        self.start()
        self.call("""
            state.data.questions.forEach((q, index) => {
                state.answers[q.key] = q.question_type === 'multiple_choice_single' ? q.options[0].id
                    : q.section === 'writing' ? 'My submitted mock exam essay.' : `recording:${index + 1}`;
            });
            persist();
            for (let index = 0; index < 4; index += 1) await submitSection();
        """)
        self.wait(lambda d: d.find_element('id', 'mockResultsView').is_displayed() and d.execute_script('return !state.mockSaving'))

    def test_completed_overview_sections_open_saved_results_without_overwriting_practice(self):
        self.driver.execute_script("""
            localStorage.setItem('celpip-practice:local_celpip1_test1', '{"celpip1_test1_writing_demo_writing_email":"Keep my unfinished essay"}');
        """)
        self.complete_mock()
        for section in ('listening', 'reading', 'writing', 'speaking'):
            self.driver.find_element('id', 'overviewBtn').click()
            self.wait(lambda d: d.find_elements('css selector', f'[data-section="{section}"].completed'))
            self.driver.find_element('css selector', f'[data-test="{TEST_ID}"][data-section="{section}"]').click()
            self.section(section)
            self.wait(lambda d: d.execute_script('return !!state.attemptReview && !!document.querySelector(".question-card")'))
            self.assertFalse(self.driver.find_elements('css selector', '.section-start-panel'))
            self.assertFalse(self.driver.execute_script('return state.timer.running'))
            self.assertIn('attempt=', self.driver.current_url)
            if section == 'writing':
                textarea = self.driver.find_element('css selector', '.long-response')
                self.assertEqual('My submitted mock exam essay.', textarea.get_attribute('value'))
                self.assertTrue(textarea.get_attribute('readonly'))
            elif section in ('listening', 'reading'):
                self.assertTrue(all(el.get_attribute('disabled') for el in self.driver.find_elements('css selector', '.option input')))
                self.assertTrue(self.driver.find_elements('css selector', '.inline-feedback'))
            self.driver.refresh()
            self.section(section)
            self.wait(lambda d: d.execute_script('return !!state.attemptReview && !!document.querySelector(".question-card")'))
        self.assertEqual({'celpip1_test1_writing_demo_writing_email': 'Keep my unfinished essay'}, json.loads(self.driver.execute_script(
            "return localStorage.getItem('celpip-practice:local_celpip1_test1')")))
        self.assertEqual([], MockHandler.drafts)
        self.assertEqual(4, len(MockHandler.submissions))

    def test_new_browser_and_history_review_exact_attempt_and_recording(self):
        self.complete_mock()
        self.driver.execute_script('localStorage.clear()')
        self.driver.get(self.url + '?view=overview')
        self.wait(lambda d: d.find_elements('css selector', '[data-section="writing"].completed'))
        self.driver.find_element('css selector', f'[data-test="{TEST_ID}"][data-section="writing"]').click()
        self.section('writing')
        self.wait(lambda d: d.execute_script("return document.querySelector('.long-response')?.value === 'My submitted mock exam essay.'"))
        speaking = MockHandler.attempts[4]
        question = speaking['responses'][1]
        recording_id = int(question['answer_value'].split(':')[1])
        MockHandler.playback_recordings[question['question_key']] = [
            {'id': recording_id + 100, 'url': '/newer-recording.webm', 'created_at': '2026-10-31', 'duration_seconds': 60},
            {'id': recording_id, 'url': '/saved-recording.webm', 'created_at': '2026-10-30', 'duration_seconds': 50},
        ]
        self.driver.find_element('id', 'historyBtn').click()
        self.wait(lambda d: d.find_elements('css selector', '.review-attempt[data-attempt="4"]'))
        self.driver.find_element('css selector', '.review-attempt[data-attempt="4"]').click()
        self.section('speaking')
        self.driver.find_element('id', 'nextBtn').click()
        self.wait(lambda d: d.execute_script("return document.querySelector('.recorded-playback')?.getAttribute('src') === '/saved-recording.webm'"))
        self.assertFalse(self.driver.execute_script('return state.timer.running'))
        self.assertEqual([], MockHandler.drafts)

    def test_review_notes_preserve_in_progress_drafts(self):
        self.complete_mock()
        original = {'test_id': TEST_ID, 'answers': {'unfinished': 'Keep my response'}, 'checked': {},
                    'submissions': {'writing': None}, 'timings': {'writing': {'remaining_seconds': 123}},
                    'notes': {'other': 'Keep this note'}, 'updated_at': '2026-10-31T10:00:00Z'}
        MockHandler.saved_drafts[TEST_ID] = json.loads(json.dumps(original))
        self.driver.get(self.url + '?attempt=3')
        self.wait(lambda d: d.find_elements('css selector', '.review-note-input'))
        self.driver.find_element('css selector', '.review-note-input').send_keys('My review note')
        self.wait(lambda d: any(draft.get('notes_only') for draft in MockHandler.drafts))
        for field in ('answers', 'checked', 'submissions', 'timings', 'updated_at'):
            self.assertEqual(original[field], MockHandler.saved_drafts[TEST_ID][field])
        self.assertEqual('Keep this note', MockHandler.saved_drafts[TEST_ID]['notes']['other'])
        self.assertEqual(4, len(MockHandler.submissions))

    def test_browser_mock_results_still_open_when_history_is_offline(self):
        self.complete_mock()
        MockHandler.history_unavailable = True
        self.driver.find_element('id', 'overviewBtn').click()
        self.wait(lambda d: d.find_elements('css selector', '[data-section="writing"].completed'))
        self.driver.find_element('css selector', f'[data-test="{TEST_ID}"][data-section="writing"]').click()
        self.section('writing')
        self.wait(lambda d: d.execute_script("return document.querySelector('.long-response')?.value === 'My submitted mock exam essay.'"))
        self.assertTrue(self.driver.find_element('css selector', '.long-response').get_attribute('readonly'))
        self.assertFalse(self.driver.find_elements('css selector', '.section-start-panel'))
        self.driver.refresh()
        self.section('writing')
        self.wait(lambda d: d.execute_script("return document.querySelector('.long-response')?.readOnly === true"))

    def test_full_exam_auto_transitions_records_and_reveals_results_only_at_end(self):
        practice = {"answer": "preserve me"}
        self.driver.execute_script("""
            localStorage.setItem('celpip-practice:local_celpip1_test1', arguments[0]);
            localStorage.setItem('celpip-practice:local_celpip1_test1:timings', '{"old":42}');
            localStorage.setItem('celpip-practice:local_celpip1_test1:submissions', '{"listening":{"correct":38}}');
        """, json.dumps(practice))
        self.start()
        self.wait(lambda d: d.find_elements('id', 'nextListeningQuestion') or d.execute_script(
            "return document.getElementById('passageStatus')?.textContent.includes('Playback could not start')"))
        if not self.driver.find_elements('id', 'nextListeningQuestion'):
            self.driver.find_element('id', 'startPassageBtn').click()
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
