import json
from dataclasses import asdict

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from apps.common import SimulationRequestDTO, SimulationResponseDTO
from apps.facilities.models import Facility
from legacy.swmm_engine_legacy import get_engine

from .models import SimulationRun
from .demo import load_demo_facilities, load_demo_payload


def _response(code: int, message: str, status: str, data=None) -> JsonResponse:
    body = SimulationResponseDTO(
        code=code,
        message=message,
        status=status,
        data=data,
    )
    return JsonResponse(asdict(body), status=code)


def _parse_request(request) -> SimulationRequestDTO:
    if not request.body:
        raise ValueError("Request body is required.")
    try:
        payload = json.loads(request.body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    rainfall_status = str(payload.get("rainfall_status", "")).strip()
    if not rainfall_status:
        raise ValueError("'rainfall_status' is required.")
    try:
        rainfall_amount = float(payload.get("rainfall_amount", 0.0))
        duration_minutes = int(payload.get("duration_minutes", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "'rainfall_amount' and 'duration_minutes' must be numbers."
        ) from exc
    if rainfall_amount < 0 or duration_minutes < 0:
        raise ValueError(
            "'rainfall_amount' and 'duration_minutes' cannot be negative."
        )

    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("'parameters' must be a JSON object.")
    model = payload.get("model", {})
    control = payload.get("control", {})
    if not isinstance(model, dict):
        raise ValueError("'model' must be a JSON object.")
    if not isinstance(control, dict):
        raise ValueError("'control' must be a JSON object.")
    return SimulationRequestDTO(
        rainfall_status=rainfall_status,
        rainfall_amount=rainfall_amount,
        duration_minutes=duration_minutes,
        parameters=parameters,
        model=model,
        control=control,
    )


class SimulationView(View):
    def get(self, request):
        runs = SimulationRun.objects.all()[:20]
        data = [
            {
                "id": run.id,
                "rainfall_status": run.rainfall_status,
                "rainfall_amount": run.rainfall_amount,
                "duration_minutes": run.duration_minutes,
                "status": run.status,
                "result": run.result,
                "created_at": run.created_at.isoformat(),
            }
            for run in runs
        ]
        return _response(200, "Simulation runs found.", "OK", data)

    def post(self, request):
        try:
            scenario = _parse_request(request)
        except ValueError as exc:
            return _response(400, str(exc), "BAD_REQUEST")

        facilities = list(
            Facility.objects.filter(is_active=True).values(
                "id",
                "name",
                "facility_type",
                "normal_value",
                "unit",
                "metadata",
            )
        )
        if not facilities:
            return _response(
                409,
                "Initialize at least one facility before simulation.",
                "CONFLICT",
            )

        try:
            channel_layer = get_channel_layer()

            def broadcast_step(snapshot):
                async_to_sync(channel_layer.group_send)(
                    "simulation",
                    {
                        "type": "simulation.result",
                        "payload": asdict(
                            SimulationResponseDTO(
                                code=200,
                                message="Simulation step.",
                                status="OK",
                                data=snapshot,
                            )
                        ),
                    },
                )

            result = get_engine().start(
                facilities=facilities,
                rainfall_status=scenario.rainfall_status,
                rainfall_amount=scenario.rainfall_amount,
                duration_minutes=scenario.duration_minutes,
                parameters=scenario.parameters,
                model=scenario.model,
                control=scenario.control,
                progress_callback=broadcast_step,
            )
        except ValueError as exc:
            return _response(400, str(exc), "BAD_REQUEST")
        except RuntimeError as exc:
            return _response(409, str(exc), "CONFLICT")
        except Exception as exc:
            return _response(
                500,
                f"Simulation engine failed: {exc}",
                "ERROR",
            )

        run = SimulationRun.objects.create(
            rainfall_status=scenario.rainfall_status,
            rainfall_amount=scenario.rainfall_amount,
            duration_minutes=scenario.duration_minutes,
            parameters=scenario.parameters,
            result=result,
        )
        response_data = {"simulation_id": run.id, **result}
        payload = asdict(
            SimulationResponseDTO(
                code=200,
                message="Simulation completed.",
                status="OK",
                data=response_data,
            )
        )
        async_to_sync(channel_layer.group_send)(
            "simulation",
            {"type": "simulation.result", "payload": payload},
        )
        return JsonResponse(payload, status=200)


class SimulationStopView(View):
    def post(self, request):
        get_engine().stop()
        return _response(200, "Simulation stopped.", "OK")


class SimulationDemoView(View):
    def get(self, request):
        return render(
            request,
            "simulation/demo.html",
            {
                "payload": json.dumps(
                    load_demo_payload(),
                    ensure_ascii=False,
                    indent=2,
                ),
                "facilities": json.dumps(
                    {"facilities": load_demo_facilities()},
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
