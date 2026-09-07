import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from './apiClient';

const NAV = [
  ['overview', 'Overview'],
  ['datasets', 'Data Products'],
  ['policies', 'Policies'],
  ['clients', 'Consumers'],
  ['agents', 'Agents'],
  ['guardrails', 'Guardrails'],
  ['indexes', 'Indexes & Pipelines'],
  ['mcp', 'MCP'],
  ['monitoring', 'Monitoring'],
];

const RESOURCE_ENDPOINT = {
  datasets: '/v1/control/datasets',
  policies: '/v1/control/policies',
  clients: '/v1/control/clients',
  agents: '/v1/control/agents',
  guardrails: '/v1/control/guardrails',
  indexes: '/v1/control/indexes',
};

const RESOURCE_ID = {
  datasets: 'id', policies: 'id', clients: 'id', agents: 'id', guardrails: 'id', indexes: 'id',
};

function Icon({ name, className = 'w-4 h-4' }) {
  const common = { className, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' };
  const paths = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    datasets: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></>,
    policies: <><path d="M12 3l7 3v5c0 4.5-2.7 8-7 10-4.3-2-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/></>,
    clients: <><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.7-3.5 2.5-5 5.5-5s4.8 1.5 5.5 5"/><path d="M16 8h5M18.5 5.5V10.5"/></>,
    agents: <><rect x="5" y="6" width="14" height="12" rx="3"/><path d="M9 11h.01M15 11h.01M9 15h6M12 3v3"/></>,
    guardrails: <><path d="M5 20V8l7-4 7 4v12"/><path d="M9 20v-5h6v5M8 10h8"/></>,
    indexes: <><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="7" cy="6" r="1"/><circle cx="17" cy="12" r="1"/><circle cx="10" cy="18" r="1"/></>,
    mcp: <><path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 4l-4 16"/></>,
    monitoring: <><path d="M3 12h4l2-6 4 12 2-6h6"/></>,
  };
  return <svg {...common}>{paths[name] || paths.overview}</svg>;
}

function StatusPill({ value }) {
  const v = String(value || 'unknown').toLowerCase();
  const good = ['active', 'healthy', 'succeeded'].includes(v);
  const bad = ['failed', 'down', 'disabled'].includes(v);
  const cls = good
    ? 'bg-jj-green-03/10 text-jj-green-03 border-jj-green-03/20'
    : bad
      ? 'bg-jj-maroon-05/10 text-jj-maroon-05 border-jj-maroon-05/20'
      : 'bg-jj-yellow-01/30 text-jj-gray-08 border-jj-yellow-01/50';
  return <span className={`inline-flex px-2 py-0.5 rounded-full border text-[10px] font-johnson-text font-semibold uppercase tracking-wide ${cls}`}>{v}</span>;
}

function MetricCard({ label, value, hint, alert = false }) {
  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl p-4 shadow-sm relative overflow-hidden">
      <div className={`absolute left-0 top-0 bottom-0 w-1 ${alert ? 'bg-jj-maroon-05' : 'bg-jj-red'}`} />
      <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05">{label}</p>
      <div className="mt-1 flex items-end gap-2">
        <p className="font-johnson-display text-2xl text-jj-gray-08 leading-none">{value ?? '—'}</p>
        {hint && <span className="font-johnson-text text-[11px] text-jj-gray-05 mb-0.5">{hint}</span>}
      </div>
    </div>
  );
}

async function readJson(res) {
  return res.json().catch(() => ({}));
}

