import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.auth.models import User
from apps.auth.tokens import issue_token

from .models import HazardAction, HazardCaseEmbedding, HazardEvent
from .services import forecast_state
from .services.maintenance_dispatcher import build_maintenance_log_payload, dispatch_maintenance_log
from .services.hazard_service import create_hazard_events_from_swmm_tick


class HazardEventServiceTests(TestCase):
    def test_creates_critical_hazard_event_from_current_swmm_risk_event_shape(self):
        tick = _critical_tick()

        created = create_hazard_events_from_swmm_tick(tick)

        self.assertEqual(len(created), 1)
        event = HazardEvent.objects.get()
        self.assertEqual(event.run_id, "run-1")
        self.assertEqual(event.hazard_type, "REVERSE_FLOW")
        self.assertEqual(event.hazard_level, "CRITICAL")
        self.assertEqual(event.target_id, "PIPE_1")
        self.assertEqual(event.source, "link")
        self.assertEqual(event.metrics_snapshot["flowCms"], -0.25)

    def test_prevents_duplicate_events_by_event_key(self):
        tick = _critical_tick()

        create_hazard_events_from_swmm_tick(tick)
        create_hazard_events_from_swmm_tick(tick)

        self.assertEqual(HazardEvent.objects.count(), 1)

    def test_ignores_non_critical_events(self):
        tick = _critical_tick()
        tick["risk"]["events"][0]["severity"] = "WARNING"

        created = create_hazard_events_from_swmm_tick(tick)

        self.assertEqual(created, [])
        self.assertEqual(HazardEvent.objects.count(), 0)


