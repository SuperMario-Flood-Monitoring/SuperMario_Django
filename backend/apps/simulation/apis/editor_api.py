from __future__ import annotations

import json
import zipfile
from io import BytesIO
from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from swmm_engine.interface import ConversionError, convert_layout_to_inp

from ..dtos import EditorConvertRequest, EditorConvertResponse, ErrorResponse


editor_api = NinjaAPI(
    title="SWMM Editor API",
    version="1.0.0",
    urls_namespace="swmm_editor_api",
)


def error_payload(message: str, detail: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "detail": detail or message}


@editor_api.exception_handler(ConversionError)
def conversion_error(request: HttpRequest, exc: ConversionError):
    return editor_api.create_response(request, error_payload(str(exc)), status=422)


@editor_api.exception_handler(ValueError)
def value_error(request: HttpRequest, exc: ValueError):
    return editor_api.create_response(request, error_payload(str(exc)), status=400)


@editor_api.post("/convert/validate", response={200: EditorConvertResponse, 400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
def editor_convert_validate(request: HttpRequest, payload: EditorConvertRequest) -> dict[str, Any]:
    return convert_layout_to_inp(payload.layout_payload, title=payload.title_value)


@editor_api.post("/export-inp", response={400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
def editor_export_inp(request: HttpRequest, payload: EditorConvertRequest) -> HttpResponse:
    result = convert_layout_to_inp(payload.layout_payload, title=payload.title_value)
    filename = payload.filename_value or "generated_from_editor.inp"
    response = HttpResponse(result["inpText"], content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response


@editor_api.post("/convert/download", response={400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
def editor_convert_download(request: HttpRequest, payload: EditorConvertRequest) -> HttpResponse:
    result = convert_layout_to_inp(payload.layout_payload, title=payload.title_value)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("generated_from_editor.inp", result["inpText"])
        archive.writestr(
            "conversion-report.json",
            json.dumps(result["report"], ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "mapping.json",
            json.dumps(result["mapping"], ensure_ascii=False, indent=2),
        )
    buffer.seek(0)

    filename = payload.filename_value or "swmm-editor-export.zip"
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response
