# ADR 005 — Durable exports with bounded parts and fresh grants

Status: implemented; real object-store/workload-identity qualification pending.

The API creates a durable job; a separate worker reads governed keyset pages and streams Parquet, CSV or JSONL parts to private KMS-encrypted object storage. Jobs carry the full identity and effective-policy fingerprint. Every page reauthorizes; completion is fenced, and downloads require a fresh matching grant.

Parts have checksums, rows and bytes; manifests have expiry. Failed attempts clean their uploaded keys, and bucket lifecycle rules collect abandoned attempts/multipart uploads. Download URLs are short-lived bearer capabilities and must not be logged.

A retry starts a fresh attempt/prefix rather than guessing at a partially written manifest. Keyset reads do not create a cross-page source snapshot. Repeatable exports require a snapshot product. Expired user identity cannot be silently renewed by the worker; long jobs require an approved workload delegation design.
