"""`python -m tantive`: read the environment, load the model, serve with uvicorn (one worker; the engine serialises)."""

from __future__ import annotations

import os
import sys

from tantive.config import ConfigError, Settings
from tantive.telemetry import configure_logging, log_event


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    configure_logging(settings.log_prompts)
    from tantive.app import create_app
    from tantive.model import OnnxModel

    try:
        model = OnnxModel(settings.model_dir, threads=settings.threads, inter_op_threads=settings.inter_op_threads)
        app = create_app(settings, model)
    except (OSError, ValueError, ConfigError) as e:
        print(f"cannot load the model from S1D1_MODEL_DIR={settings.model_dir}: {e}", file=sys.stderr)
        return 2
    log_event("starting", host=settings.host, port=settings.port, root_path=settings.root_path or None)
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
