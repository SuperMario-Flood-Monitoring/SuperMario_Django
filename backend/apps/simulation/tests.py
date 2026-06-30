import json
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from apps.simulation.realtime_alerts import build_realtime_alert
from swmm_engine import llm_dispatcher
from swmm_engine.converter.editor_layout_to_swmm_inp import convert_layout, render_inp
from swmm_engine.engine import runtime_engine
from swmm_engine.engine.runtime_engine import RealtimeSwmmSession
from swmm_engine.llm_dispatcher import (
    build_langchain_request_payload,
    extract_situation_id,
    normalize_langchain_situation_id,
)


class EditorLayoutConversionTests(SimpleTestCase):
    def test_fully_blocked_conduit_does_not_emit_invalid_control_rule(self):
        layout = {
            "version": 1,
            "groundSurfaceY": 0,
            "nodes": [
                {
                    "id": "pipe-1",
                    "swmmId": "pipe_1",
                    "name": "막힘 관",
                    "type": "pipeSegment",
                    "x": 0,
                    "y": 100,
                    "width": 200,
                    "height": 40,
                    "props": {
                        "blockage": 100,
                        "pipeKind": "storm",
                        "size": "medium",
                    },
                },
            ],
            "links": [],
        }

        result = convert_layout(layout)
        inp_text = render_inp(result, title="test")

        self.assertEqual(result.links[0].initial_setting, 0.0)
        self.assertIn("[CONDUITS]", inp_text)
        self.assertNotIn("[CONTROLS]", inp_text)
        self.assertNotIn("THEN CONDUIT", inp_text)


