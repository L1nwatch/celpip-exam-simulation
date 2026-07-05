import tempfile
import unittest
from io import BytesIO
from http import HTTPStatus
from pathlib import Path
from unittest import mock

import server


class ServerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "webapp" / "celpip_practice.db"
        self.recordings_dir = self.root / "webapp" / "recordings"
        patches = [
            mock.patch.object(server, "DB_PATH", self.db_path),
            mock.patch.object(server, "RECORDINGS_DIR", self.recordings_dir),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        server.init_db()

    def test_save_and_load_draft_roundtrip(self):
        result = server.save_draft(
            {
                "test_id": "local_celpip1_test1",
                "answers": {"q1": "a"},
                "checked": {"q1": True},
                "submissions": {"reading": {"correct": 1}},
                "timings": {"reading": 123},
                "updated_at": "2026-07-04T12:00:00+00:00",
            }
        )

        self.assertEqual("local_celpip1_test1", result["test_id"])
        drafts = server.saved_drafts()
        self.assertEqual(1, len(drafts))
        self.assertEqual({"q1": "a"}, drafts[0]["answers"])
        self.assertEqual({"q1": True}, drafts[0]["checked"])
        self.assertEqual({"reading": {"correct": 1}}, drafts[0]["submissions"])
        self.assertEqual({"reading": 123}, drafts[0]["timings"])

    def test_save_submission_roundtrip(self):
        result = server.save_submission(
            {
                "test_id": "local_celpip1_test1",
                "section": "reading",
                "total_questions": 1,
                "answered_count": 1,
                "correct_count": 1,
                "estimated_level": "10-12",
                "note": "Practice estimate only.",
                "elapsed_seconds": 61,
                "responses": [
                    {
                        "question_key": "reading_q1",
                        "group_id": "part1",
                        "answer_value": "A",
                        "answer_text": "A",
                        "is_correct": True,
                        "correct_answers": ["A"],
                        "question_number": 1,
                        "source_file": "pages/reading/part1.html",
                    }
                ],
            }
        )

        self.assertGreater(result["attempt_id"], 0)
        attempts = server.recent_attempts()
        self.assertEqual(1, len(attempts))
        self.assertEqual("local_celpip1_test1", attempts[0]["test_id"])
        self.assertEqual("reading", attempts[0]["section"])
        self.assertEqual("1/1", attempts[0]["raw_score"])
        self.assertEqual(61, attempts[0]["elapsed_seconds"])

    def test_invalid_payloads_raise_value_error(self):
        with self.assertRaisesRegex(ValueError, "Missing required"):
            server.save_submission({"test_id": "local_celpip1_test1"})
        with self.assertRaisesRegex(ValueError, "test_id must"):
            server.save_draft({"test_id": "", "answers": {}, "checked": {}, "submissions": {}})

    def test_openai_api_key_uses_environment(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-" + "x" * 30}):
            self.assertEqual("sk-" + "x" * 30, server.openai_api_key())

    def test_openai_api_key_requires_valid_environment_value(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "not-a-key"}):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                server.openai_api_key()

    def test_load_dotenv_does_not_override_existing_environment(self):
        env_path = self.root / ".env"
        env_path.write_text("OPENAI_API_KEY=sk-from-file\nCELPIP_PORT=9999\n", encoding="utf-8")
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-existing"}, clear=False):
            server.load_dotenv(env_path)
            self.assertEqual("sk-existing", server.os.environ["OPENAI_API_KEY"])
            self.assertEqual("9999", server.os.environ["CELPIP_PORT"])

    def test_recording_roundtrip_uses_temp_recording_directory(self):
        result = server.save_recording(
            "local_celpip1_test1",
            "speaking_q1",
            "audio/webm",
            2.5,
            b"fake-webm",
        )

        self.assertTrue((self.recordings_dir / Path(result["url"]).name).exists())
        recordings = server.recordings_for("local_celpip1_test1", "speaking_q1")
        self.assertEqual(1, len(recordings))
        self.assertEqual("audio/webm", recordings[0]["mime_type"])
        self.assertEqual(len(b"fake-webm"), recordings[0]["size_bytes"])

    def test_handler_serves_recordings_from_configured_directory(self):
        recording = self.recordings_dir / "sample.webm"
        recording.parent.mkdir(parents=True, exist_ok=True)
        recording.write_bytes(b"recording-data")
        handler = server.Handler.__new__(server.Handler)
        handler.wfile = BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()

        path = handler.recording_file_path("/webapp/recordings/sample.webm")
        handler.send_recording_file(path)

        self.assertEqual(recording.resolve(), path)
        handler.send_response.assert_called_once_with(HTTPStatus.OK)
        handler.send_header.assert_any_call("Content-Type", "video/webm")
        handler.send_header.assert_any_call("Content-Length", str(len(b"recording-data")))
        self.assertEqual(b"recording-data", handler.wfile.getvalue())

    def test_handler_rejects_recording_path_traversal(self):
        handler = server.Handler.__new__(server.Handler)
        self.assertIsNone(handler.recording_file_path("/webapp/recordings/../celpip_practice.db"))

    def test_writing_questions_read_from_materials_directory(self):
        materials_dir = self.root / "materials" / "private" / "packs"
        pack = materials_dir / "local_celpip1_test1"
        pack.mkdir(parents=True)
        (pack / "questions.json").write_text(
            """
            {
              "questions": [
                {"key": "writing_q1", "section": "writing"},
                {"key": "reading_q1", "section": "reading"}
              ]
            }
            """,
            encoding="utf-8",
        )
        with mock.patch.object(server, "MATERIALS_DIR", materials_dir):
            questions = server.writing_questions("local_celpip1_test1")

        self.assertEqual(["writing_q1"], list(questions))

    def test_handler_blocks_legacy_output_static_routes(self):
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/output/local_celpip1_test1/questions.json"
        handler.send_error = mock.Mock()

        handler.do_GET()

        handler.send_error.assert_called_once_with(
            HTTPStatus.NOT_FOUND,
            "Legacy output snapshots are not served",
        )


if __name__ == "__main__":
    unittest.main()
