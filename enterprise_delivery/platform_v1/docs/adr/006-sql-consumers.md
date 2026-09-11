# ADR 006 — Restricted SQL ingress and standard-client integration boundary

Status: governed HTTP ingress implemented; JDBC/Flight SQL frontend not implemented.

`POST /v1/sql/query` accepts `{api_id, sql, cursor}`. SQLGlot parses one SELECT over one unqualified logical alias. Projection, supported literal predicates, ordering and LIMIT become `StructuredQueryRequest`; execution uses the same policy, tenant, field, masking, deadline, admission and audit path as REST/MCP. DDL/DML, joins, CTEs, nested queries, functions, grouping, OFFSET, locking and schema-qualified physical names are rejected. Aggregates are available through the dedicated governed aggregate API.

Do not implement a new database wire protocol. A standard-client deployment should use an established server SDK, for example [Apache Calcite Avatica](https://calcite.apache.org/avatica/docs/) for a JDBC frontend or an existing [Arrow Flight SQL](https://arrow.apache.org/docs/format/FlightSql.html) server implementation. This repository does not claim to provide either endpoint today.

The integration contract is:

1. Authenticate at the standard-client frontend and forward the verified workload/user JWT and trace context in headers to the governed API. Never forward source credentials or trust a client-provided subject override.
2. Resolve metadata through authorized logical discovery/schema endpoints. Expose one logical catalog/schema namespace; do not proxy source catalog metadata.
3. Compile prepared statement values as typed logical predicate values. Do not concatenate bound values into SQL text.
4. Execute supported SELECTs through the SQL ingress and fetch successive pages with its encrypted cursor. Persist statement/ticket ownership in a shared durable store or use short-lived, encrypted, identity-bound tickets.
5. Reauthorize every fetch; reject unsupported SQL before execution. Cancel deadlines/requests and close abandoned statements within bounded resource budgets.
6. Qualify the frontend with actual JDBC/Flight SQL clients, metadata calls, tenant/policy changes, pagination, expiry and unsupported-construct tests before enabling it.

Avatica provides a Java server/driver framework; Flight SQL defines SQL operations over Arrow Flight. Neither framework automatically supplies this platform's policy integration. That frontend implementation and its interoperability tests remain an explicit delivery gate.