class LangChainDispatchPayloadTests(SimpleTestCase):
    def setUp(self):
        llm_dispatcher.reset_dispatch_policy_state()

    def test_normalizes_react_rainfall_preset_values(self):
        cases = [
            (0, "맑음"),
            (5, "맑음"),
            (10, "우천"),
            (50, "우천"),
            (100, "호우"),
            (101, "폭우"),
            (300, "폭우"),
            ("0", "맑음"),
            ("5", "맑음"),
            ("10", "우천"),
            ("50", "우천"),
            ("100", "호우"),
            ("101", "폭우"),
            ("300", "폭우"),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                snapshot = {"control": {"id": raw_value}}

                self.assertEqual(extract_situation_id(snapshot, {}, {}), expected)

    def test_normalizes_react_rainfall_preset_labels(self):
        cases = [
            ("맑음", "맑음"),
            ("비옴", "우천"),
            ("약한비", "우천"),
            ("우천", "우천"),
            ("호우", "호우"),
            ("폭우", "폭우"),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                self.assertEqual(normalize_langchain_situation_id(raw_value), expected)

    def test_uses_runtime_rainfall_ratio_when_explicit_id_is_missing(self):
        cases = [
            (0.05, "맑음"),
            (0.1, "우천"),
            (1.0, "호우"),
            (3.0, "폭우"),
            (10, "우천"),
            (50, "우천"),
            (100, "호우"),
            (300, "폭우"),
        ]

        for rainfall_ratio, expected in cases:
            with self.subTest(rainfall_ratio=rainfall_ratio):
                context = {"simulation": {"control": {"rainfallRatio": rainfall_ratio}}}

                self.assertEqual(extract_situation_id({}, {}, context), expected)

    def test_builds_langchain_request_payload_shape(self):
        context = {
            "simulation": {"control": {"rainfallPercent": 100.0}},
            "riskEvents": [{"severity": "CRITICAL"}],
        }

        with patch.object(
            llm_dispatcher,
            "build_notification_payload",
            return_value={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": ["chat-1"]},
        ):
            payload = build_langchain_request_payload({}, {}, context)

        self.assertEqual(payload["id"], "호우")
        self.assertEqual(json.loads(payload["swmm_raw_data"]), context)
        self.assertEqual(payload["TELEGRAM_BOT_TOKEN"], "token")
        self.assertEqual(payload["TELEGRAM_CHAT_ID"], ["chat-1"])

    def test_queues_dispatch_during_aggregation_window(self):
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
        self.assertEqual(append_log.call_args_list[0].kwargs["status"], "aggregation_queued")
        self.assertEqual(append_log.call_args_list[1].kwargs["status"], "aggregation_duplicate_skipped")
        self.assertEqual(create_task.call_count, 1)

    def test_flushes_aggregation_batch_and_starts_cooldown(self):
        first_payload = _llm_trigger_payload(step_index=1)
        second_payload = _llm_trigger_payload(step_index=2, source_id="PIPE_2")

        with (
            patch.object(llm_dispatcher.time, "monotonic", side_effect=[1000.0, 1001.0, 1002.0, 1003.0]),
            patch.object(llm_dispatcher, "append_llm_dispatch_log") as append_log,
            patch.object(llm_dispatcher, "dispatch_llm_analysis", return_value=object()),
            patch.object(llm_dispatcher.asyncio, "create_task", side_effect=_close_coroutine) as create_task,
        ):
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(first_payload))
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(second_payload))
            self.assertTrue(llm_dispatcher.dispatch_candidate_batch("aggregation_window"))

        self.assertEqual(append_log.call_args_list[-1].kwargs["status"], "scheduled")
        self.assertEqual(create_task.call_count, 4)
        self.assertGreater(llm_dispatcher.llm_dispatch_cooldown_remaining_seconds(1003.0), 0)

    def test_queues_pending_during_cooldown_and_flushes_after_window(self):
        first_payload = _llm_trigger_payload(step_index=1)
        second_payload = _llm_trigger_payload(step_index=2, source_id="PIPE_2")

        with (
            patch.object(llm_dispatcher.time, "monotonic", side_effect=[1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0]),
            patch.object(llm_dispatcher, "append_llm_dispatch_log") as append_log,
            patch.object(llm_dispatcher, "dispatch_llm_analysis", return_value=object()),
            patch.object(llm_dispatcher.asyncio, "create_task", side_effect=_close_coroutine),
        ):
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(first_payload))
            self.assertTrue(llm_dispatcher.dispatch_candidate_batch("aggregation_window"))
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(second_payload))
            self.assertTrue(llm_dispatcher.dispatch_pending_queue())

        self.assertEqual(
            [call.kwargs["status"] for call in append_log.call_args_list],
            ["aggregation_queued", "scheduled", "pending_queued", "scheduled"],
        )

    def test_queues_runtime_blockage_in_emergency_window_without_cooldown(self):
        payload = _llm_trigger_payload(step_index=1, event_type="BLOCKAGE_CLOSED")

        with (
            patch.object(llm_dispatcher.time, "monotonic", return_value=1000.0),
            patch.object(llm_dispatcher, "append_llm_dispatch_log") as append_log,
            patch.object(llm_dispatcher, "dispatch_llm_analysis", return_value=object()),
            patch.object(llm_dispatcher.asyncio, "create_task", side_effect=_close_coroutine),
        ):
            self.assertTrue(llm_dispatcher.schedule_llm_analysis_dispatch(payload))
            self.assertTrue(llm_dispatcher.dispatch_candidate_batch("emergency_aggregation", emergency=True))

        self.assertEqual(
            [call.kwargs["status"] for call in append_log.call_args_list],
            ["emergency_queued", "scheduled"],
        )
        self.assertEqual(llm_dispatcher.llm_dispatch_cooldown_remaining_seconds(1000.0), 0.0)

    def test_response_timeout_is_not_classified_as_dispatch_failed(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        context = trigger["context"]

        with (
            patch.object(
                llm_dispatcher,
                "build_langchain_request_payload_async",
                new=AsyncMock(return_value={"id": "우천", "swmm_raw_data": "{}", "TELEGRAM_BOT_TOKEN": None, "TELEGRAM_CHAT_ID": []}),
            ),
            patch.object(llm_dispatcher, "broadcast_llm_request_alert", new=AsyncMock()),
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

    def test_broadcasts_realtime_alert_right_before_llm_post(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        context = trigger["context"]
        order: list[str] = []

        async def fake_broadcast_llm_request_alert(*args):
            order.append("broadcast")

        async def fake_post_langchain_analysis(_request_payload):
            order.append("post")
            return {"statusCode": 200, "responseBody": "{}"}

        with (
            patch.object(
                llm_dispatcher,
                "build_langchain_request_payload_async",
                new=AsyncMock(return_value={"id": "우천", "swmm_raw_data": "{}", "TELEGRAM_BOT_TOKEN": None, "TELEGRAM_CHAT_ID": []}),
            ),
            patch.object(llm_dispatcher, "broadcast_llm_request_alert", new=fake_broadcast_llm_request_alert),
            patch.object(llm_dispatcher, "post_langchain_analysis", new=fake_post_langchain_analysis),
            patch.object(llm_dispatcher, "append_llm_dispatch_result_log"),
        ):
            result = async_to_sync(llm_dispatcher.dispatch_llm_analysis)(
                payload,
                trigger,
                context,
                "dispatch-key",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(order, ["broadcast", "post"])


class SimulationRealtimeAlertTests(SimpleTestCase):
    def test_builds_realtime_alert_payload_for_triggered_llm_event(self):
        trigger = _llm_trigger_payload(step_index=1, event_type="PREDICTED_FULL_PIPE", source_id="PIPE_1")["llmTrigger"]

        alert = build_realtime_alert(trigger, source="llm_request")

        self.assertIsNotNone(alert)
        alert = alert or {}
        self.assertEqual(alert["kind"], "persistent_abnormal")
        self.assertEqual(alert["severity"], "CRITICAL")
        self.assertEqual(alert["title"], "지속적인 이상 현상 감지")
        self.assertEqual(alert["reason"], "new_issue")
        self.assertIn("LLM 분석 요청을 전송", alert["message"])
        self.assertIn("PIPE_1 만관 예측", alert["message"])
        self.assertEqual(alert["key"], "llm_request|new_issue|PREDICTED_FULL_PIPE:link:PIPE_1")
        self.assertEqual(alert["triggeredIssues"][0]["sourceId"], "PIPE_1")

    def test_ignores_non_triggered_llm_event(self):
        alert = build_realtime_alert({"shouldTrigger": False}, source="llm_request")

        self.assertIsNone(alert)


class RiskLifecycleTriggerTests(SimpleTestCase):
    def test_triggers_after_risk_is_sustained_for_delay(self):
        session = _risk_lifecycle_session()
        risk_result = _critical_risk_result(event_type="LINK_SURCHARGE")

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
        risk_result = _critical_risk_result(event_type="LINK_SURCHARGE")

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

    def test_runtime_reverse_flow_triggers_without_sustain_delay(self):
        session = _risk_lifecycle_session()
        risk_result = _critical_risk_result(event_type="REVERSE_FLOW")

        with patch.object(runtime_engine.time, "monotonic", return_value=1000.0):
            session.step_index = 1
            result = session.update_risk_issue_lifecycle(risk_result)

        self.assertTrue(result["shouldTrigger"])
        self.assertEqual(result["reason"], "sustained_risk_with_new_or_escalated_issue")
        self.assertEqual(result["triggeredIssues"][0]["eventType"], "REVERSE_FLOW")


def _llm_trigger_payload(step_index: int, event_type: str = "PREDICTED_FULL_PIPE", source_id: str = "PIPE_1") -> dict:
    issue_id = f"{event_type}:link:{source_id}"
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
                "riskEvents": [
                    {
                        "eventId": issue_id,
                        "eventType": event_type,
                        "severity": "CRITICAL",
                        "source": "link",
                        "sourceId": source_id,
                    }
                ],
            },
            "triggeredIssues": [
                {
                    "issueId": issue_id,
                    "eventType": event_type,
                    "severity": "CRITICAL",
                    "source": "link",
                    "sourceId": source_id,
                    "lastTriggeredStepIndex": step_index,
                }
            ],
        },
    }


def _close_coroutine(coroutine):
    if hasattr(coroutine, "close"):
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


def _critical_risk_result(event_type: str = "REVERSE_FLOW"):
    return {
        "events": [
            {
                "eventId": "event-1",
                "eventType": event_type,
                "severity": "CRITICAL",
                "source": "link",
                "sourceId": "PIPE_1",
                "metrics": {"reverseTicks": 30},
            }
        ]
    }
