import json
from types import SimpleNamespace
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
        junction_depths = [node.max_depth for node in result.nodes.values() if node.section == "JUNCTIONS"]
        junction_ponded_areas = [node.ponded_area for node in result.nodes.values() if node.section == "JUNCTIONS"]

        self.assertEqual(result.links[0].initial_setting, 0.0)
        self.assertTrue(junction_depths)
        self.assertTrue(all(depth < 1000.0 for depth in junction_depths))
        self.assertTrue(all(area > 0.0 for area in junction_ponded_areas))
        self.assertIn("[CONDUITS]", inp_text)
        self.assertNotIn("[CONTROLS]", inp_text)
        self.assertNotIn("THEN CONDUIT", inp_text)

    def test_connector_allows_swmm_ponding_depth(self):
        layout = {
            "version": 1,
            "groundSurfaceY": 0,
            "nodes": [
                {
                    "id": "connector-1",
                    "swmmId": "J_CONN_1",
                    "name": "접합부",
                    "type": "connector",
                    "x": 0,
                    "y": 100,
                    "width": 40,
                    "height": 40,
                },
            ],
            "links": [],
        }

        result = convert_layout(layout)
        node = result.nodes["J_CONN_1"]

        self.assertLess(node.max_depth, 1000.0)
        self.assertEqual(node.ponded_area, 8.0)

    def test_blocked_link_includes_upstream_node_depth_in_editor_state(self):
        session = RealtimeSwmmSession.__new__(RealtimeSwmmSession)
        session.swmm_links = {
            "PIPE_1": {
                "kind": "CONDUIT",
                "fromNode": "UPSTREAM_NODE",
                "toNode": "DOWNSTREAM_NODE",
            },
        }
        session.swmm_nodes = {}
        session.blockages_by_id = {"PIPE_1": 1.0}
        session.mapping = {
            "editorNodes": {
                "pipe-editor": {
                    "swmmNodes": [],
                    "swmmLinks": ["PIPE_1"],
                },
            },
            "editorLinks": {
                "link-editor": {
                    "swmmLinks": ["PIPE_1"],
                },
            },
        }
        session.node_connected_links = {}

        editor_states = session.aggregate_editor_states(
            {
                "UPSTREAM_NODE": {
                    "depthRatio": 1.25,
                    "floodingCms": 0.03,
                    "totalInflowCms": 0.5,
                },
                "DOWNSTREAM_NODE": {
                    "depthRatio": 0.05,
                    "floodingCms": 0.0,
                    "totalInflowCms": 0.0,
                },
            },
            {
                "PIPE_1": {
                    "fullness": 0.2,
                    "capacityRatio": 0.0,
                    "blockageRatio": 1.0,
                    "flowCms": 0.0,
                    "velocityMps": 0.0,
                },
            },
        )

        self.assertEqual(editor_states["pipe-editor"]["maxDepthRatio"], 1.25)
        self.assertEqual(editor_states["pipe-editor"]["maxFullness"], 0.0)
        self.assertEqual(editor_states["pipe-editor"]["maxFloodingCms"], 0.03)
        self.assertEqual(editor_states["link-editor"]["maxDepthRatio"], 1.25)
        self.assertEqual(editor_states["link-editor"]["maxFullness"], 0.0)
        self.assertEqual(editor_states["link-editor"]["totalInflowCms"], 0.5)

    def test_full_link_blockage_closes_upstream_node_outflows_only(self):
        session = RealtimeSwmmSession.__new__(RealtimeSwmmSession)
        session.swmm_links = {
            "PIPE_1": {
                "kind": "CONDUIT",
                "fromNode": "NODE_A",
                "toNode": "NODE_B",
            },
            "PIPE_BRANCH": {
                "kind": "CONDUIT",
                "fromNode": "NODE_A",
                "toNode": "NODE_C",
            },
            "PIPE_INCOMING": {
                "kind": "CONDUIT",
                "fromNode": "NODE_UP",
                "toNode": "NODE_A",
            },
        }
        session.swmm_nodes = {
            "NODE_A": {},
            "NODE_B": {},
            "NODE_C": {},
            "NODE_UP": {},
        }
        session.control_link_ids = {"PIPE_1"}
        session.blockages_by_id = {"PIPE_1": 1.0}

        self.assertEqual(session.fully_blocked_outflow_nodes(), {"NODE_A"})
        self.assertEqual(session.blockage_for_link("PIPE_1"), 1.0)
        self.assertEqual(session.blockage_for_link("PIPE_BRANCH"), 1.0)
        self.assertEqual(session.blockage_for_link("PIPE_INCOMING"), 0.0)
        self.assertEqual(session.blockage_control_link_ids(), {"PIPE_1", "PIPE_BRANCH"})

        session.blockages_by_id = {"PIPE_1": 0.99}

        self.assertEqual(session.fully_blocked_outflow_nodes(), set())
        self.assertEqual(session.blockage_for_link("PIPE_1"), 0.99)
        self.assertEqual(session.blockage_for_link("PIPE_BRANCH"), 0.0)
        self.assertEqual(session.blockage_control_link_ids(), {"PIPE_1"})


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
            (500, "폭우"),
            ("0", "맑음"),
            ("5", "맑음"),
            ("10", "우천"),
            ("50", "우천"),
            ("100", "호우"),
            ("101", "폭우"),
            ("300", "폭우"),
            ("500", "폭우"),
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
            (500, "폭우"),
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

    def test_merged_dispatch_uses_latest_control_context(self):
        first_payload = _llm_trigger_payload(step_index=1)
        second_payload = _llm_trigger_payload(step_index=2, source_id="PIPE_2")
        first_payload["control"] = {"rainfallRatio": 0.0, "rainfallPercent": 0.0}
        first_payload["llmTrigger"]["context"]["simulation"] = {
            "control": {"rainfallRatio": 0.0, "rainfallPercent": 0.0},
        }
        second_payload["control"] = {"rainfallRatio": 5.0, "rainfallPercent": 500.0}
        second_payload["llmTrigger"]["context"]["simulation"] = {
            "control": {"rainfallRatio": 5.0, "rainfallPercent": 500.0},
        }

        payload, trigger, context, _dispatch_key = llm_dispatcher.merge_dispatch_candidates(
            [
                llm_dispatcher.DispatchCandidate(
                    payload=first_payload,
                    trigger=first_payload["llmTrigger"],
                    context=first_payload["llmTrigger"]["context"],
                    sanitized_context=first_payload["llmTrigger"]["context"],
                    dispatch_key="first",
                    signatures={"first"},
                ),
                llm_dispatcher.DispatchCandidate(
                    payload=second_payload,
                    trigger=second_payload["llmTrigger"],
                    context=second_payload["llmTrigger"]["context"],
                    sanitized_context=second_payload["llmTrigger"]["context"],
                    dispatch_key="second",
                    signatures={"second"},
                ),
            ],
            "aggregation_window",
        )

        self.assertEqual(payload["stepIndex"], 2)
        self.assertEqual(trigger["reason"], "aggregation_window")
        self.assertEqual(len(trigger["triggeredIssues"]), 2)
        self.assertEqual(context["simulation"]["control"]["rainfallPercent"], 500.0)
        self.assertEqual(extract_situation_id(payload, trigger, context), "폭우")

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
            patch.object(llm_dispatcher, "current_engine_dispatch_skip_detail", return_value=None),
            patch.object(llm_dispatcher, "pause_engine_for_llm_dispatch", new=AsyncMock(return_value=None)),
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

    def test_skips_llm_post_when_engine_is_paused_or_stopped(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        context = trigger["context"]
        cases = [
            (
                "engine_paused",
                {
                    "running": False,
                    "paused": True,
                    "hasSession": True,
                    "runId": "test-run",
                    "stepIndex": 1,
                },
            ),
            (
                "engine_stopped",
                {
                    "running": False,
                    "paused": False,
                    "hasSession": False,
                    "runId": None,
                    "stepIndex": 0,
                },
            ),
        ]

        for expected_reason, status in cases:
            with self.subTest(expected_reason=expected_reason):
                build_payload = AsyncMock(return_value={"id": "우천", "swmm_raw_data": "{}", "TELEGRAM_BOT_TOKEN": None, "TELEGRAM_CHAT_ID": []})
                broadcast_alert = AsyncMock()
                post_analysis = AsyncMock(return_value={"statusCode": 200, "responseBody": "{}"})
                with (
                    patch.object(llm_dispatcher, "current_engine_status_payload", return_value=status),
                    patch.object(llm_dispatcher, "build_langchain_request_payload_async", new=build_payload),
                    patch.object(llm_dispatcher, "broadcast_llm_request_alert", new=broadcast_alert),
                    patch.object(llm_dispatcher, "post_langchain_analysis", new=post_analysis),
                    patch.object(llm_dispatcher, "append_llm_dispatch_result_log") as append_result_log,
                ):
                    result = async_to_sync(llm_dispatcher.dispatch_llm_analysis)(
                        payload,
                        trigger,
                        context,
                        "dispatch-key",
                    )

                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "engine_state_skipped")
                self.assertEqual(result["reason"], expected_reason)
                build_payload.assert_not_called()
                broadcast_alert.assert_not_called()
                post_analysis.assert_not_called()
                append_result_log.assert_called_once()
                self.assertEqual(append_result_log.call_args.kwargs["status"], "engine_state_skipped")
                self.assertEqual(append_result_log.call_args.kwargs["detail"]["reason"], expected_reason)

    def test_pause_engine_for_llm_dispatch_broadcasts_paused_status(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        captured_payload: dict[str, object] = {}

        class FakeEngine:
            async def pause(self):
                return {
                    "ok": True,
                    "running": False,
                    "paused": True,
                    "hasSession": True,
                    "runId": "test-run",
                    "stepIndex": 1,
                    "modelTime": "2026-06-30T00:00:01",
                }

        fake_state = SimpleNamespace(
            engine=FakeEngine(),
            status_payload=lambda: {
                "ok": True,
                "running": False,
                "paused": True,
                "hasSession": True,
                "runId": "test-run",
                "stepIndex": 1,
                "modelTime": "2026-06-30T00:00:01",
                "websocketClients": 2,
            },
        )

        async def fake_broadcast_engine_pause_status(pause_payload):
            captured_payload.update(pause_payload)

        with (
            patch.object(llm_dispatcher, "current_engine_dispatch_skip_detail", return_value=None),
            patch.object(llm_dispatcher, "simulation_state_for_dispatch", return_value=fake_state),
            patch.object(llm_dispatcher, "broadcast_engine_pause_status", new=fake_broadcast_engine_pause_status),
            patch.object(llm_dispatcher, "append_llm_dispatch_result_log") as append_result_log,
        ):
            result = async_to_sync(llm_dispatcher.pause_engine_for_llm_dispatch)(
                payload,
                trigger,
                "dispatch-key",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "engine_auto_paused")
        self.assertFalse(captured_payload["running"])
        self.assertTrue(captured_payload["paused"])
        self.assertEqual(captured_payload["type"], "paused")
        self.assertEqual(captured_payload["pauseReason"], "llm_dispatch")
        self.assertEqual(captured_payload["llmDispatchKey"], "dispatch-key")
        self.assertEqual(captured_payload["websocketClients"], 2)
        append_result_log.assert_called_once()
        self.assertEqual(append_result_log.call_args.kwargs["status"], "engine_auto_paused")

    def test_pauses_engine_and_broadcasts_realtime_alert_right_before_llm_post(self):
        payload = _llm_trigger_payload(step_index=1)
        trigger = payload["llmTrigger"]
        context = trigger["context"]
        order: list[str] = []

        async def fake_pause_engine_for_llm_dispatch(*args):
            order.append("pause")
            return {"ok": True, "status": "engine_auto_paused"}

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
            patch.object(llm_dispatcher, "current_engine_dispatch_skip_detail", return_value=None),
            patch.object(llm_dispatcher, "pause_engine_for_llm_dispatch", new=fake_pause_engine_for_llm_dispatch),
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
        self.assertEqual(order, ["pause", "broadcast", "post"])


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
