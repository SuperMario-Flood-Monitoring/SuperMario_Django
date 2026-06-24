import json
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from swmm_engine import llm_dispatcher
from swmm_engine.engine import runtime_engine
from swmm_engine.engine.runtime_engine import RealtimeSwmmSession
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

        with patch.object(
            llm_dispatcher,
            "build_notification_payload",
            return_value={"bot_token": "token", "target": ["chat-1"]},
        ):
            payload = build_langchain_request_payload({}, {}, context)

        self.assertEqual(payload["id"], "약한비")
        self.assertEqual(json.loads(payload["swmm_raw_data"]), context)
        self.assertEqual(payload["notification"], {"bot_token": "token", "target": ["chat-1"]})

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

        self.assertEqual(append_log.call_count, 2)
        self.assertEqual(append_log.call_args_list[0].kwargs["status"], "scheduled")
        self.assertEqual(append_log.call_args_list[1].kwargs["status"], "cooldown_skipped")
        self.assertIn("remainingSeconds", append_log.call_args_list[1].kwargs["detail"])
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

    def test_response_timeout_is_not_classified_as_dispatch_failed(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        context = trigger["context"]

        with (
            patch.object(
                llm_dispatcher,
                "build_langchain_request_payload_async",
                new=AsyncMock(return_value={"id": "약한비", "swmm_raw_data": "{}", "notification": {}}),
            ),
            patch.object(llm_dispatcher, "post_langchain_analysis", side_effect=TimeoutError("timed out")),
            patch.object(llm_dispatcher, "append_llm_dispatch_result_log") as append_result_log,
            self.assertLogs("swmm_engine.llm_dispatcher", level="INFO") as logs,
        ):
            result = async_to_sync(llm_dispatcher.dispatch_llm_analysis)(
                payload,
                trigger,
                context,
                "dispatch-key",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "response_timeout")
        self.assertNotIn("LLM dispatch failed.", "\n".join(logs.output))
        append_result_log.assert_called_once()
        self.assertEqual(append_result_log.call_args.kwargs["status"], "response_timeout")


class RiskLifecycleTriggerTests(SimpleTestCase):
    def test_triggers_after_risk_is_sustained_for_delay(self):
        session = _risk_lifecycle_session()
        risk_result = _critical_risk_result()

        with (
            patch.object(runtime_engine, "RISK_LLM_SUSTAIN_SECONDS", 60),
            patch.object(runtime_engine.time, "monotonic", side_effect=[1000.0, 1059.0, 1060.0]),
        ):
            session.step_index = 1
            first = session.update_risk_issue_lifecycle(risk_result)
            session.step_index = 2
            before_delay = session.update_risk_issue_lifecycle(risk_result)
            session.step_index = 3
            after_delay = session.update_risk_issue_lifecycle(risk_result)

        self.assertFalse(first["shouldTrigger"])
        self.assertEqual(first["newIssueCount"], 1)
        self.assertFalse(before_delay["shouldTrigger"])
        self.assertTrue(after_delay["shouldTrigger"])
        self.assertEqual(after_delay["reason"], "sustained_risk")
        self.assertEqual(after_delay["sustainedIssueCount"], 1)
        self.assertEqual(after_delay["triggeredIssues"][0]["sustainedSeconds"], 60.0)

    def test_retriggers_when_risk_remains_sustained_after_next_delay(self):
        session = _risk_lifecycle_session()
        risk_result = _critical_risk_result()

        with (
            patch.object(runtime_engine, "RISK_LLM_SUSTAIN_SECONDS", 60),
            patch.object(runtime_engine.time, "monotonic", side_effect=[1000.0, 1060.0, 1119.0, 1120.0]),
        ):
            session.step_index = 1
            session.update_risk_issue_lifecycle(risk_result)
            session.step_index = 2
            first_trigger = session.update_risk_issue_lifecycle(risk_result)
            session.step_index = 3
            before_next_delay = session.update_risk_issue_lifecycle(risk_result)
            session.step_index = 4
            second_trigger = session.update_risk_issue_lifecycle(risk_result)

        self.assertTrue(first_trigger["shouldTrigger"])
        self.assertFalse(before_next_delay["shouldTrigger"])
        self.assertTrue(second_trigger["shouldTrigger"])
        self.assertEqual(second_trigger["triggeredIssues"][0]["lastTriggeredStepIndex"], 4)
        self.assertEqual(second_trigger["triggeredIssues"][0]["sustainedSeconds"], 60.0)


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


def _risk_lifecycle_session():
    session = RealtimeSwmmSession.__new__(RealtimeSwmmSession)
    session.step_index = 0
    session.active_risk_issues = {}
    session.risk_clear_counts = {}
    session.swmm_links = {
        "PIPE_1": {
            "sourceEditorName": "테스트 관로",
            "fromNode": "N1",
            "toNode": "N2",
        }
    }
    session.swmm_nodes = {
        "N1": {"sourceEditorName": "상류 노드"},
        "N2": {"sourceEditorName": "하류 노드"},
    }
    return session


def _critical_risk_result():
    return {
        "events": [
            {
                "eventId": "event-1",
                "eventType": "REVERSE_FLOW",
                "severity": "CRITICAL",
                "source": "link",
                "sourceId": "PIPE_1",
                "metrics": {"reverseTicks": 30},
            }
        ]
    }
