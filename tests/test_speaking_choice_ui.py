import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


ROOT = Path(__file__).resolve().parents[1]
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
if Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists():
    CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

IMAGE_SOURCE = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
IMAGE = f'src="{IMAGE_SOURCE}"'
OPTIONS = [f'<td><img src="{IMAGE_SOURCE}#{index}"><span style="font-weight: bold">{label}</span><ul><li>{detail}</li></ul></td>'
           for index, (label, detail) in enumerate([("Art camp", "Paint a mural"), ("Sports camp", "Play tennis")])]
CHOICE_HTML = '<div><p><span>Instructions</span><br><strong>This task is made up of THREE parts:</strong></p><ul><li>Choose an option</li><li>Preparation time</li><li>Speaking</li></ul></div><table class="kouyutable"><tr>' + "".join(OPTIONS) + '</tr></table>'
NEW_OPTION = f'<p>Friend\'s Choice</p><table class="kouyutable"><tr><td><img {IMAGE}></td><td><strong>Science camp</strong><ul><li>Build a robot</li></ul></td></tr></table>'
PERSUASION_HTML = NEW_OPTION + '<p>Your Choice</p><table class="kouyutable"><tr>' + '<td>OR</td>'.join(OPTIONS) + '</tr></table>'


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b'<!doctype html><div id="cards"></div>')

    def log_message(self, *args):
        pass


