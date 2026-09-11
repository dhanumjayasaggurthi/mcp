# SmartHub MCP & Agentic Gateway control schema migration 001

The executable migration is `enterprise_data_platform.durable.migrate`, invoked by:

```bash
python -m enterprise_data_platform.workers migrate
```

`EDP_CONTROL_DSN_REF` and `EDP_SECRET_DIR` select a mounted PostgreSQL DSN. Use a dedicated DDL owner. The API and worker factories check schema version 1 and do not automatically create or modify tables on startup.

Migration 001 creates versioned objects/history, audit, durable jobs, queue counters, quota windows, leases, record versions, worker checkpoints and canonical chunks. PostgreSQL chunks and record ledgers have 32 hash partitions by dataset. Indexes cover audit time, job claims, leases and record hydration. A PostgreSQL advisory transaction lock serializes concurrent migration attempts. Re-running the migration is tested.

No migration truncates source or legacy data. This is an initial schema, not a converter from legacy SQLite metadata. Import existing registrations through validated admin APIs. Migrations after schema 1 must be ordered, backward-compatible expansion steps followed by a separately reviewed contraction; do not alter this migration to rewrite an already deployed schema.

## Runtime role example

Apply within the dedicated application schema, adapting the role/schema names. This file contains no passwords and does not create privileged accounts.

```sql
GRANT USAGE ON SCHEMA edp TO edp_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA edp TO edp_runtime;
REVOKE UPDATE, DELETE, TRUNCATE ON edp.edp_audit, edp.edp_object_history FROM edp_runtime;
GRANT SELECT, INSERT ON edp.edp_audit, edp.edp_object_history TO edp_runtime;
REVOKE CREATE ON SCHEMA edp FROM edp_runtime;
```

Set both database roles' default `search_path` to the same dedicated schema using an administrator-managed role/database setting, for example `ALTER ROLE edp_runtime IN DATABASE edp_control SET search_path = edp`. The factory supplies connection `options` for mandatory timeouts; do not depend on a DSN `options` override for schema selection. Do not grant table ownership or superuser privileges to `edp_runtime`. Keep public-schema creation privileges under administrative control. Apply equivalent grants to future partitions and new tables in subsequent migrations.

## Recovery

Take and restore a backup before production rollout. Application rollback preserves schema 1, jobs, chunks and audit; it does not execute a destructive down migration. If a migration fails, PostgreSQL rolls back the transaction. If a migration version newer than supported code is present, startup is rejected. Test recovery/failover on the actual HA database; CI verifies SQL contracts on an isolated PostgreSQL instance.