class HazardApiTests(TestCase):
    def setUp(self):
        forecast_state.reset()
        self.user = User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("password"),
        )
        self.auth_header = f"Bearer {issue_token(self.user, 'access')}"

    def test_lists_open_hazard_rows_without_metrics_snapshot(self):
        HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            metrics_snapshot={"flowCms": -0.25},
        )

        response = self.client.get("/api/hazards?status=OPEN", HTTP_AUTHORIZATION=self.auth_header)

        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_id"], "PIPE_1")
        self.assertNotIn("metrics_snapshot", rows[0])

    def test_detail_includes_metrics_snapshot(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            metrics_snapshot={"flowCms": -0.25},
        )

        response = self.client.get(f"/api/hazards/{event.id}", HTTP_AUTHORIZATION=self.auth_header)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metrics_snapshot"], {"flowCms": -0.25})

    def test_action_start_marks_event_in_progress_without_embedding_dispatch(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            metrics_snapshot={"flowCms": -0.25},
        )

        with patch(
            "apps.monitoring.services.maintenance_dispatcher.post_maintenance_log",
            return_value={"ok": True, "vector_id": "vector-1"},
        ) as post_maintenance_log:
            response = self.client.post(
                f"/api/hazards/{event.id}/actions",
                data=json.dumps(
                    {
                        "action_detail": "하류 관로 현장 점검 진행",
                        "action_type": "FIELD_CHECK",
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=self.auth_header,
            )

        self.assertEqual(response.status_code, 201)
        event.refresh_from_db()
        action = HazardAction.objects.get()
        self.assertEqual(event.status, HazardEvent.Status.IN_PROGRESS)
        self.assertFalse(event.is_deleted)
        self.assertEqual(action.action_detail, "하류 관로 현장 점검 진행")
        self.assertEqual(action.fastapi_sync_status, HazardAction.FastApiSyncStatus.PENDING)
        post_maintenance_log.assert_not_called()
        self.assertEqual(HazardCaseEmbedding.objects.count(), 0)

    def test_action_completion_resolves_event_and_dispatches_embedding_payload(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            status=HazardEvent.Status.IN_PROGRESS,
            metrics_snapshot={"flowCms": -0.25},
        )
        action = HazardAction.objects.create(
            event=event,
            action_detail="하류 관로 현장 점검 진행",
            action_type="FIELD_CHECK",
        )

        with patch(
            "apps.monitoring.services.maintenance_dispatcher.post_maintenance_log",
            return_value={"ok": True, "vector_id": "vector-1"},
        ) as post_maintenance_log:
            response = self.client.patch(
                f"/api/hazards/{event.id}/actions/{action.id}",
                data=json.dumps(
                    {
                        "result_detail": "토사 제거 후 수위 안정화",
                        "result_status": "RESOLVED",
                        "recurrence_note": "폭우 시 상류 맨홀 우선 점검",
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=self.auth_header,
            )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(event.status, HazardEvent.Status.RESOLVED)
        self.assertTrue(event.is_deleted)
        self.assertEqual(action.fastapi_sync_status, HazardAction.FastApiSyncStatus.SENT)
        self.assertEqual(action.fastapi_vector_id, "vector-1")
        dispatched_payload = post_maintenance_log.call_args.args[0]
        self.assertEqual(dispatched_payload["event"]["target_id"], "PIPE_1")
        self.assertEqual(dispatched_payload["event"]["hazard_type"], "REVERSE_FLOW")
        self.assertEqual(dispatched_payload["metrics"]["flowCms"], -0.25)
        self.assertEqual(dispatched_payload["action"]["initial_action_detail"], "하류 관로 현장 점검 진행")
        self.assertEqual(dispatched_payload["action"]["result_detail"], "토사 제거 후 수위 안정화")
        self.assertEqual(dispatched_payload["action"]["recurrence_note"], "폭우 시 상류 맨홀 우선 점검")
        self.assertEqual(HazardCaseEmbedding.objects.count(), 1)
        embedding_text = HazardCaseEmbedding.objects.get().embedding_text
        self.assertIn("하류 관로 현장 점검 진행", embedding_text)
        self.assertIn("폭우 시 상류 맨홀 우선 점검", embedding_text)

    def test_action_completion_requires_result_detail(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            metrics_snapshot={"flowCms": -0.25},
        )
        action = HazardAction.objects.create(event=event, action_detail="현장 확인 요청")

        response = self.client.patch(
            f"/api/hazards/{event.id}/actions/{action.id}",
            data=json.dumps({"recurrence_note": "확인 필요"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 400)
        event.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(event.status, HazardEvent.Status.OPEN)
        self.assertFalse(event.is_deleted)
        self.assertEqual(HazardAction.objects.count(), 1)
        self.assertEqual(action.fastapi_sync_status, HazardAction.FastApiSyncStatus.PENDING)
        self.assertEqual(HazardCaseEmbedding.objects.count(), 0)

    def test_action_saves_even_when_fastapi_dispatch_fails(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            status=HazardEvent.Status.IN_PROGRESS,
            metrics_snapshot={"flowCms": -0.25},
        )
        action = HazardAction.objects.create(event=event, action_detail="현장 확인 요청")

        with patch(
            "apps.monitoring.services.maintenance_dispatcher.post_maintenance_log",
            side_effect=OSError("connection refused"),
        ):
            response = self.client.patch(
                f"/api/hazards/{event.id}/actions/{action.id}",
                data=json.dumps(
                    {
                        "result_detail": "현장 확인 완료",
                        "result_status": "RESOLVED",
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=self.auth_header,
            )

        self.assertEqual(response.status_code, 200)
        action.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(event.status, HazardEvent.Status.RESOLVED)
        self.assertEqual(action.fastapi_sync_status, HazardAction.FastApiSyncStatus.FAILED)
        self.assertIn("connection refused", action.fastapi_error_message)

    def test_rejects_hazard_api_without_admin_token(self):
        response = self.client.get("/api/hazards?status=OPEN")

        self.assertEqual(response.status_code, 401)

    def test_forecast_api_returns_runtime_buffer_prediction(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.1))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.2))

        response = self.client.get("/api/hazards/forecast?minutes=10", HTTP_AUTHORIZATION=self.auth_header)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["forecastMinutes"], 10)
        self.assertEqual(payload["highestSeverity"], "WARNING")
        self.assertEqual(payload["events"][0]["eventType"], "PREDICTED_FULL_PIPE")


def _critical_tick():
    return {
        "runId": "run-1",
        "stepIndex": 10,
        "modelTime": "2026-06-16T00:00:10",
        "links": {
            "PIPE_1": {
                "id": "PIPE_1",
                "sourceEditorName": "테스트 관로",
                "flowCms": -0.25,
                "velocityMps": -1.1,
                "direction": "reverse",
            }
        },
        "risk": {
            "events": [
                {
                    "eventId": "REVERSE_FLOW:link:PIPE_1",
                    "eventType": "REVERSE_FLOW",
                    "severity": "CRITICAL",
                    "source": "link",
                    "sourceId": "PIPE_1",
                    "metrics": {"reverseTicks": 30},
                    "reason": "reverse_flow_sustained",
                }
            ]
        },
    }


class MaintenanceDispatcherTests(TestCase):
    def test_builds_fastapi_payload_with_prompt_level_25_shape(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
            metrics_snapshot={"flowCms": -0.25, "direction": "reverse"},
        )
        action = HazardAction.objects.create(
            event=event,
            action_detail="원문 조치 내용",
            action_type="FIELD_CHECK",
            result_detail="현장 확인 완료",
            result_status="RESOLVED",
            recurrence_note="역류 시 하류 관로 우선 확인",
        )

        payload = build_maintenance_log_payload(action)

        self.assertEqual(payload["event"]["id"], event.id)
        self.assertEqual(payload["event"]["target_id"], "PIPE_1")
        self.assertEqual(payload["event"]["hazard_type"], "REVERSE_FLOW")
        self.assertEqual(payload["event"]["hazard_level"], "CRITICAL")
        self.assertEqual(payload["metrics"], {"flowCms": -0.25, "direction": "reverse"})
        self.assertEqual(payload["action"]["initial_action_detail"], "원문 조치 내용")
        self.assertEqual(payload["action"]["result_detail"], "현장 확인 완료")
        self.assertEqual(payload["action"]["recurrence_note"], "역류 시 하류 관로 우선 확인")

    def test_dispatch_records_vector_id_from_fastapi_response(self):
        event = HazardEvent.objects.create(
            event_key="run-1:REVERSE_FLOW:PIPE_1:CRITICAL",
            run_id="run-1",
            target_id="PIPE_1",
            source="link",
            hazard_level="CRITICAL",
            hazard_type="REVERSE_FLOW",
            hazard_detail="역류 감지",
        )
        action = HazardAction.objects.create(event=event, action_detail="원문 조치 내용")

        with patch(
            "apps.monitoring.services.maintenance_dispatcher.post_maintenance_log",
            return_value={"ok": True, "vector_id": "fastapi-vector-1"},
        ):
            result = dispatch_maintenance_log(action)

        action.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(action.fastapi_sync_status, HazardAction.FastApiSyncStatus.SENT)
        self.assertEqual(action.fastapi_vector_id, "fastapi-vector-1")


class ForecastStateTests(TestCase):
    def setUp(self):
        forecast_state.reset()

    def test_predicts_10_minute_future_risk_from_runtime_buffer(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.1))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.2))

        result = forecast_state.forecast(minutes=10)

        self.assertEqual(result["forecastMinutes"], 10)
        self.assertEqual(result["highestSeverity"], "WARNING")
        event = result["events"][0]
        self.assertEqual(event["sourceId"], "PIPE_1")
        self.assertEqual(event["eventType"], "PREDICTED_FULL_PIPE")
        self.assertGreater(event["metrics"]["predictedValue"], 0.69)

    def test_builds_forecast_llm_payload_only_for_critical_predictions(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.1))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.3))
        forecast = forecast_state.forecast(minutes=10)

        payload = forecast_state.build_forecast_llm_payload(_forecast_snapshot(step_index=121, fullness=0.3), forecast)

        self.assertIsNotNone(payload)
        trigger = payload["llmTrigger"]
        self.assertTrue(trigger["shouldTrigger"])
        self.assertEqual(trigger["reason"], "predicted_10_min_risk")
        self.assertEqual(trigger["context"]["systemMeta"]["triggerBasis"], "forecast")

    def test_does_not_build_llm_payload_without_critical_prediction(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.1))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.12))
        forecast = forecast_state.forecast(minutes=10)

        payload = forecast_state.build_forecast_llm_payload(_forecast_snapshot(step_index=121, fullness=0.12), forecast)

        self.assertIsNone(payload)

    def test_does_not_predict_from_short_startup_window(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.0, rainfall_ratio=0.0))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=2, fullness=0.04, rainfall_ratio=0.0))

        result = forecast_state.forecast(minutes=10)
        payload = forecast_state.build_forecast_llm_payload(
            _forecast_snapshot(step_index=2, fullness=0.04, rainfall_ratio=0.0),
            result,
        )

        self.assertEqual(result["highestSeverity"], "NORMAL")
        self.assertEqual(result["events"], [])
        self.assertIn("Need at least", result["message"])
        self.assertIsNone(payload)

    def test_clear_weather_requires_higher_current_level_for_forecast_event(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.0, rainfall_ratio=0.0))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.15, rainfall_ratio=0.0))

        result = forecast_state.forecast(minutes=10)
        payload = forecast_state.build_forecast_llm_payload(
            _forecast_snapshot(step_index=121, fullness=0.15, rainfall_ratio=0.0),
            result,
        )

        self.assertEqual(result["highestSeverity"], "NORMAL")
        self.assertEqual(result["events"], [])
        self.assertIsNone(payload)

    def test_heavy_rain_allows_forecast_event_with_lower_current_level(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.0, rainfall_ratio=3.0))
        forecast_state.record_snapshot(_forecast_snapshot(step_index=121, fullness=0.15, rainfall_ratio=3.0))

        result = forecast_state.forecast(minutes=10)

        self.assertEqual(result["highestSeverity"], "CRITICAL")
        self.assertEqual(result["events"][0]["eventType"], "PREDICTED_FULL_PIPE")
        self.assertGreaterEqual(result["events"][0]["metrics"]["minCurrentValue"], 0.05)

    def test_blockage_control_creates_critical_forecast_event_during_startup_window(self):
        forecast_state.record_snapshot(_forecast_snapshot(step_index=1, fullness=0.0, rainfall_ratio=1.0))
        forecast_state.record_snapshot(
            _forecast_snapshot(step_index=2, fullness=0.0, rainfall_ratio=1.0, blockage_ratio=1.0)
        )

        result = forecast_state.forecast(minutes=10)
        payload = forecast_state.build_forecast_llm_payload(
            _forecast_snapshot(step_index=2, fullness=0.0, rainfall_ratio=1.0, blockage_ratio=1.0),
            result,
        )

        self.assertEqual(result["highestSeverity"], "CRITICAL")
        self.assertEqual(result["events"][0]["eventType"], "PREDICTED_BLOCKAGE_CLOSED")
        self.assertEqual(result["events"][0]["sourceId"], "PIPE_1")
        self.assertEqual(result["events"][0]["metrics"]["currentValue"], 1.0)
        self.assertIn("Need at least", result["message"])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["llmTrigger"]["context"]["riskEvents"][0]["eventType"], "PREDICTED_BLOCKAGE_CLOSED")


def _forecast_snapshot(
    step_index: int,
    fullness: float,
    rainfall_ratio: float = 3.0,
    blockage_ratio: float = 0.0,
) -> dict:
    return {
        "type": "tick",
        "ok": True,
        "runId": "forecast-run",
        "stepIndex": step_index,
        "stepSeconds": 1,
        "modelTime": f"2026-06-16T00:{step_index // 60:02d}:{step_index % 60:02d}",
        "control": {"rainfallRatio": rainfall_ratio},
        "nodes": {
            "NODE_1": {
                "depthRatio": 0.1,
                "floodingCms": 0.0,
            }
        },
        "links": {
            "PIPE_1": {
                "fullness": fullness,
                "capacityRatio": 0.1,
                "flowCms": 0.01,
                "blockageRatio": blockage_ratio,
            }
        },
    }
