import functools
import shutil
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


class TimingFixtureHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/webapp/timing-fixture.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'''<!doctype html><link rel="stylesheet" href="styles.css">
                <div id="practiceView"><div class="timer"><span id="timerLabel"></span>
                <strong id="timerValue"></strong><button id="timerBtn"></button></div>
                <div id="mediaArea"></div><div id="answerArea"></div><div id="feedback"></div>
                <div id="cards" style="max-width:900px;margin:40px auto"></div></div>''')
        else:
            super().do_GET()

    def log_message(self, *args):
        pass


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for speaking timing UI tests")
class SpeakingTimingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(TimingFixtureHandler, directory=str(ROOT)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        for flag in ("--headless=new", "--no-sandbox", "--use-fake-device-for-media-stream",
                     "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"):
            options.add_argument(flag)
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_script_timeout(8)
        cls.source = (ROOT / "webapp/app.js").read_text().split("init().catch")[0]

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.driver.get(f"http://127.0.0.1:{self.server.server_port}/webapp/timing-fixture.html")
        self.wait = WebDriverWait(self.driver, 8, poll_frequency=0.05)
        self.driver.execute_script(self.source + """
            window.timing = {state, renderQuestionCard, bindSpeakingRecorder, renderSpeakingIntro,
                stopPracticePlayback, updateTimer, startOverallTimer: toggleTimer, stopOverallTimer: stopTimer,
                events: [], uploads: [], advances: 0, streams: [], starts: 0, stops: []};
            persist = () => {};
            renderStats = () => {};
            advanceSpeakingTask = () => { timing.advances++; };
            renderPracticeStep = async () => { timing.starts++; };
            toggleTimer = () => { state.timer.running = true; };
            const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async (...args) => {
                const stream = await getUserMedia(...args);
                timing.streams.push(stream);
                return stream;
            };
            const NativeRecorder = MediaRecorder;
            window.MediaRecorder = class extends NativeRecorder {
                start(...args) {
                    timing.events.push({event: 'record', at: performance.now()});
                    super.start(...args);
                }
                stop(...args) {
                    timing.stops.push(performance.now());
                    super.stop(...args);
                }
            };
            window.fetch = async (url, options = {}) => {
                if (options.method === 'POST') timing.uploads.push({url, size: options.body.size});
                return {ok: true, json: async () => ({recordings: timing.recordings || [], recording_id: 123})};
            };
            state.section = 'speaking';
            state.data = {questions: []};
        """)

    def tearDown(self):
        self.driver.execute_script("timing.stopOverallTimer(); timing.stopPracticePlayback()")

    def start(self, prep=0.8, recording=2, setup="", review=False, choice=False):
        question = {
            "key": "task", "section": "speaking", "number": 5 if choice else 1,
            "question_type": "speaking_task",
            "question_text": "You do not need to speak for this part." if choice else "Give your friend some advice.",
            "timing": {"preparation_seconds": prep, "recording_seconds": recording},
        }
        self.driver.execute_script("""
            timing.state.data.questions = [arguments[0]];
            if (arguments[1]) {
                timing.state.submissions.speaking = {elapsed_seconds: 42};
                timing.recordings = [{url: 'assets/audio/start-speaking-now-natural.mp3',
                    duration_seconds: 60, created_at: '2026-10-01T12:00:00Z'}];
            }
            document.getElementById('cards').innerHTML = timing.renderQuestionCard(arguments[0]);
            document.querySelector('.speaking-start-cue')?.addEventListener('play', () => {
                timing.events.push({event: 'cue', at: performance.now()});
            });
            document.querySelector('.speaking-start-cue')?.addEventListener('ended', () => {
                timing.events.push({event: 'cue-ended', at: performance.now()});
            });
        """, question, review)
        if setup:
            self.driver.execute_script(setup)
        self.driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            timing.updateTimer();
            timing.bindSpeakingRecorder(document.querySelector('.question-card'),
                timing.state.data.questions[0]).then(() => done(null)).catch(e => done(e.message));
        """)

    def js(self, expression):
        return self.driver.execute_script(f"return ({expression})")

    def test_recording_gets_full_time_after_the_cue_ends(self):
        self.start()
        self.wait.until(lambda _: self.js("Number(document.querySelector('[role=progressbar]').getAttribute('aria-valuenow')) > 0"))
        self.assertEqual("Preparation", self.js("document.querySelector('.speaking-phase-label').textContent"))
        self.assertFalse(self.driver.find_element("css selector", ".timer").is_displayed())
        self.assertFalse(self.driver.find_element("css selector", ".recording-time").is_displayed())
        self.wait.until(lambda _: self.js("timing.events.some(e => e.event === 'record')"))
        self.assertEqual("Recording", self.js("document.querySelector('.speaking-phase-label').textContent"))
        events = self.js("timing.events")
        self.assertEqual(["cue", "cue-ended", "record"], [event["event"] for event in events])
        self.assertGreaterEqual(events[2]["at"], events[1]["at"])
        self.assertLess(events[2]["at"] - events[1]["at"], 250)
        self.assertTrue(self.js("document.querySelector('.speaking-start-cue').paused"))
        self.assertTrue(self.driver.find_element("css selector", ".recording-indicator").is_displayed())
        self.wait.until(lambda _: self.js("timing.uploads.length === 1 && timing.advances === 1"))
        self.assertGreater(self.js("timing.uploads[0].size"), 0)
        recorded_ms = self.js("timing.stops[0] - timing.events.find(e => e.event === 'record').at")
        self.assertGreaterEqual(recorded_ms, 1950)
        self.assertLess(recorded_ms, 2500)
        self.assertEqual("100", self.js("document.querySelector('[role=progressbar]').getAttribute('aria-valuenow')"))
        self.assertTrue(self.js("timing.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"))

    def test_blocked_audio_waits_for_retry_before_recording(self):
        self.start(prep=0, recording=0.5, setup="""
            const cue = document.querySelector('.speaking-start-cue');
            timing.playCue = cue.play.bind(cue);
            cue.play = () => Promise.reject(new DOMException('Blocked', 'NotAllowedError'));
        """)
        self.wait.until(lambda _: self.driver.find_element("css selector", ".record-response").is_displayed())
        self.assertEqual("Play Start Cue", self.driver.find_element("css selector", ".record-response").text)
        self.assertEqual([], self.js("timing.events"))
        self.assertFalse(self.driver.find_element("css selector", ".recording-indicator").is_displayed())
        self.driver.execute_script("document.querySelector('.speaking-start-cue').play = timing.playCue")
        self.driver.find_element("css selector", ".record-response").click()
        self.wait.until(lambda _: self.js("timing.uploads.length === 1 && timing.advances === 1"))
        self.assertEqual(["cue", "cue-ended", "record"], self.js("timing.events.map(e => e.event)"))

    def test_slow_audio_waits_for_ended_and_excludes_cue_from_saved_total(self):
        self.start(prep=0.3, recording=1.2, setup="""
            const cue = document.querySelector('.speaking-start-cue');
            // A fulfilled play() means playback started, not that the cue finished.
            cue.play = () => { cue.dispatchEvent(new Event('play')); return Promise.resolve(); };
            const fetch = window.fetch;
            window.fetch = (url, options = {}) => options.method === 'POST'
                ? fetch(url, options) : new Promise(() => {});
            timing.state.timer.remaining = 100;
        """)
        self.wait.until(lambda _: self.js("timing.events.some(e => e.event === 'cue')"))
        self.driver.execute_async_script("""
            timing.startOverallTimer();
            setTimeout(arguments[arguments.length - 1], 2200);
        """)
        self.assertEqual(["cue"], self.js("timing.events.map(e => e.event)"))
        self.assertEqual("Start cue", self.js("document.querySelector('.speaking-phase-label').textContent"))
        self.assertEqual("0", self.js("document.querySelector('[role=progressbar]').getAttribute('aria-valuenow')"))
        self.assertEqual(0, self.js("timing.state.timer.elapsed"))
        self.assertEqual(100, self.js("timing.state.timer.remaining"))
        self.driver.execute_script("document.querySelector('.speaking-start-cue').dispatchEvent(new Event('ended'))")
        self.wait.until(lambda _: self.js("timing.state.timer.elapsed >= 1"))
        self.assertLess(self.js("timing.state.timer.elapsed"), 3)
        self.assertEqual(self.js("timing.state.timer.elapsed"), self.js("timing.state.timings.speaking.elapsed_seconds"))
        self.assertEqual(100 - self.js("timing.state.timer.elapsed"), self.js("timing.state.timer.remaining"))
        self.wait.until(lambda _: self.js("timing.uploads.length === 1 && timing.advances === 1"))
        self.assertEqual("Time is up", self.js("document.querySelector('.speaking-phase-label').textContent"))

    def test_leaving_during_cue_never_starts_recording(self):
        self.start(prep=0)
        self.wait.until(lambda _: self.js("timing.events.some(e => e.event === 'cue')"))
        self.assertFalse(self.driver.find_element("css selector", ".recording-indicator").is_displayed())
        self.driver.execute_async_script("""
            timing.stopPracticePlayback();
            document.querySelector('.speaking-start-cue').dispatchEvent(new Event('ended'));
            setTimeout(arguments[arguments.length - 1], 300);
        """)
        self.assertFalse(self.js("timing.events.some(e => e.event === 'record')"))
        self.assertEqual([], self.js("timing.uploads"))
        self.assertEqual(0, self.js("timing.advances"))
        self.assertIsNone(self.js("timing.state.timer.cueExcludedSince"))

    def test_begin_checks_microphone_before_showing_first_task(self):
        self.driver.execute_script("""
            timing.state.sectionIntro = true;
            timing.renderSpeakingIntro([]);
            const getUserMedia = navigator.mediaDevices.getUserMedia;
            navigator.mediaDevices.getUserMedia = () => new Promise(resolve => {
                timing.allowMic = async () => resolve(await getUserMedia({audio: true}));
            });
        """)
        self.driver.find_element("css selector", ".start-speaking-section").click()
        self.assertEqual(0, self.js("timing.starts"))
        self.assertTrue(self.js("timing.state.sectionIntro"))
        self.assertEqual("Checking microphone...", self.driver.find_element("css selector", ".speaking-setup-status").text)
        self.driver.execute_script("timing.allowMic()")
        self.wait.until(lambda _: self.js("timing.starts === 1"))
        self.assertFalse(self.js("timing.state.sectionIntro"))
        self.assertTrue(self.js("timing.streams[0].getTracks().every(t => t.readyState === 'ended')"))

    def test_microphone_failure_stays_on_start_screen_and_allows_retry(self):
        self.driver.execute_script("""
            timing.state.sectionIntro = true;
            timing.renderSpeakingIntro([]);
            timing.getUserMedia = navigator.mediaDevices.getUserMedia;
            navigator.mediaDevices.getUserMedia = async () => { throw new Error('Permission denied'); };
        """)
        self.driver.find_element("css selector", ".start-speaking-section").click()
        self.wait.until(lambda _: "Permission denied" in self.driver.find_element("css selector", ".speaking-setup-status").text)
        self.assertEqual(0, self.js("timing.starts"))
        self.assertTrue(self.js("timing.state.sectionIntro"))
        self.assertTrue(self.driver.find_element("css selector", ".start-speaking-section").is_enabled())
        self.driver.find_element("css selector", ".speaking-sound-check").click()
        self.wait.until(lambda _: self.js("!document.querySelector('.speaking-sound-preview').paused"))
        self.driver.execute_script("navigator.mediaDevices.getUserMedia = timing.getUserMedia")
        self.driver.find_element("css selector", ".start-speaking-section").click()
        self.wait.until(lambda _: self.js("timing.starts === 1"))
        self.assertTrue(self.js("document.querySelector('.speaking-sound-preview').paused"))

    def test_leaving_preparation_cancels_automatic_recording(self):
        self.start(prep=0.5)
        self.wait.until(lambda _: self.js("timing.streams.length === 1"))
        self.driver.execute_async_script("""
            timing.stopPracticePlayback();
            setTimeout(arguments[arguments.length - 1], 900);
        """)
        self.assertEqual([], self.js("timing.events"))
        self.assertEqual([], self.js("timing.uploads"))
        self.assertTrue(self.js("timing.streams[0].getTracks().every(t => t.readyState === 'ended')"))

    def test_leaving_recording_stops_audio_without_upload_or_advance(self):
        self.start(prep=0)
        self.wait.until(lambda _: self.js("timing.events.some(e => e.event === 'record')"))
        self.driver.execute_async_script("""
            timing.stopPracticePlayback();
            setTimeout(arguments[arguments.length - 1], 1000);
        """)
        self.assertTrue(self.js("document.querySelector('.speaking-start-cue').paused"))
        self.assertEqual([], self.js("timing.uploads"))
        self.assertEqual(0, self.js("timing.advances"))

    def test_choice_step_advances_without_cue_or_microphone(self):
        self.start(prep=0.3, recording=0, choice=True)
        self.wait.until(lambda _: self.js("timing.advances === 1"))
        self.assertEqual([], self.js("timing.events"))
        self.assertEqual([], self.js("timing.streams"))
        self.assertTrue(self.js("timing.state.answers.task.startsWith('choice:')"))

    def test_review_keeps_saved_duration_and_playback_without_live_progress(self):
        self.start(review=True)
        self.assertFalse(self.driver.find_element("css selector", ".speaking-phase-progress").is_displayed())
        self.assertTrue(self.driver.find_element("css selector", ".recorded-player").is_displayed())
        self.assertEqual("01:00", self.driver.find_element("css selector", ".recording-time").text)
        self.assertTrue(self.driver.find_element("css selector", ".timer").is_displayed())
        self.assertEqual([], self.js("timing.events"))


if __name__ == "__main__":
    unittest.main()
