import React, { useEffect, useRef, useState } from 'react';
import { requestJson } from '../apiClient.js';
import { useResource } from './hooks.js';
import { Badge, Button, Card, DownloadButton, Empty, ErrorNotice, Field, Icon, JsonView, PageHeading, Skeleton } from './primitives.jsx';

const ROUTES = { query:'query', lookup:'records/lookup', keyword:'search/keyword', vector:'search/vector', hybrid:'search/hybrid', retrieve:'retrieve', aggregate:'aggregate' };
export function requestTemplate(operation, schema, retrieval) {
  const select = (schema?.fields || []).filter(x => x.selectable).slice(0,6).map(x => x.name);
  if (operation === 'query') return { select, limit:25, count_mode:'none' };
  if (operation === 'lookup') return { id_field:schema?.identity_fields?.[0] || '', ids:[], select, limit:25 };
  if (operation === 'vector') return retrieval?.vector?.query_text_supported
    ? { query_text:'', top_k:10, return_text:true, return_metadata:true }
    : { vector:[], vector_profile:retrieval?.vector?.profile_id || '', top_k:10, return_text:true, return_metadata:true };
  if (operation === 'retrieve') return { query:'', mode:'keyword', top_k:10, include_metadata:true };
  if (operation === 'aggregate') return { group_by:[], measures:[], limit:25 };
  return { query:'', top_k:10, return_text:true, return_metadata:true };
}
function parseValue(raw, field, op) {
  if (['in','not_in','between','array_overlaps','array_contains_all','json_contains'].includes(op)) return JSON.parse(raw);
  if (['exists','array_is_empty'].includes(op) || /bool/i.test(field?.data_type || '')) {
    if (!['true','false'].includes(raw)) throw new Error('Boolean filters require true or false.');
    return raw === 'true';
  }
  if (/int|numeric|decimal|float|double|real/i.test(field?.data_type || '')) {
    if (!raw.trim() || !Number.isFinite(Number(raw))) throw new Error('This field requires a numeric filter value.');
    return Number(raw);
  }
  return raw;
}
function QuickFilters({ contract, onApply }) {
  const [rules, setRules] = useState([]);
  const [logic, setLogic] = useState('and');
  const [error, setError] = useState(null);
  const fields = contract?.fields || [];
  function update(index, patch) { setRules(rows => rows.map((x,i) => i === index ? { ...x, ...patch } : x)); }
  return <details className="filter-builder"><summary><Icon name="shield" size={17}/> Build multi-field filters <span>AND / OR</span></summary>
    <p className="muted">Apply these rules to the request below. Nested AND / OR / NOT and JSON paths can also be edited directly in the request.</p>
    <Field label="Match rules"><select value={logic} onChange={e => setLogic(e.target.value)}><option value="and">All rules (AND)</option><option value="or">Any rule (OR)</option></select></Field>
    {rules.map((rule,i) => { const field = fields.find(x => x.field === rule.field); return <div className="filter-row" key={rule.key}>
      <select aria-label={'Filter field ' + (i+1)} value={rule.field} onChange={e => update(i,{ field:e.target.value, op:fields.find(x => x.field === e.target.value)?.operators[0] || 'eq', value:'' })}>{fields.map(x => <option key={x.field} value={x.field}>{x.field}</option>)}</select>
      <select aria-label={'Filter operator ' + (i+1)} value={rule.op} onChange={e => update(i,{ op:e.target.value, value:'' })}>{(field?.operators || []).map(x => <option key={x}>{x}</option>)}</select>
      <input aria-label={'Filter value ' + (i+1)} value={rule.value} placeholder="Value or JSON array" onChange={e => update(i,{ value:e.target.value })}/>
      <Button icon="close" aria-label={'Remove filter ' + (i+1)} onClick={() => setRules(rows => rows.filter((_,n) => n !== i))}/>
    </div>; })}
    <ErrorNotice error={error}/>
    <div className="button-row"><Button icon="plus" disabled={!fields.length || rules.length >= 20} onClick={() => setRules(rows => [...rows,{ key:crypto.randomUUID(),field:fields[0].field,op:fields[0].operators[0],value:'' }])}>Add rule</Button>
      <Button onClick={() => { try { onApply(rules.length ? { [logic]:rules.map(x => ({ field:x.field, op:x.op, value:parseValue(x.value,fields.find(f => f.field === x.field),x.op) })) } : null); setError(null); } catch(e) { setError(e); } }}>Apply filters to request</Button></div>
  </details>;
}
export function Explorer() {
  const catalog = useResource('/v1/datasets');
  const [dataset, setDataset] = useState('');
  const [operation, setOperation] = useState('');
  const [schema, setSchema] = useState(null);
  const [caps, setCaps] = useState([]);
  const [retrieval, setRetrieval] = useState(null);
  const [contract, setContract] = useState(null);
  const [request, setRequest] = useState('');
  const [response, setResponse] = useState(null);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(null);
  const running = useRef(null);
  const original = useRef(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; running.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    running.current?.abort(); setSchema(null); setCaps([]); setOperation(''); setResponse(null); setContract(null); setRetrieval(null); setError(null); setRequest('');
    if (!dataset) return () => controller.abort();
    setBindingBusy(true);
    (async () => {
      const root = '/v1/datasets/' + encodeURIComponent(dataset);
      const [s,c] = await Promise.all([requestJson(root + '/schema',{signal:controller.signal}),requestJson(root + '/capabilities',{signal:controller.signal})]);
      const available = c.capabilities.filter(x => ROUTES[x]);
      let r = null;
      if (available.some(x => ['keyword','vector','hybrid','retrieve'].includes(x))) r = await requestJson(root + '/retrieval-contract',{signal:controller.signal});
      if (!controller.signal.aborted) { setSchema(s); setRetrieval(r); setCaps(available); setOperation(available.includes('query') ? 'query' : available[0] || ''); }
    })().catch(e => { if (!controller.signal.aborted) setError(e); }).finally(() => { if (!controller.signal.aborted) setBindingBusy(false); });
    return () => controller.abort();
  }, [dataset]);
  useEffect(() => {
    const controller = new AbortController();
    running.current?.abort(); setResponse(null); setError(null); setContract(null); setBusy(false); original.current=null;
    if (!operation || !schema) return () => controller.abort();
    setRequest(JSON.stringify(requestTemplate(operation,schema,retrieval),null,2));
    const filterOp = ['lookup','aggregate'].includes(operation) ? 'query' : operation;
    requestJson('/v1/datasets/' + encodeURIComponent(dataset) + '/filters?operation=' + filterOp,{signal:controller.signal})
      .then(value => { if (!controller.signal.aborted) setContract(value); })
      .catch(e => { if (!controller.signal.aborted) setError(e); });
    return () => controller.abort();
  }, [operation,schema,retrieval,dataset]);
  async function run(next = false) {
    const controller = new AbortController(); running.current?.abort(); running.current=controller;
    setBusy(true); setError(null); const started=performance.now();
    try {
      const body = next ? { ...original.current, cursor:response.next_cursor } : JSON.parse(request);
      if (!next) original.current=structuredClone(body);
      const value = await requestJson('/v1/datasets/' + encodeURIComponent(dataset) + '/' + ROUTES[operation],{method:'POST',body,signal:controller.signal});
      if (!controller.signal.aborted && mounted.current) { setResponse(value); setElapsed(Math.round(performance.now()-started)); }
    } catch(e) { if (!controller.signal.aborted && mounted.current) { setResponse(null); setError(e); } }
    finally { if (mounted.current && running.current===controller) setBusy(false); }
  }
  function edit(value) { running.current?.abort(); setRequest(value); setResponse(null); setError(null); original.current=null; }
  const allowed = caps.flatMap(x => x === 'query' ? ['query','lookup'] : [x]);
  return <><PageHeading eyebrow="Consumer API workbench" title="API explorer" description="Discover permitted fields, build compound filters, and inspect real responses under your signed-in identity."/>
    <Card className="explorer-controls"><div className="form-grid">
      <Field label="Authorized data product"><select value={dataset} disabled={busy || catalog.loading} onChange={e => setDataset(e.target.value)}><option value="">Select a data product</option>{(catalog.data?.datasets || []).map(x => <option key={x.id} value={x.id}>{x.display_name} · {x.id}</option>)}</select></Field>
      <Field label="Operation"><select value={operation} disabled={busy || bindingBusy || !allowed.length} onChange={e => setOperation(e.target.value)}>{!allowed.length && <option value="">Select a data product first</option>}{allowed.map(x => <option key={x} value={x}>{x === 'lookup' ? 'Exact ID lookup' : x}</option>)}</select></Field>
    </div><ErrorNotice error={catalog.error} onRetry={catalog.refresh}/>{!catalog.loading && !catalog.error && !catalog.data?.datasets?.length && <Empty icon="key" title="No data products authorized for this identity" description="The API applies client grants, OAuth scopes, and data policies. Console administration alone does not grant source-record access."/>}</Card>
    {bindingBusy ? <Skeleton/> : dataset && operation && <>
      <div className="endpoint-strip"><span>POST</span><code>/v1/datasets/{dataset}/{ROUTES[operation]}</code><Badge value="governed"/></div>
      <div className="explorer-grid">
        <Card title="Request" subtitle="Every request is sent to the API; no browser-generated results.">
          {contract && <QuickFilters key={dataset+operation} contract={contract} onApply={filter => { const parsed=JSON.parse(request); if(filter)parsed.filter=filter;else delete parsed.filter; edit(JSON.stringify(parsed,null,2)); }}/>}
          <div className="card-padding"><Field label="Request JSON"><textarea className="code-input request-editor" spellCheck="false" disabled={busy} value={request} onChange={e => edit(e.target.value)}/></Field><ErrorNotice error={error}/><div className="button-row"><Button icon="play" variant="primary" busy={busy} disabled={!request} onClick={() => run(false)}>Run request</Button>{busy && <Button onClick={() => { running.current?.abort(); setBusy(false); }}>Cancel request</Button>}</div></div>
          <details className="contract-details"><summary>Inspect authorized schema & filter contract</summary><JsonView value={{schema,filters:contract,retrieval}}/></details>
        </Card>
        <Card title="Response" subtitle={response ? 'Returned by SmartHub · ' + elapsed + ' ms round trip' : 'Run a request to retrieve authorized data.'} action={response && <DownloadButton value={response} filename="smarthub-api-response.json" label="Save response"/>}>
          {busy ? <Skeleton/> : response ? <div className="response-content"><div className="response-meta"><Badge value="succeeded"/><span>{response.returned_rows ?? response.results?.length ?? response.rows?.length ?? 0} returned</span></div>{response.trace_id && <p className="trace-label">Trace ID <code>{response.trace_id}</code></p>}
            {response.results?.length > 0 && <div className="retrieval-hits">{response.results.map((hit,i) => <article key={(hit.chunk_id || hit.record_id) + ':' + i}><div><strong>{hit.metadata?.file_name || hit.source?.citations?.document_name || hit.record_id}</strong><span>#{i+1}</span></div><p>{hit.text || 'Text was not returned for this request.'}</p><details><summary>Scores, identity & citation fields</summary><JsonView value={{record_id:hit.record_id,chunk_id:hit.chunk_id,scores:hit.scores,ranks:hit.ranks,source:hit.source}}/></details></article>)}</div>}
            {response.rows?.length > 0 && <div className="table-scroll"><table><thead><tr>{Object.keys(response.rows[0]).map(key => <th key={key}>{key}</th>)}</tr></thead><tbody>{response.rows.slice(0,100).map((row,i) => <tr key={i}>{Object.keys(response.rows[0]).map(key => <td key={key}>{typeof row[key] === 'object' ? JSON.stringify(row[key]) : String(row[key] ?? 'NULL')}</td>)}</tr>)}</tbody></table>{response.rows.length>100 && <p className="muted">First 100 rows shown. The JSON response contains the complete returned page.</p>}</div>}
            {!response.rows?.length && !response.results?.length && <Empty icon="search" title="The request succeeded with no matches" description="Check the query, filters, and permissions; no results have been substituted."/>}
            <details open={!response.results?.length && !response.rows?.length}><summary>Full JSON response</summary><JsonView value={response}/></details>
            {response.next_cursor && <Button onClick={() => run(true)} busy={busy}>Load next page</Button>}
          </div> : <Empty icon="code" title="Your response will appear here" description="Text, metadata, citation fields, scores, and cursors are displayed as returned by the API."/>}
        </Card>
      </div>
    </>}
    {!dataset && <Card><Empty icon="code" title="Explore a real data contract" description="Start with a permitted data product. Query, exact lookup, keyword and vector operations appear only when your identity is allowed to use them."/></Card>}
    {error && (!dataset || !operation) && <ErrorNotice error={error}/>}
  </>;
}
