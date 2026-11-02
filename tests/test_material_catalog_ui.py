import functools
import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from test_reading_dropdown_ui import CHROME, QuietHandler

ROOT = Path(__file__).resolve().parents[1]
PACK_ID = "local_celpass_listening26"


class CatalogHandler(QuietHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            body = b'{"drafts":[],"reviews":[],"attempts":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


@unittest.skipUnless(CHROME, "Chrome is required for material catalog UI tests")
class MaterialCatalogUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        shutil.copytree(ROOT / "webapp", root / "webapp", ignore=shutil.ignore_patterns("*.db", "recordings", "logs"))
        cls.pack = root / "materials/private/packs" / PACK_ID
        shutil.copytree(ROOT / "materials/demo/local_celpip1_test1", cls.pack)
        data = json.loads((cls.pack / "questions.json").read_text())
        data["questions"] = [q for q in data["questions"] if q["section"] == "listening"]
        data["sections"] = {"listening": 2, "reading": 0, "writing": 0, "speaking": 0}
        data["source"] = {"label": "Fixture Publisher", "url": "https://example.com/listening", "note": "Audio discussion."}
        (cls.pack / "questions.json").write_text(json.dumps(data))
        (cls.pack.parent / "catalog.json").write_text(json.dumps({"tests": [{"id": PACK_ID, "label": "Additional Listening Fixture", "sections": data["sections"]}]}))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(CatalogHandler, directory=str(root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        options = Options()
        options.binary_location = CHROME
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_window_size(1280, 900)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/webapp/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def test_overview_offers_only_the_included_listening_section(self):
        self.driver.get(self.url + "?view=overview")
        row = WebDriverWait(self.driver, 8).until(lambda d: d.find_element("css selector", f'button[data-test="{PACK_ID}"]')).find_element("xpath", "ancestor::tr")
        self.assertEqual(1, len(row.find_elements("css selector", "button[data-section]")))
        self.assertEqual(3, len(row.find_elements("css selector", ".unavailable-section")))
        self.assertFalse(row.find_elements("css selector", "[data-mock-test]"))
        self.assertIn("105 not started", self.driver.find_element("id", "overviewSummary").text)

    def test_new_deep_link_loads_catalog_before_resolving_the_test(self):
        self.driver.get(self.url + "?test=celpass-listening26&section=listening&part=1")
        WebDriverWait(self.driver, 8).until(lambda d: "Additional Listening Fixture" in d.title)
        self.assertIn("Fixture Publisher", self.driver.find_element("id", "materialAttribution").text)
        tabs = self.driver.find_elements("css selector", ".section-tab")
        self.assertTrue(tabs[0].is_enabled())
        self.assertTrue(all(not tab.is_enabled() for tab in tabs[1:]))
        self.assertEqual("https://example.com/original.m4a", self.driver.execute_script("return assetUrl('https://example.com/original.m4a')"))

    def test_missing_section_link_returns_to_listening(self):
        self.driver.get(self.url + "?test=celpass-listening26&section=reading&part=2")
        WebDriverWait(self.driver, 8).until(lambda d: "section=listening" in d.current_url)
        self.assertTrue(self.driver.find_element("id", "partPassageMedia").is_displayed())