function Overview({ onOpenSection }) {
  const [state, setState] = useState({ loading: true, overview: null, indexes: [], guardrails: [], error: '' });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const [o, i, g] = await Promise.all([
        apiFetch('/v1/control/overview'),
        apiFetch('/v1/control/indexes'),
        apiFetch('/v1/control/guardrails'),
      ]);
      if (!o.ok || !i.ok || !g.ok) throw new Error('Control plane returned an error');
      const [overview, indexesPayload, guardrailsPayload] = await Promise.all([o.json(), i.json(), g.json()]);
      setState({ loading: false, overview, indexes: indexesPayload.indexes || [], guardrails: guardrailsPayload.guardrails || [], error: '' });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: e.message }));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (state.loading) return <PanelSkeleton />;
  if (state.error) return <ErrorCard message={state.error} retry={load} />;
  const o = state.overview || {};
  const risky = state.guardrails.filter((g) => g.enabled && ['high', 'critical'].includes(g.severity));

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        <MetricCard label="Data products" value={o.datasets} hint="governed" />
        <MetricCard label="Consumers" value={o.active_clients} hint={`${o.clients || 0} registered`} />
        <MetricCard label="Agents" value={o.active_agents} hint={`${o.agents || 0} registered`} />
        <MetricCard label="Index health" value={o.healthy_indexes} hint={`${o.degraded_indexes || 0} degraded`} alert={(o.degraded_indexes || 0) > 0} />
      </div>

      <div className="grid xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 bg-white border border-jj-gray-02 rounded-xl overflow-hidden shadow-sm">
          <SectionHeader title="Index & pipeline health" action="Manage indexes" onAction={() => onOpenSection('indexes')} />
          <div className="divide-y divide-jj-gray-01">
            {state.indexes.length === 0 ? <Empty text="No indexes registered yet." /> : state.indexes.slice(0, 8).map((idx) => (
              <div key={idx.id} className="px-5 py-3 flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-jj-gray-01 flex items-center justify-center text-jj-gray-06"><Icon name="indexes" /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><p className="font-johnson-text text-xs font-semibold text-jj-gray-08 truncate">{idx.dataset_id}</p><StatusPill value={idx.state} /></div>
                  <p className="font-johnson-text text-[11px] text-jj-gray-05 mt-0.5">{idx.index_type} · v{idx.active_version} · {Number(idx.indexed_records || 0).toLocaleString()} records · {idx.shard_count} shards × {idx.replica_count} replicas</p>
                </div>
                <span className="font-mono text-[10px] text-jj-gray-05">lag {idx.freshness_lag_seconds || 0}s</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white border border-jj-gray-02 rounded-xl overflow-hidden shadow-sm">
          <SectionHeader title="Active guardrails" action="Manage" onAction={() => onOpenSection('guardrails')} />
          <div className="divide-y divide-jj-gray-01">
            {risky.length === 0 ? <Empty text="No high-severity guardrails configured." /> : risky.slice(0, 6).map((g) => (
              <div key={g.id} className="px-4 py-3">
                <div className="flex items-center gap-2"><StatusPill value={g.severity} /><p className="font-johnson-text text-xs font-medium text-jj-gray-08 truncate">{g.name}</p></div>
                <p className="font-johnson-text text-[11px] text-jj-gray-05 mt-1">{g.kind} · {g.action} · {g.scope}{g.target ? `:${g.target}` : ''}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-jj-gray-08 rounded-xl p-5 text-white flex flex-wrap items-center gap-5">
        <div className="w-10 h-10 rounded-xl bg-white/10 flex items-center justify-center"><Icon name="mcp" className="w-5 h-5" /></div>
        <div className="flex-1 min-w-[260px]">
          <p className="font-johnson-display text-lg">One policy plane for REST, RAG and MCP</p>
          <p className="font-johnson-text text-xs text-white/70 mt-1">Agents and MCP tools reuse the same data-product permissions, row filters, field controls, top-k limits and audit path.</p>
        </div>
        <button onClick={() => onOpenSection('mcp')} className="px-4 py-2 rounded-lg bg-jj-red text-white font-johnson-text text-xs font-semibold hover:opacity-90">Open MCP controls</button>
      </div>
    </div>
  );
}

function ResourcePanel({ type }) {
  const endpoint = RESOURCE_ENDPOINT[type];
  const [state, setState] = useState({ loading: true, items: [], error: '' });
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const res = await apiFetch(endpoint);
      const body = await readJson(res);
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      setState({ loading: false, items: body[type] || [], error: '' });
    } catch (e) {
      setState({ loading: false, items: [], error: e.message });
    }
  }, [endpoint, type]);
  useEffect(() => { load(); }, [load]);

  const save = async (item) => {
    const id = item[RESOURCE_ID[type]];
    if (!id) throw new Error('id is required');
    const res = await apiFetch(`${endpoint}/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(item),
    });
    const body = await readJson(res);
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    setSelected(null); setCreating(false); await load();
  };

  const remove = async (item) => {
    if (!window.confirm(`Delete ${type.slice(0, -1)} "${item.id}"?`)) return;
    const res = await apiFetch(`${endpoint}/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
    if (!res.ok) {
      const body = await readJson(res);
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    setSelected(null); await load();
  };

  if (state.loading) return <PanelSkeleton />;
  if (state.error) return <ErrorCard message={state.error} retry={load} />;

  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl overflow-hidden shadow-sm">
      <SectionHeader title={NAV.find(([id]) => id === type)?.[1] || type} action={type === 'datasets' ? null : `Add ${type.slice(0, -1)}`} onAction={() => setCreating(true)} />
      <ResourceTable type={type} items={state.items} onSelect={setSelected} />
      {(selected || creating) && (
        <EditorDrawer
          type={type}
          item={selected}
          creating={creating}
          onClose={() => { setSelected(null); setCreating(false); }}
          onSave={save}
          onDelete={selected ? remove : null}
        />
      )}
    </div>
  );
}

function ResourceTable({ type, items, onSelect }) {
  if (!items.length) return <Empty text={`No ${type} configured.`} />;
  const columns = tableColumns(type);
  return (
    <div className="overflow-auto">
      <table className="w-full min-w-[780px]">
        <thead className="bg-jj-gray-01 border-b border-jj-gray-02">
          <tr>{columns.map((c) => <th key={c.key} className="px-4 py-2.5 text-left font-johnson-text text-[10px] uppercase tracking-wider text-jj-gray-05 font-semibold">{c.label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-jj-gray-01">
          {items.map((item) => (
            <tr key={item.id} onClick={() => onSelect(item)} className="hover:bg-jj-gray-01/60 cursor-pointer transition-colors">
              {columns.map((c) => <td key={c.key} className="px-4 py-3 font-johnson-text text-xs text-jj-gray-07">{renderCell(item, c)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function tableColumns(type) {
  switch (type) {
    case 'datasets': return [{ key: 'display_name', label: 'Data product' }, { key: 'version', label: 'Version' }, { key: 'status', label: 'Status' }, { key: 'capabilities', label: 'Capabilities' }, { key: 'source', label: 'Source' }];
    case 'policies': return [{ key: 'id', label: 'Policy' }, { key: 'effect', label: 'Effect' }, { key: 'dataset_patterns', label: 'Datasets' }, { key: 'operations', label: 'Operations' }, { key: 'priority', label: 'Priority' }];
    case 'clients': return [{ key: 'display_name', label: 'Consumer' }, { key: 'auth_mode', label: 'Identity' }, { key: 'status', label: 'Status' }, { key: 'allowed_datasets', label: 'Datasets' }, { key: 'rate_limit_rps', label: 'RPS' }, { key: 'max_concurrency', label: 'Concurrency' }];
    case 'agents': return [{ key: 'display_name', label: 'Agent' }, { key: 'service_principal', label: 'Service principal' }, { key: 'status', label: 'Status' }, { key: 'allowed_datasets', label: 'Datasets' }, { key: 'mcp_enabled', label: 'MCP' }];
    case 'guardrails': return [{ key: 'name', label: 'Guardrail' }, { key: 'kind', label: 'Kind' }, { key: 'scope', label: 'Scope' }, { key: 'action', label: 'Action' }, { key: 'severity', label: 'Severity' }];
    case 'indexes': return [{ key: 'dataset_id', label: 'Dataset' }, { key: 'index_type', label: 'Index' }, { key: 'active_version', label: 'Active version' }, { key: 'state', label: 'State' }, { key: 'indexed_records', label: 'Records' }, { key: 'freshness_lag_seconds', label: 'Lag' }];
    default: return [{ key: 'id', label: 'ID' }];
  }
}

function renderCell(item, column) {
  const value = item[column.key];
  if (['status', 'state', 'effect', 'severity'].includes(column.key)) return <StatusPill value={value} />;
  if (column.key === 'mcp_enabled') return value ? <StatusPill value="active" /> : <StatusPill value="disabled" />;
  if (Array.isArray(value)) return <span className="line-clamp-1">{value.join(', ')}</span>;
  if (value && typeof value === 'object') {
    if (column.key === 'source') return <span>{value.connector} · {value.environment} · {value.object_name}</span>;
    return <code className="font-mono text-[10px]">{JSON.stringify(value)}</code>;
  }
  if (column.key === 'indexed_records') return Number(value || 0).toLocaleString();
  if (column.key === 'freshness_lag_seconds') return `${value || 0}s`;
  return String(value ?? '—');
}

function EditorDrawer({ type, item, creating, onClose, onSave, onDelete }) {
  const initial = useMemo(() => item || defaultResource(type), [item, type]);
  const [text, setText] = useState(() => JSON.stringify(initial, null, 2));
  const [error, setError] = useState('');
  const submit = async () => {
    try {
      const parsed = JSON.parse(text);
      await onSave(parsed);
    } catch (e) { setError(e.message); }
  };
  return (
    <div className="fixed inset-0 z-[100] flex justify-end bg-black/25" onClick={onClose}>
      <div className="h-full w-full max-w-2xl bg-white shadow-2xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-jj-gray-02 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-jj-red/10 text-jj-red flex items-center justify-center"><Icon name={type} /></div>
          <div className="flex-1"><p className="font-johnson-display text-lg text-jj-gray-08">{creating ? 'Create' : 'Edit'} {type.slice(0, -1)}</p><p className="font-johnson-text text-[11px] text-jj-gray-05">Changes are written to the Control Hub API and become policy/configuration state.</p></div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-jj-gray-01 text-jj-gray-06">×</button>
        </div>
        <div className="flex-1 overflow-auto p-6">
          {type === 'datasets' && <div className="mb-4 px-4 py-3 rounded-lg bg-jj-yellow-01/20 border border-jj-yellow-01/60 font-johnson-text text-xs text-jj-gray-07">Data products use optimistic versioning. Production promotion should occur through the dataset wizard and validation gates; direct JSON editing is intended for advanced administrators.</div>}
          <label className="block font-johnson-text text-xs font-semibold text-jj-gray-07 mb-2">Configuration JSON</label>
          <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} className="w-full min-h-[520px] p-4 rounded-xl bg-jj-gray-08 text-jj-gray-01 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-jj-red" />
          {error && <p className="mt-3 text-xs font-johnson-text text-jj-maroon-05">{error}</p>}
        </div>
        <div className="px-6 py-4 border-t border-jj-gray-02 flex items-center gap-2">
          {onDelete && <button onClick={() => onDelete(item).catch((e) => setError(e.message))} className="px-4 py-2 rounded-lg border border-jj-maroon-05/30 text-jj-maroon-05 font-johnson-text text-xs hover:bg-jj-maroon-05/5">Delete</button>}
          <div className="flex-1" />
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-jj-gray-03 font-johnson-text text-xs text-jj-gray-07 hover:bg-jj-gray-01">Cancel</button>
          <button onClick={submit} className="px-5 py-2 rounded-lg bg-jj-red text-white font-johnson-text text-xs font-semibold hover:opacity-90">Validate & save</button>
        </div>
      </div>
    </div>
  );
}

function defaultResource(type) {
  if (type === 'policies') return { id: 'new-policy', enabled: true, effect: 'allow', dataset_patterns: ['*'], operations: ['query'], subjects: [], client_ids: [], groups: [], tenants: [], denied_fields: [], require_tenant_isolation: false, priority: 100 };
  if (type === 'clients') return { id: 'new-client', display_name: 'New consumer', owner: '', status: 'active', auth_mode: 'oauth2', allowed_datasets: [], allowed_capabilities: [], rate_limit_rps: 50, max_concurrency: 20, environment: 'prod', labels: {} };
  if (type === 'agents') return { id: 'new-agent', display_name: 'New agent', owner: '', service_principal: '', status: 'active', allowed_datasets: [], allowed_capabilities: ['retrieve'], require_citations: true, allow_raw_vector_input: false, max_top_k: 20, max_context_chars: 100000, mcp_enabled: false };
  if (type === 'guardrails') return { id: 'new-guardrail', name: 'New guardrail', enabled: true, scope: 'global', target: null, kind: 'require_citations', action: 'deny', config: {}, severity: 'high' };
  if (type === 'indexes') return { id: 'dataset-vector', dataset_id: '', index_type: 'vector', active_version: 'v1', candidate_version: null, state: 'healthy', freshness_lag_seconds: 0, indexed_records: 0, shard_count: 1, replica_count: 2, traffic_to_candidate_percent: 0, last_validation: null };
  return {};
}

function MCPPanel() {
  return (
    <div className="grid xl:grid-cols-2 gap-4">
      <div className="bg-white border border-jj-gray-02 rounded-xl p-5 shadow-sm">
        <div className="w-10 h-10 rounded-xl bg-jj-red/10 text-jj-red flex items-center justify-center mb-4"><Icon name="mcp" className="w-5 h-5" /></div>
        <h3 className="font-johnson-display text-lg text-jj-gray-08">MCP is a governed transport, not a new data path</h3>
        <p className="font-johnson-text text-xs text-jj-gray-06 mt-2 leading-relaxed">Expose describe_dataset, query_dataset, search_dataset and retrieve_context only for agents explicitly approved in Control Hub. Every tool call passes through the same data-product policy decision point as REST/RAG.</p>
      </div>
      <ResourcePanel type="agents" />
    </div>
  );
}

function MonitoringPanel() {
  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl p-8 shadow-sm">
      <div className="flex items-center gap-3"><div className="w-10 h-10 rounded-xl bg-jj-red/10 text-jj-red flex items-center justify-center"><Icon name="monitoring" className="w-5 h-5" /></div><div><h3 className="font-johnson-display text-lg text-jj-gray-08">SLO & telemetry integration point</h3><p className="font-johnson-text text-xs text-jj-gray-05">Latency percentiles, error rate, rate-limit events, source saturation, index freshness, recall quality and policy denials.</p></div></div>
      <div className="mt-6 grid md:grid-cols-3 gap-3">{['API p95 latency', 'Retrieval quality', 'Index freshness'].map((x) => <div key={x} className="rounded-lg border border-dashed border-jj-gray-03 p-5 text-center font-johnson-text text-xs text-jj-gray-05">{x}<br/><span className="text-[10px]">connect enterprise telemetry backend</span></div>)}</div>
    </div>
  );
}

function SectionHeader({ title, action, onAction }) {
  return <div className="px-5 py-3.5 border-b border-jj-gray-02 flex items-center gap-3"><h3 className="font-johnson-display text-[15px] text-jj-gray-08 flex-1">{title}</h3>{action && <button onClick={onAction} className="px-3 py-1.5 rounded-lg bg-jj-gray-08 text-white font-johnson-text text-[11px] hover:bg-jj-gray-07">{action}</button>}</div>;
}
function Empty({ text }) { return <div className="px-6 py-12 text-center font-johnson-text text-xs text-jj-gray-05">{text}</div>; }
function ErrorCard({ message, retry }) { return <div className="bg-white border border-jj-maroon-05/20 rounded-xl p-6"><p className="font-johnson-text text-sm text-jj-maroon-05">{message}</p><button onClick={retry} className="mt-3 px-3 py-1.5 rounded-lg bg-jj-gray-08 text-white text-xs">Retry</button></div>; }
function PanelSkeleton() { return <div className="bg-white border border-jj-gray-02 rounded-xl p-6 animate-pulse"><div className="h-4 w-40 rounded bg-jj-gray-02"/><div className="mt-5 space-y-3">{[1,2,3,4].map((n) => <div key={n} className="h-10 rounded bg-jj-gray-01"/>)}</div></div>; }

export default function EnterpriseControlHub() {
  const [section, setSection] = useState('overview');
  const label = NAV.find(([id]) => id === section)?.[1] || 'Control Hub';
  return (
    <div className="h-screen flex bg-jj-gray-01 text-jj-gray-08 overflow-hidden">
      <aside className="w-64 bg-white border-r border-jj-gray-02 flex flex-col flex-shrink-0">
        <div className="px-5 py-5 border-b border-jj-gray-02">
          <div className="flex items-center gap-2.5"><div className="w-8 h-8 rounded-lg bg-jj-red flex items-center justify-center text-white font-johnson-display text-sm font-bold">J&J</div><div><p className="font-johnson-display text-base leading-tight">Data Control Hub</p><p className="font-johnson-text text-[10px] text-jj-gray-05 uppercase tracking-wider">Enterprise Retrieval</p></div></div>
        </div>
        <nav className="p-3 space-y-1 overflow-y-auto flex-1">
          {NAV.map(([id, text]) => <button key={id} onClick={() => setSection(id)} className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors ${section === id ? 'bg-jj-red/10 text-jj-red' : 'text-jj-gray-06 hover:bg-jj-gray-01 hover:text-jj-gray-08'}`}><Icon name={id} /><span className="font-johnson-text text-xs font-semibold">{text}</span>{section === id && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-jj-red"/>}</button>)}
        </nav>
        <div className="p-4 border-t border-jj-gray-02"><div className="rounded-lg bg-jj-gray-08 text-white px-3 py-3"><div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-jj-green-03"/><span className="font-johnson-text text-[11px] font-semibold">Control plane connected</span></div><p className="font-johnson-text text-[10px] text-white/60 mt-1">Policies enforced on every route</p></div></div>
      </aside>

      <main className="flex-1 min-w-0 flex flex-col">
        <header className="h-16 bg-white border-b border-jj-gray-02 px-6 flex items-center gap-4 flex-shrink-0">
          <div><p className="font-johnson-text text-[10px] uppercase tracking-wider text-jj-gray-05">Control Hub</p><h1 className="font-johnson-display text-xl text-jj-gray-08 leading-tight">{label}</h1></div>
          <div className="flex-1"/><span className="font-johnson-text text-[11px] text-jj-gray-05">PROD</span><StatusPill value="healthy" />
        </header>
        <div className="flex-1 overflow-auto p-6">
          {section === 'overview' && <Overview onOpenSection={setSection} />}
          {RESOURCE_ENDPOINT[section] && <ResourcePanel type={section} />}
          {section === 'mcp' && <MCPPanel />}
          {section === 'monitoring' && <MonitoringPanel />}
        </div>
      </main>
    </div>
  );
}
