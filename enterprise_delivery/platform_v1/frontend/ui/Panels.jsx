import React, { useState } from 'react';
import { useCollection, useResource } from './hooks.js';
import { Badge, Button, Card, DownloadButton, Empty, ErrorNotice, Icon, JsonView, PageHeading, Skeleton, Field } from './primitives.jsx';
import { displayValue } from './resources.js';

export function Metric({ label, value, icon, color = 'violet', hint, onClick }) {
  const content = <><span className={'icon-tile ' + color}><Icon name={icon}/></span><span className="metric-label">{label}</span><strong>{typeof value === 'number' ? value.toLocaleString() : value ?? '—'}</strong><small>{hint}</small></>;
  return onClick ? <button className="metric-card" onClick={onClick}>{content}<Icon name="arrow" size={17} className="metric-arrow"/></button> : <div className="metric-card">{content}</div>;
}
export function Overview({ session, navigate, onOnboard }) {
  const snapshot = useResource('/v1/control/dashboard');
  const summary = useResource(snapshot.data && !snapshot.data.resources ? '/v1/control/overview' : null);
  const feed = useResource('/v1/control/audit?limit=6&action=control.put');
  const [refreshing, setRefreshing] = useState(false);
  const data = snapshot.data;
  const counts = data?.resources || summary.data || {};
  const stats = [['datasets','Data products','database','violet'],['sources','Registered sources','source','blue'],
    ['clients','Consumers','users','rose'],['agents','Agents','bot','violet'],['policies','Access policies','shield','teal'],['indexes','Search indexes','layers','amber']];
  function refresh() { setRefreshing(true); snapshot.refresh(); summary.refresh(); feed.refresh(); setTimeout(() => setRefreshing(false), 600); }
  return <>
    <PageHeading eyebrow="Your governed data workspace" title="Platform overview" description="Connect your sources. Govern access. Give every consumer the right data." action={<Button icon="refresh" busy={refreshing || snapshot.loading} onClick={refresh}>Refresh overview</Button>}/>
    <section className="welcome-banner"><div><span className="eyebrow">SmartHub control plane</span><h2>Every connection.<br/><em>One trusted workspace.</em></h2><p>Bring structured data, retrieval services, and agent access together with explicit, reviewable controls.</p><div className="button-row"><Button variant="primary" icon="plus" onClick={onOnboard}>Create data product</Button><Button icon="play" onClick={() => navigate('explorer')}>Open API explorer</Button></div></div><div className="welcome-art" aria-hidden="true"><span className="orbit"/><span className="hub-symbol"><Icon name="spark" size={43}/></span><span className="satellite one"><Icon name="database" size={25}/></span><span className="satellite two"><Icon name="bot" size={25}/></span><span className="satellite three"><Icon name="shield" size={25}/></span><span className="satellite four"><Icon name="source" size={25}/></span><span className="art-caption">Connect · Govern · Serve</span></div></section>
    <ErrorNotice error={snapshot.error || summary.error} onRetry={refresh}/>
    {snapshot.loading || summary.loading ? <Skeleton/> : <div className="metrics-grid">{stats.map(([key,label,icon,color]) => <Metric key={key} label={label} value={counts[key]} icon={icon} color={color} hint="Registered control state" onClick={() => navigate(key)}/>)}</div>}
    <div className="two-columns">
      <Card title="Build your data offering" subtitle="Move from a registered source to a governed consumer API.">
        <div className="workflow-list">{[
          ['source','blue','Connect a source','Register credentials by reference and inspect available objects.','sources'],
          ['database','violet','Publish a data product','Define its schema, identities, filters, and retrieval profile.','datasets'],
          ['shield','teal','Grant consumer access','Set policies, application grants, and operation limits.','policies'],
          ['code','rose','Test the contract','Run authorized requests and inspect actual API responses.','explorer'],
        ].map(([icon,color,title,description,target],i) => <button key={target} onClick={() => navigate(target)}><span className={'icon-tile ' + color}><Icon name={icon}/></span><span><small>0{i+1}</small><strong>{title}</strong><p>{description}</p></span><Icon name="chevron" size={18}/></button>)}</div>
      </Card>
      <Card title="Latest configuration changes" subtitle="Read from the durable audit trail." action={<Button onClick={() => navigate('audit')}>View audit</Button>}>
        {feed.loading ? <Skeleton/> : feed.error ? <ErrorNotice error={feed.error} onRetry={feed.refresh}/> : !feed.data?.events?.length ? <Empty icon="history" title="No configuration changes in this window" description="Saved registrations and policies will appear here."/> : <div className="event-list">{feed.data.events.map(event => <div key={event.id}><span className="event-dot"/><div><strong>{event.resource}</strong><p>{event.action} · {event.actor}</p><time>{new Date(event.time).toLocaleString()}</time></div></div>)}</div>}
      </Card>
    </div>
    <div className="two-columns">
      <Card title="Runtime details" action={<Button onClick={() => navigate('environments')}>View environment</Button>}><dl className="detail-list"><div><dt>Environment</dt><dd><Badge value={session.environment}/></dd></div><div><dt>Release</dt><dd>{session.release || 'Not reported by deployment'}</dd></div><div><dt>Runtime</dt><dd>{session.runtime_mode}</dd></div><div><dt>Last snapshot</dt><dd>{data?.generated_at ? new Date(data.generated_at).toLocaleString() : 'Not available'}</dd></div></dl><p className="card-note">Control-store availability does not establish source, retrieval, or fleet health.</p></Card>
      <Card title="Worker queue" subtitle="Current durable job counts by kind and state." action={<Button onClick={() => navigate('monitoring')}>View monitoring</Button>}>{!data?.jobs?.length ? <Empty icon="layers" title="No recorded worker jobs" description="Submitted indexing and export jobs will be reflected here."/> : <div className="queue-list">{data.jobs.map(row => <div key={row.kind + row.status}><span>{row.kind}</span><Badge value={row.status}/><strong>{row.count.toLocaleString()}</strong></div>)}</div>}</Card>
    </div>
  </>;
}

