import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services.agent_orchestrator import AgentOrchestrator
from backend.app.services.agent_service import chat, status


class AgentChatApiTestCase(unittest.TestCase):
    def test_orchestrator_prefers_llm_classification_when_available(self):
        class FakeLLMClient:
            api_key = "test-key"
            enable_llm = True
            local_env_enabled = False
            loaded_env_files = []
            base_url = "https://example.com"
            model = "fake-model"
            last_error = ""
            last_generation_used_llm = False

            @property
            def is_enabled(self):
                return True

            def classify_intent(self, *, query: str, local_intent: str):
                return "replay"

            def generate_answer(self, **kwargs):
                return kwargs["default_answer"]

        orchestrator = AgentOrchestrator(llm_client=FakeLLMClient())
        intent, source = orchestrator._resolve_intent("当前跑的是方案一还是方案二，为什么")
        self.assertEqual(intent, "replay")
        self.assertEqual(source, "llm_classify")

    def test_summary_intent_contains_three_core_blocks(self):
        data = chat(query="overview")
        self.assertEqual(data["intent"], "summary")
        self.assertIn(data.get("agent_mode"), {"local_fallback", "local_fallback_answer", "hybrid_llm"})
        self.assertIn("runtime", data["data"])
        self.assertIn("alerts", data["data"])
        self.assertIn("replay", data["data"])

    def test_runtime_intent(self):
        data = chat(query="runtime status")
        self.assertEqual(data["intent"], "runtime")
        self.assertIn("runtime", data["data"])
        self.assertIn("engine", data["data"]["runtime"])
        self.assertIn("tracker", data["data"]["runtime"])

    def test_alerts_intent(self):
        data = chat(query="latest alerts", limit=5)
        self.assertEqual(data["intent"], "alerts")
        self.assertIn("alerts", data["data"])
        self.assertIn("total", data["data"]["alerts"])
        self.assertLessEqual(len(data["data"]["alerts"]["items"]), 5)

    def test_replay_intent(self):
        data = chat(query="replay for latest event")
        self.assertEqual(data["intent"], "replay")
        self.assertIn("replay", data["data"])
        self.assertIn("available", data["data"]["replay"])
        self.assertIn("video_analysis", data["data"])
        self.assertIn("analysis_available", data["data"]["video_analysis"])

    def test_chinese_intent_keywords(self):
        runtime = chat(query="\u8fd0\u884c\u72b6\u6001")
        alerts = chat(query="\u6700\u8fd1\u544a\u8b66")
        replay = chat(query="\u56de\u653e\u5b9a\u4f4d")
        self.assertEqual(runtime["intent"], "runtime")
        self.assertEqual(alerts["intent"], "alerts")
        self.assertEqual(replay["intent"], "replay")

    def test_agent_status_shape(self):
        s = status()
        self.assertIn("enable_flag", s)
        self.assertIn("llm_enabled", s)
        self.assertIn("has_api_key", s)
        self.assertIn("key_source", s)
        self.assertIn("key_tail", s)
        self.assertIn("local_env_enabled", s)
        self.assertIn("local_env_files", s)
        self.assertIn("base_url", s)
        self.assertIn("model", s)
        self.assertIn("last_error", s)


if __name__ == "__main__":
    unittest.main()
