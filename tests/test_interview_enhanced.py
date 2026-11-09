import asyncio
import io
import unittest
import wave
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import interview_enhanced as interview
from app.server import app


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.answer = {"question": "Tell me about a challenge", "kind": "behavioral",
                       "answer": "Um, I solved it, you know, by testing.", "duration": 60}

    def test_metrics_and_validation(self):
        response = self.client.post("/api/interview/metrics", json=self.answer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overall_wpm"], 8)
        self.assertEqual(response.json()["filler_candidates"], 2)
        self.assertEqual(self.client.post("/api/interview/metrics", json={**self.answer, "duration": -1}).status_code, 422)
        self.assertIsNone(interview.metrics("A typed answer", None)["overall_wpm"])

    def test_existing_interview_and_release_version(self):
        response = self.client.get("/api/interview/questions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["questions"]), 5)
        self.assertEqual(self.client.get("/api/version").json()["version"], "0.5.0")

    def test_feedback_checks_quotes(self):
        class Writer:
            async def chat(self, messages, max_tokens):
                return '{"strength":"Specific", "improvement":"Explain the result", "next_step":"Add an outcome", "evidence":"invented quote"}'
        result = asyncio.run(interview.coach(Writer(), "Question?", "behavioral", "My actual answer."))
        self.assertEqual(result["evidence"], "")
        self.assertEqual(result["improvement"], "Explain the result")

    def test_coaching_endpoint_uses_writer_and_does_not_persist(self):
        class Writer:
            model_used = "test-model"
            async def chat(self, messages, max_tokens):
                return '{"strength":"Good example", "next_step":"Add a result", "evidence":"I solved it"}'
        with patch("app.server._free_writer", return_value=Writer()):
            response = self.client.post("/api/interview/coach", json=self.answer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["feedback"]["evidence"], "I solved it")

    def test_transcription_boundaries_and_in_memory_upload(self):
        self.assertEqual(self.client.post("/api/interview/transcribe", files={"file": ("a.webm", b"")}).status_code, 400)
        self.assertEqual(self.client.post("/api/interview/transcribe", files={"file": ("a.webm", b"x" * (15 * 1024 * 1024 + 1))}).status_code, 413)
        with patch("app.server.interview_enhanced.transcribe", return_value={"text": "hello", "words": []}) as transcribe:
            response = self.client.post("/api/interview/transcribe", files={"file": ("a.webm", io.BytesIO(b"audio"))})
        self.assertEqual(response.json()["text"], "hello")
        transcribe.assert_called_once_with(b"audio")

    def test_whisper_audio_decoder_and_vad(self):
        # Verify the real decoder and packaged VAD model without downloading the speech model.
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps
        sample = io.BytesIO()
        with wave.open(sample, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 16000)
        sample.seek(0)
        audio = decode_audio(sample)
        self.assertEqual(len(audio), 16000)
        self.assertEqual(get_speech_timestamps(audio), [])

    def test_vision_runtime_serves_only_permitted_assets(self):
        response = self.client.get("/api/interview/vision/vision_bundle.mjs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(self.client.get("/api/interview/vision/package.json").status_code, 404)
        self.assertEqual(self.client.get("/api/interview/vision/wasm/vision_wasm_internal.wasm").headers["content-type"], "application/wasm")
        self.assertEqual(self.client.get("/static/models/pose_landmarker_lite.task").status_code, 200)


if __name__ == "__main__":
    unittest.main()