export function AuditPanel() {
  const [filters, setFilters] = useState({ action: '', subject: '', resource: '', since: '' });
  const [query, setQuery] = useState('');
  const [pages, setPages] = useState([null]);
  const cursor = pages[pages.length - 1];
  const params = new URLSearchParams(query);
  params.set('limit', '50');
  if (cursor) { params.set('before_time', cursor.before_time); params.set('before_id', cursor.before_id); if (!params.has('since')) params.set('since', cursor.since); }
  const state = useResource('/v1/control/audit?' + params.toString());
  function apply(event) {
    event.preventDefault();
    const next = new URLSearchParams();
    for (const [key,value] of Object.entries(filters)) if (value) next.set(key, key === 'since' ? new Date(value).toISOString() : value);
    setQuery(next.toString()); setPages([null]); state.refresh();
  }
  return <><PageHeading eyebrow="Governance & accountability" title="Audit trail" description="Explore actual access and configuration events. Filters use exact matches; the default window is seven days."/>
    <Card><form className="audit-filters" onSubmit={apply}><Field label="Action"><select value={filters.action} onChange={e => setFilters(f => ({ ...f, action:e.target.value }))}><option value="">All actions</option>{['control.put','control.delete','authorization.deny','execution.failed','execution.complete','authentication.allow','authentication.deny','administration.deny'].map(x => <option key={x}>{x}</option>)}</select></Field>
      {['subject','resource'].map(key => <Field key={key} label={key === 'subject' ? 'Subject' : 'Resource'}><input value={filters[key]} onChange={e => setFilters(f => ({ ...f, [key]:e.target.value }))}/></Field>)}
      <Field label="Since (local time)"><input type="datetime-local" value={filters.since} onChange={e => setFilters(f => ({ ...f, since:e.target.value }))}/></Field><Button type="submit" variant="primary">Apply filters</Button></form>
      <ErrorNotice error={state.error} onRetry={state.refresh}/>
      {state.loading ? <Skeleton/> : !state.data?.events?.length ? <Empty icon="history" title="No events match these filters" description="Try a different action, subject, resource, or time window."/> :
        <div className="table-scroll"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Resource</th><th>Trace ID</th></tr></thead><tbody>{state.data.events.map(x => <tr key={x.id}><td>{new Date(x.time).toLocaleString()}</td><td>{x.actor}</td><td><code>{x.action}</code></td><td>{x.resource}</td><td><code>{x.trace_id || '—'}</code></td></tr>)}</tbody></table></div>}
      <div className="table-footer"><span>Page {pages.length} · {state.data?.events?.length ?? 0} events</span><div className="button-row">{state.data && <DownloadButton value={state.data.events} filename="smarthub-audit-page.json" label="Export this page"/>}<Button disabled={pages.length === 1 || state.loading} onClick={() => setPages(p => p.slice(0,-1))}>Previous</Button><Button disabled={!state.data?.next_cursor || state.loading} onClick={() => setPages(p => [...p, { ...state.data.next_cursor, since:state.data.since }])}>Next page</Button></div></div>
    </Card></>;
}