@unittest.skipUnless(CHROME, "Chrome or Chromium is required for speaking UI tests")
class SpeakingChoiceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        cls.driver = webdriver.Chrome(options=options)
        cls.source = (ROOT / "webapp/app.js").read_text().split("init().catch")[0]

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.driver.get(f"http://127.0.0.1:{self.server.server_port}/")
        self.driver.execute_script(self.source + """
            renderStats = () => {};
            scheduleDraftSync = () => {};
            window.speakingTest = {state, renderQuestionCard, speakingQuestionHtml,
                bindSpeakingChoiceSelector, loadSpeakingPrompt, readLocalDraft};
        """)
        questions = [
            {"key": "task4", "number": 4, "question_text": "Make a prediction."},
            {"key": "choose", "number": 5, "question_text": "You do not need to speak for this part.", "question_html": CHOICE_HTML},
            {"key": "persuade", "number": 5, "question_text": "Persuade your friend.", "question_html": PERSUASION_HTML},
            {"key": "task6", "number": 6, "question_text": "Solve a problem.", "question_html": PERSUASION_HTML},
        ]
        for question in questions:
            question.update(section="speaking", question_type="speaking_task", source_file=f"pages/{question['key']}.html")
        self.driver.execute_script("""
            localStorage.clear();
            speakingTest.state.section = 'speaking';
            speakingTest.state.data = {questions: arguments[0]};
        """, questions)

    def render_card(self, index):
        self.driver.execute_script("""
            const q = speakingTest.state.data.questions[arguments[0]];
            document.getElementById('cards').innerHTML = speakingTest.renderQuestionCard(q);
        """, index)
        return self.driver.find_element("id", "cards").text

    def select(self, index=None):
        self.render_card(1)
        result = self.driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const index = arguments[0];
            speakingTest.bindSpeakingChoiceSelector(document.querySelector('.question-card'),
                speakingTest.state.data.questions[1], null).then(ui => {
                if (index === null) ui.ensureSelection();
                else document.querySelectorAll('.speaking-choice-button')[index].click();
                done(speakingTest.state.answers.choose);
            }).catch(error => done({error: error.message}));
        """, index)
        self.assertIsInstance(result, str)
        return result

    def assert_selected_prompt(self, selected):
        text = self.render_card(2)
        self.assertIn(["Art camp", "Sports camp"][selected], text)
        self.assertIn(["Paint a mural", "Play tennis"][selected], text)
        self.assertNotIn(["Art camp", "Sports camp"][1 - selected], text)
        self.assertNotIn(["Paint a mural", "Play tennis"][1 - selected], text)
        self.assertIn("Science camp", text)
        self.assertIn("Build a robot", text)
        self.assertIn("Your selected option", text)
        self.assertEqual(2, len(self.driver.find_elements("css selector", "#cards img")))
        self.assertFalse(self.driver.find_elements("xpath", "//td[normalize-space()='OR']"))

    def test_each_choice_removes_only_the_unchosen_original(self):
        for index in (0, 1):
            with self.subTest(index=index):
                self.select(index)
                self.assert_selected_prompt(index)
                # Reviewing Part 1 still presents both original options.
                text = self.render_card(1)
                self.assertIn("Art camp", text)
                self.assertIn("Sports camp", text)

    def test_automatic_choice_and_restored_draft(self):
        answer = self.select()
        index = int(answer.split(":")[1])
        self.driver.execute_script("""
            speakingTest.state.answers = {};
            speakingTest.state.answers = speakingTest.readLocalDraft(speakingTest.state.testId).answers;
            speakingTest.state.submissions.speaking = {submitted_at: '2026-10-12'};
        """)
        self.assert_selected_prompt(index)

    def test_missing_or_invalid_choice_keeps_prompt_and_adjacent_task(self):
        for answer in (None, "preparation-complete", "choice:9:Invalid"):
            with self.subTest(answer=answer):
                self.driver.execute_script("speakingTest.state.answers.choose = arguments[0]", answer)
                text = self.render_card(2)
                self.assertIn("Art camp", text)
                self.assertIn("Sports camp", text)
                self.assertIn("Science camp", text)
        self.select(0)
        text = self.render_card(3)
        self.assertIn("Art camp", text)
        self.assertIn("Sports camp", text)
        self.assertNotIn("Your selected option", text)

    def test_layout_without_or_separator(self):
        self.driver.execute_script("speakingTest.state.data.questions[2].question_html = arguments[0]",
                                   PERSUASION_HTML.replace("<td>OR</td>", ""))
        self.select(1)
        self.assert_selected_prompt(1)

    def test_choice_step_ignores_or_separator(self):
        html = '<table class="kouyutable"><tr>' + '<td>OR</td>'.join(OPTIONS) + '</tr></table>'
        self.driver.execute_script("speakingTest.state.data.questions[1].question_html = arguments[0]", html)
        answer = self.select(1)
        self.assertIn("Sports%20camp", answer)
        self.assert_selected_prompt(1)

    def test_fixed_original_option_is_replaced_without_removing_rival(self):
        rival = f'<td><img {IMAGE}><strong>Science camp</strong><ul><li>Build a robot</li></ul></td>'
        original = OPTIONS[0].replace('<td>', '<td><div>Your Choice</div>', 1)
        html = '<table class="kouyutable"><tr>' + rival + original + '</tr></table>'
        self.driver.execute_script("speakingTest.state.data.questions[2].question_html = arguments[0]", html)
        for index in (0, 1):
            with self.subTest(index=index):
                self.select(index)
                self.assert_selected_prompt(index)
                self.assertIn("Your selected option", self.driver.find_element("id", "cards").text)

    def test_legacy_prompt_loads_from_source_and_omits_samples(self):
        self.select(1)
        html = '<div itemprop="articleBody">' + PERSUASION_HTML + '<div class="mod-custom">Sample response</div></div>'
        result = self.driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const html = arguments[0];
            const q = speakingTest.state.data.questions[2];
            delete q.question_html;
            window.fetch = async () => ({ok: true, text: async () => html});
            speakingTest.loadSpeakingPrompt(q).then(() => done(q.question_html));
        """, html)
        self.assertNotIn("Sample response", result)
        self.assert_selected_prompt(1)

    def test_generic_instructions_can_expand_without_hiding_options(self):
        self.render_card(1)
        details = self.driver.find_element('css selector', '.speaking-comparison-instructions')
        self.assertFalse(details.get_attribute('open'))
        self.assertIn('Art camp', self.driver.find_element('id', 'cards').text)
        details.find_element('tag name', 'summary').click()
        self.assertIn('This task is made up of THREE parts', details.text)
        self.assertIn('Preparation time', details.text)

    def test_failed_source_load_preserves_text_prompt(self):
        self.driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const q = speakingTest.state.data.questions[2];
            delete q.question_html;
            window.fetch = async () => { throw new Error('Offline'); };
            speakingTest.loadSpeakingPrompt(q).then(done);
        """)
        self.assertIn("Persuade your friend.", self.render_card(2))

    def test_all_other_speaking_tasks_recover_structure_without_samples_or_source_styles(self):
        html = f'''<main itemprop="articleBody"><div style="min-height: 900px; width: 1200px">
            <p><span style="font-size: 28px; font-weight: bold; color: red">A fictional workplace needs your advice.</span>
            <br><span style="font-size: 10px">Preparation: <strong>30 seconds</strong> Recording: <strong>60 seconds</strong></span></p>
            <p>Choose ONE:<br>EITHER<br>Explain the first option.<br>OR<br>Explain the second option.</p>
            <p>&nbsp;</p><img src="../images/scene.jpg" width="1800" height="1200" style="margin-top: 100px">
            <p>*NOTE: This practice test is not recording your response.</p></div>
            <div data-rlta-element="container"><p>CELPIP 10–12 Response</p><p>HIDDEN SAMPLE</p></div>
            <div class="mod-custom"><textarea>OLD EDITOR</textarea></div></main>'''
        for number in (None, 1, 2, 3, 4, 6, 7, 8):
            with self.subTest(task=number):
                result = self.driver.execute_async_script("""
                    const [html, number, done] = arguments;
                    const q = speakingTest.state.data.questions[0];
                    delete q.question_html; q.number = number;
                    q.media = [{type: 'image', path: 'images/scene.jpg'}];
                    window.fetch = async () => ({ok: true, text: async () => html});
                    speakingTest.loadSpeakingPrompt(q).then(() => {
                        document.getElementById('cards').innerHTML = speakingTest.renderQuestionCard(q);
                        const prompt = document.querySelector('.speaking-prompt');
                        done({text: prompt.innerText, paragraphs: prompt.querySelectorAll('p').length,
                            breaks: prompt.querySelectorAll('br').length,
                            emphasis: prompt.querySelector('strong').textContent,
                            styles: prompt.querySelectorAll('[style], [width], [height]').length,
                            sources: [...prompt.querySelectorAll('img')].map(img => img.getAttribute('src')),
                            timing: prompt.querySelector('.speaking-task-timing')?.textContent});
                    }).catch(e => done({error: e.message}));
                """, html, number)
                self.assertNotIn('error', result)
                self.assertIn('A fictional workplace needs your advice.', result['text'])
                self.assertIn('Explain the second option.', result['text'])
                self.assertNotIn('HIDDEN SAMPLE', result['text'])
                self.assertNotIn('OLD EDITOR', result['text'])
                self.assertNotIn('not recording your response', result['text'])
                self.assertEqual(2, result['paragraphs'])
                self.assertEqual(4, result['breaks'])
                self.assertEqual('A fictional workplace needs your advice.', result['emphasis'])
                self.assertEqual(0, result['styles'])
                self.assertEqual(['../materials/private/packs/local_celpip1_test1/images/scene.jpg'], result['sources'])
                self.assertIn('Preparation:', result['timing'])

    def test_missing_inline_scene_appears_after_instructions_once(self):
        self.driver.execute_script("""
            const q = speakingTest.state.data.questions[0];
            q.question_html = '<p>Describe the picture below.</p>';
            q.media = [{type: 'image', path: 'images/scene.jpg'}];
        """)
        self.render_card(0)
        self.assertEqual(1, len(self.driver.find_elements('css selector', '.speaking-prompt img')))
        self.assertEqual(['P', 'DIV'], self.driver.execute_script(
            "return [...document.querySelector('.speaking-prompt').children].map(node => node.tagName)"))

    def test_selected_task5_does_not_reintroduce_rejected_images_from_media(self):
        self.driver.execute_script("""
            speakingTest.state.data.questions[2].media = [0, 1, 2].map(index => ({
                type: 'image', path: arguments[0] + (index < 2 ? '#' + index : '')}));
        """, IMAGE_SOURCE)
        self.select(1)
        self.assert_selected_prompt(1)
        self.assertFalse(self.driver.find_elements('css selector', f'img[src="{IMAGE_SOURCE}#0"]'))

    def test_offline_plain_text_keeps_choice_and_timing_on_separate_lines(self):
        self.driver.execute_script("""
            const q = speakingTest.state.data.questions[0];
            q.question_text = 'A friend needs advice. Choose ONE: EITHER Talk about the first option. OR Talk about the second option. Preparation: 30 seconds Recording: 60 seconds';
        """)
        text = self.render_card(0)
        self.assertIn('Choose ONE:', text)
        self.assertEqual(6, len(self.driver.find_elements('css selector', '.speaking-prompt p')))


if __name__ == "__main__":
    unittest.main()
