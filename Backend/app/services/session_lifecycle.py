"""End one browser/login session and purge the resources owned by that SID."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from app.core import redis as redis_module
from app.core.storage import get_storage

logger = logging.getLogger(__name__)
_SID = re.compile(r"\A[0-9a-f]{32}\Z")


async def _indexed_values(key: str, *, sorted_set: bool = False) -> list[str]:
    client = redis_module.job_redis
    return await (client.zrange(key, 0, -1) if sorted_set else client.smembers(key))


async def end_session(sid: str) -> None:
    """Cancel work and remove session-owned Redis, object-store and disk data."""
    if not _SID.fullmatch(sid):
        raise ValueError("Invalid session identifier")

    job_client = redis_module.job_redis
    audio_ids = await _indexed_values(f"session:{sid}:audio")
    job_ids = await _indexed_values(f"session:{sid}:jobs", sorted_set=True)
    model_ids = await _indexed_values(f"session:{sid}:custom-models", sorted_set=True)
    lens_ids = await _indexed_values(f"session:{sid}:jacobian-lenses", sorted_set=True)

    audio_raws = await job_client.mget([f"audio:{item_id}" for item_id in audio_ids]) if audio_ids else []
    job_raws = await job_client.mget([f"job:{job_id}" for job_id in job_ids]) if job_ids else []
    model_raws = await job_client.mget([f"custom-model:{model_id}" for model_id in model_ids]) if model_ids else []
    lens_raws = await job_client.mget([f"jacobian-lens:{lens_id}" for lens_id in lens_ids]) if lens_ids else []

    def records(raws: list[str | None]) -> list[dict]:
        result = []
        for raw in raws:
            try:
                value = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                value = None
            if isinstance(value, dict):
                result.append(value)
        return result

    audio, jobs, models, lenses = map(records, (audio_raws, job_raws, model_raws, lens_raws))
    task_ids = [record.get("task_id") for record in jobs + models]
    for record in jobs:
        task_ids.extend(record.get("child_task_ids") or [])
    from app.core.celery_app import revoke_async

    try:
        await revoke_async(task_ids)
    except Exception:
        # Privacy cleanup must continue if the broker is down; deleted job
        # records also cause queued workers to treat the work as obsolete.
        logger.warning("Could not revoke all work while ending session %s", sid)

    # Remove exact keys referenced by records and all known per-session object
    # namespaces. Shared content-addressed analysis caches are intentionally
    # outside these prefixes and contain no user-uploaded source files.
    storage = get_storage()
    exact_keys = [
        value
        for record in audio + jobs + lenses
        for value in (record.get("object_key"), record.get("result_key"),
                      record.get("artifact_key"), record.get("metadata_key"))
        if isinstance(value, str) and value
    ]
    def delete_stored_objects() -> None:
        for key in exact_keys:
            storage.delete(key)
        for prefix in (
            f"uploads/{sid}", f"generated/{sid}", f"results/{sid}",
            f"fairness/{sid}", f"jacobian-lenses/{sid}",
        ):
            storage.delete_prefix(prefix)

    await asyncio.to_thread(delete_stored_objects)

    from app.services.custom_dataset_service import cleanup_session_datasets

    if not await asyncio.to_thread(cleanup_session_datasets, sid):
        raise RuntimeError("Could not remove this session's custom datasets")

    # Records and per-job counters can outlive the two session index keys.
    keys_to_delete = [
        f"session:{sid}:audio", f"session:{sid}:jobs",
        f"session:{sid}:custom-models", f"session:{sid}:jacobian-lenses",
    ]
    for job_id in job_ids:
        keys_to_delete.extend([key async for key in job_client.scan_iter(match=f"job:{job_id}:*")])
    keys_to_delete.extend(f"audio:{item_id}" for item_id in audio_ids)
    keys_to_delete.extend(f"job:{job_id}" for job_id in job_ids)
    keys_to_delete.extend(f"custom-model:{model_id}" for model_id in model_ids)
    keys_to_delete.extend(f"jacobian-lens:{lens_id}" for lens_id in lens_ids)
    if keys_to_delete:
        await job_client.delete(*set(keys_to_delete))

    session_keys = [key async for key in redis_module.redis.scan_iter(match=f"sess:{sid}*")]
    session_keys.extend([f"auth-session:{sid}"])
    if session_keys:
        await redis_module.redis.delete(*session_keys)
    logger.info("Ended and purged ECHO session %s", sid)