export function MonitoringPanel() {
  const telemetry = useResource('/v1/control/telemetry');
  const dashboard = useResource('/v1/control/dashboard');
  const data = telemetry.data, totals = data?.totals;
  return <><PageHeading eyebrow="Observe your services" title="Monitoring" description="Measured service activity, durable worker counts, and explicit telemetry scope." action={<Button icon="refresh" busy={telemetry.loading} onClick={() => { telemetry.refresh(); dashboard.refresh(); }}>Refresh metrics</Button>}/>
    <ErrorNotice error={telemetry.error} onRetry={telemetry.refresh}/>
    {telemetry.loading ? <Skeleton/> : data && <>
      <div className="scope-banner"><Icon name="activity"/><span><strong>Current API replica · rolling five-minute window</strong><small>{data.instance} · {data.truncated ? 'Capped at the latest ' + data.sample_limit.toLocaleString() + ' observations' : 'Up to ' + data.window_seconds + ' seconds'} · {data.covered_seconds}s covered</small></span><Badge value="observed"/></div>
      <div className="metrics-grid four"><Metric label="Operations observed" value={totals.requests} icon="activity" hint="Retained observations"/><Metric label="P95 latency" value={totals.p95_ms === null ? '—' : totals.p95_ms + ' ms'} icon="activity" color="blue" hint="Measured operation duration"/><Metric label="Execution error rate" value={totals.error_rate === null ? '—' : totals.error_rate + '%'} icon="warning" color="rose" hint="Excludes denied and overloaded"/><Metric label="Access denials" value={totals.denied} icon="shield" color="amber" hint="Observed authorization failures"/></div>
      <Card title="Operation activity" subtitle="Counts from measured executions on the responding replica. No fleet-wide or historical totals are inferred.">
        {!data.operations.length ? <Empty icon="activity" title="No operations observed yet" description="Run an authorized API request to begin collecting measurements on this replica."/> : <div className="operation-bars">{data.operations.map((row,i) => <div key={row.operation}><span className="bar-label">{row.operation}</span><span className="bar-track"><span className={'bar-fill tone-' + i%4} style={{ width: Math.max(1, row.requests / Math.max(...data.operations.map(x => x.requests)) * 100) + '%' }}/></span><strong>{row.requests}</strong><small>P95 {row.p95_ms} ms</small></div>)}</div>}
        <p className="card-note">Replica samples reset on process restart and may change behind a load balancer. OpenTelemetry export is {data.exporter_configured ? 'configured' : 'not configured'}; this is configuration status, not collector health.</p>
      </Card>
    </>}
    <Card title="Durable jobs" subtitle="Queue state from the shared control store."><ErrorNotice error={dashboard.error} onRetry={dashboard.refresh}/>{dashboard.loading ? <Skeleton/> : dashboard.data?.jobs?.length ? <div className="queue-list">{dashboard.data.jobs.map(x => <div key={x.kind+x.status}><strong>{x.kind}</strong><Badge value={x.status}/><span>{x.count} jobs</span></div>)}</div> : <Empty icon="layers" title="No jobs recorded"/>}</Card>
  </>;
}

