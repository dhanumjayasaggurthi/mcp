from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Sequence

from .backends import ExportBackend
from .models import DataProduct, ExportJob, ExportStatus


class ExportJobNotFound(KeyError):
    pass


class ExportStateError(RuntimeError):
    pass


class QueuedExportBackend(ExportBackend):
    """Asynchronous export control-plane backend.

    Submission stores only job metadata and enqueues a compact work descriptor;
    it never materializes source rows in the API process. Production should
    replace the in-memory metadata map with durable SQL and the callback with a
    durable queue (Kafka/SQS/Service Bus/etc.).
    """

    def __init__(self, enqueue: Callable[[str, Dict[str, Any]], None]) -> None:
        self._enqueue = enqueue
        self._jobs: Dict[str, ExportJob] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        *,
        product: DataProduct,
        principal_subject: str,
        fields: Sequence[str],
        filter_expr: Optional[Dict[str, Any]],
        format: str,
        compression: Optional[str],
    ) -> ExportJob:
        now = datetime.now(timezone.utc).isoformat()
        job = ExportJob(
            id=str(uuid.uuid4()),
            dataset_id=product.id,
            requested_by=principal_subject,
            status=ExportStatus.QUEUED,
            created_at=now,
            updated_at=now,
            format=format,
        )
        payload = {
            "dataset_id": product.id,
            "dataset_version": product.version,
            "fields": list(fields),
            "filter": filter_expr,
            "format": format,
            "compression": compression,
            "requested_by": principal_subject,
        }
        with self._lock:
            self._jobs[job.id] = job
        try:
            self._enqueue(job.id, payload)
        except Exception:
            with self._lock:
                self._jobs.pop(job.id, None)
            raise
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> ExportJob:
        with self._lock:
            if job_id not in self._jobs:
                raise ExportJobNotFound(job_id)
            return self._jobs[job_id].model_copy(deep=True)

    def cancel(self, job_id: str, *, requested_by: Optional[str] = None) -> ExportJob:
        with self._lock:
            job = self._get_mutable(job_id)
            if requested_by and job.requested_by != requested_by:
                raise PermissionError("export job belongs to another principal")
            if job.status in {ExportStatus.SUCCEEDED, ExportStatus.FAILED, ExportStatus.CANCELLED}:
                raise ExportStateError(f"cannot cancel job in state {job.status.value}")
            updated = job.model_copy(update={"status": ExportStatus.CANCELLED, "updated_at": self._now()})
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def mark_running(self, job_id: str) -> ExportJob:
        return self._transition(job_id, {ExportStatus.QUEUED}, ExportStatus.RUNNING)

    def mark_succeeded(
        self,
        job_id: str,
        *,
        row_count: int,
        bytes_written: int,
        download_url: str,
        expires_at: str,
    ) -> ExportJob:
        with self._lock:
            job = self._get_mutable(job_id)
            if job.status != ExportStatus.RUNNING:
                raise ExportStateError(f"job must be running; current={job.status.value}")
            updated = job.model_copy(
                update={
                    "status": ExportStatus.SUCCEEDED,
                    "updated_at": self._now(),
                    "row_count": row_count,
                    "bytes_written": bytes_written,
                    "download_url": download_url,
                    "expires_at": expires_at,
                }
            )
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def mark_failed(self, job_id: str, error: str) -> ExportJob:
        with self._lock:
            job = self._get_mutable(job_id)
            if job.status not in {ExportStatus.QUEUED, ExportStatus.RUNNING}:
                raise ExportStateError(f"cannot fail job in state {job.status.value}")
            updated = job.model_copy(update={"status": ExportStatus.FAILED, "updated_at": self._now(), "error": error[:2000]})
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def _transition(self, job_id: str, from_states: set[ExportStatus], to_state: ExportStatus) -> ExportJob:
        with self._lock:
            job = self._get_mutable(job_id)
            if job.status not in from_states:
                raise ExportStateError(f"invalid transition {job.status.value} -> {to_state.value}")
            updated = job.model_copy(update={"status": to_state, "updated_at": self._now()})
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)

    def _get_mutable(self, job_id: str) -> ExportJob:
        if job_id not in self._jobs:
            raise ExportJobNotFound(job_id)
        return self._jobs[job_id]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
