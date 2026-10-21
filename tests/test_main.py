import os
import subprocess
import sys


def test_main_bad_env_exits_2():
    env = {**os.environ, "S1D1_FORMATS": "nope", "S1D1_MODEL_DIR": "/nonexistent"}
    r = subprocess.run([sys.executable, "-m", "tantive"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "S1D1_FORMATS" in r.stderr


def test_main_missing_model_dir_exits_2():
    env = {**os.environ, "S1D1_MODEL_DIR": "/nonexistent", "S1D1_THREADS": "1"}
    r = subprocess.run([sys.executable, "-m", "tantive"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "/nonexistent" in r.stderr
