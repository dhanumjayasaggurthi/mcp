"""Durable asynchronous exports: governed pages -> bounded parts -> manifest."""
from __future__ import annotations
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import Protocol
from datetime import datetime, timedelta, timezone

from .backends import ExportBackend
from .context import current_context
from .durable import fingerprint
from .models import Capability, ExportJob, ExportRequest, Principal, StructuredQueryRequest
from .query_validation import validate_projection
from .services import AccessDenied


def owner_scope(principal):
    return fingerprint([principal.subject, principal.client_id, principal.tenant, principal.agent_id])


class ObjectStorage(Protocol):
    def put_file(self, key: str, path: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, seconds: int) -> str: ...


class S3ObjectStorage:
    def __init__(self, client, bucket, kms_key_id):
        if not kms_key_id:
            raise ValueError('export object storage requires a KMS key')
        self.client, self.bucket, self.kms_key_id = client, bucket, kms_key_id

    def put_file(self, key, path):
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(str(path), self.bucket, key,
            ExtraArgs={'ServerSideEncryption': 'aws:kms', 'SSEKMSKeyId': self.kms_key_id},
            Config=TransferConfig(max_concurrency=2, multipart_chunksize=8*1024*1024))

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key, seconds=300):
        if not 1 <= seconds <= 900:
            raise ValueError('download URL lifetime must be 1..900 seconds')
        return self.client.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': key}, ExpiresIn=seconds)


class DurableExportBackend(ExportBackend):
    def __init__(self, queue, object_storage, retention_seconds=86400):
        self.queue, self.objects = queue, object_storage
        if not 300 <= retention_seconds <= 604800:
            raise ValueError('export retention must be 300..604800 seconds')
        self.retention_seconds = retention_seconds

    def submit(self, **kwargs):
        raise AccessDenied('durable exports require the complete governed identity context')

    def submit_governed(self, *, product, principal, request, fields, decision, filter_expr):
        if request.format != 'parquet' and request.compression not in {None, 'none', 'gzip'}:
            raise ValueError('CSV/JSONL support only none or gzip compression')
        context = current_context.get()
        payload = {'dataset_id': product.id, 'dataset_version': product.version, 'principal': {**principal.model_dump(mode='json'), 'groups': sorted(principal.groups)},
            'request': request.model_dump(mode='json'), 'fields': list(fields),
            'decision_hash': fingerprint(decision.model_dump()), 'trace_id': context.trace_id if context else None,
            'retention_seconds': self.retention_seconds}
        job = self.queue.enqueue('export', owner_scope(principal), payload,
            idempotency_key=context.idempotency_key if context else None)
        return self._model(job)

    def _model(self, job):
        payload, result = job['payload'], job.get('result') or {}
        return ExportJob(id=job['id'], dataset_id=payload['dataset_id'], requested_by=payload['principal']['subject'],
            status=job['status'], created_at=job['created_at'], updated_at=job['updated_at'],
            format=payload['request']['format'], row_count=result.get('row_count'), bytes_written=result.get('bytes_written'),
            expires_at=result.get('expires_at'), error=job.get('error'))

    def get_for(self, principal, job_id):
        return self._model(self.queue.get(job_id, scope=owner_scope(principal)))

    def cancel_for(self, principal, job_id):
        return self._model(self.queue.cancel(job_id, scope=owner_scope(principal)))

    def download(self, principal, job_id, service):
        job = self.queue.get(job_id, scope=owner_scope(principal))
        product, decision = service._decision(principal, job['payload']['dataset_id'], Capability.EXPORT)
        if job['status'] != 'succeeded':
            raise ValueError('export is not ready')
        remaining = (datetime.fromisoformat(job['result']['expires_at']) - datetime.now(timezone.utc)).total_seconds()
        if remaining < 1:
            raise AccessDenied('export expired; submit a new export')
        if product.version != job['payload']['dataset_version'] or fingerprint(decision.model_dump()) != job['payload']['decision_hash']:
            raise AccessDenied('export authorization changed; submit a new export')
        if service.guardrails:
            service.guardrails.evaluate(principal=principal, product=product, operation=Capability.EXPORT,
                requested_fields=job['payload']['fields'])
        self.queue.store.audit('export.download', job_id)
        seconds = min(300, int(remaining))
        return {'manifest_url': self.objects.signed_url(job['result']['manifest_key'], seconds),
            'parts': [{'url': self.objects.signed_url(p['key'], seconds), 'sha256': p['sha256'], 'rows': p['rows'], 'bytes': p['bytes']}
                for p in job['result']['parts']], 'expires_in_seconds': seconds}


def arrow_schema(product, fields, masked_fields):
    import pyarrow as pa
    types = {'int': pa.int64(), 'integer': pa.int64(), 'bigint': pa.int64(), 'float': pa.float64(),
        'double': pa.float64(), 'boolean': pa.bool_(), 'bool': pa.bool_(), 'date': pa.date32(),
        'datetime': pa.timestamp('us', tz='UTC'), 'timestamp': pa.timestamp('us', tz='UTC')}
    return pa.schema([pa.field(name, pa.string() if name in masked_fields else
        types.get(product.field_map()[name].data_type.lower(), pa.string())) for name in fields])


