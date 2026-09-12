import React, { useState } from 'react';
import { requestJson } from '../apiClient.js';
import { useCollection } from './hooks.js';
import { RESOURCES, displayValue } from './resources.js';
import { Badge, Button, Card, Dialog, DownloadButton, Empty, ErrorNotice, Field, Icon, JsonView, PageHeading, Skeleton } from './primitives.jsx';

function StructuredField({ definition, value, onChange, onValidity, readonly }) {
  const [key, label, type, choices] = definition;
  const [text, setText] = useState(() => type === 'json' ? JSON.stringify(value ?? null, null, 2) : Array.isArray(value) ? value.join(', ') : '');
  if (type === 'boolean') return <label className="switch-field"><input type="checkbox" checked={Boolean(value)} onChange={e => onChange(e.target.checked)}/><span className="switch-track"/><span>{label}</span></label>;
  if (type === 'json') return <Field label={label}><textarea className="code-input" value={text} rows={key === 'fields' ? 10 : 4} spellCheck="false" onChange={e => {
    setText(e.target.value);
    try { onChange(JSON.parse(e.target.value)); onValidity(key, true); } catch { onValidity(key, false); }
  }}/></Field>;
  if (type === 'list') return <Field label={label} hint="Separate values with commas."><input value={text} onChange={e => {
    setText(e.target.value); onChange(e.target.value.split(',').map(x => x.trim()).filter(Boolean));
  }}/></Field>;
  if (type === 'select') {
    const values = [...new Set([...(choices || '').split(','), ...(value ? [value] : [])])];
    return <Field label={label}><select value={value ?? ''} onChange={e => onChange(e.target.value)}>{values.map(x => <option key={x} value={x}>{x.replaceAll('_', ' ')}</option>)}</select></Field>;
  }
  return <Field label={label}><input readOnly={readonly} type={type === 'number' ? 'number' : 'text'} step={type === 'number' ? 'any' : undefined} value={value ?? ''} onChange={e => onChange(type === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : (e.target.value || (value === null ? null : '')))}/></Field>;
}

