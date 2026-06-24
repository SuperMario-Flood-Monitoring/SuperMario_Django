import json
from unittest.mock import patch

from django.test import SimpleTestCase

from swmm_engine import llm_dispatcher
from swmm_engine.llm_dispatcher import (
    build_langchain_request_payload,
    extract_situation_id,
    normalize_langchain_situation_id,
)


class LangChainDispatchPayloadTests(SimpleTestCase):
    def setUp(self):
        llm_dispatcher._scheduled_dispatch_keys.clear()
        llm_dispatcher._scheduled_dispatch_key_order.clear()
        llm_dispatcher._last_llm_dispatch_scheduled_at = None

    def test_normalizes_react_rainfall_preset_values(self):
        cases = [
            (0, "맑음"),
            (100, "약한비"),
            (300, "폭우"),
            ("0", "맑음"),
            ("100", "약한비"),
            ("300", "폭우"),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                snapshot = {"control": {"id": raw_value}}

                self.assertEqual(extract_situation_id(snapshot, {}, {}), expected)

    def test_normalizes_react_rainfall_preset_labels(self):
        cases = [
            ("맑음", "맑음"),
            ("비옴", "약한비"),
            ("약한비", "약한비"),
            ("폭우", "폭우"),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                self.assertEqual(normalize_langchain_situation_id(raw_value), expected)

    def test_uses_runtime_rainfall_ratio_when_explicit_id_is_missing(self):
        context = {"simulation": {"control": {"rainfallRatio": 3.0}}}

        self.assertEqual(extract_situation_id({}, {}, context), "폭우")

    def test_builds_langchain_request_payload_shape(self):
        context = {
            "simulation": {"control": {"rainfallPercent": 100.0}},
            "riskEvents": [{"severity": "CRITICAL"}],
        }

        payload = build_langchain_request_payload({}, {}, context)

        self.assertEqual(payload["id"], "약한비")
        self.assertEqual(json.loads(payload["swmm_raw_data"]), context)

    def test_skips_dispatch_during_cooldown(self):
        first_payload = _llm_trigger_payload(step_index=1)
        second_payload = _llm_trigger_payload(step_index=2)

        with (
            patch.object(llm_dispatcher.time, "monotonic", side_effect=[1000.0, 1001.0]),
            patch.object(llm_dispatcher, "append_llm_dispatch_log") as append_log,
            patch.object(llm_dispatcher, "dispatch_llm_analysis", return_value=object()),
            patch.object(llm_dispatcher.asyncio, "create_task", side_effect=_close_coroutine) as create_task,
        ):
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(first_payload))
            self.assertFalse(llm_dispatcher.schedule_llm_analysis_dispatch(second_payload))

        self.assertEqual(append_log.call_count, 1)
        self.assertEqual(create_task.call_count, 1)

    def test_allows_dispatch_after_cooldown(self):
        first_payload = _llm_trigger_payload(step_index=1)
        second_payload = _llm_trigger_payload(step_index=2)

        with (
            patch.object(llm_dispatcher.time, "monotonic", side_effect=[1000.0, 1301.0]),
            patch.object(llm_dispatcher, "append_llm_dispatch_log") as append_log,
            patch.object(llm_dispatcher, "dispatch_llm_analysis", return_value=object()),
            patch.object(llm_dispatcher.asyncio, "create_task", side_effect=_close_coroutine) as create_task,
        ):
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(first_payload))
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(second_payload))

        self.assertEqual(append_log.call_count, 2)
        self.assertEqual(create_task.call_count, 2)


def _llm_trigger_payload(step_index: int) -> dict:
    return {
        "runId": "test-run",
        "stepIndex": step_index,
        "control": {"id": 100},
        "llmTrigger": {
            "shouldTrigger": True,
            "reason": "new_issue",
            "contextLevel": "optimal",
            "context": {
                "highestSeverity": "CRITICAL",
                "riskEvents": [{"severity": "CRITICAL"}],
            },
            "triggeredIssues": [
                {
                    "issueId": f"issue-{step_index}",
                    "severity": "CRITICAL",
                    "lastTriggeredStepIndex": step_index,
                }
            ],
        },
    }


def _close_coroutine(coroutine):
    coroutine.close()
    return None
