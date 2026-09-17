"""Independent durable worker entry point; never start workers inside the API."""
import argparse
import json
import signal
from threading import Event

from .durable import migrate
from .durable_exports import ExportWorker
from .identity import MountedSecretProvider
from .ingestion import IngestionWorker
from .jobs import LeaseLost
from .production_app import build_runtime, control_engine
from .search import OpenSearchSink


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['migrate', 'export', 'indexing'])
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.role == 'migrate':
        import os
        from .configuration import configured_secrets
        engine = control_engine(configured_secrets())
        try:
            migrate(engine)
        finally:
            engine.dispose()
        return
    runtime = build_runtime()
    from pathlib import Path
    import tempfile
    ready = Path(tempfile.gettempdir()) / 'edp-ready'
    stopping = Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda *_: stopping.set())
    if args.role == 'export':
        if runtime.object_storage is None:
            runtime.close()
            raise RuntimeError('export worker requires object storage configuration')
        handler = ExportWorker(queue=runtime.queue, service=runtime.service, object_storage=runtime.object_storage,
            limits=runtime.limits)
        kind = 'export'
    else:
        if runtime.search is None or runtime.embedder is None:
            runtime.close()
            raise RuntimeError('indexing worker requires search and embedding configuration')
        handler = IngestionWorker(queue=runtime.queue, catalog=runtime.catalog, chunk_store=runtime.chunks,
            keyword_sink=OpenSearchSink(runtime.search, 'keyword'), vector_sink=OpenSearchSink(runtime.search, 'vector'),
            embedder=runtime.embedder, governor=runtime.service.governor)
        kind = 'ingestion'
    ready.touch()
    try:
        while not stopping.is_set():
            job = runtime.queue.claim(kind)
            if job:
                try:
                    handler.process(job)
                except LeaseLost:
                    runtime.store.audit('worker.lease_lost', job['id'])
                except Exception as exc:
                    try:
                        runtime.queue.fail(job, type(exc).__name__)
                    except LeaseLost:
                        runtime.store.audit('worker.lease_lost', job['id'])
                    print(json.dumps({'event': 'worker.failure', 'job_id': job['id'], 'error_type': type(exc).__name__}), flush=True)
            if args.once:
                break
            if not job:
                stopping.wait(.5)
    finally:
        ready.unlink(missing_ok=True)
        runtime.close()


if __name__ == '__main__':
    main()

