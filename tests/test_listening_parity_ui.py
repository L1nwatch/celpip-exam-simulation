import functools
import io
import json
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from test_reading_dropdown_ui import CHROME, QuietHandler, TEST_ID


ROOT = Path(__file__).resolve().parents[1]


def fixture_wav(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(struct.pack("<h", 0) * round(8000 * seconds))
    return output.getvalue()


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for Listening parity tests")
class ListeningParityUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        preview = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts/build_pages_preview.py"),
                        "--output", cls.tmp.name], check=True, capture_output=True)
        pack = preview / "output" / TEST_ID
        (pack / "main.wav").write_bytes(fixture_wav(20))
        (pack / "question.wav").write_bytes(fixture_wav(.7))
        data = json.loads((pack / "questions.json").read_text())
        data["questions"] = [q for q in data["questions"] if q["section"] != "listening"]
        groups = []
        titles = ["Listening to Problem Solving Section 1", "Listening to Problem Solving Section 2",
                  "Listening to Problem Solving Section 3", "Listening to a Daily Life Conversation",
                  "Listening for Information", "Listening to a News Item", "Listening to a Discussion",
                  "Listening for Viewpoints"]
        for index, (title, count) in enumerate(zip(titles, (2, 3, 3, 5, 6, 5, 8, 6))):
            page = f"pages/listening/parity-{index}.html"
            (pack / page).write_text('<main itemprop="articleBody"><p>Fake transcript for review.</p></main>')
            keys = []
            for number in range(1, count + 1):
                key = f"listening-{index}-{number}"
                keys.append(key)
                images = [{"type": "image", "path": f"assets/images/demo-community-board.svg#{i}"} for i in range(4)] if index == 0 and number == 1 else []
                question_audio = f"question-{index}-{number}.wav"
                (pack / question_audio).write_bytes(fixture_wav(.7))
                data["questions"].append({
                    "key": key, "number": number, "section": "listening", "question_type": "multiple_choice_single",
                    "question_text": f"Question {number} Listen to a short statement. You will hear it only once."
                    if index < 5 else f"{number}. The speakers recommend",
                    "media": ([{"type": "audio", "path": question_audio}] + images) if index < 5 else [],
                    "options": [{"id": f"{key}-o{i}", "value": f"{key}-v{i}", "text": f"answer {i}",
                                 "is_correct": i == 1, "media": [images[i - 1]] if images else []} for i in range(1, 5)],
                    "source_pages": [{"file": page, "title": title}],
                })
            groups.append({"id": f"parity-{index}", "title": title, "section": "listening", "source_file": page,
                           "question_keys": keys, "media": [{"type": "video", "path": "assets/video/demo-listening-notice.mp4"}]
                           if index == 6 else [{"type": "audio", "path": "main.wav"}]})
        data["question_groups"]["listening"] = groups
        data["sections"]["listening"] = 38
        (pack / "questions.json").write_text(json.dumps(data))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=cls.tmp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        for flag in ("--headless=new", "--no-sandbox", "--autoplay-policy=no-user-gesture-required"):
            options.add_argument(flag)
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

    def wait(self, callback):
        return WebDriverWait(self.driver, 8, poll_frequency=.05).until(callback)

    def setUp(self):
        self.driver.get(self.url + "?view=overview")
        self.driver.execute_script("localStorage.clear()")
        self.driver.get(self.url + "?test=celpip1-test1&section=listening&part=1")
        self.wait(lambda d: d.find_elements("id", "partPassageMedia"))

    def show_group(self, index, unlocked=True, review=False):
        error = self.driver.execute_async_script("""
            const [index, unlocked, review, done] = arguments;
            stopPracticePlayback(); stopListeningQuestionTimer(); stopTimer();
            state.section = 'listening'; state.index = index; state.submissions = {};
            const group = currentGroup();
            if (unlocked) state.listeningUnlocked.add(listeningUnlockKey(group));
            else state.listeningUnlocked.delete(listeningUnlockKey(group));
            if (review) {
                state.submissions.listening = {correct: 0, total: 38, elapsed_seconds: 50};
                group.questions.forEach(q => {state.answers[q.key] = q.options[1].value; state.checked[q.key] = false;});
            }
            render().then(() => done(null)).catch(e => done(e.message));
        """, index, unlocked, review)
        self.assertIsNone(error)

    def test_all_six_parts_have_the_correct_answer_mode_and_phase_timer(self):
        for index, total in enumerate((2, 3, 3, 5, 6, 5, 8, 6)):
            self.show_group(index)
            with self.subTest(index=index):
                self.assertFalse(self.driver.find_element("css selector", ".timer").is_displayed())
                if index < 5:
                    self.assertEqual(4, len(self.driver.find_elements("css selector", '#answerArea input[type="radio"]')))
                    self.assertFalse(self.driver.find_elements("css selector", ".reading-select"))
                    self.assertIn("00:30", self.driver.find_element("id", "listeningQuestionTime").text)
                else:
                    self.assertEqual(total, len(self.driver.find_elements("css selector", ".reading-select")))
                    self.assertFalse(self.driver.find_elements("css selector", '#answerArea input[type="radio"]'))
                    control = self.driver.find_element("css selector", ".reading-select")
                    control.click()
                    options = self.driver.find_elements("css selector", '[role="option"]')
                    self.assertEqual(4, len(options))
                    options[1].click()
                    self.assertEqual(f"listening-{index}-1-o2", self.driver.execute_script("return state.answers[arguments[0]]", f"listening-{index}-1"))

    def test_problem_solving_numbers_continue_across_the_three_sections(self):
        for index, number in ((0, 1), (1, 3), (2, 6)):
            self.show_group(index)
            self.assertIn(f"Question {number} of 8", self.driver.find_element("id", "mediaArea").text)
            self.assertEqual(f"Question {number}", self.driver.find_element("css selector", ".question-card h2").text)

    def test_image_answers_are_not_duplicated_above_the_options(self):
        self.show_group(0)
        self.assertEqual(4, len(self.driver.find_elements("css selector", ".option-media img")))
        self.assertFalse(self.driver.find_elements("css selector", ".card-media img"))

    def test_passage_autoplays_once_and_only_then_opens_questions(self):
        self.wait(lambda d: d.execute_script("return !document.getElementById('partPassageMedia').paused"))
        self.assertFalse(self.driver.find_elements("css selector", ".question-card"))
        self.assertFalse(self.driver.find_element("id", "partPassageMedia").get_attribute("controls"))
        self.driver.execute_script("document.getElementById('partPassageMedia').dispatchEvent(new Event('ended'))")
        self.wait(lambda d: d.find_elements("id", "listeningQuestionAudio"))
        self.assertFalse(self.driver.find_elements("id", "partPassageMedia"))
        self.assertFalse(self.driver.find_element("id", "nextListeningQuestion").is_enabled())
        self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())

    def test_audio_permission_failure_pauses_time_and_retry_resumes(self):
        self.driver.execute_script("""
            const play = HTMLMediaElement.prototype.play;
            let blocked = true;
            HTMLMediaElement.prototype.play = function(...args) {
                if (this.id === 'listeningQuestionAudio' && blocked) {
                    blocked = false; return Promise.reject(new DOMException('Blocked', 'NotAllowedError'));
                }
                return play.apply(this, args);
            };
        """)
        self.show_group(0)
        self.wait(lambda d: d.find_element("id", "startQuestionAudio").is_displayed())
        before = self.driver.find_element("id", "listeningQuestionTime").text
        time.sleep(1.1)
        self.assertEqual(before, self.driver.find_element("id", "listeningQuestionTime").text)
        self.assertFalse(self.driver.execute_script("return state.timer.running"))
        self.driver.find_element("id", "startQuestionAudio").click()
        self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())
        self.assertTrue(self.driver.execute_script("return state.timer.running"))

    def test_missing_spoken_question_does_not_start_a_silent_countdown(self):
        self.driver.execute_script("state.data.questions.find(q => q.key === 'listening-2-2').media = []")
        self.show_group(2, unlocked=False)
        self.assertIn("Question 7", self.driver.find_element("css selector", ".data-quality-warning").text)
        self.assertFalse(self.driver.find_elements("id", "partPassageMedia"))
        self.assertFalse(self.driver.find_elements("id", "listeningQuestionTime"))
        self.assertFalse(self.driver.execute_script("return state.timer.running"))

    def test_phase_clock_uses_elapsed_time_when_the_browser_is_busy(self):
        self.show_group(5)
        self.driver.execute_script("""
            stopListeningQuestionTimer();
            window.expired = 0;
            startListeningQuestionTimer(currentGroup(), 3, () => window.expired++);
            const start = performance.now(); while (performance.now() - start < 2200) {}
        """)
        self.wait(lambda d: d.find_element("id", "listeningQuestionTime").text == "00:01")
        self.wait(lambda d: d.execute_script("return window.expired === 1"))
        time.sleep(.2)
        self.assertEqual(1, self.driver.execute_script("return window.expired"))

    def test_late_part_review_restores_dropdown_answers_and_notes(self):
        self.show_group(6, review=True)
        controls = self.driver.find_elements("css selector", ".reading-select")
        self.assertEqual(8, len(controls))
        self.assertTrue(all(c.get_attribute("aria-readonly") == "true" for c in controls))
        self.assertEqual("listening-6-1-o2", controls[0].get_attribute("data-value"))
        self.assertEqual(8, len(self.driver.find_elements("css selector", ".review-note-input")))

    def test_phone_review_can_tap_choices_without_changing_submitted_answers(self):
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width":390, "height":844, "deviceScaleFactor":1, "mobile":True})
        self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled":True})

        def tap(element):
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'})", element)
            point = self.driver.execute_script("""
                const r = arguments[0].getBoundingClientRect();
                return {x:r.x+r.width/2, y:r.y+r.height/2};
            """, element)
            self.assertTrue(self.driver.execute_script("""
                return arguments[0].contains(document.elementFromPoint(arguments[1].x, arguments[1].y));
            """, element, point), self.driver.execute_script("""
                return {point:arguments[0], hit:document.elementFromPoint(arguments[0].x, arguments[0].y)?.outerHTML};
            """, point))
            self.driver.execute_cdp_cmd("Input.dispatchTouchEvent", {"type":"touchStart", "touchPoints":[point]})
            self.driver.execute_cdp_cmd("Input.dispatchTouchEvent", {"type":"touchEnd", "touchPoints":[]})

        try:
            for index in (5, 6, 7):
                with self.subTest(part=index - 1):
                    self.show_group(index, review=True)
                    control = self.driver.find_element("css selector", ".reading-select")
                    original = self.driver.execute_script("""
                        return JSON.stringify({answers:state.answers, checked:state.checked,
                            submission:state.submissions.listening, storage:{...localStorage}});
                    """)
                    self.assertEqual("0", control.get_attribute("tabindex"))
                    self.assertTrue(control.find_element("css selector", ".reading-answer-arrow").is_displayed())
                    tap(control.find_element("css selector", ".reading-answer-arrow"))
                    menu = self.driver.find_element("id", "readingAnswerOptions")
                    self.wait(lambda d: menu.is_displayed())
                    options = menu.find_elements("css selector", '[role="option"]')
                    self.assertEqual(4, len(options))
                    self.assertEqual("true", options[1].get_attribute("aria-selected"))
                    self.assertEqual("Your answer", options[1].find_element("css selector", ".reading-choice-saved").text)
                    self.assertNotIn("Correct answer:", menu.text)
                    self.assertTrue(self.driver.execute_script("""
                        const r = document.getElementById('readingAnswerOptions').getBoundingClientRect();
                        return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
                    """))
                    tap(options[0])
                    self.wait(lambda d: not menu.is_displayed())
                    self.assertEqual(original, self.driver.execute_script("""
                        return JSON.stringify({answers:state.answers, checked:state.checked,
                            submission:state.submissions.listening, storage:{...localStorage}});
                    """))
                    self.assertTrue(control.get_attribute("data-value").endswith("-o2"))
        finally:
            self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled":False})
            self.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})

    def seed_mock(self, index, unlocked=False, heard=False):
        self.driver.execute_script("""
            stopPracticePlayback(); stopListeningQuestionTimer(); stopTimer();
            state.section = 'listening'; state.index = arguments[0];
            state.answers = {}; state.checked = {}; state.submissions = {}; state.timings = {}; state.notes = {};
            state.exam = {testId:state.testId, status:'active', started_at:new Date().toISOString()};
            const group = currentGroup(), key = listeningUnlockKey(group);
            state.listeningUnlocked = new Set(arguments[1] ? [key] : []);
            state.listeningQuestionIndex = new Map([[key, 0]]);
            state.exam.listeningPassageTimes = {[key]: 6};
            state.exam.listeningRemaining = {[key+':0']: 23};
            if (arguments[2]) state.exam.listeningQuestionAudio = {[group.questions[0].key]:{position:0,ended:true}};
            persistMockExam();
        """, index, unlocked, heard)
        self.driver.get(self.url + '?mode=mock&test=celpip1-test1')

    def test_mock_refresh_resumes_the_passage_instead_of_replaying_it(self):
        self.seed_mock(0)
        self.wait(lambda d: d.execute_script("return document.getElementById('partPassageMedia')?.currentTime >= 6"))
        self.assertLess(self.driver.execute_script("return document.getElementById('partPassageMedia').currentTime"), 10)
        self.assertTrue(self.driver.execute_script("return state.timer.running"))

    def test_mock_refresh_keeps_remaining_time_and_does_not_replay_finished_questions(self):
        for index in (1, 5):
            with self.subTest(index=index):
                self.seed_mock(index, unlocked=True, heard=True)
                self.wait(lambda d: d.find_elements('id', 'listeningQuestionTime'))
                self.assertIn(self.driver.find_element('id', 'listeningQuestionTime').text, ('00:23', '00:22'))
                self.assertTrue(self.driver.execute_script('return state.timer.running'))
                self.assertFalse(self.driver.find_elements('id', 'partPassageMedia'))
                if index == 1:
                    self.assertTrue(self.driver.execute_script("return document.getElementById('listeningQuestionAudio').paused"))
                    self.assertTrue(self.driver.find_element('id', 'nextListeningQuestion').is_enabled())
                else:
                    self.assertEqual(5, len(self.driver.find_elements('css selector', '.reading-select')))

    def test_leaving_playback_detaches_the_old_ended_handler(self):
        self.driver.execute_script("window.oldPassage = document.getElementById('partPassageMedia')")
        self.driver.find_element("id", "overviewBtn").click()
        self.driver.execute_script("window.oldPassage.dispatchEvent(new Event('ended'))")
        self.assertTrue(self.driver.find_element("id", "overviewView").is_displayed())
        self.assertTrue(self.driver.execute_script("return window.oldPassage.paused"))

    def test_next_question_plays_from_complete_preload_with_network_offline(self):
        self.wait(lambda d: d.execute_script("""
            return [...practiceResources.assets.values()].some(e => e.url.endsWith('question-0-2.wav') && e.blobUrl);
        """))
        self.assertFalse(self.driver.find_elements("css selector", ".question-card"))
        self.assertFalse(self.driver.execute_script("return state.listeningUnlocked.has(listeningUnlockKey(currentGroup()))"))
        self.driver.execute_script("document.getElementById('partPassageMedia').dispatchEvent(new Event('ended'))")
        self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.emulateNetworkConditions", {
                "offline": True, "latency": 0, "downloadThroughput": 0, "uploadThroughput": 0})
            self.driver.find_element("id", "nextListeningQuestion").click()
            self.wait(lambda d: "Question 2" in d.find_element("id", "mediaArea").text)
            self.assertTrue(self.driver.find_element("id", "listeningQuestionAudio").get_attribute("src").startswith("blob:"))
            self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())
            self.assertTrue(self.driver.execute_script("return state.timer.running"))
            self.assertEqual({}, self.driver.execute_script("return state.answers"))
        finally:
            self.driver.execute_cdp_cmd("Network.emulateNetworkConditions", {
                "offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})

    def test_external_preload_is_silent_and_reuses_the_buffered_player(self):
        other = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=self.tmp.name))
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            self.driver.execute_script("""
                stopPracticePlayback(); resetPracticeResources();
                // Production publishers use HTTPS. This fixture's second origin
                // is a plain local HTTP server with no CORS headers.
                window.originalAssetUrl = assetUrl;
                assetUrl = path => path.startsWith('http://') ? path : originalAssetUrl(path);
                const group = state.data.question_groups.listening[1];
                group.media = [{type:'audio', path:arguments[0]}];
                render();
            """, f"http://127.0.0.1:{other.server_port}/output/{TEST_ID}/main.wav")
            self.wait(lambda d: d.execute_script("""
                return [...practiceResources.assets.values()].some(e => e.player?.readyState >= 4);
            """))
            self.assertTrue(self.driver.execute_script("""
                window.preparedPlayer = [...practiceResources.assets.values()].find(e => e.player)?.player;
                return preparedPlayer.paused && preparedPlayer.currentTime === 0;
            """))
            self.driver.execute_script("preparedPlayer.dispatchEvent(new Event('ended'))")
            self.assertEqual(0, self.driver.execute_script("return state.listeningUnlocked.size"))
            self.show_group(1, unlocked=False)
            self.assertTrue(self.driver.execute_script("return preparedPlayer === document.getElementById('partPassageMedia')"))
            self.wait(lambda d: d.execute_script("return !preparedPlayer.paused && preparedPlayer.currentTime > 0"))
        finally:
            self.driver.execute_script("assetUrl = originalAssetUrl")
            other.shutdown()
            other.server_close()
            thread.join()

    def test_leaving_practice_aborts_speculative_load_and_releases_session_cache(self):
        self.wait(lambda d: d.execute_script("return practiceResources.bytes > 0"))
        self.driver.find_element("id", "overviewBtn").click()
        self.assertEqual({"scope": "", "assets": 0, "bytes": 0}, self.driver.execute_script("""
            return {scope:practiceResources.scope, assets:practiceResources.assets.size, bytes:practiceResources.bytes};
        """))

    def test_next_part_video_plays_offline_after_preloading(self):
        self.show_group(5)
        self.wait(lambda d: d.execute_script("""
            return [...practiceResources.assets.values()].some(e => e.type === 'video' && e.blobUrl);
        """))
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.emulateNetworkConditions", {
                "offline": True, "latency": 0, "downloadThroughput": 0, "uploadThroughput": 0})
            self.driver.find_element("id", "nextListeningQuestion").click()
            self.wait(lambda d: d.find_elements("css selector", "video#partPassageMedia"))
            self.assertTrue(self.driver.find_element("id", "partPassageMedia").get_attribute("src").startswith("blob:"))
            self.wait(lambda d: d.execute_script("return document.getElementById('partPassageMedia').currentTime > .1"))
            self.assertFalse(self.driver.find_elements("css selector", ".question-card"))
        finally:
            self.driver.execute_cdp_cmd("Network.emulateNetworkConditions", {
                "offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})

    def test_failed_speculative_request_does_not_block_normal_question_playback(self):
        self.driver.execute_script("""
            stopPracticePlayback(); resetPracticeResources();
            const originalFetch = fetch;
            window.fetch = (...args) => String(args[0]).endsWith('question-0-2.wav')
                ? Promise.reject(new TypeError('Simulated preload connection failure')) : originalFetch(...args);
            render();
        """)
        self.wait(lambda d: d.execute_script("""
            return [...practiceResources.assets.values()].some(e => e.url.endsWith('question-0-2.wav') && e.finished && !e.ready);
        """))
        self.driver.execute_script("document.getElementById('partPassageMedia').dispatchEvent(new Event('ended'))")
        self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())
        self.driver.find_element("id", "nextListeningQuestion").click()
        self.wait(lambda d: "Question 2" in d.find_element("id", "mediaArea").text)
        self.wait(lambda d: d.find_element("id", "nextListeningQuestion").is_enabled())
        self.assertTrue(self.driver.execute_script("return state.timer.running"))


if __name__ == "__main__":
    unittest.main()
