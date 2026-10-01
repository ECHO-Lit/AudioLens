"""GPU inference functions used by ECHO's Celery workers.

Deploy from this directory with ``modal deploy modal_app.py``. Inputs are
individual audio files; ECHO retains job orchestration, Redis, result storage,
and CPU-only operations on the VPS.
"""

from __future__ import annotations

import json
import math
import struct
import time
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
    started_at = time.monotonic()
    print(
        f"ECHO_MODAL inference_started model={model} operation={operation} audio_bytes={len(audio_bytes)}",
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="echo-modal-") as temp_dir:
        suffix = audio_suffix if audio_suffix.startswith(".") and len(audio_suffix) <= 12 else ".wav"
        audio_path = Path(temp_dir) / f"input{suffix}"
        audio_path.write_bytes(audio_bytes)
        result = _jsonable(_execute_one(operation, model, audio_path, parameters, spec))

    model_cache.commit()
    runtime = detect_inference_runtime().as_dict()
    print(
        "ECHO_MODAL inference_completed "
        f"model={model} operation={operation} device={runtime.get('device')} "
        f"backend={runtime.get('backend')} duration_seconds={time.monotonic() - started_at:.3f}",
        flush=True,
    )
    # Return JSON text so Modal's pickle transport never requires torch on the
    # VPS or CLI that invoked this function.
    return json.dumps(
        {"result": result, "runtime": runtime, "model": model, "operation": operation},
        default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value),
    )


@app.function(
    image=image,
    gpu="A10G",
    volumes={MODEL_CACHE_PATH: model_cache},
    timeout=3300,
    max_containers=1,
    scaledown_window=60,
)
def fit_jacobian_lens(
    model: str,
    model_spec_data,
    samples: list[dict],
    probe_count: int,
    max_audio_seconds: float,
):
    """Fit a decoder lens on Modal; return the trusted torch artifact as bytes."""
    import tempfile
    from pathlib import Path

    import torch
    from app.schemas.jobs import RuntimeModelSpec
    from app.services.jacobian_lens_service import fit_decoder_jacobian_lens
    from app.worker.model_adapters import get_model_adapter
    from app.worker.model_registry import model_registry

    spec = RuntimeModelSpec.model_validate(model_spec_data) if model_spec_data else None
    started_at = time.monotonic()
    print(f"ECHO_MODAL jacobian_lens_fit_started model={model} samples={len(samples)}", flush=True)
    with tempfile.TemporaryDirectory(prefix="echo-modal-jlens-") as temp_dir:
        sample_paths = []
        for index, sample in enumerate(samples):
            suffix = sample["suffix"] if sample["suffix"].startswith(".") else ".wav"
            path = Path(temp_dir) / f"sample-{index}{suffix}"
            path.write_bytes(sample["audio_bytes"])
            sample_paths.append((str(path), sample["transcript"]))
        adapter = get_model_adapter(model, spec)
        resource = model_registry.prepare(adapter, "jacobian_lens_fit")
        artifact = fit_decoder_jacobian_lens(
            adapter,
            resource,
            sample_paths,
            probe_count=int(probe_count),
            max_audio_seconds=float(max_audio_seconds),
        )
        artifact_path = Path(temp_dir) / "lens.pt"
        torch.save(artifact, artifact_path)
        artifact_bytes = artifact_path.read_bytes()
    model_cache.commit()
    print(
        f"ECHO_MODAL jacobian_lens_fit_completed model={model} device=cuda "
        f"duration_seconds={time.monotonic() - started_at:.3f}",
        flush=True,
    )
    return artifact_bytes


@app.function(
    image=image,
    gpu="A10G",
    volumes={MODEL_CACHE_PATH: model_cache},
    timeout=3300,
    max_containers=1,
    scaledown_window=60,
)
def apply_jacobian_lens(
    model: str,
    model_spec_data,
    audio_bytes: bytes,
    audio_suffix: str,
    artifact_bytes: bytes,
    top_k: int,
    transcript: str | None = None,
    max_new_tokens: int = 64,
):
    """Apply an existing decoder lens on Modal and return JSON-safe output."""
    import io
    import tempfile
    from pathlib import Path

    import torch
    from app.schemas.jobs import RuntimeModelSpec
    from app.services.jacobian_lens_service import apply_decoder_jacobian_lens
    from app.worker.executor import _jsonable
    from app.worker.model_adapters import get_model_adapter
    from app.worker.model_registry import model_registry

    spec = RuntimeModelSpec.model_validate(model_spec_data) if model_spec_data else None
    started_at = time.monotonic()
    print(
        f"ECHO_MODAL jacobian_lens_apply_started model={model} audio_bytes={len(audio_bytes)}",
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="echo-modal-jlens-apply-") as temp_dir:
        suffix = audio_suffix if audio_suffix.startswith(".") and len(audio_suffix) <= 12 else ".wav"
        audio_path = Path(temp_dir) / f"input{suffix}"
        audio_path.write_bytes(audio_bytes)
        artifact = torch.load(io.BytesIO(artifact_bytes), map_location="cpu", weights_only=True)
        adapter = get_model_adapter(model, spec)
        resource = model_registry.prepare(adapter, "jacobian_lens_apply")
        result = apply_decoder_jacobian_lens(
            adapter,
            resource,
            artifact,
            str(audio_path),
            top_k=int(top_k),
            transcript=transcript,
            max_new_tokens=int(max_new_tokens),
        )
    model_cache.commit()
    from app.core.device import detect_inference_runtime

    runtime = detect_inference_runtime().as_dict()
    print(
        f"ECHO_MODAL jacobian_lens_apply_completed model={model} "
        f"device={runtime.get('device')} duration_seconds={time.monotonic() - started_at:.3f}",
        flush=True,
    )
    return json.dumps({"result": _jsonable(result), "runtime": runtime}, ensure_ascii=False)


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
