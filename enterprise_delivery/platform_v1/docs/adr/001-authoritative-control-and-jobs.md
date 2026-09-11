# ADR 001 — PostgreSQL authority and fenced jobs

Status: implemented; deployed HA qualification pending.

The reference dictionaries, locks and job descriptors cannot coordinate independent API replicas. Store control objects, revisions, history, audit, queue state and checkpoints transactionally in PostgreSQL. Keep schema migration separate from runtime startup. Use optimistic revisions for edits, semantic Data Product versions, bounded job admission, leased claims and fencing tokens.

Canonical chunks and record ledgers share the SQL transaction with job/checkpoint completion so partial external sink writes cannot make uncommitted text authoritative. Audit/history triggers and application grants prevent ordinary runtime mutation; an immutable archive remains an infrastructure responsibility.

The cost is a control-database dependency and queue-counter contention. SQL admission fails closed during an outage. This implementation has real PostgreSQL concurrency tests, but neither a single-node CI database nor 32 hash partitions proves billion-chunk throughput. A different distributed chunk/queue implementation must preserve the same consistency contract.
