"""Django에서 사용하는 SWMM 엔진 경계.

이 패키지는 FastAPI 임시 서버를 거치지 않고 Django 코드가 직접 사용할
PySWMM 실행 엔진을 제공한다. 일반 Django view/consumer에서는 가능하면
`swmm_engine.interface`를 import하고, 엔진 구현이 꼭 필요할 때만 이 패키지를
직접 사용한다.
"""

from .runtime_engine import (
    RealtimeSwmmSession,
    RuntimeModelSpec,
    SwmmRuntimeEngine,
    SwmmRuntimeError,
)

__all__ = [
    "RealtimeSwmmSession",
    "RuntimeModelSpec",
    "SwmmRuntimeEngine",
    "SwmmRuntimeError",
]
