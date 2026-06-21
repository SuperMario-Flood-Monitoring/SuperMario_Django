# Django SWMM server package

This folder is the Django-side boundary for SWMM conversion, engine runtime,
risk detection, and LLM context generation.

The React app should stay in `../react-viewer`. Django code can add this
directory to `PYTHONPATH` and import the public interface:

```python
from swmm.interface import (
    apply_controls,
    build_llm_context,
    convert_layout_to_inp,
    create_engine_session,
    detect_risks,
    get_latest_snapshot,
    pause_engine,
    resume_engine,
    start_engine,
    stop_engine,
    validate_snapshot,
)
```

## Package shape

```text
django-server/
└── swmm/
    ├── interface.py      # Public Django-facing API
    ├── converter/        # React editor JSON -> SWMM INP boundary
    ├── engine/           # Django-owned PySWMM session/runtime engine
    ├── runtime/          # Snapshot/control payload helpers
    ├── risk/             # Risk detection and LLM context helpers
    ├── models/           # Django-managed SWMM model files
    └── logs/             # Django-managed runtime logs
```

Current split status:

- `swmm.converter` contains the Django-side React JSON -> SWMM INP converter.
- `swmm.engine` contains the Django-side PySWMM runtime engine. It no longer
  imports the temporary FastAPI runtime server.
- `swmm.risk` contains the Django-side snapshot validation, risk detection, and
  LLM context helpers.
- `swmm.interface` is the only module Django views/workers should import.
- FastAPI is treated as a legacy/temporary development server and is not part
  of this package boundary.

Generated SWMM files such as `.inp`, `.rpt`, `.out`, and mapping JSON can be
stored under `swmm/models/` or a Django-managed storage backend. The React
editor layout JSON should remain the source of truth; `.inp` is the generated
execution/export artifact for SWMM.