class PartWriter:
    def __init__(self, path, format, compression, fields, schema):
        self.path, self.format, self.fields = path, format, fields
        self.schema = schema
        self.rows = 0
        if format == 'parquet':
            import pyarrow.parquet as pq
            self.writer = pq.ParquetWriter(path, schema, compression=None if compression in {None, 'none'} else compression)
            self.stream = None
        else:
            self.stream = gzip.open(path, 'wt', newline='', encoding='utf-8') if compression == 'gzip' else path.open('w', newline='', encoding='utf-8')
            self.writer = csv.DictWriter(self.stream, fieldnames=fields) if format == 'csv' else None
            if self.writer:
                self.writer.writeheader()

    def write(self, rows):
        if self.format == 'parquet':
            import pyarrow as pa
            normalized = []
            for row in rows:
                normalized.append({f.name: (str(row[f.name]) if row.get(f.name) is not None and pa.types.is_string(f.type) else row.get(f.name)) for f in self.schema})
            self.writer.write_table(pa.Table.from_pylist(normalized, schema=self.schema))
        elif self.format == 'csv':
            self.writer.writerows(rows)
        else:
            for row in rows:
                self.stream.write(json.dumps(row, default=str, ensure_ascii=False) + '\n')
        self.rows += len(rows)

    def close(self):
        if self.stream:
            self.stream.close()
        else:
            self.writer.close()


class ExportWorker:
    def __init__(self, *, queue, service, object_storage, limits, part_rows=50000):
        self.queue, self.service, self.objects, self.limits = queue, service, object_storage, limits
        self.part_rows = part_rows

    def process(self, job):
        payload = job['payload']
        principal = Principal.model_validate(payload['principal'])
        request = ExportRequest.model_validate(payload['request'])
        product, decision = self.service._decision(principal, payload['dataset_id'], Capability.EXPORT)
        if product.version != payload['dataset_version'] or fingerprint(decision.model_dump()) != payload['decision_hash']:
            raise AccessDenied('export authorization or dataset changed before execution')
        fields = validate_projection(product, request.select, decision.allowed_fields)
        schema = arrow_schema(product, fields, decision.masked_fields) if request.format == 'parquet' else None
        prefix = 'exports/' + job['scope'] + '/' + job['id'] + '/' + job['token'] + '/'
        keys, parts = [], []
        total_rows = total_bytes = 0
        cursor = None
        writer = None
        try:
            with tempfile.TemporaryDirectory(prefix='edp-export-') as directory:
                part_number = 0
                while True:
                    self.queue.heartbeat(job, {'row_count': total_rows, 'bytes_written': total_bytes})
                    if writer is None:
                        suffix = '.' + request.format + ('.gz' if request.compression == 'gzip' and request.format != 'parquet' else '')
                        path = Path(directory) / ('part-' + str(part_number).zfill(6) + suffix)
                        writer = PartWriter(path, request.format, request.compression, fields, schema)
                    page = self.service.export_page(principal, product.id, StructuredQueryRequest(select=fields,
                        filter=request.filter, limit=min(product.max_limit, 1000), cursor=cursor))
                    if total_rows + len(page.rows) > self.limits.max_export_rows:
                        raise ValueError('export row quota exceeded')
                    # Bound a single row/page before any write.
                    if len(json.dumps(page.rows, default=str).encode()) > self.limits.max_result_bytes:
                        raise ValueError('export page byte budget exceeded')
                    writer.write(page.rows)
                    total_rows += len(page.rows)
                    if writer.rows >= self.part_rows or path.stat().st_size >= 64_000_000 or not page.next_cursor:
                        writer.close(); part_count = writer.rows; writer = None
                        size = path.stat().st_size
                        total_bytes += size
                        if total_bytes > self.limits.max_export_bytes:
                            raise ValueError('export byte quota exceeded')
                        with path.open('rb') as stream:
                            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                        key = prefix + path.name
                        keys.append(key)
                        self.queue.heartbeat(job)
                        self.objects.put_file(key, path)
                        parts.append({'key': key, 'rows': part_count, 'bytes': size, 'sha256': digest})
                        path.unlink()
                        part_number += 1
                        if len(parts) > 10000:
                            raise ValueError('export manifest partition budget exceeded')
                    if not page.next_cursor:
                        break
                    if cursor == page.next_cursor:
                        raise ValueError('export cursor did not advance')
                    cursor = page.next_cursor
                _, final_decision = self.service._decision(principal, product.id, Capability.EXPORT)
                if fingerprint(final_decision.model_dump()) != payload['decision_hash']:
                    raise AccessDenied('export policy changed during execution')
                expires_at = (datetime.now(timezone.utc) + timedelta(seconds=payload.get('retention_seconds', 86400))).isoformat()
                manifest = {'dataset_id': product.id, 'dataset_version': product.version, 'row_count': total_rows, 'expires_at': expires_at,
                    'bytes_written': total_bytes, 'format': request.format, 'parts': parts}
                path = Path(directory) / 'manifest.json'
                path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
                key = prefix + 'manifest.json'; keys.append(key)
                self.queue.heartbeat(job)
                self.objects.put_file(key, path)
                self.queue.complete(job, {**manifest, 'manifest_key': key})
        except Exception:
            if writer:
                writer.close()
            cleanup_failures = 0
            for key in keys:
                try:
                    self.objects.delete(key)
                except Exception:
                    cleanup_failures += 1
            if cleanup_failures:
                self.queue.store.audit('export.cleanup_required', job['id'], {'failed_objects': cleanup_failures})
            raise
