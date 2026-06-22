import json

from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.facilities.models import Facility
from config.asgi import application
from legacy.swmm_engine_legacy import (
    csv_to_records,
    get_engine,
    normalize_model_payload,
    records_to_csv,
)

from .demo import load_demo_facilities, load_demo_payload
from .models import SimulationRun


class SimulationViewTests(TestCase):
    def test_requires_initialized_facilities(self):
        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(
                {"rainfall_status": "HEAVY_RAIN", "rainfall_amount": 80}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)

    def test_runs_pyswmm_engine(self):
        Facility.objects.create(
            name="catch-basin-1",
            facility_type=Facility.Type.CATCH_BASIN,
            normal_value=10,
            unit="cm",
        )

        payload = load_demo_payload(realtime=False)
        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["engine"], "pyswmm")
        self.assertGreater(response.json()["data"]["steps"], 0)
        self.assertGreaterEqual(response.json()["data"]["steps"], 28)
        self.assertEqual(len(response.json()["data"]["nodes"]), 2)
        self.assertEqual(
            response.json()["data"]["schema_version"],
            "2026-06-15-swmm-output-v1",
        )
        pipe = next(
            item
            for item in response.json()["data"]["facilities"]
            if item["id"] == "pipe_1"
        )
        self.assertIn("water_level_percent", pipe)
        self.assertIn("blockage_percent", pipe)
        self.assertIn("has_failure", pipe)
        self.assertEqual(SimulationRun.objects.count(), 1)

    def test_rejects_model_with_unknown_link_node(self):
        Facility.objects.create(name="catch-basin-1")
        payload = load_demo_payload(realtime=False)
        payload["model"]["links"][0]["to"]["nodeId"] = "missing"

        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_rejects_duration_over_thirty_seconds(self):
        Facility.objects.create(name="pipe_1")
        payload = load_demo_payload(realtime=False)
        payload["control"]["durationSeconds"] = 31

        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_normalizes_swmm_section_model(self):
        model = normalize_model_payload(_swmm_section_model())

        self.assertEqual(model["inputFormat"], "swmm-section-v1")
        self.assertEqual(len(model["nodes"]), 3)
        self.assertEqual(len(model["links"]), 2)
        self.assertEqual(model["links"][0]["props"]["length"], 180.0)
        self.assertEqual(model["links"][0]["props"]["diameter"], 0.6)

    def test_runs_swmm_section_model_from_api(self):
        Facility.objects.create(name="pipe_1")
        payload = load_demo_payload(realtime=False)
        payload["model"] = _swmm_section_model()
        payload["control"]["durationSeconds"] = 3

        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["model_input_format"], "swmm-section-v1")
        self.assertGreater(response.json()["data"]["steps"], 0)

    def test_demo_page_is_available(self):
        response = self.client.get(reverse("simulation:demo"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PySWMM Engine Demo")
        self.assertContains(response, "더미 시설 초기화")
        self.assertContains(response, "initial_water_percent")
        self.assertContains(response, "obstruction_type")

    def test_initial_water_is_applied_to_first_swmm_step(self):
        payload = load_demo_payload(realtime=False)
        snapshots = []

        get_engine().start(
            facilities=load_demo_facilities(),
            rainfall_status=payload["rainfall_status"],
            rainfall_amount=payload["rainfall_amount"],
            duration_minutes=payload["duration_minutes"],
            parameters=payload["parameters"],
            model=payload["model"],
            control=payload["control"],
            progress_callback=snapshots.append,
        )

        catch_basin = next(
            item
            for item in snapshots[0]["facilities"]
            if item["id"] == "catch_basin_1"
        )
        manhole = next(
            item
            for item in snapshots[0]["facilities"]
            if item["id"] == "manhole_1"
        )
        self.assertGreater(catch_basin["water_level_percent"], 25)
        self.assertGreater(manhole["water_level_percent"], 15)

    def test_demo_facilities_prevent_initialization_conflict(self):
        initialize_response = self.client.post(
            reverse("facilities:list-create"),
            data=json.dumps({"facilities": load_demo_facilities()}),
            content_type="application/json",
        )
        self.assertEqual(initialize_response.status_code, 200)
        self.assertEqual(Facility.objects.count(), 4)

        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(load_demo_payload(realtime=False)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        pipe = next(
            item
            for item in response.json()["data"]["facilities"]
            if item["id"] == "pipe_1"
        )
        self.assertEqual(pipe["blockage_percent"], 60)
        self.assertEqual(pipe["obstruction_type"], "LEAVES")

    def test_high_blockage_is_reported_as_failure(self):
        Facility.objects.create(name="pipe_1")
        payload = load_demo_payload(realtime=False)
        payload["control"]["blockagesById"]["pipe_1"] = 85

        response = self.client.post(
            reverse("simulation:list-start"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        pipe = next(
            item
            for item in response.json()["data"]["facilities"]
            if item["id"] == "pipe_1"
        )
        self.assertEqual(pipe["status"], "CRITICAL")
        self.assertTrue(pipe["has_failure"])
        self.assertEqual(pipe["blockage_percent"], 85)

    def test_csv_utility_round_trip(self):
        records = [
            {
                "id": "pipe_1",
                "water_level_percent": 12.3,
                "status": "NORMAL",
            }
        ]

        content = records_to_csv(records)
        restored = csv_to_records(content)

        self.assertEqual(restored[0]["id"], "pipe_1")
        self.assertEqual(restored[0]["water_level_percent"], "12.3")


def _swmm_section_model():
    return {
        "format": "swmm-section-v1",
        "version": 1,
        "junctions": [
            {
                "id": "CB_1",
                "elevation": 0.1,
                "max_depth": 1.2,
                "initial_depth": 0.2,
                "x": 100,
                "y": 100,
                "catchment": {
                    "area": 1.5,
                    "impervious": 80,
                    "width": 120,
                    "slope": 1,
                },
            },
            {
                "id": "MH_1",
                "elevation": 0.0,
                "max_depth": 1.5,
                "x": 300,
                "y": 160,
            },
        ],
        "outfalls": [
            {
                "id": "OUT_1",
                "elevation": 0.0,
                "type": "FREE",
                "x": 520,
                "y": 220,
            }
        ],
        "conduits": [
            {
                "id": "P_1",
                "from_node": "CB_1",
                "to_node": "MH_1",
                "length": 180,
                "roughness": 0.013,
                "slope": 0.01,
                "xsection": {"shape": "CIRCULAR", "diameter": 0.6},
            },
            {
                "id": "P_2",
                "from_node": "MH_1",
                "to_node": "OUT_1",
                "length": 180,
                "roughness": 0.013,
                "slope": 0.01,
                "xsection": {"shape": "CIRCULAR", "diameter": 0.6},
            },
        ],
    }


class SimulationSocketTests(TransactionTestCase):
    def test_connects_and_receives_ready_message(self):
        async def scenario():
            communicator = WebsocketCommunicator(application, "/ws/simulation/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            message = await communicator.receive_json_from()
            self.assertEqual(message["status"], "OK")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_receives_one_second_swmm_step_from_api(self):
        Facility.objects.create(name="pipe_1")

        async def scenario():
            communicator = WebsocketCommunicator(application, "/ws/simulation/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.receive_json_from()

            response = await sync_to_async(
                self.client.post,
                thread_sensitive=True,
            )(
                reverse("simulation:list-start"),
                data=json.dumps(load_demo_payload(realtime=False)),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

            message = await communicator.receive_json_from(timeout=2)
            self.assertEqual(message["data"]["event"], "simulation.step")
            self.assertEqual(message["data"]["interval_seconds"], 1)
            self.assertGreater(len(message["data"]["facilities"]), 0)
            await communicator.disconnect()

        async_to_sync(scenario)()
