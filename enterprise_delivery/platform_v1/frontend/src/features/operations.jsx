import React, { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity,
  Database,
  Users,
  Bot,
  ArrowUpRight,
  RefreshCw,
  Download,
  Plus,
  ShieldCheck,
  Clock3,
} from "lucide-react";
import { queryOptions, request, control, params, downloadJson } from "../api";
import {
  Button,
  Badge,
  Panel,
  PageTitle,
  Table,
  ErrorBox,
  Loading,
  Empty,
  Field,
  JsonView,
  Modal,
} from "../components/ui";
export function Overview({ monitoring = false }) {
  const q = useQuery({
    ...queryOptions(["dashboard"], `${control}/dashboard`),
    refetchInterval: 30000,
  });
  const d = q.data;
  const metrics = d?.metrics || {};
  return (
    <>
      <PageTitle
        eyebrow={
          monitoring ? "OPERATIONS / MONITORING" : "WORKSPACE / OVERVIEW"
        }
        title={monitoring ? "Every signal. In view." : "Your data. Governed."}
        description={
          monitoring
            ? "Measured execution signals and durable queue state."
            : "A clear view of your data products, access and operations."
        }
      >
        <Button onClick={() => q.refetch()} busy={q.isFetching}>
          <RefreshCw size={16} />
          Refresh
        </Button>
        {!monitoring && (
          <Link className="button primary" to="/datasets/new">
            <Plus size={16} />
            Onboard dataset
          </Link>
        )}
      </PageTitle>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorBox error={q.error} retry={q.refetch} />
      ) : (
        <>
          {d.reference_mode && (
            <div className="issue">Reference runtime · Test data only</div>
          )}
          <div className="metric-grid">
            {[
              {
                key: "active_data_products",
                label: "Active data products",
                icon: Database,
                color: "blue",
                to: "/datasets",
              },
              {
                key: "active_consumers",
                label: "Active consumers",
                icon: Users,
                color: "teal",
                to: "/clients",
              },
              {
                key: "healthy_indexes",
                label: "Healthy indexes",
                icon: Activity,
                color: "purple",
                to: "/indexes",
              },
            ].map(({ key, label, icon: Icon, color, to }) => (
              <Link key={key} to={to} className="metric">
                <div className="metric-top">
                  <span className={`icon-tile ${color}`}>
                    <Icon size={20} />
                  </span>
                  <ArrowUpRight size={16} />
                </div>
                <span>{label}</span>
                <strong>{metrics[key]?.value ?? "Unavailable"}</strong>
                <small>From control storage</small>
              </Link>
            ))}
          </div>
          {monitoring ? (
            <>
              <Panel title="Execution telemetry">
                <div className="panel-body">
                  {d.telemetry?.available ? (
                    <>
                      <p className="muted">
                        {d.telemetry.scope} · Up to {d.telemetry.sample_limit}{" "}
                        recent requests
                      </p>
                      <Table
                        rows={d.telemetry.operations}
                        columns={[
                          { key: "operation", label: "Operation" },
                          { key: "requests", label: "Requests" },
                          { key: "p95_ms", label: "p95 (ms)" },
                          { key: "errors", label: "Errors" },
                          { key: "rows", label: "Rows returned" },
                        ]}
                      />
                    </>
                  ) : (
                    <Empty title="Telemetry unavailable">
                      {d.telemetry?.reason ||
                        "Connect a telemetry reader to show measured latency and error rates."}
                    </Empty>
                  )}
                </div>
              </Panel>
              <QueuePanel jobs={d.jobs || []} />
            </>
          ) : (
            <div className="dashboard-grid">
              <Panel
                title="Latest activity"
                action={
                  <Link to="/audit" className="text-button">
                    Open audit
                    <ArrowUpRight size={14} />
                  </Link>
                }
              >
                <Table
                  rows={d.audit_events || []}
                  columns={[
                    { key: "action", label: "Action" },
                    { key: "resource", label: "Resource" },
                    { key: "actor", label: "Actor" },
                    {
                      key: "time",
                      label: "Time",
                      render: (v) => new Date(v).toLocaleString(),
                    },
                  ]}
                />
              </Panel>
              <Panel title="Operational readiness">
                <div className="readiness-row">
                  <span className="icon-tile teal">
                    <ShieldCheck size={18} />
                  </span>
                  <div>
                    <strong>Service health</strong>
                    <p>
                      <Badge value={d.system_status} />
                    </p>
                  </div>
                </div>
                <div className="readiness-row">
                  <span className="icon-tile purple">
                    <Bot size={18} />
                  </span>
                  <div>
                    <strong>Agent governance</strong>
                    <p>
                      <Link to="/agents">Manage identities & tools</Link>
                    </p>
                  </div>
                </div>
                <div className="readiness-row">
                  <span className="icon-tile blue">
                    <Database size={18} />
                  </span>
                  <div>
                    <strong>Source contracts</strong>
                    <p>
                      <Link to="/sources">Inspect fields & indexes</Link>
                    </p>
                  </div>
                </div>
              </Panel>
            </div>
          )}
          <div className="dashboard-bottom">
            <QueuePanel jobs={d.jobs || []} />
            <Panel title="Deployment">
              <div className="panel-body">
                <h3>
                  {d.environment?.toUpperCase() || "Environment unavailable"}
                </h3>
                <p>Release: {d.deployment?.current || "Not reported"}</p>
                <p className="muted">
                  Updated {new Date(d.generated_at).toLocaleString()}
                </p>
                <Link className="text-button" to="/monitoring">
                  View monitoring
                  <ArrowUpRight size={14} />
                </Link>
              </div>
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
function QueuePanel({ jobs }) {
  return (
    <Panel
      title="Jobs & queues"
      action={
        <Link to="/jobs" className="text-button">
          View jobs
        </Link>
      }
    >
      <Table
        rows={jobs}
        columns={[
          { key: "kind", label: "Kind" },
          {
            key: "status",
            label: "Status",
            render: (v) => <Badge value={v} />,
          },
          { key: "count", label: "Jobs" },
        ]}
      />
    </Panel>
  );
}
export function Audit() {
  const [filters, setFilters] = useState({});
  const [applied, setApplied] = useState({});
  const [cursor, setCursor] = useState(null);
  const q = useQuery(
    queryOptions(
      ["audit", applied, cursor],
      `${control}/audit?${params({ ...applied, cursor })}`,
    ),
  );
  return (
    <>
      <PageTitle
        eyebrow="GOVERNANCE / AUDIT"
        title="A trail you can trust"
        description="Search durable access and configuration events."
      >
        <Button
          disabled={!q.data}
          onClick={() => downloadJson("audit-page.json", q.data)}
        >
          <Download size={16} />
          Export page
        </Button>
      </PageTitle>
      <Panel>
        <form
          className="filter-toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            setCursor(null);
            setApplied(filters);
          }}
        >
          {["actor", "action", "resource", "trace", "since", "until"].map(
            (k) => (
              <Field label={k} key={k}>
                <input
                  type={
                    k === "since" || k === "until" ? "datetime-local" : "text"
                  }
                  value={filters[k] || ""}
                  onChange={(e) =>
                    setFilters({ ...filters, [k]: e.target.value })
                  }
                />
              </Field>
            ),
          )}
          <Button variant="primary">Apply filters</Button>
        </form>
        {q.isPending ? (
          <Loading />
        ) : q.isError ? (
          <ErrorBox error={q.error} retry={q.refetch} />
        ) : (
          <Table
            rows={q.data.events}
            columns={[
              {
                key: "created_at",
                label: "Time",
                render: (v) => new Date(v).toLocaleString(),
              },
              { key: "action", label: "Action" },
              { key: "resource", label: "Resource" },
              { key: "actor", label: "Actor", render: (v) => v.subject },
              { key: "trace_id", label: "Trace" },
              {
                key: "details",
                label: "Details",
                render: (v) => (
                  <details>
                    <summary>Inspect</summary>
                    <JsonView value={v} />
                  </details>
                ),
              },
            ]}
          />
        )}
        <div className="pagination">
          <Button disabled={!cursor} onClick={() => setCursor(null)}>
            Latest
          </Button>
          <Button
            disabled={!q.data?.next_cursor}
            onClick={() => setCursor(q.data.next_cursor)}
          >
            Older events
          </Button>
        </div>
      </Panel>
    </>
  );
}
export function Jobs() {
  const [status, setStatus] = useState("");
  const [cursor, setCursor] = useState(null);
  const [selected, setSelected] = useState(null);
  const q = useQuery({
    ...queryOptions(
      ["jobs", status, cursor],
      `${control}/jobs?${params({ status, cursor })}`,
    ),
    refetchInterval: 15000,
  });
  const replay = useMutation({
    mutationFn: (id) =>
      request(`${control}/jobs/${id}/replay`, { method: "POST" }),
    onSuccess: () => {
      setSelected(null);
      q.refetch();
    },
  });
  return (
    <>
      <PageTitle
        eyebrow="OPERATIONS / JOBS"
        title="Keep work moving"
        description="Inspect durable jobs, failures and worker leases."
      >
        <Button busy={q.isFetching} onClick={() => q.refetch()}>
          <RefreshCw size={16} />
          Refresh
        </Button>
      </PageTitle>
      <Panel>
        <div className="toolbar">
          <Field label="Status">
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setCursor(null);
              }}
            >
              <option value="">All statuses</option>
              {["queued", "running", "failed", "succeeded", "cancelled"].map(
                (x) => (
                  <option key={x}>{x}</option>
                ),
              )}
            </select>
          </Field>
          <Link className="button" to="/playground">
            Create an export
          </Link>
        </div>
        {q.isPending ? (
          <Loading />
        ) : q.isError ? (
          <ErrorBox error={q.error} retry={q.refetch} />
        ) : (
          <Table
            rows={q.data.jobs}
            onOpen={setSelected}
            columns={[
              { key: "id", label: "Job ID" },
              { key: "kind", label: "Kind" },
              {
                key: "status",
                label: "Status",
                render: (v) => <Badge value={v} />,
              },
              { key: "attempts", label: "Attempts" },
              {
                key: "updated_at",
                label: "Updated",
                render: (v) => new Date(v).toLocaleString(),
              },
            ]}
          />
        )}
        <div className="pagination">
          <Button onClick={() => setCursor(null)} disabled={!cursor}>
            Latest
          </Button>
          <Button
            disabled={!q.data?.next_cursor}
            onClick={() => setCursor(q.data.next_cursor)}
          >
            Older jobs
          </Button>
        </div>
      </Panel>
      {selected && (
        <Modal title="Job details" onClose={() => setSelected(null)}>
          <div className="modal-body">
            <JsonView value={selected} />
            <ErrorBox error={replay.error} />
          </div>
          <footer>
            <Button onClick={() => setSelected(null)}>Close</Button>
            {selected.status === "failed" && (
              <Button
                variant="primary"
                busy={replay.isPending}
                onClick={() => replay.mutate(selected.id)}
              >
                Replay failed job
              </Button>
            )}
          </footer>
        </Modal>
      )}
    </>
  );
}
