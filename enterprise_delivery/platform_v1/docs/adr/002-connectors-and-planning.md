# ADR 002 — Capability-specific pushdown and governed planning

Status: implemented for documented strategies; advanced federation remains an extension.

Keep source-specific connection and SQL behavior in connector factories/adapters. The shared service combines current authorization with the caller's filter before any backend executes it. SQLAlchemy compiles bound predicates/projections and compound NULL-safe keysets. PostgreSQL EXPLAIN provides advisory cost/scan estimates; unknown-cost systems require an explicitly qualified native governor.

The planner records capability-compatible source/retrieval/export stages and may make the configured deadline fallback for hybrid retrieval. It does not silently scan an entire source when an accelerator fails. Counts and aggregates require dedicated permissions. SQL result streaming is bounded to a page; no query path sorts a whole production dataset in Python.

General joins, federation, parallel partition execution and materialized/result-cache acceleration are not implemented. Their enum names reserve extension vocabulary and must not be advertised as shipped functionality. Driver differences remain visible in capabilities and qualification tests.
