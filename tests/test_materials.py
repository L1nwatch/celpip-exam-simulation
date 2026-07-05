import json
import subprocess
import sys
import tempfile
import unittest
from importlib import util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONVERTER_SPEC = util.spec_from_file_location(
    "convert_output_materials",
    ROOT / "scripts" / "convert_output_materials.py",
)
convert_output_materials = util.module_from_spec(CONVERTER_SPEC)
CONVERTER_SPEC.loader.exec_module(convert_output_materials)
OPTIMIZER_SPEC = util.spec_from_file_location(
    "optimize_material_media",
    ROOT / "scripts" / "optimize_material_media.py",
)
optimize_material_media = util.module_from_spec(OPTIMIZER_SPEC)
OPTIMIZER_SPEC.loader.exec_module(optimize_material_media)


class MaterialFixtureTests(unittest.TestCase):
    def test_demo_material_pack_has_expected_shape(self):
        pack = ROOT / "materials" / "demo" / "local_celpip1_test1"
        material = json.loads((pack / "material.json").read_text(encoding="utf-8"))
        questions = json.loads((pack / "questions.json").read_text(encoding="utf-8"))

        self.assertEqual("local_celpip1_test1", material["id"])
        self.assertIn("questions", questions)
        self.assertIn("sections", questions)
        self.assertGreater(len(questions["questions"]), 0)
        for section in ("listening", "reading", "writing", "speaking"):
            self.assertIn(section, questions["sections"])

    def test_demo_material_pack_has_public_safe_media_assets(self):
        pack = ROOT / "materials" / "demo" / "local_celpip1_test1"
        questions = json.loads((pack / "questions.json").read_text(encoding="utf-8"))
        media_paths = []
        for groups in questions["question_groups"].values():
            for group in groups:
                media_paths.extend(item["path"] for item in group.get("media", []))
        for question in questions["questions"]:
            media_paths.extend(item["path"] for item in question.get("media", []))

        self.assertIn("assets/video/demo-listening-notice.mp4", media_paths)
        self.assertIn("assets/images/demo-community-board.svg", media_paths)
        self.assertIn("assets/images/demo-course-choice.svg", media_paths)
        for path in set(media_paths):
            with self.subTest(path=path):
                self.assertTrue((pack / path).exists())

    def test_pages_preview_build_has_demo_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview_root = Path(tmp) / "pages-preview"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_pages_preview.py"), "--output", str(preview_root)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            questions_path = preview_root / "output" / "local_celpip1_test1" / "questions.json"
            self.assertTrue(questions_path.exists())
            data = json.loads(questions_path.read_text(encoding="utf-8"))
            sections = {question["section"] for question in data["questions"]}
            self.assertTrue({"listening", "reading", "writing", "speaking"}.issubset(sections))
            self.assertTrue((preview_root / "output" / "local_celpip1_test1" / "assets" / "video" / "demo-listening-notice.mp4").exists())
            self.assertTrue((preview_root / "output" / "local_celpip1_test1" / "assets" / "images" / "demo-community-board.svg").exists())
            self.assertTrue((preview_root / "output" / "local_celpip1_test1" / "assets" / "images" / "demo-course-choice.svg").exists())

    def test_converter_builds_compact_private_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "output"
            source_pack = source_root / "local_celpip99_test1"
            destination = root / "materials" / "private" / "packs"
            (source_pack / "pages" / "listening").mkdir(parents=True)
            (source_pack / "iframes").mkdir()
            (source_pack / "assets" / "audio").mkdir(parents=True)
            (source_pack / "assets" / "images").mkdir(parents=True)

            (source_pack / "index.html").write_text("<html>legacy shell</html>", encoding="utf-8")
            (source_pack / "iframes" / "index.html").write_text("<audio src='../assets/audio/ignored.mp3'>", encoding="utf-8")
            (source_pack / "assets" / "audio" / "part1.mp3").write_bytes(b"fake-mp3")
            (source_pack / "assets" / "images" / "logo.png").write_bytes(b"fake-logo")
            (source_pack / "assets" / "images" / "gptsmall.png").write_bytes(b"fake-gpt")
            (source_pack / "assets" / "images" / "gif-30-60.gif").write_bytes(b"fake-timer")
            (source_pack / "pages" / "listening" / "part1.html").write_text(
                """
                <html><body>
                  <header><img src="../../assets/images/logo.png"></header>
                  <main itemprop="articleBody">
                    <p>Useful passage text.</p>
                    <audio src="../../assets/audio/part1.mp3"></audio>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )
            questions = {
                "generated_from": "test fixture",
                "sections": {"listening": 1},
                "questions": [
                    {
                        "key": "q1",
                        "section": "listening",
                        "source_file": "pages/listening/part1.html",
                        "question_html": '<p>Prompt</p><img src="../../assets/images/gptsmall.png"><img src="../../assets/images/gif-30-60.gif">',
                        "media": [{"type": "audio", "path": "assets/audio/part1.mp3"}],
                        "timer_media": [{"type": "image", "path": "assets/images/logo.png"}],
                        "source_iframe": "iframes/index.html",
                        "answer_source_iframe": "iframes/results.html",
                        "answer_extraction_status": "legacy",
                        "is_result_source": False,
                        "explanation": "legacy extraction details",
                        "correct_answers": ["assets/audio/part1.mp3"],
                    }
                ],
                "questions_by_key": {
                    "q1": {
                        "key": "q1",
                        "section": "listening",
                        "source_file": "pages/listening/part1.html",
                        "media": [{"type": "audio", "path": "assets/audio/part1.mp3"}],
                    }
                },
                "question_groups": {
                    "listening": [
                        {
                            "source_file": "pages/listening/part1.html",
                            "media": [{"type": "audio", "path": "assets/audio/part1.mp3"}],
                            "question_keys": ["q1"],
                        }
                    ]
                },
            }
            (source_pack / "questions.json").write_text(json.dumps(questions), encoding="utf-8")

            manifests = convert_output_materials.convert_all(source_root, destination, clean=True)

            converted = destination / "local_celpip99_test1"
            self.assertEqual(1, len(manifests))
            self.assertTrue((converted / "questions.json").exists())
            self.assertTrue((converted / "material.json").exists())
            self.assertTrue((converted / "pages" / "listening" / "part1.html").exists())
            self.assertTrue((converted / "audio" / "part1.mp3").exists())
            self.assertFalse((converted / "assets").exists())
            self.assertFalse((converted / "images" / "logo.png").exists())
            self.assertFalse((converted / "images" / "gif-30-60.gif").exists())
            self.assertFalse((converted / "index.html").exists())
            self.assertFalse((converted / "iframes").exists())
            converted_questions = json.loads((converted / "questions.json").read_text(encoding="utf-8"))
            self.assertNotIn("questions_by_key", converted_questions)
            self.assertNotIn("source_root", converted_questions)
            self.assertNotIn("notes", converted_questions)
            self.assertNotIn("timer_media", converted_questions["questions"][0])
            self.assertNotIn("source_iframe", converted_questions["questions"][0])
            self.assertNotIn("answer_source_iframe", converted_questions["questions"][0])
            self.assertNotIn("answer_extraction_status", converted_questions["questions"][0])
            self.assertNotIn("is_result_source", converted_questions["questions"][0])
            self.assertNotIn("explanation", converted_questions["questions"][0])
            self.assertNotIn("gif-30-60.gif", converted_questions["questions"][0]["question_html"])
            self.assertEqual("audio/part1.mp3", converted_questions["questions"][0]["media"][0]["path"])
            self.assertEqual(["audio/part1.mp3"], converted_questions["questions"][0]["correct_answers"])
            cleaned_page = (converted / "pages" / "listening" / "part1.html").read_text(encoding="utf-8")
            self.assertIn("Useful passage text.", cleaned_page)
            self.assertNotIn("logo.png", cleaned_page)
            catalog = json.loads((destination.parent / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(["local_celpip99_test1"], [pack["id"] for pack in catalog["packs"]])

    def test_audio_optimizer_rewrites_pack_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "materials" / "private" / "packs" / "local_celpip99_test1"
            (pack / "audio").mkdir(parents=True)
            (pack / "audio" / "part1.mp3").write_bytes(b"fake-mp3")
            (pack / "questions.json").write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "key": "q1",
                                "media": [{"type": "audio", "path": "audio/part1.mp3"}],
                                "correct_answers": ["audio/part1.mp3"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (pack / "material.json").write_text(
                json.dumps({"files": {"audio": ["audio/part1.mp3"]}}),
                encoding="utf-8",
            )

            optimize_material_media.rewrite_pack_json(pack, {"audio/part1.mp3": "audio/part1.m4a"})

            questions = json.loads((pack / "questions.json").read_text(encoding="utf-8"))
            material = json.loads((pack / "material.json").read_text(encoding="utf-8"))
            self.assertEqual("audio/part1.m4a", questions["questions"][0]["media"][0]["path"])
            self.assertEqual(["audio/part1.m4a"], questions["questions"][0]["correct_answers"])
            self.assertEqual(["audio/part1.m4a"], material["files"]["audio"])

    def test_media_optimizer_finds_video_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs = Path(tmp) / "materials" / "private" / "packs"
            video_dir = packs / "local_celpip99_test1" / "video"
            video_dir.mkdir(parents=True)
            (video_dir / "part.mp4").write_bytes(b"fake-mp4")
            (video_dir / "ignore.txt").write_text("not media", encoding="utf-8")

            jobs = optimize_material_media.video_jobs(packs)

            self.assertEqual([video_dir / "part.mp4"], [job.source for job in jobs])


if __name__ == "__main__":
    unittest.main()
