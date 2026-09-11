# ADR 003 — Shared admission, disposable pools and bounded deadlines

Status: implemented; environment failure drills pending.

Use PostgreSQL quota rows and expiring leases to coordinate global, tenant, client, principal/agent, source and workload limits. Acquire locks in a fixed order, reject overload immediately and reserve capacity for interactive work. Indexing/export workers run separately from API processes.

Request context carries a trace, deadline, cancellation flag and idempotency key. Pools have no overflow; retries are bounded and respect remaining time. Circuit breakers and caches are local optimizations whose loss does not alter authorization correctness. API threadpool work uses bounded synchronous drivers; async is reserved for request/network orchestration where useful.

This adds database writes to admission and can reduce small-query throughput. Driver timeouts are essential because cancellation is cooperative; arbitrary native drivers cannot be forcibly interrupted by a Python flag. There is no blanket claim of distributed fairness, universal physical scan accounting or per-tenant network bandwidth enforcement.
