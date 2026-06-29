from pathlib import Path
import sys
import unittest
from unittest.mock import ANY, patch

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.app.services import agent_tools as agent_tools_mod


class AgentToolsReplayAnalysisTestCase(unittest.TestCase):
    def test_analyze_replay_video_falls_back_to_original_video_when_clip_is_missing(self):
        alert = {
            "timestamp": "2026-05-07T10:00:00",
            "camera_id": "cam_fence",
            "rule_id": "fence_intrusion",
            "message": "人员翻越围栏",
        }

        with patch.object(
            agent_tools_mod.replay_service,
            "generate_clip_for_event",
            return_value=(
                None,
                {
                    "video_found": True,
                    "video_path": "D:/video/original.mp4",
                    "display_time": "2026-05-07 10:00:00",
                },
            ),
        ), patch.object(
            agent_tools_mod.engine,
            "get_alert_history",
            return_value=[alert],
        ), patch.object(
            agent_tools_mod.mimo_video_client,
            "api_key",
            "test-key",
        ), patch.object(
            agent_tools_mod.mimo_video_client,
            "analyze_security_event_clip",
            return_value={
                "analysis_available": True,
                "summary": "检测到人员靠近围栏",
                "model": "mimo-v2.5",
            },
        ) as analyze_mock, patch.object(
            agent_tools_mod.storage_service,
            "upsert_video_analysis",
            return_value={"analysis_available": True},
        ) as upsert_mock:
            result = agent_tools_mod.analyze_replay_video({"camera_id": "cam_fence"})

        self.assertTrue(result["available"])
        self.assertTrue(result["analysis_available"])
        self.assertEqual(result["analysis_target_kind"], "source_video")
        self.assertEqual(result["analysis_target_path"], "D:/video/original.mp4")
        self.assertIn("original replay video", result["message"])
        analyze_mock.assert_called_once_with(
            video_path_or_url="D:/video/original.mp4",
            camera_id="cam_fence",
            scene_id="",
            rule_id="fence_intrusion",
            alert_message="人员翻越围栏",
            rule_context=ANY,
        )
        upsert_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
