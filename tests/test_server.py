import tempfile
import unittest
import json
from io import BytesIO
from http import HTTPStatus
from pathlib import Path
from unittest import mock

import server


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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
                "notes": {"q1": "I missed the contrast word."},
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
        self.assertEqual({"q1": "I missed the contrast word."}, drafts[0]["notes"])

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
        self.assertEqual(
            [{"question_key": "reading_q1", "answer_value": "A", "is_correct": True}],
            attempts[0]["responses"],
        )

    def test_saved_draft_recovers_completed_listening_review_from_history(self):
        submission = server.save_submission(
            {
                "test_id": "local_celpip1_test1",
                "section": "listening",
                "total_questions": 1,
                "answered_count": 1,
                "correct_count": 1,
                "estimated_level": "10-12",
                "note": "Practice estimate.",
                "responses": [
                    {
                        "question_key": "listening_q1",
                        "answer_value": "A",
                        "answer_text": "A",
                        "is_correct": True,
                    }
                ],
            }
        )
        server.save_draft(
            {
                "test_id": "local_celpip1_test1",
                "answers": {"listening_q1": "B"},
                "checked": {},
                "submissions": {},
            }
        )

        draft = server.saved_drafts()[0]
        self.assertEqual("A", draft["answers"]["listening_q1"])
        self.assertTrue(draft["checked"]["listening_q1"])
        self.assertEqual(1, draft["submissions"]["listening"]["correct"])
        self.assertEqual(submission["attempt_id"], draft["submissions"]["listening"]["db_attempt_id"])

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

    def test_clean_writing_prompt_removes_trailing_timing_text(self):
        prompt = (
            "Read the following information. Write an email in about 150-200 words. "
            "Explain the problem and suggest two improvements. 26 Minutes"
        )

        self.assertEqual(
            "Read the following information. Write an email in about 150-200 words. "
            "Explain the problem and suggest two improvements.",
            server.clean_writing_prompt(prompt),
        )

    def test_clean_writing_prompt_keeps_task_content(self):
        prompt = "Choose one option and explain your reasons. Time limit: 25 minutes"

        self.assertEqual(
            "Choose one option and explain your reasons.",
            server.clean_writing_prompt(prompt),
        )

    def test_clean_speaking_prompt_removes_timer_text(self):
        prompt = "Describe a memorable trip. Preparation: 30 seconds Recording: 60 seconds"

        self.assertEqual("Describe a memorable trip.", server.clean_speaking_prompt(prompt))

    def test_request_speaking_assessment_transcribes_and_scores_recordings(self):
        materials_dir = self.root / "materials" / "private" / "packs"
        pack = materials_dir / "local_celpip1_test1"
        pack.mkdir(parents=True)
        (pack / "questions.json").write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "key": "speaking_q1",
                            "section": "speaking",
                            "number": 1,
                            "question_text": "Describe a memorable trip. Preparation: 30 seconds Recording: 60 seconds",
                            "timing": {"preparation_seconds": 30, "recording_seconds": 60},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        recording = server.save_recording("local_celpip1_test1", "speaking_q1", "audio/webm", 3.5, b"fake-webm")
        submission = server.save_submission(
            {
                "test_id": "local_celpip1_test1",
                "section": "speaking",
                "total_questions": 1,
                "answered_count": 1,
                "responses": [
                    {
                        "question_key": "speaking_q1",
                        "answer_value": f"recording:{recording['recording_id']}",
                        "answer_text": f"recording:{recording['recording_id']}",
                        "question_number": 1,
                    }
                ],
            }
        )
        assessment_payload = {
            "overall_level": 8,
            "summary": "Clear response with enough detail.",
            "task_assessments": [
                {
                    "question_key": "speaking_q1",
                    "task_number": 1,
                    "estimated_level": 8,
                    "transcript": "I went to Vancouver and enjoyed the mountains.",
                    "criteria": [
                        {"name": "Content/Coherence", "level": 8, "feedback": "Organized and relevant."},
                        {"name": "Vocabulary", "level": 8, "feedback": "Appropriate range."},
                        {"name": "Listenability", "level": 8, "feedback": "Easy to follow."},
                        {"name": "Task Fulfillment", "level": 8, "feedback": "Addresses the task."},
                    ],
                    "strengths": ["Relevant details"],
                    "improvements": ["Add more development"],
                }
            ],
        }
        api_payload = {
            "id": "resp_speaking",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(assessment_payload)}]}],
        }

        with (
            mock.patch.object(server, "MATERIALS_DIR", materials_dir),
            mock.patch.object(server, "transcribe_recording", return_value="I went to Vancouver and enjoyed the mountains."),
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-" + "x" * 30}),
            mock.patch.object(server, "urlopen", return_value=FakeResponse(api_payload)),
        ):
            assessment = server.request_speaking_assessment(submission["attempt_id"])

        self.assertEqual(8, assessment["overall_level"])
        self.assertEqual("gpt-4o-mini-transcribe", assessment["transcription_model"])
        attempts = server.recent_attempts()
        self.assertEqual("8", attempts[0]["estimated_level"])
        self.assertEqual(server.SPEAKING_NOTE, attempts[0]["note"])

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