export function EnvironmentPanel({ session }) {
  const health = useResource('/readyz');
  const entries = (globalThis.smarthubConfig?.environments || []).filter(x => {
    try { return new URL(x.url).protocol === 'https:'; } catch { return false; }
  });
  return <><PageHeading eyebrow="Deployment context" title="Environment" description="The identity, configuration, and readiness of the environment you are actually connected to."/>
    <div className="two-columns"><Card title="Current runtime"><dl className="detail-list">{[['Environment',session.environment],['Runtime mode',session.runtime_mode],['Release',session.release || 'Not reported'],['API version','v1']].map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></Card>
      <Card title="Control-store readiness" action={<Button icon="refresh" busy={health.loading} onClick={health.refresh}>Check</Button>}><ErrorNotice error={health.error}/>{health.loading ? <Skeleton/> : health.data && <div className="readiness"><span className="icon-tile teal"><Icon name="check" size={30}/></span><Badge value={health.data.status}/><p>Checked {health.updatedAt?.toLocaleTimeString()}</p></div>}<p className="card-note">This probe covers the control store. Source connections have separate checks in Sources.</p></Card></div>
    <Card title="Other configured environments">{entries.length ? <div className="environment-links">{entries.map(x => <a key={x.url} href={x.url} className="environment-link" rel="noreferrer"><Icon name="globe"/><span>{x.label || new URL(x.url).hostname}</span><Icon name="arrow"/></a>)}</div> : <Empty icon="globe" title="No other console URLs configured" description="Environment links are supplied by deployment configuration. Credentials are never forwarded to another environment."/>}</Card>
  </>;
}
export function AccessPanel({ session, navigate }) {
  return <><PageHeading eyebrow="Identity & permissions" title="Access Control" description="Your authenticated identity and the controls governing consumer access."/>
    <div className="two-columns"><Card title="Current identity"><dl className="detail-list">{[['Subject',session.subject],['Client ID',session.client_id],['Tenant',session.tenant],['Console administration',session.is_admin ? 'Authorized' : 'Not authorized']].map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{v || 'Not present in identity'}</dd></div>)}</dl></Card>
      <Card title="Identity claims"><h3 className="section-label">Groups</h3><div className="chips">{session.groups.length ? session.groups.map(x => <span key={x}>{x}</span>) : <p className="muted">No group claims.</p>}</div><h3 className="section-label">OAuth scopes</h3><div className="chips">{session.scopes.length ? session.scopes.map(x => <code key={x}>{x}</code>) : <p className="muted">No OAuth scopes reported by this identity adapter.</p>}</div></Card></div>
    <Card title="Manage access rules" subtitle="Administrative access does not automatically grant access to source records."><div className="action-grid">{[['policies','shield','Row and field policies'],['clients','users','Consumer registrations'],['agents','bot','Agent permissions']].map(([target,icon,label]) => <button key={target} onClick={() => navigate(target)}><span className="icon-tile violet"><Icon name={icon}/></span><strong>{label}</strong><Icon name="arrow" size={18}/></button>)}</div></Card>
  </>;
}
export function MCPPanel({ navigate }) {
  const tools = useResource('/v1/control/mcp/tools');
  const agents = useCollection('agents');
  const enabled = agents.items.filter(x => x.mcp_enabled);
  return <><PageHeading eyebrow="Agentic integration" title="MCP tool catalog" description="Inspect the governed facade's actual tool definitions and agent registrations." action={<Button icon="bot" onClick={() => navigate('agents')}>Manage agents</Button>}/>
    <ErrorNotice error={tools.error} onRetry={tools.refresh}/>
    <p className="inline-note"><Icon name="code"/>This catalog describes the server's governed facade. A registered tool or agent does not establish that an external MCP transport is deployed.</p>
    {tools.loading ? <Skeleton/> : <div className="tool-grid">{(tools.data?.tools || []).map((tool,i) => <Card key={tool.name} className="tool-card"><span className={'icon-tile ' + ['violet','blue','teal','rose'][i%4]}><Icon name="code"/></span><h2>{tool.name}</h2><p>{tool.description}</p><details><summary>Inspect input schema</summary><JsonView value={tool.inputSchema}/></details></Card>)}</div>}
    <Card title="MCP-enabled agent registrations" subtitle={agents.items.length + ' agent registrations loaded.'}><ErrorNotice error={agents.error} onRetry={agents.refresh}/>{agents.loading && !agents.items.length ? <Skeleton/> : enabled.length ? <div className="queue-list">{enabled.map(x => <div key={x.id}><span><strong>{x.display_name}</strong><small>{x.service_principal}</small></span><Badge value={x.status}/><span>{x.allowed_datasets.length} allowed datasets</span></div>)}</div> : <Empty icon="bot" title="No MCP-enabled agents in the loaded page" description="Configure an agent's service identity and explicit dataset grants before enabling the facade."/>}{agents.next && <div className="card-padding"><Button onClick={agents.loadMore} busy={agents.loading}>Load more agents</Button></div>}</Card>
  </>;
}