export function ResourceEditor({ kind, item, initial, onClose, onSaved, session }) {
  const config = RESOURCES[kind];
  const [draft, setDraft] = useState(() => structuredClone(item || initial || config.defaults));
  const [tab, setTab] = useState('form');
  const [json, setJson] = useState('');
  const [invalid, setInvalid] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [validation, setValidation] = useState(null);
  const supported = session.routes || [];
  const valid = !Object.values(invalid).some(Boolean);
  function selectTab(next) {
    if (next === 'json') setJson(JSON.stringify(draft, null, 2));
    else { try { setDraft(JSON.parse(json)); setInvalid({}); } catch { setError(new Error('Correct the JSON before switching to the form.')); return; } }
    setError(null); setTab(next);
  }
  async function save(event) {
    event?.preventDefault();
    setBusy(true); setError(null);
    try {
      const body = tab === 'json' ? JSON.parse(json) : structuredClone(draft);
      if (!body.id || typeof body.id !== 'string') throw new Error('A resource ID is required.');
      if (item && body.id !== item.id) throw new Error('An existing resource ID cannot be changed.');
      if (kind === 'datasets' && item && body.version === item.version) throw new Error('Use a new immutable version before saving this data product.');
      if (item && kind !== 'datasets') body.revision = item.revision ?? 0;
      const condition = kind === 'datasets' && item ? '?expected_version=' + encodeURIComponent(item.version) : '';
      await requestJson('/v1/control/' + kind + '/' + encodeURIComponent(body.id) + condition, { method: 'PUT', body });
      onSaved(config.singular + ' saved');
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setError(null);
    try {
      const condition = kind === 'datasets' ? 'expected_version=' + encodeURIComponent(item.version) : 'expected_revision=' + (item.revision ?? 0);
      await requestJson('/v1/control/' + kind + '/' + encodeURIComponent(item.id) + '?' + condition, { method: 'DELETE' });
      onSaved(config.singular + ' deleted');
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function validate() {
    setBusy(true); setError(null); setValidation(null);
    try {
      const body = tab === 'json' ? JSON.parse(json) : draft;
      setValidation(await requestJson('/v1/control/onboarding/validate', { method: 'POST', body }));
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <Dialog title={(item ? 'Edit ' : 'Create ') + config.singular} subtitle={item ? 'Saving uses the version you opened. Concurrent changes are rejected.' : 'The server validates the configuration before storing it.'} onClose={onClose} busy={busy} wide>
    <form onSubmit={save}>
      <div className="dialog-body">
        <div className="tabs" role="tablist" aria-label="Editor mode">
          <button type="button" role="tab" aria-selected={tab === 'form'} onClick={() => tab !== 'form' && selectTab('form')}>Guided form</button>
          <button type="button" role="tab" aria-selected={tab === 'json'} onClick={() => tab !== 'json' && selectTab('json')}>Advanced JSON</button>
        </div>
        <ErrorNotice error={error}/>
        {tab === 'form' ? <div className="form-grid">{config.fields.map(def => <div key={def[0]} className={def[2] === 'json' ? 'full-span' : ''}><StructuredField definition={def} value={draft[def[0]]} readonly={Boolean(item && def[0] === 'id')}
          onChange={value => { setDraft(d => ({ ...d, [def[0]]: value })); setValidation(null); }}
          onValidity={(key, isValid) => setInvalid(v => ({ ...v, [key]: !isValid }))}/></div>)}</div> :
          <Field label="Resource configuration"><textarea className="code-input large" spellCheck="false" value={json} onChange={e => { setJson(e.target.value); setValidation(null); }}/></Field>}
        {!valid && <p className="validation-message" role="alert">One or more JSON fields are invalid.</p>}
        {kind === 'policies' && <p className="inline-note"><Icon name="shield"/>Consumer registration and OAuth scopes also apply. This form never impersonates a consumer.</p>}
        {validation && <div className="validation-result"><Badge value={validation.valid ? 'ready' : 'failed'}/>{(validation.issues || []).map(x => <p key={x}>{x}</p>)}{(validation.notes || []).map(x => <small key={x}>{x}</small>)}</div>}
        {deleting && <div className="delete-confirm"><p>Delete this registration? Type <strong>{item.id}</strong> to confirm.</p><Field label="Confirm resource ID"><input autoFocus value={confirmation} onChange={e => setConfirmation(e.target.value)}/></Field><Button variant="danger" busy={busy} disabled={confirmation !== item.id} onClick={remove}>Confirm deletion</Button></div>}
      </div>
      <div className="dialog-footer">{item && !deleting && <Button variant="danger-quiet" icon="trash" disabled={busy} onClick={() => setDeleting(true)}>Delete</Button>}<div className="spacer"/>
        {kind === 'datasets' && supported.includes('/v1/control/onboarding/validate') && <Button busy={busy} disabled={!valid} onClick={validate}>Validate binding</Button>}
        <Button disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" busy={busy} disabled={!valid || deleting}>Save {config.singular}</Button>
      </div>
    </form>
  </Dialog>;
}

export function PromotionDialog({ item, onClose, onSaved }) {
  const [operation, setOperation] = useState('validate');
  const [evidence, setEvidence] = useState('');
  const [percent, setPercent] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const risky = operation === 'promote' || operation === 'rollback-canary' || (operation === 'canary' && percent > 0);
  async function run() {
    setBusy(true); setError(null);
    try {
      const body = operation === 'validate' ? JSON.parse(evidence) : operation === 'canary' ? { percent: Number(percent) } : {};
      await requestJson('/v1/control/indexes/' + encodeURIComponent(item.id) + '/' + operation, { method: 'POST', body });
      onSaved('Index transition completed');
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <Dialog title="Index promotion" subtitle={item.id} onClose={onClose} busy={busy}>
    <div className="dialog-body"><Badge value={item.state}/><p>Active version <strong>{item.active_version}</strong> · Candidate <strong>{item.candidate_version || 'not configured'}</strong></p>
      <Field label="Transition"><select value={operation} onChange={e => { setOperation(e.target.value); setConfirmed(false); setError(null); }}><option value="validate">Validate candidate evidence</option><option value="canary">Set canary traffic</option><option value="promote">Promote candidate</option><option value="rollback-canary">Rollback canary traffic</option></select></Field>
      {operation === 'validate' && <Field label="Measured quality evidence JSON" hint="Provide evaluated_queries, recall_at_k, precision_at_k, citation_coverage, p95_latency_ms and error_rate from your evaluation."><textarea rows={10} className="code-input" value={evidence} onChange={e => setEvidence(e.target.value)} placeholder="Paste actual evaluation evidence"/></Field>}
      {operation === 'canary' && <Field label="Candidate traffic (%)"><input type="number" min="0" max="100" value={percent} onChange={e => { setPercent(e.target.value); setConfirmed(false); }}/></Field>}
      {risky && <label className="checkline"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>I confirm this change to live index routing.</label>}
      <p className="muted">The server enforces quality, version, and promotion gates. A rejected transition leaves the active version unchanged.</p><ErrorNotice error={error}/>
    </div>
    <div className="dialog-footer"><Button onClick={onClose} disabled={busy}>Cancel</Button><Button variant="primary" busy={busy} disabled={(risky && !confirmed) || (!item.candidate_version && operation !== 'rollback-canary')} onClick={run}>Run transition</Button></div>
  </Dialog>;
}

export function ResourcePanel({ kind, session, onOnboard, notify }) {
  const config = RESOURCES[kind], data = useCollection(kind);
  const [filter, setFilter] = useState('');
  const [status, setStatus] = useState('');
  const [sort, setSort] = useState('asc');
  const [editor, setEditor] = useState(null);
  const [promotion, setPromotion] = useState(null);
  const [connection, setConnection] = useState({});
  const [checking, setChecking] = useState(null);
  const [error, setError] = useState(null);
  const rows = data.items.filter(item => JSON.stringify(item).toLowerCase().includes(filter.toLowerCase()) &&
    (!status || String(item.status || item.state || (item.enabled === true ? 'enabled' : item.enabled === false ? 'disabled' : '')) === status))
    .sort((a,b) => String(a.display_name || a.name || a.id).localeCompare(String(b.display_name || b.name || b.id)) * (sort === 'asc' ? 1 : -1));
  const states = [...new Set(data.items.map(x => x.status || x.state || (x.enabled === true ? 'enabled' : x.enabled === false ? 'disabled' : '')).filter(Boolean))];
  const saved = message => { setEditor(null); setPromotion(null); data.refresh(); notify(message); };
  async function check(item) {
    setChecking(item.id); setError(null);
    try { const result = await requestJson('/v1/control/sources/' + encodeURIComponent(item.id) + '/check', { method: 'POST' }); setConnection(s => ({ ...s, [item.id]: result })); notify('Source connection checked'); }
    catch (e) { setConnection(s => ({ ...s, [item.id]: null })); setError(e); } finally { setChecking(null); }
  }
  return <>
    <PageHeading eyebrow="Manage your platform" title={config.title} description={config.description} action={<Button icon="plus" variant="primary" onClick={() => kind === 'datasets' && session.routes.includes('/v1/control/onboarding/preview') ? onOnboard() : setEditor({})}>Create {config.singular}</Button>}/>
    <Card className="registry-card">
      <div className="toolbar"><div className="search-box"><Icon name="search" size={18}/><input aria-label="Filter loaded resources" placeholder="Filter loaded resources…" value={filter} onChange={e => setFilter(e.target.value)}/></div>
        <select aria-label="Filter status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All states</option>{states.map(x => <option key={x}>{x}</option>)}</select>
        <select aria-label="Sort resources" value={sort} onChange={e => setSort(e.target.value)}><option value="asc">Name A–Z</option><option value="desc">Name Z–A</option></select>
        <Button icon="refresh" aria-label="Refresh resources" busy={data.loading} onClick={data.refresh}/></div>
      <ErrorNotice error={data.error || error} onRetry={data.refresh}/>
      {data.loading && !data.items.length ? <Skeleton/> : !rows.length ? <Empty icon={config.icon} title={data.items.length ? 'No matches in loaded resources' : 'Your ' + config.title.toLowerCase() + ' start here'} description={data.items.length ? 'Adjust the filters or load the next page.' : 'Create a registration to make it available in this workspace.'}/> :
        <div className="table-scroll"><table><caption className="sr-only">{config.title} registry</caption><thead><tr>{config.columns.map(([key,label]) => <th key={key}>{label}</th>)}{['sources','indexes'].includes(kind) && <th>Actions</th>}</tr></thead><tbody>
          {rows.map(item => <tr key={item.id}>{config.columns.map(([key], index) => <td key={key}>{index === 0 ? <button className="resource-name" onClick={() => setEditor({ item })}><span className={'icon-tile ' + config.color}><Icon name={config.icon}/></span><span><strong>{displayValue(item[key])}</strong>{item[key] !== item.id && <small>{item.id}</small>}</span></button> :
            ['status','state','effect','severity'].includes(key) ? <Badge value={item[key]}/> : <span className="cell-value">{displayValue(item[key])}</span>}</td>)}
            {kind === 'indexes' && <td><Button onClick={() => setPromotion(item)}>Promotion</Button></td>}
            {kind === 'sources' && <td><Button busy={checking === item.id} disabled={!item.enabled || Boolean(checking)} onClick={() => check(item)}>Check connection</Button>{connection[item.id] && <small className="connection-result">Reachable · {new Date(connection[item.id].checked_at).toLocaleTimeString()}</small>}</td>}
          </tr>)}
        </tbody></table></div>}
      <div className="table-footer"><span>{rows.length} shown · {data.items.length} loaded{data.next ? ' · more available' : ''}</span>{data.next && <Button busy={data.loading} onClick={data.loadMore}>Load more</Button>}</div>
    </Card>
    {editor && <ResourceEditor key={editor.item?.id || 'create'} kind={kind} item={editor.item} session={session} onClose={() => setEditor(null)} onSaved={saved}/>}
    {promotion && <PromotionDialog item={promotion} onClose={() => setPromotion(null)} onSaved={saved}/>}
  </>;
}

export function Onboarding({ session, onClose, notify }) {
  const sources = useCollection('sources');
  const [input, setInput] = useState({ source_id: '', schema_name: '', object_name: '', dataset_id: '', display_name: '', environment: session.environment, template: 'table', version: '1' });
  const [objects, setObjects] = useState(null);
  const [preview, setPreview] = useState(null);
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [edit, setEdit] = useState(false);
  async function call(action) {
    setBusy(true); setError(null);
    try {
      if (action === 'objects') {
        setObjects(await requestJson('/v1/control/sources/' + encodeURIComponent(input.source_id) + '/objects?limit=100' +
          (input.schema_name ? '&schema_name=' + encodeURIComponent(input.schema_name) : '')));
      } else if (action === 'preview') {
        const body = { ...input, schema_name: input.schema_name || null };
        setPreview(await requestJson('/v1/control/onboarding/preview', { method: 'POST', body }));
      } else setPlan(await requestJson('/v1/control/onboarding/index-plan', { method: 'POST', body: preview.dataset }));
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  if (edit) return <ResourceEditor kind="datasets" initial={preview.dataset} session={session} onClose={() => setEdit(false)} onSaved={message => { notify(message); onClose(true); }}/>;
  return <Dialog title="Create a data product" subtitle="Inspect a real source, review the generated contract, then save a governed draft." onClose={() => onClose(false)} busy={busy} wide>
    <div className="dialog-body"><div className="step-strip"><span className={!preview ? 'current' : 'complete'}>1 · Select source</span><span className={preview ? 'current' : ''}>2 · Review schema</span><span>3 · Save draft</span></div>
      <ErrorNotice error={error || sources.error}/>
      {!preview ? <div className="form-grid">
        <Field label="Registered source"><select value={input.source_id} onChange={e => { setInput(s => ({ ...s, source_id: e.target.value, object_name: '' })); setObjects(null); }}><option value="">Select a source</option>{sources.items.filter(s => s.enabled).map(s => <option key={s.id} value={s.id}>{s.id} · {s.kind}</option>)}</select></Field>
        <Field label="Schema name" hint="Leave empty to use the source's default schema."><input value={input.schema_name} onChange={e => { setInput(s => ({ ...s, schema_name: e.target.value })); setObjects(null); }}/></Field>
        <div className="full-span"><Button disabled={!input.source_id} busy={busy} onClick={() => call('objects')}>Inspect available objects</Button>{sources.next && <Button onClick={sources.loadMore} busy={sources.loading}>Load more sources</Button>}</div>
        <Field label="Table or view"><input list="source-objects" value={input.object_name} onChange={e => setInput(s => ({ ...s, object_name: e.target.value }))}/></Field><datalist id="source-objects">{(objects?.objects || []).map(x => <option key={x.object_name} value={x.object_name}/>)}</datalist>
        <Field label="Contract template"><select value={input.template} onChange={e => setInput(s => ({ ...s, template: e.target.value }))}><option value="table">Structured data</option><option value="postgres_chunks">PostgreSQL document chunks</option></select></Field>
        <Field label="Dataset ID"><input value={input.dataset_id} onChange={e => setInput(s => ({ ...s, dataset_id: e.target.value }))} placeholder="rdh.rimdocs.clinical"/></Field>
        <Field label="Display name"><input value={input.display_name} onChange={e => setInput(s => ({ ...s, display_name: e.target.value }))}/></Field>
        {objects?.truncated && <p className="inline-note full-span">The object list was truncated. Enter the exact table or view name if it is not shown.</p>}
      </div> : <>
        <div className="schema-summary"><span className="icon-tile violet"><Icon name="database"/></span><div><h3>{preview.dataset.display_name}</h3><p>{preview.source?.schema_name} · {preview.source?.object_name} · {preview.dataset.fields.length} fields</p></div><Badge value="draft"/></div>
        <div className="table-scroll"><table><thead><tr><th>Field</th><th>Type</th><th>Nullable</th><th>Filterable</th></tr></thead><tbody>{preview.dataset.fields.map(x => <tr key={x.name}><td><code>{x.name}</code></td><td>{x.data_type}</td><td>{x.nullable ? 'Yes' : 'No'}</td><td>{x.filterable ? 'Yes' : 'No'}</td></tr>)}</tbody></table></div>
        <p className="inline-note"><Icon name="shield"/>Review source fields, tenant scope, and business approval filters before activating this product. Ingestion status does not establish document approval.</p>
        {preview.dataset.retrieval?.backend === 'postgres' && <Button busy={busy} onClick={() => call('plan')}>Generate source index plan</Button>}
        {plan && <Card title="Index plan" subtitle="Generated SQL is for source-owner review. SmartHub has not executed it." action={<DownloadButton value={plan} filename="smarthub-index-plan.json"/>}><JsonView value={plan.statements}/></Card>}
      </>}
    </div>
    <div className="dialog-footer"><Button onClick={() => preview ? (setPreview(null), setPlan(null)) : onClose(false)} disabled={busy}>{preview ? 'Back to source' : 'Cancel'}</Button><Button variant="primary" busy={busy} disabled={!preview && (!input.source_id || !input.object_name || !input.dataset_id || !input.display_name)} onClick={() => preview ? setEdit(true) : call('preview')}>{preview ? 'Review & save draft' : 'Preview data contract'}</Button></div>
  </Dialog>;
}
