import json
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings

from apps.auth.models import User
from apps.auth.tokens import issue_token
from config.asgi import application
from swmm_engine.interface import (
    build_llm_context,
    convert_layout_to_inp,
    detect_risks,
)
from swmm_engine.llm_dispatcher import (
    build_langchain_request_payload,
    dispatch_llm_analysis,
)

from . import state


def minimal_editor_layout():
    return {
        "version": 1,
        "groundSurfaceY": 330,
        "nodes": [
            {
                "id": "catchBasin_1",
                "swmmId": "catchBasin_1",
                "name": "빗물받이 1",
                "type": "catchBasin",
                "x": 100,
                "y": 300,
                "width": 170,
                "height": 110,
                "ports": [
                    {"id": "top", "side": "top"},
                    {"id": "right", "side": "right"},
                    {"id": "bottom", "side": "bottom"},
                    {"id": "left", "side": "left"},
                ],
                "props": {},
            },
            {
                "id": "outfall_1",
                "swmmId": "outfall_1",
                "name": "방류구 1",
                "type": "outfall",
                "x": 600,
                "y": 500,
                "width": 120,
                "height": 80,
                "ports": [
                    {"id": "left", "side": "left"},
                ],
                "props": {},
            },
        ],
        "links": [
            {
                "id": "link_1",
                "swmmId": "link_1",
                "name": "관로 1",
                "type": "relation",
                "from": {"nodeId": "catchBasin_1", "portId": "right"},
                "to": {"nodeId": "outfall_1", "portId": "left"},
                "size": "medium",
                "props": {
                    "route": "elbow",
                    "slope": 0.03,
                    "blockage": 0,
                },
            }
        ],
    }


def auth_header():
    user = User.objects.create(
        username="admin",
        role=User.Role.ADMIN,
        password=make_password("password"),
    )
    return f"Bearer {issue_token(user, 'access')}"


def stop_runtime_engine():
    async_to_sync(state.engine.stop)()


class SwmmEngineInterfaceTests(TestCase):
    def test_converts_react_editor_layout_to_swmm_inp(self):
        result = convert_layout_to_inp(minimal_editor_layout())

        self.assertTrue(result["ok"])
        self.assertIn("[OUTFALLS]", result["inpText"])
        self.assertIn("[CONDUITS]", result["inpText"])
        self.assertEqual(result["report"]["counts"]["outfalls"], 1)
        self.assertGreaterEqual(result["report"]["counts"]["conduits"], 1)
        self.assertIn("link_1_CONDUIT", result["mapping"]["swmmLinks"])
        self.assertEqual(
            result["mapping"]["swmmLinks"]["link_1_CONDUIT"]["sourceEditorId"],
            "link_1",
        )

    def test_detects_risks_from_current_swmm_snapshot_shape(self):
        snapshot = {
            "nodes": {
                "catchBasin_1": {
                    "depthRatio": 1.1,
                    "floodingCms": 0.02,
                }
            },
            "links": {
                "link_1": {
                    "flowCms": -0.3,
                    "fullness": 1.0,
                    "capacityRatio": 1.2,
                    "direction": "reverse",
                }
            },
            "editorObjects": {},
            "summary": {},
        }

        risk = detect_risks(snapshot, policy_level="sensitive")
        context = build_llm_context(snapshot, risk)

        self.assertEqual(risk["highestSeverity"], "CRITICAL")
        self.assertGreater(len(risk["events"]), 0)
        self.assertEqual(context["highestSeverity"], "CRITICAL")


class CurrentSimulationApiTests(TestCase):
    def setUp(self):
        stop_runtime_engine()
        self.auth_header = auth_header()

    def tearDown(self):
        stop_runtime_engine()

    def test_editor_convert_validate_uses_current_swmm_converter(self):
        response = self.client.post(
            "/api/editor/convert/validate",
            data=json.dumps({"layout": minimal_editor_layout()}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("[OUTFALLS]", body["inpText"])
        self.assertIn("swmmLinks", body["mapping"])

    def test_engine_start_control_and_stop_use_current_runtime(self):
        start_response = self.client.post(
            "/api/engine/start",
            data=json.dumps(
                {
                    "layout": minimal_editor_layout(),
                    "stepSeconds": 1,
                    "control": {
                        "rainfallRatio": 0,
                        "blockagesById": {},
                        "speedMultiplier": 1,
                    },
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(start_response.status_code, 200)
        started = start_response.json()
        self.assertTrue(started["ok"])
        self.assertEqual(started["snapshot"]["type"], "started")
        self.assertIn("nodes", started["snapshot"])
        self.assertIn("links", started["snapshot"])

        control_response = self.client.post(
            "/api/engine/control",
            data=json.dumps(
                {
                    "id": "폭우",
                    "rainfallRatio": 0.5,
                    "blockagesById": {"link_1": 30},
                    "speedMultiplier": 2,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(control_response.status_code, 200)
        controlled = control_response.json()
        self.assertTrue(controlled["ok"])
        self.assertEqual(controlled["snapshot"]["control"]["id"], "폭우")
        self.assertEqual(controlled["snapshot"]["type"], "control")

        stop_response = self.client.post(
            "/api/engine/stop",
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(stop_response.status_code, 200)
        self.assertFalse(stop_response.json()["hasSession"])

    def test_engine_start_requires_react_editor_layout(self):
        response = self.client.post(
            "/api/engine/start",
            data=json.dumps({"control": {"rainfallRatio": 0}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["error"], "layout_required")

    def test_websocket_uses_current_simulation_route(self):
        async def scenario():
            communicator = WebsocketCommunicator(application, "/api/ws/simulation/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            message = await communicator.receive_json_from()
            self.assertTrue(message["ok"])
            await communicator.disconnect()

        async_to_sync(scenario)()


class LlmDispatcherTests(TestCase):
    def test_builds_level10_langchain_request_payload(self):
        payload = build_langchain_request_payload(
            {
                "control": {"id": "폭우"},
                "runId": "run-1",
                "stepIndex": 7,
            },
            {"reason": "new_issue"},
            {"highestSeverity": "CRITICAL", "riskEvents": [{"eventType": "FLOODING"}]},
        )

        self.assertEqual(payload["id"], "폭우")
        raw_data = json.loads(payload["swmm_raw_data"])
        self.assertEqual(raw_data["highestSeverity"], "CRITICAL")
        self.assertEqual(raw_data["riskEvents"][0]["eventType"], "FLOODING")

    @override_settings(SUPERMARIO_LLM_ANALYZE_URL="http://llm.example/analyze")
    def test_dispatch_posts_to_langchain_server(self):
        with (
            patch("swmm_engine.llm_dispatcher.post_langchain_request") as post_request,
            patch("swmm_engine.llm_dispatcher.append_llm_dispatch_result_log"),
        ):
            post_request.return_value = {"httpStatus": 200, "responseBody": "{}"}

            result = self.async_to_sync_dispatch()

        self.assertTrue(result["ok"])
        post_request.assert_called_once()
        sent_payload = post_request.call_args.args[0]
        self.assertEqual(sent_payload["id"], "폭우")
        self.assertIn("swmm_raw_data", sent_payload)

    @staticmethod
    def async_to_sync_dispatch():
        import asyncio

        return asyncio.run(
            dispatch_llm_analysis(
                {"control": {"id": "폭우"}, "runId": "run-1", "stepIndex": 1},
                {"reason": "new_issue"},
                {"highestSeverity": "CRITICAL", "riskEvents": []},
                "dispatch-key",
            )
        )
