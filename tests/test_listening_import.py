import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_material_catalog import build_catalog
from import_celpass_listening import COUNTS, convert


def source_fixture():
    sections, answers, media = [], [], []
    number = 0
    for index, count in enumerate(COUNTS):
        main = f"/api/public/sample-audio/fixture-part-{index}.m4a"
        media.append({"path": main, "duration": 60, "error": 0})
        questions = []
        for _ in range(count):
            number += 1
            audio = f"/api/public/sample-audio/fixture-question-{number}.m4a" if index < 5 else None
            if audio:
                media.append({"path": audio, "duration": 3, "error": 0})
            options = [{"value": letter, "text": f"Choice {letter} for {number}"} for letter in "ABCD"]
            questions.append({"number": number, "questionAudio": audio, "options": options})
            answers.append(f'<li>What did speaker {number} decide? <strong>B. Choice B for {number}</strong></li>')
        sections.append({"mainAudio": main, "questions": questions, "transcript": "Fake dialogue for automated tests."})
    return {"url": "https://celpass.com/celpip/listening/practice-tests/26", "sections": sections,
            "answerHtml": "".join(answers), "media_metadata": media}


class ListeningImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"%PDF-fake-fixture")
        self.destination = self.root / "packs" / "local_celpass_listening26"

    def test_import_preserves_published_answers_audio_and_source(self):
        data = convert(source_fixture(), self.pdf, self.destination)
        self.assertEqual(list(COUNTS), [len(g["question_keys"]) for g in data["question_groups"]["listening"]])
        self.assertEqual(38, len(data["questions"]))
        self.assertEqual(19, sum(bool(q["media"]) for q in data["questions"]))
        self.assertEqual({"listening": 38, "reading": 0, "writing": 0, "speaking": 0}, data["sections"])
        for q in data["questions"]:
            self.assertEqual(1, sum(o["is_correct"] for o in q["options"]))
            self.assertEqual(q["correct_option_ids"], [q["options"][1]["id"]])
        self.assertEqual(self.pdf.read_bytes(), (self.destination / "source/celpass-listening-26.pdf").read_bytes())
        self.assertIn("CELPass", (self.destination / "pages/listening/part-1.html").read_text())
        catalog = build_catalog(self.destination.parent)
        self.assertEqual(data["sections"], catalog["tests"][0]["sections"])
        self.assertNotIn("questions", catalog["tests"][0])
        with self.assertRaisesRegex(ValueError, "already exists"):
            convert(source_fixture(), self.pdf, self.destination)

    def test_incomplete_recordings_and_inconsistent_answer_keys_are_rejected(self):
        broken = source_fixture()
        broken["media_metadata"][0]["error"] = 4
        with self.assertRaisesRegex(ValueError, "playback"):
            convert(broken, self.pdf, self.destination)
        broken = source_fixture()
        broken["answerHtml"] = broken["answerHtml"].replace("B. Choice B for 1", "B. Wrong answer", 1)
        with self.assertRaisesRegex(ValueError, "does not match"):
            convert(broken, self.pdf, self.destination)
        self.assertFalse(self.destination.exists())

    def test_catalog_counts_actual_questions_and_ignores_invalid_pack_names(self):
        convert(source_fixture(), self.pdf, self.destination)
        metadata = self.destination / "material.json"
        value = json.loads(metadata.read_text())
        value["sections"] = {"reading": 99}
        metadata.write_text(json.dumps(value))
        (self.destination.parent / "invalid-folder").mkdir()
        catalog = build_catalog(self.destination.parent)
        self.assertEqual(1, len(catalog["tests"]))
        self.assertEqual(38, catalog["tests"][0]["sections"]["listening"])
        self.assertEqual(0, catalog["tests"][0]["sections"]["reading"])
