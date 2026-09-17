import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, Link } from "react-router-dom";
import {
  Plus,
  ArrowLeft,
  ArrowRight,
  Database,
  ShieldCheck,
  Users,
  Bot,
  Layers,
  Workflow,
  SlidersHorizontal,
  Search,
  Save,
  Trash2,
  Download,
  CheckCircle2,
} from "lucide-react";
import { request, control, queryOptions, params, downloadJson } from "../api";
import {
  Button,
  Badge,
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Panel,
  Table,
  Modal,
  SchemaFields,
  defaults,
  Field,
  JsonView,
  FilterBuilder,
} from "../components/ui";
export const resources = {
  sources: {
    title: "Sources",
    singular: "source",
    icon: Database,
    description: "Connect and inspect governed data sources.",
    columns: ["id", "kind", "environment", "max_scan_rows", "revision"],
  },
  datasets: {
    title: "Data products",
    singular: "data product",
    icon: Layers,
    description: "Discoverable contracts. Explicit access. Traceable changes.",
    columns: ["display_name", "status", "version", "capabilities"],
  },
  policies: {
    title: "Policies",
    singular: "policy",
    icon: ShieldCheck,
    description: "Define exactly who can access which data.",
    columns: ["id", "effect", "dataset_patterns", "operations", "revision"],
  },
  clients: {
    title: "Consumers",
    singular: "consumer",
    icon: Users,
    description: "Manage application identity, access and usage limits.",
    columns: ["display_name", "status", "owner", "rate_limit_rps", "revision"],
  },
  agents: {
    title: "Agents & MCP",
    singular: "agent",
    icon: Bot,
    description: "Register agents and govern every tool call.",
    columns: [
      "display_name",
      "status",
      "service_principal",
      "mcp_enabled",
      "revision",
    ],
  },
  guardrails: {
    title: "Guardrails",
    singular: "guardrail",
    icon: SlidersHorizontal,
    description: "Enforce safety and resource budgets.",
    columns: ["name", "kind", "scope", "action", "severity"],
  },
  indexes: {
    title: "Indexes & pipelines",
    singular: "index",
    icon: Workflow,
    description: "Validate, canary and promote retrieval indexes.",
    columns: ["id", "dataset_id", "state", "active_version", "indexed_records"],
  },
  collections: {
    title: "Collections",
    singular: "collection",
    icon: Layers,
    description: "Retrieve across approved datasets with rank-based fusion.",
    columns: ["display_name", "status", "members", "revision"],
  },
};
export function ResourceList({ type }) {
  const nav = useNavigate();
  const cfg = resources[type];
  const [after, setAfter] = useState("");
  const [search, setSearch] = useState("");
  const q = useQuery(
    queryOptions(
      ["list", type, after],
      `${control}/${type}?${params({ after, limit: 50 })}`,
    ),
  );
  const rows = (q.data?.[type] || []).filter((r) =>
    JSON.stringify(r).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        eyebrow="WORKSPACE / MANAGE"
        title={cfg.title}
        description={cfg.description}
      >
        <Button variant="primary" onClick={() => nav(`/${type}/new`)}>
          <Plus size={16} />
          {type === "datasets" ? "Onboard dataset" : `Add ${cfg.singular}`}
        </Button>
      </PageTitle>
      {type === "agents" && <MCPConnection />}
      <Panel>
        <div className="toolbar">
          <div className="search">
            <Search size={17} />
            <input
              aria-label={`Search ${cfg.title}`}
              placeholder="Search this page…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <span className="muted">{q.data?.[type]?.length ?? 0} loaded</span>
        </div>
        {q.isPending ? (
          <Loading />
        ) : q.isError ? (
          <ErrorBox error={q.error} retry={q.refetch} />
        ) : (
          <Table
            columns={cfg.columns.map((k) => ({
              key: k,
              label: k.replaceAll("_", " "),
              render: ["status", "state", "effect", "severity"].includes(k)
                ? (v) => <Badge value={v} />
                : undefined,
            }))}
            rows={rows}
            onOpen={(r) => nav(`/${type}/${encodeURIComponent(r.id)}`)}
          />
        )}
        <div className="pagination">
          <Button disabled={!after} onClick={() => setAfter("")}>
            <ArrowLeft size={14} />
            First page
          </Button>
          <Button
            disabled={!q.data?.next_after}
            onClick={() => setAfter(q.data.next_after)}
          >
            Next page
            <ArrowRight size={14} />
          </Button>
        </div>
      </Panel>
    </>
  );
}
export function ResourceRoute({ type }) {
  const { id } = useParams();
  const forms = useQuery(queryOptions(["forms"], `${control}/forms`));
  const item = useQuery(
    queryOptions(
      ["item", type, id],
      `${control}/${type}/${encodeURIComponent(id)}`,
      id !== "new",
    ),
  );
  if (forms.isPending || (id !== "new" && item.isPending)) return <Loading />;
  if (forms.isError || item.isError)
    return (
      <ErrorBox
        error={forms.error || item.error}
        retry={() => {
          forms.refetch();
          item.refetch();
        }}
      />
    );
  return (
    <ResourceEditor
      key={`${type}-${id}-${item.data?.revision || item.data?.version || 0}`}
      type={type}
      initial={item.data || defaults(forms.data[type])}
      schema={forms.data[type]}
      creating={id === "new"}
    />
  );
}
export function ResourceEditor({ type, initial, schema, creating = false }) {
  const cfg = resources[type];
  const nav = useNavigate();
  const cache = useQueryClient();
  const [value, setValue] = useState(initial);
  const [tab, setTab] = useState("contract");
  const [review, setReview] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState(null);
  const [notice, setNotice] = useState("");
  const [operationError, setOperationError] = useState(null);
  const [operationBusy, setOperationBusy] = useState(false);
  const change = (v) => {
    setValue(v);
    setResult(null);
    setNotice("");
  };
  const mutate = useMutation({
    mutationFn: ({ remove = false } = {}) =>
      request(
        `${control}/${type}/${encodeURIComponent(value.id)}?${params(type === "datasets" ? { expected_version: creating ? null : initial.version } : remove ? { expected_revision: initial.revision } : {})}`,
        { method: remove ? "DELETE" : "PUT", body: remove ? undefined : value },
      ),
    onSuccess: (saved) => {
      cache.invalidateQueries();
      setReview(false);
      setNotice("Saved successfully");
      if (saved) {
        setValue(saved);
        nav(`/${type}/${encodeURIComponent(saved.id)}`);
      } else nav(`/${type}`);
    },
  });
  async function operation(name) {
    setOperationBusy(true);
    setOperationError(null);
    try {
      setResult(
        await request(`${control}/onboarding/${name}`, {
          method: "POST",
          body: value,
        }),
      );
    } catch (e) {
      setOperationError(e);
    } finally {
      setOperationBusy(false);
    }
  }
  const keys =
    type === "datasets"
      ? Object.fromEntries(
          Object.entries(schema.properties).filter(
            ([k]) => !["fields", "retrieval", "mandatory_filter"].includes(k),
          ),
        )
      : schema.properties;
  const tabs =
    type === "datasets"
      ? ["contract", "fields", "retrieval", "validation", "access", "history"]
      : [
          "contract",
          ...(type === "sources" ? ["objects"] : []),
          ...(type === "indexes" ? ["promotion"] : []),
          ...(!creating ? ["history"] : []),
        ];
  return (
    <>
      <Link className="back-link" to={`/${type}`}>
        <ArrowLeft size={15} />
        {cfg.title}
      </Link>
      <PageTitle
        eyebrow={creating ? "NEW REGISTRATION" : "RESOURCE DETAILS"}
        title={
          creating
            ? `New ${cfg.singular}`
            : value.display_name || value.name || value.id
        }
        description={
          creating
            ? "Configure, review and save."
            : `ID ${initial.id} · ${type === "datasets" ? `Version ${initial.version}` : `Revision ${initial.revision ?? 0}`}`
        }
      >
        <Badge value={value.status || value.state || "configuration"} />
      </PageTitle>
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t}
            className={tab === t ? "selected" : ""}
            onClick={() => setTab(t)}
          >
            {t.replaceAll("_", " ")}
          </button>
        ))}
      </div>
      {notice && (
        <div className="success" role="status">
          <CheckCircle2 size={18} />
          {notice}
        </div>
      )}
      <ErrorBox error={mutate.error || operationError} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setReview(true);
        }}
      >
        <Panel>
          <div className="panel-body">
            {tab === "contract" && (
              <SchemaFields
                schema={{ ...schema, properties: keys }}
                root={schema}
                value={value}
                onChange={change}
                locked={creating ? [] : ["id"]}
                fields={value.fields}
              />
            )}
            {tab === "fields" && (
              <>
                <FieldTable
                  fields={value.fields || []}
                  onChange={(fields) => change({ ...value, fields })}
                />
                <h3>Mandatory row filter</h3>
                <FilterBuilder
                  value={value.mandatory_filter}
                  fields={value.fields}
                  onChange={(mandatory_filter) =>
                    change({ ...value, mandatory_filter })
                  }
                />
              </>
            )}
            {tab === "retrieval" && (
              <SchemaFields
                schema={{
                  ...schema,
                  properties: { retrieval: schema.properties.retrieval },
                }}
                root={schema}
                value={value}
                onChange={change}
                fields={value.fields}
              />
            )}
            {tab === "validation" && (
              <>
                <h2>Validate against the physical source</h2>
                <p>
                  Activation runs the same server-side checks. Index DDL is
                  generated for review and is never executed here.
                </p>
                <div className="actions">
                  <Button
                    type="button"
                    busy={operationBusy}
                    onClick={() => operation("validate")}
                  >
                    <ShieldCheck size={16} />
                    Validate binding
                  </Button>
                  <Button
                    type="button"
                    busy={operationBusy}
                    disabled={value.retrieval?.backend !== "postgres"}
                    onClick={() => operation("index-plan")}
                  >
                    Generate index plan
                  </Button>
                </div>
                {result && (
                  <>
                    <Badge
                      value={
                        result.valid === undefined
                          ? "review required"
                          : result.valid
                            ? "valid"
                            : "invalid"
                      }
                    />
                    {result.issues?.map((x) => (
                      <div className="issue" key={x}>
                        {x}
                      </div>
                    ))}
                    {result.notes?.map((x) => (
                      <p key={x}>{x}</p>
                    ))}
                    {result.statements && (
                      <JsonView value={result.statements} />
                    )}
                    <details>
                      <summary>Inspection details</summary>
                      <JsonView value={result} />
                    </details>
                  </>
                )}
              </>
            )}
            {tab === "access" && <AccessSimulator datasetId={value.id} />}{" "}
            {tab === "history" && <History type={type} id={initial.id} />}{" "}
            {tab === "objects" && <SourceObjects sourceId={initial.id} />}{" "}
            {tab === "promotion" && <Promotion id={initial.id} />}
          </div>
        </Panel>
        {["contract", "fields", "retrieval"].includes(tab) && (
          <div className="save-bar">
            <span className="muted">
              {type === "datasets" && !creating
                ? "Set a new version before saving."
                : "Changes require review before saving."}
            </span>
            <div className="actions">
              {!creating && (
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => setDeleting(true)}
                >
                  <Trash2 size={15} />
                  Delete
                </Button>
              )}
              <Button type="submit" variant="primary">
                <Save size={16} />
                Review changes
              </Button>
            </div>
          </div>
        )}
      </form>
      {review && (
        <Modal
          title="Review configuration changes"
          onClose={() => setReview(false)}
          wide
        >
          <div className="modal-body">
            <div className="diff">
              <Panel title="Current">
                <JsonView value={creating ? null : initial} />
              </Panel>
              <Panel title="Proposed">
                <JsonView value={value} />
              </Panel>
            </div>
            <ErrorBox error={mutate.error} />
          </div>
          <footer>
            <Button onClick={() => setReview(false)}>Back to editing</Button>
            <Button
              variant="primary"
              busy={mutate.isPending}
              onClick={() => mutate.mutate({})}
            >
              Save configuration
            </Button>
          </footer>
        </Modal>
      )}
      {deleting && (
        <Modal
          title={`Delete ${cfg.singular}`}
          onClose={() => setDeleting(false)}
        >
          <div className="modal-body">
            <p>
              Type <strong>{initial.id}</strong> to confirm deletion.
            </p>
            <Field label="Resource ID">
              <input value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
            <ErrorBox error={mutate.error} />
          </div>
          <footer>
            <Button onClick={() => setDeleting(false)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={typed !== initial.id}
              busy={mutate.isPending}
              onClick={() => mutate.mutate({ remove: true })}
            >
              Delete resource
            </Button>
          </footer>
        </Modal>
      )}
    </>
  );
}
function FieldTable({ fields, onChange }) {
  const update = (i, k, v) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, [k]: v } : f)));
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Field</th>
            <th>Type</th>
            {["selectable", "filterable", "sortable", "sensitive"].map((k) => (
              <th key={k}>{k}</th>
            ))}
            <th>Policy</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f, i) => (
            <tr key={f.name}>
              <td>
                <strong>{f.name}</strong>
                <small>{f.nullable ? "Nullable" : "Not null"}</small>
              </td>
              <td>{f.data_type}</td>
              {["selectable", "filterable", "sortable", "sensitive"].map(
                (k) => (
                  <td key={k}>
                    <input
                      aria-label={`${f.name} ${k}`}
                      type="checkbox"
                      checked={f[k] || false}
                      onChange={(e) => update(i, k, e.target.checked)}
                    />
                  </td>
                ),
              )}
              <td>
                <select
                  aria-label={`${f.name} policy`}
                  value={f.default_policy}
                  onChange={(e) => update(i, "default_policy", e.target.value)}
                >
                  {["visible", "masked", "hidden"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function AccessSimulator({ datasetId = "" }) {
  const [dataset, setDataset] = useState(datasetId);
  const [subject, setSubject] = useState("");
  const [client, setClient] = useState("");
  const [tenant, setTenant] = useState("");
  const [groups, setGroups] = useState("");
  const [scopes, setScopes] = useState("");
  const [operation, setOperation] = useState("retrieve");
  const m = useMutation({
    mutationFn: () =>
      request(`${control}/simulate`, {
        method: "POST",
        body: {
          dataset_id: dataset,
          operation,
          principal: {
            subject,
            client_id: client || null,
            tenant: tenant || null,
            groups: groups
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
            attributes: { oauth_scope: scopes },
          },
        },
      }),
  });
  return (
    <div>
      <div className="form-grid">
        <Field label="Dataset ID">
          <input value={dataset} onChange={(e) => setDataset(e.target.value)} />
        </Field>
        <Field label="Operation">
          <select
            value={operation}
            onChange={(e) => setOperation(e.target.value)}
          >
            {[
              "retrieve",
              "query",
              "keyword",
              "vector",
              "hybrid",
              "export",
              "discover",
            ].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Field>
        <Field label="Subject">
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </Field>
        <Field label="Client ID">
          <input value={client} onChange={(e) => setClient(e.target.value)} />
        </Field>
        <Field label="Tenant">
          <input value={tenant} onChange={(e) => setTenant(e.target.value)} />
        </Field>
        <Field label="Groups (comma separated)">
          <input value={groups} onChange={(e) => setGroups(e.target.value)} />
        </Field>
        <Field label="OAuth scopes (space separated)">
          <input
            value={scopes}
            onChange={(e) => setScopes(e.target.value)}
            placeholder="edp:retrieve"
          />
        </Field>
      </div>
      <Button
        type="button"
        variant="primary"
        busy={m.isPending}
        disabled={!subject || !dataset}
        onClick={() => m.mutate()}
      >
        <ShieldCheck size={16} />
        Simulate access
      </Button>
      <ErrorBox error={m.error} />
      {m.data && (
        <div className="simulation-result" aria-live="polite">
          <Badge value={m.data.allowed ? "allowed" : "denied"} />
          <p>{m.data.reason}</p>
          <JsonView
            value={{
              fields: m.data.allowed_fields,
              masked: m.data.masked_fields,
              filter: m.data.mandatory_filter,
              limits: { rows: m.data.max_limit, top_k: m.data.max_top_k },
              trace: m.data.trace_id,
            }}
          />
        </div>
      )}
    </div>
  );
}
function History({ type, id }) {
  const [before, setBefore] = useState(null);
  const q = useQuery(
    queryOptions(
      ["history", type, id, before],
      `${control}/history/${type}/${encodeURIComponent(id)}?${params({ before })}`,
    ),
  );
  return q.isPending ? (
    <Loading />
  ) : q.isError ? (
    <ErrorBox error={q.error} retry={q.refetch} />
  ) : (
    <>
      <Table
        columns={[
          { key: "revision", label: "Revision" },
          { key: "created_at", label: "Saved at" },
          { key: "deleted", label: "Deleted" },
          {
            key: "payload",
            label: "Configuration",
            render: (v) => (
              <details>
                <summary>Inspect revision</summary>
                <JsonView value={v} />
              </details>
            ),
          },
        ]}
        rows={q.data.history}
      />
      <Button
        type="button"
        disabled={!q.data.next_before}
        onClick={() => setBefore(q.data.next_before)}
      >
        Older revisions
      </Button>
    </>
  );
}
export function SourceObjects({ sourceId, onSelect }) {
  const [schema, setSchema] = useState("");
  const [applied, setApplied] = useState("");
  const [inspected, setInspected] = useState(null);
  const q = useQuery(
    queryOptions(
      ["objects", sourceId, applied],
      `${control}/sources/${encodeURIComponent(sourceId)}/objects?${params({ schema_name: applied })}`,
    ),
  );
  const m = useMutation({
    mutationFn: (row) =>
      request(`${control}/sources/${encodeURIComponent(sourceId)}/inspect`, {
        method: "POST",
        body: { schema_name: row.schema_name, object_name: row.object_name },
      }),
    onSuccess: setInspected,
  });
  return (
    <>
      <div className="toolbar">
        <Field label="Schema">
          <input
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
            placeholder="Default source schema"
          />
        </Field>
        <Button type="button" onClick={() => setApplied(schema)}>
          Browse objects
        </Button>
      </div>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorBox error={q.error} retry={q.refetch} />
      ) : (
        <Table
          columns={[
            {
              key: "object_name",
              label: "Object",
              render: (v, r) => (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => (onSelect ? onSelect(r) : m.mutate(r))}
                >
                  {v}
                </button>
              ),
            },
            { key: "schema_name", label: "Schema" },
            { key: "kind", label: "Kind" },
          ]}
          rows={q.data.objects}
        />
      )}{" "}
      {q.data?.truncated && (
        <p className="issue">
          Object list reached the source limit. Narrow the schema.
        </p>
      )}
      <ErrorBox error={m.error} />
      {m.isPending && <Loading />}
      {inspected && (
        <>
          <h3>Columns</h3>
          <Table
            columns={["name", "data_type", "nullable"].map((k) => ({
              key: k,
              label: k.replaceAll("_", " "),
            }))}
            rows={inspected.columns}
          />
          <h3>Indexes</h3>
          <JsonView value={inspected.indexes} />
          <p>pgvector: {inspected.pgvector_version || "Not installed"}</p>
        </>
      )}
    </>
  );
}
function MCPConnection() {
  const q = useQuery(queryOptions(["session"], `${control}/session`));
  return (
    <Panel title="Connect an agent">
      <div className="panel-body">
        <p>
          {q.data?.mcp_endpoint
            ? "Authenticated Streamable HTTP endpoint"
            : "MCP transport is unavailable in this runtime."}
        </p>
        {q.data?.mcp_endpoint && (
          <code>
            {
              new URL(
                q.data.mcp_endpoint,
                import.meta.env.VITE_API_BASE_URL || location.origin,
              ).href
            }
          </code>
        )}
        <p className="muted">
          Use an environment-issued bearer token with an agent_id claim matching
          an active registration. Dataset permissions and tool capabilities are
          enforced on every call.
        </p>
      </div>
    </Panel>
  );
}
function Promotion({ id }) {
  const [percent, setPercent] = useState(5);
  const [evidence, setEvidence] = useState({});

  const m = useMutation({
    mutationFn: ({ action, body }) =>
      request(`${control}/indexes/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        body,
      }),
  });
  return (
    <>
      <h3>Candidate lifecycle</h3>
      <p>Enter measured evaluation results. All six values are required.</p>
      <div className="form-grid">
        {[
          ["evaluated_queries", "Evaluated queries", undefined, 1],
          ["recall_at_k", "Recall at K", 1, 0.001],
          ["precision_at_k", "Precision at K", 1, 0.001],
          ["citation_coverage", "Citation coverage", 1, 0.001],
          ["p95_latency_ms", "P95 latency (ms)", undefined, 0.1],
          ["error_rate", "Error rate", 1, 0.0001],
        ].map(([key, label, max, step]) => (
          <Field key={key} label={label}>
            <input
              type="number"
              min="0"
              max={max}
              step={step}
              value={evidence[key] ?? ""}
              onChange={(e) =>
                setEvidence({
                  ...evidence,
                  [key]:
                    e.target.value === "" ? undefined : Number(e.target.value),
                })
              }
            />
          </Field>
        ))}
      </div>
      <div className="actions">
        <Button
          type="button"
          busy={m.isPending}
          disabled={
            Object.values(evidence).filter(Number.isFinite).length !== 6
          }
          onClick={() => m.mutate({ action: "validate", body: evidence })}
        >
          Validate candidate
        </Button>
        <Field label="Canary traffic %">
          <input
            type="number"
            min="0"
            max="100"
            value={percent}
            onChange={(e) => setPercent(Number(e.target.value))}
          />
        </Field>
        <Button
          type="button"
          busy={m.isPending}
          onClick={() => m.mutate({ action: "canary", body: { percent } })}
        >
          Set canary
        </Button>
        <Button
          type="button"
          busy={m.isPending}
          onClick={() => m.mutate({ action: "promote" })}
        >
          Promote
        </Button>
        <Button
          type="button"
          busy={m.isPending}
          onClick={() => m.mutate({ action: "rollback-canary" })}
        >
          Rollback canary
        </Button>
      </div>
      <ErrorBox error={m.error} />
      {m.data && <JsonView value={m.data} />}
    </>
  );
}
