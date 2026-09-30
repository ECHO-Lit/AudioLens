"""GPU inference functions used by ECHO's Celery workers.

Deploy from this directory with ``modal deploy modal_app.py``. Inputs are
individual audio files; ECHO retains job orchestration, Redis, result storage,
and CPU-only operations on the VPS.
"""

from __future__ import annotations

import json
import math
import struct
import wave

import modal


APP_NAME = "echo-inference"
MODEL_CACHE_PATH = "/root/.cache/huggingface"
model_cache = modal.Volume.from_name("echo-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "gcc")
    .pip_install("torch")
    .pip_install_from_requirements("requirements-modal.txt")
    .env({"HF_HOME": MODEL_CACHE_PATH, "ML_DEVICE": "auto", "INFERENCE_BACKEND": "local"})
    .add_local_dir("app", remote_path="/root/app")
)

app = modal.App(APP_NAME)


def _infer(
    operation: str,
    model: str,
    audio_bytes: bytes,
    audio_suffix: str,
    parameters: dict,
    model_spec_data,
):
    import tempfile
    from pathlib import Path

    from app.schemas.jobs import RuntimeModelSpec
    from app.worker.executor import _execute_one, _jsonable
    from app.core.device import detect_inference_runtime

    spec = RuntimeModelSpec.model_validate(model_spec_data) if model_spec_data else None
    with tempfile.TemporaryDirectory(prefix="echo-modal-") as temp_dir:
        suffix = audio_suffix if audio_suffix.startswith(".") and len(audio_suffix) <= 12 else ".wav"
        audio_path = Path(temp_dir) / f"input{suffix}"
        audio_path.write_bytes(audio_bytes)
        result = _jsonable(_execute_one(operation, model, audio_path, parameters, spec))

    model_cache.commit()
    runtime = detect_inference_runtime().as_dict()
    # Return JSON text so Modal's pickle transport never requires torch on the
    # VPS or CLI that invoked this function.
    return json.dumps(
        {"result": result, "runtime": runtime, "model": model, "operation": operation},
        default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value),
    )


@app.function(
    image=image,
    gpu="T4",
    volumes={MODEL_CACHE_PATH: model_cache},
    timeout=3300,
    max_containers=1,
    scaledown_window=60,
)
def infer_fast(
    operation: str, model: str, audio_bytes: bytes, audio_suffix: str,
    parameters: dict, model_spec_data=None,
):
    return _infer(operation, model, audio_bytes, audio_suffix, parameters, model_spec_data)


@app.function(
    image=image,
    gpu="A10G",
    volumes={MODEL_CACHE_PATH: model_cache},
    timeout=3300,
    max_containers=1,
    scaledown_window=60,
)
def infer_large(
    operation: str, model: str, audio_bytes: bytes, audio_suffix: str,
    parameters: dict, model_spec_data=None,
):
    return _infer(operation, model, audio_bytes, audio_suffix, parameters, model_spec_data)


@app.local_entrypoint()
def smoke_test():
    """Run one inexpensive real Whisper-base prediction on Modal's T4 GPU."""
    import tempfile
    from pathlib import Path

    sample_rate = 16_000
    samples = 16_000  # one second, synthetic tone; validates model execution/device
    with tempfile.TemporaryDirectory(prefix="echo-modal-smoke-") as temp_dir:
        audio_path = Path(temp_dir) / "tone.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            pcm = b"".join(
                struct.pack("<h", int(5_000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
                for i in range(samples)
            )
            audio.writeframes(pcm)
        response_json = infer_fast.remote(
            "prediction", "whisper-base", audio_path.read_bytes(), ".wav", {}, None
        )
    print(response_json)
