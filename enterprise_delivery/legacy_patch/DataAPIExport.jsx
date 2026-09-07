import React, { useState, useEffect, useCallback, useMemo, memo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiFetch } from '../utils/apiClient';
import {
  API_BASE_URL,
  readJson,
  tableUrl,
  tableUiUrl,
  downloadTextFile,
  buildConsumerDoc,
} from './dataApiDocs';

import networkIcon from "/icons/JJ_Icon_Network_RGB.svg";
import addIcon from "/icons/JJ_Icon_Add.svg";
import deleteIcon from "/icons/JJ_Icon_Delete_RGB.svg";
import closeIcon from "/icons/JJ_Icon_Close.svg";
import checkCircleIcon from "/icons/JJ_Icon_Check_RGB.svg";
import alertTriangleIcon from "/icons/JJ_Icon_Alert_RGB.svg";
import infoIcon from "/icons/JJ_Icon_Info_RGB.svg";
import databaseIcon from "/icons/JJ_Icon_Database_RGB.svg";
import refreshIcon from "/icons/JJ_Icon_Refresh_black.svg";
import lockIcon from "/icons/JJ_Icon_Lock_black.svg";
import linkIcon from "/icons/JJ_Icon_Link_RGB.svg";
import searchIcon from "/icons/JJ_Icon_Search_RGB.svg";

/* ───────────────────────────── icon helper ─────────────────────────────── */
/* The J&J SVGs ship with baked-in fills (a few are white), so colour comes
   from a filter rather than currentColor. */
const FILTERS = {
  white: 'brightness(0) invert(1)',
  red: 'brightness(0) saturate(100%) invert(13%) sepia(97%) saturate(6477%) hue-rotate(359deg) brightness(91%) contrast(99%)',
  blue: 'brightness(0) saturate(100%) invert(37%) sepia(93%) saturate(1535%) hue-rotate(189deg) brightness(91%) contrast(101%)',
  green: 'brightness(0) saturate(100%) invert(37%) sepia(87%) saturate(1158%) hue-rotate(71deg) brightness(91%) contrast(93%)',
  gray: 'brightness(0) saturate(100%) invert(50%) sepia(6%) saturate(656%) hue-rotate(346deg) brightness(92%) contrast(88%)',
  dark: 'brightness(0) saturate(100%) invert(15%) sepia(7%) saturate(806%) hue-rotate(346deg) brightness(95%) contrast(89%)',
};

const Icon = ({ src, alt = '', className = 'w-4 h-4', tone }) => (
  <img src={src} alt={alt} className={className} style={tone ? { filter: FILTERS[tone] } : undefined} />
);

const ENVIRONMENTS = [
  { id: 'dev', label: 'DEV' },
  { id: 'qa', label: 'QA' },
  { id: 'prod', label: 'PROD' },
];

const maskKey = (key) =>
  !key ? '' : `${key.slice(0, 9)}${'·'.repeat(Math.max(4, key.length - 13))}${key.slice(-4)}`;

/* Every time on this page renders in US Eastern, whatever the viewer's own
   timezone is. Rows written by the current backend carry an Eastern offset;
   older rows are naive UTC and get tagged before parsing, so both land on the
   right instant. */
const ET = 'America/New_York';

const parseTs = (iso) => {
  if (!iso) return null;
  const tagged = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(tagged);
  return Number.isNaN(d.getTime()) ? null : d;
};

const absolute = (iso) => {
  const d = parseTs(iso);
  if (!d) return '—';
  return `${d.toLocaleString('en-US', {
    timeZone: ET,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })} ET`;
};

const relative = (iso) => {
  const parsed = parseTs(iso);
  if (!parsed) return 'Never';
  const then = parsed.getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  const days = Math.round(hrs / 24);
  if (days < 31) return `${days} day${days === 1 ? '' : 's'} ago`;
  return parsed.toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric', year: 'numeric' });
};

const compactNum = (n) => {
  const v = Number(n) || 0;
  if (v >= 1000000) return `${(v / 1000000).toFixed(v >= 10000000 ? 0 : 1)}M`;
  if (v >= 10000) return `${Math.round(v / 1000)}k`;
  return v.toLocaleString();
};

/* Bucket dates are already Eastern calendar days — noon UTC keeps the label on
   the right day when it is formatted back in Eastern. */
const dayLabel = (isoDate) =>
  new Date(`${isoDate}T12:00:00Z`).toLocaleDateString('en-US', { timeZone: ET, month: 'short', day: 'numeric' });

const slugify = (s) =>
  (s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

const tablePath = (t) =>
  [t.source_system, (t.env || '').toUpperCase(), t.database, t.db_schema || t.schema, t.table]
    .filter(Boolean)
    .join(' · ');

/* ───────────────────────────────── toast ───────────────────────────────── */
const Toast = memo(({ message, type = 'success', onClose }) => {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const tone = type === 'error' ? 'bg-jj-maroon-05' : type === 'info' ? 'bg-jj-blue-03' : 'bg-jj-gray-08';
  const iconSrc = type === 'error' ? alertTriangleIcon : type === 'info' ? infoIcon : checkCircleIcon;

  return (
    <div className="fixed top-5 right-6 z-[80] animate-slide-in">
      <div className={`${tone} text-white px-4 py-3 rounded-xl shadow-2xl flex items-center gap-3 max-w-md`}>
        <Icon src={iconSrc} className="w-4 h-4 flex-shrink-0" tone="white" />
        <p className="font-johnson-text text-xs flex-1">{message}</p>
        <button onClick={onClose} aria-label="Dismiss" className="hover:opacity-70 transition-opacity">
          <Icon src={closeIcon} className="w-3.5 h-3.5" tone="white" />
        </button>
      </div>
    </div>
  );
});
Toast.displayName = 'Toast';

/* ───────────────────────── shared small primitives ─────────────────────── */
const StatusPill = ({ active }) => (
  <span
    className={`px-2.5 py-1 rounded-full text-[11px] font-johnson-text font-medium ${
      active ? 'bg-jj-green-03/10 text-jj-green-03' : 'bg-jj-gray-02 text-jj-gray-06'
    }`}
  >
    {active ? 'Active' : 'Inactive'}
  </span>
);

const GhostButton = ({ children, className = '', ...rest }) => (
  <button
    {...rest}
    className={`px-3 py-2 rounded-lg border border-jj-gray-03 bg-white font-johnson-text text-xs text-jj-gray-08 hover:bg-jj-gray-01 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${className}`}
  >
    {children}
  </button>
);

const DarkButton = ({ children, className = '', ...rest }) => (
  <button
    {...rest}
    className={`px-3 py-2 rounded-lg bg-jj-gray-08 text-white font-johnson-text text-xs hover:bg-jj-gray-07 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${className}`}
  >
    {children}
  </button>
);

const RedButton = ({ children, className = '', ...rest }) => (
  <button
    {...rest}
    className={`px-4 py-2.5 rounded-lg bg-jj-red text-white font-johnson-text text-sm font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity ${className}`}
  >
    {children}
  </button>
);

const FIELD =
  'w-full px-3 py-2 rounded-lg border border-jj-gray-03 bg-white font-johnson-text text-xs text-jj-gray-08 focus:outline-none focus:ring-2 focus:ring-jj-blue-03';

/* Live keys are never rendered into a URL on screen. Anything displayed shows
   this stand-in; Copy buttons and links substitute the real key. */
const KEY_PLACEHOLDER = 'YOUR_API_KEY';

/* Code panes (live responses and guide snippets) share one light/dark setting,
   toggled from the detail header. */
const codePane = (theme) =>
  theme === 'light'
    ? 'bg-jj-gray-01 border border-jj-gray-02 text-jj-gray-08'
    : 'bg-jj-gray-08 text-jj-gray-02';

const readCodeTheme = () => {
  try {
    return localStorage.getItem('dataApiCodeTheme') === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
};

function CodeThemeToggle({ value, onChange }) {
  return (
    <div className="flex gap-1 p-1 rounded-lg bg-jj-gray-01 border border-jj-gray-02">
      {[['light', 'Light'], ['dark', 'Dark']].map(([id, label]) => (
        <button
          key={id}
          onClick={() => onChange(id)}
          aria-pressed={value === id}
          title={`${label} code blocks`}
          className={`px-2.5 py-1 rounded font-johnson-text text-[11px] transition-colors ${
            value === id ? 'bg-jj-gray-08 text-white' : 'text-jj-gray-06 hover:text-jj-gray-08'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

const Label = ({ children }) => (
  <label className="block text-[11px] font-johnson-text text-jj-gray-06 mb-1">{children}</label>
);

/* ──────────────────────────── table picker ─────────────────────────────── */
/* Cascading source → env → database → schema → table. Unchanged behaviour,
   laid out as a compact inline grid instead of a modal block. */
function TablePicker({ existingAliases, onAdd, onCancel }) {
  const [sources, setSources] = useState([]);
  const [loadingSources, setLoadingSources] = useState(true);

  const [sourceSystem, setSourceSystem] = useState('');
  const [env, setEnv] = useState('prod');
  const [database, setDatabase] = useState('');
  const [schema, setSchema] = useState('');
  const [table, setTable] = useState('');
  const [alias, setAlias] = useState('');
  const [rowLimit, setRowLimit] = useState(1000);

  const [databases, setDatabases] = useState({ applicable: false, databases: [] });
  const [schemas, setSchemas] = useState({ applicable: false, schemas: [] });
  const [tables, setTables] = useState([]);
  const [loadingDbs, setLoadingDbs] = useState(false);
  const [loadingSchemas, setLoadingSchemas] = useState(false);
  const [loadingTables, setLoadingTables] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch('/data-api/sources');
        if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
        const data = await res.json();
        setSources(data.sources || []);
      } catch (e) {
        setError(`Could not load source systems: ${e.message}`);
      } finally {
        setLoadingSources(false);
      }
    })();
  }, []);

  const selected = useMemo(() => sources.find((s) => s.id === sourceSystem), [sources, sourceSystem]);
  const levels = selected?.levels || [];
  const hasEnvChoice = (selected?.environments || []).length > 0;

  useEffect(() => {
    setDatabase(''); setSchema(''); setTable(''); setAlias('');
    setDatabases({ applicable: false, databases: [] });
    setSchemas({ applicable: false, schemas: [] });
    setTables([]);
    if (!sourceSystem || !levels.includes('database')) return;
    setLoadingDbs(true);
    apiFetch(`/data-api/source-databases?source_system=${encodeURIComponent(sourceSystem)}&env=${encodeURIComponent(env)}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
        return res.json();
      })
      .then(setDatabases)
      .catch((e) => setError(`Could not load databases: ${e.message}`))
      .finally(() => setLoadingDbs(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSystem, env]);

  useEffect(() => {
    setSchema(''); setTable(''); setAlias('');
    setSchemas({ applicable: false, schemas: [] });
    setTables([]);
    if (!sourceSystem || !levels.includes('schema')) return;
    if (levels.includes('database') && !database) return;
    setLoadingSchemas(true);
    apiFetch(`/data-api/source-schemas?source_system=${encodeURIComponent(sourceSystem)}&env=${encodeURIComponent(env)}${database ? `&database=${encodeURIComponent(database)}` : ''}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
        return res.json();
      })
      .then(setSchemas)
      .catch((e) => setError(`Could not load schemas: ${e.message}`))
      .finally(() => setLoadingSchemas(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSystem, env, database]);

  useEffect(() => {
    setTable(''); setAlias(''); setTables([]);
    if (!sourceSystem) return;
    if (levels.includes('database') && !database) return;
    if (levels.includes('schema') && !schema) return;
    setLoadingTables(true);
    const params = new URLSearchParams({ source_system: sourceSystem, env });
    if (database) params.set('database', database);
    if (schema) params.set('schema', schema);
    apiFetch(`/data-api/source-tables?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setTables(data.tables || []))
      .catch((e) => setError(`Could not load tables: ${e.message}`))
      .finally(() => setLoadingTables(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSystem, env, database, schema]);

  const aliasTaken = !!alias.trim() && existingAliases.includes(alias.trim().toLowerCase());
  const canAdd =
    sourceSystem && table && alias.trim() && !aliasTaken &&
    (!levels.includes('database') || database) &&
    (!levels.includes('schema') || schema);

  const handleAdd = () => {
    if (!canAdd) return;
    onAdd({
      source_system: sourceSystem,
      env,
      database: database || null,
      db_schema: schema || null,
      table,
      alias: alias.trim(),
      row_limit: Number(rowLimit) || 1000,
    });
    setTable(''); setAlias('');
  };

  return (
    <div className="bg-jj-gray-01 border border-jj-gray-02 rounded-xl p-4">
      {error && (
        <div className="flex items-center gap-2 mb-3 text-jj-maroon-05 text-xs font-johnson-text">
          <Icon src={alertTriangleIcon} className="w-3.5 h-3.5" tone="red" /> {error}
        </div>
      )}

      <p className="text-[11px] font-johnson-text text-jj-gray-06 mb-3">
        Pick a table, then give it the alias consumers will use in the URL. Nothing else in the source becomes reachable.
      </p>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <div>
          <Label>Source system</Label>
          <select className={FIELD} value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} disabled={loadingSources}>
            <option value="">{loadingSources ? 'Loading…' : 'Select a source…'}</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id} disabled={!s.configured}>
                {s.label}{!s.configured ? ' (not configured)' : ''}
              </option>
            ))}
          </select>
        </div>

        {hasEnvChoice && (
          <div>
            <Label>Environment</Label>
            <select className={FIELD} value={env} onChange={(e) => setEnv(e.target.value)}>
              {ENVIRONMENTS.filter((e) => (selected?.environments || []).includes(e.id)).map((e) => (
                <option key={e.id} value={e.id}>{e.label}</option>
              ))}
            </select>
          </div>
        )}

        {sourceSystem && levels.includes('database') && (
          <div>
            <Label>Database</Label>
            <select className={FIELD} value={database} onChange={(e) => setDatabase(e.target.value)} disabled={loadingDbs}>
              <option value="">{loadingDbs ? 'Loading…' : 'Select a database…'}</option>
              {databases.databases.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        )}

        {sourceSystem && levels.includes('schema') && (!levels.includes('database') || database) && (
          <div>
            <Label>Schema</Label>
            <select className={FIELD} value={schema} onChange={(e) => setSchema(e.target.value)} disabled={loadingSchemas}>
              <option value="">{loadingSchemas ? 'Loading…' : 'Select a schema…'}</option>
              {schemas.schemas.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        )}

        {sourceSystem && (!levels.includes('database') || database) && (!levels.includes('schema') || schema) && (
          <div>
            <Label>Table</Label>
            <select
              className={FIELD}
              value={table}
              disabled={loadingTables}
              onChange={(e) => {
                setTable(e.target.value);
                setAlias(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'));
              }}
            >
              <option value="">{loadingTables ? 'Loading…' : 'Select a table…'}</option>
              {tables.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        )}

        {table && (
          <>
            <div>
              <Label>Alias — used in the URL</Label>
              <input
                className={FIELD}
                value={alias}
                onChange={(e) => setAlias(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))}
                placeholder="e.g. customers"
              />
              {aliasTaken && (
                <p className="text-[11px] text-jj-maroon-05 mt-1 font-johnson-text">Alias already used on this API.</p>
              )}
            </div>
            <div>
              <Label>Rows per page</Label>
              <input type="number" min={1} max={10000} className={FIELD} value={rowLimit} onChange={(e) => setRowLimit(e.target.value)} />
              <p className="text-[11px] text-jj-gray-06 mt-1 font-johnson-text">
                Caps one call. Callers page through the whole table with <code>offset</code>.
              </p>
            </div>
          </>
        )}
      </div>

      <div className="flex items-center gap-2 mt-4">
        <DarkButton onClick={handleAdd} disabled={!canAdd} className="flex items-center gap-1.5">
          <Icon src={addIcon} className="w-3.5 h-3.5" tone="white" /> Add table
        </DarkButton>
        {onCancel && <GhostButton onClick={onCancel}>Cancel</GhostButton>}
      </div>
    </div>
  );
}

/* ─────────────────────────── endpoint reference ────────────────────────── */
function endpointsFor(publicId, tables, apiKey) {
  // Consumer secrets never appear in displayed/copied URLs. The generated cURL
  // command and live probes send the key in the Authorization header instead.
  const base = `${API_BASE_URL}/data-api/${publicId}`;
  const authHeader = `Bearer ${apiKey}`;
  const withCurl = (url) => `curl -H \"Authorization: Bearer ${apiKey}\" \"${url}\"`;
  const first = tables[0];
  const alias = first ? first.alias : null;
  const limit = first?.row_limit ?? 1000;
  const defs = [
    {
      key: 'tables',
      path: '/tables',
      url: `${base}/tables`,
      copyCommand: withCurl(`${base}/tables`),
      probe: `/data-api/${publicId}/tables`,
      authHeader,
      description: 'Lists every alias on this API with its source and row limit. The first call a consumer makes.',
    },
  ];
  if (!alias) return defs;
  return defs.concat([
    {
      key: 'columns',
      path: `/${alias}/columns`,
      url: `${base}/${alias}/columns`,
      copyCommand: withCurl(`${base}/${alias}/columns`),
      probe: `/data-api/${publicId}/${alias}/columns`,
      authHeader,
      description: 'Column names and types for one alias — enough to build a schema before paging rows.',
    },
    {
      key: 'count',
      path: `/${alias}/count`,
      url: `${base}/${alias}/count`,
      copyCommand: withCurl(`${base}/${alias}/count`),
      probe: `/data-api/${publicId}/${alias}/count`,
      authHeader,
      description: 'Total rows, optionally filtered by search. Pass column= alongside search to scan one column instead of all of them — much faster on wide tables.',
    },
    {
      key: 'rows',
      path: `/${alias}/rows`,
      url: `${base}/${alias}/rows?limit=${limit}&offset=0`,
      copyCommand: withCurl(`${base}/${alias}/rows?limit=${limit}&offset=0`),
      probe: `/data-api/${publicId}/${alias}/rows?limit=3&offset=0`,
      authHeader,
      probeNote: 'run with limit=3',
      description: 'The data. Always follow the server-provided next_offset; the server may cap the requested page size.',
    },
  ]);
}

/* Interactive request console. Builds the URL from real aliases and sends the
   real call — the preview masks the key, the request carries it. */
function TryItConsole({ publicId, tables, apiKey, showToast, codeTheme }) {
  const [alias, setAlias] = useState(tables[0]?.alias || '');
  const selected = tables.find((t) => t.alias === alias) || tables[0];
  const [limit, setLimit] = useState(String(selected?.row_limit ?? 1000));
  const [offset, setOffset] = useState('0');
  const [search, setSearch] = useState('');
  const [column, setColumn] = useState('');
  const [columns, setColumns] = useState({ status: 'idle', list: [] });
  const [state, setState] = useState({ status: 'idle' });

  /* Column list comes from the API's own /columns endpoint, so the picker can
     only ever offer columns that really exist on the source table. */
  useEffect(() => {
    if (!alias) return;
    let cancelled = false;
    setColumns({ status: 'loading', list: [] });
    setColumn('');
    fetch(`${API_BASE_URL}/data-api/${publicId}/${alias}/columns`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setColumns({ status: 'done', list: (data.columns || []).map((c) => c.name) });
      })
      .catch(() => {
        if (!cancelled) setColumns({ status: 'error', list: [] });
      });
    return () => { cancelled = true; };
  }, [publicId, alias, apiKey]);

  const onAlias = (next) => {
    setAlias(next);
    const t = tables.find((x) => x.alias === next);
    setLimit(String(t?.row_limit ?? 1000));
    setOffset('0');
    setState({ status: 'idle' });
  };

  const qs = () => {
    const p = new URLSearchParams();
    p.set('limit', limit || '0');
    p.set('offset', offset || '0');
    if (search.trim()) {
      p.set('search', search.trim());
      /* column only narrows a search — sending it alone would do nothing but
         cost a validation round trip. */
      if (column) p.set('column', column);
    }
    return p.toString();
  };

  const previewUrl = `${API_BASE_URL}/data-api/${publicId}/${alias}/rows?${qs()}`;

  const send = async () => {
    setState({ status: 'sending' });
    const started = performance.now();
    try {
      const res = await fetch(`${API_BASE_URL}/data-api/${publicId}/${alias}/rows?${qs()}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const body = await res.json().catch(() => null);
      setState({
        status: 'done',
        httpStatus: res.status,
        ms: Math.round(performance.now() - started),
        rowCount: body && typeof body.row_count === 'number' ? body.row_count : null,
        body: body === null ? '(no JSON body)' : JSON.stringify(body, null, 2),
      });
    } catch (e) {
      setState({ status: 'failed', ms: Math.round(performance.now() - started), message: e.message });
    }
  };

  const badge =
    state.status === 'idle' ? { text: 'not sent', tone: 'bg-jj-gray-02 text-jj-gray-06' }
    : state.status === 'sending' ? { text: 'sending', tone: 'bg-jj-gray-02 text-jj-gray-07' }
    : state.status === 'failed' ? { text: 'failed', tone: 'bg-jj-maroon-05/10 text-jj-maroon-05' }
    : state.httpStatus >= 400 ? { text: `HTTP ${state.httpStatus}`, tone: 'bg-jj-maroon-05/10 text-jj-maroon-05' }
    : { text: `HTTP ${state.httpStatus}`, tone: 'bg-jj-green-03/10 text-jj-green-03' };

  const hint =
    state.status === 'idle' ? 'press Send'
    : state.status === 'sending' ? '…'
    : state.status === 'failed' ? `${state.ms} ms`
    : `${state.ms} ms${state.rowCount != null ? ` · ${state.rowCount.toLocaleString()} rows` : ''}`;

  const truncated = state.body && state.body.length > 6000;

  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-jj-gray-01">
        <h3 className="font-johnson-display text-[15px] text-jj-gray-08">Try it</h3>
        <p className="font-johnson-text text-[11px] text-jj-gray-05">Runs against the live source with your key</p>
      </div>

      <div className="flex flex-wrap items-end gap-3 px-5 py-4">
        <div className="w-64">
          <Label>Alias / table</Label>
          <select className={FIELD} value={alias} onChange={(e) => onAlias(e.target.value)}>
            {tables.map((t) => (
              <option key={t.alias} value={t.alias}>/{t.alias} · {t.table}</option>
            ))}
          </select>
        </div>
        <div className="w-24">
          <Label>Limit</Label>
          <input
            className={FIELD}
            value={limit}
            onChange={(e) => setLimit(e.target.value.replace(/[^0-9]/g, ''))}
          />
        </div>
        <div className="w-24">
          <Label>Offset</Label>
          <input
            className={FIELD}
            value={offset}
            onChange={(e) => setOffset(e.target.value.replace(/[^0-9]/g, ''))}
          />
        </div>
        <div className="flex-1 min-w-[180px]">
          <Label>Search</Label>
          <input className={FIELD} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="optional term" />
        </div>
        <div className="w-52">
          <Label>In column</Label>
          <select
            className={FIELD}
            value={column}
            disabled={columns.status !== 'done' || !columns.list.length}
            onChange={(e) => setColumn(e.target.value)}
          >
            <option value="">
              {columns.status === 'loading' ? 'Loading columns…'
                : columns.status === 'error' ? 'Columns unavailable'
                : 'All columns'}
            </option>
            {columns.list.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <DarkButton onClick={send} disabled={!alias || state.status === 'sending'} className="px-5 py-2.5 text-sm">
          {state.status === 'sending' ? 'Sending…' : 'Send'}
        </DarkButton>
      </div>

      <div className="flex items-center gap-3 px-5 pb-1">
        <p className="font-johnson-text text-[11px] text-jj-gray-05">
          {search.trim()
            ? column
              ? `Scans ${column} only — much faster than searching every column.`
              : 'Scans every column. Pick one to narrow it and speed up wide tables.'
            : 'Leave Search empty to page straight through the table.'}
        </p>
      </div>

      <div className="flex items-center gap-3 px-5 pb-3">
        <span className={`font-johnson-text text-[11px] px-2 py-1 rounded ${badge.tone} flex-shrink-0`}>{badge.text}</span>
        <code className="flex-1 font-mono text-[11px] text-jj-gray-07 truncate">{previewUrl}</code>
        <button
          onClick={() => {
            navigator.clipboard.writeText(`curl -H \"Authorization: Bearer ${apiKey}\" \"${API_BASE_URL}/data-api/${publicId}/${alias}/rows?${qs()}\"`);
            showToast('cURL copied with Authorization header.');
          }}
          className="font-johnson-text text-[11px] text-jj-blue-03 hover:text-jj-red transition-colors flex-shrink-0"
        >
          copy cURL
        </button>
        <span className="font-johnson-text text-[11px] text-jj-gray-05 flex-shrink-0">{hint}</span>
      </div>

      <pre className={`mx-5 mb-5 rounded-lg px-4 py-3 font-mono text-[11px] leading-relaxed overflow-auto max-h-96 ${codePane(codeTheme)}`}>
        {state.status === 'idle'
          ? '// Response appears here.\n// Nothing leaves the page until you press Send.'
          : state.status === 'sending'
          ? '// Calling the endpoint…'
          : state.status === 'failed'
          ? `// Request failed\n${state.message}`
          : truncated
          ? `${state.body.slice(0, 6000)}\n… truncated`
          : state.body}
      </pre>
    </div>
  );
}

function EndpointCard({ endpoint, showToast, codeTheme }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: 'idle' });

  const run = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setState({ status: 'loading' });
    const started = performance.now();
    try {
      const res = await fetch(`${API_BASE_URL}${endpoint.probe}`, {
        headers: { Authorization: endpoint.authHeader },
      });
      const body = await res.json().catch(() => null);
      setState({
        status: 'done',
        httpStatus: res.status,
        ms: Math.round(performance.now() - started),
        body: body === null ? '(no JSON body)' : JSON.stringify(body, null, 2),
      });
    } catch (e) {
      setState({ status: 'error', message: e.message });
    }
  };

  const truncated = state.body && state.body.length > 4000;

  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl p-4">
      <div className="flex items-center gap-3 mb-2">
        <span className="font-mono text-[10px] font-bold text-jj-green-03 border border-jj-green-03/30 rounded px-1.5 py-0.5">GET</span>
        <code className="font-mono text-xs font-medium text-jj-gray-08">{endpoint.path}</code>
        <span className="flex-1" />
        <GhostButton
          onClick={() => { navigator.clipboard.writeText(endpoint.copyCommand); showToast('cURL copied with Authorization header.'); }}
        >
          Copy cURL
        </GhostButton>
        <DarkButton onClick={run}>{open ? 'Hide response' : 'Run it'}</DarkButton>
      </div>
      <p className="font-johnson-text text-xs text-jj-gray-06 mb-3 max-w-[80ch] leading-relaxed">{endpoint.description}</p>
      <div className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2">
        <Icon src={linkIcon} className="w-3.5 h-3.5 flex-shrink-0" tone="blue" />
        <code className="font-mono text-[11px] text-jj-gray-07 truncate">{endpoint.url}</code>
      </div>

      {open && (
        <>
          <div className="flex items-center gap-2 mt-3 mb-1.5">
            <p className="font-johnson-text text-[11px] text-jj-gray-05">
              Live response{endpoint.probeNote ? ` — ${endpoint.probeNote}` : ''}
              {state.status === 'done' ? ` · HTTP ${state.httpStatus} · ${state.ms} ms` : ''}
            </p>
            <span className="flex-1" />
            {state.status === 'done' && (
              <button onClick={run} className="font-johnson-text text-[11px] text-jj-blue-03 hover:text-jj-red transition-colors">
                Run again
              </button>
            )}
          </div>
          {state.status === 'loading' && (
            <p className="font-johnson-text text-xs text-jj-gray-06 py-2">Calling the endpoint…</p>
          )}
          {state.status === 'error' && (
            <p className="font-johnson-text text-xs text-jj-maroon-05 py-2">Request failed: {state.message}</p>
          )}
          {state.status === 'done' && (
            <pre className={`rounded-lg px-4 py-3 font-mono text-[11px] leading-relaxed overflow-x-auto max-h-80 ${codePane(codeTheme)}`}>
              {truncated ? `${state.body.slice(0, 4000)}\n… truncated` : state.body}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

/* ─────────────────────────── usage presentation ────────────────────────── */
/* Everything here renders whatever /data-api/:id/usage returned. No synthetic
   series: an API with no calls draws an empty axis and says so. */
/* Minimal markdown renderer, scoped to what buildConsumerDoc emits: headings,
   fenced code, pipe tables, dash lists, rules, bold and inline code. The Docs
   tab renders the guide string itself, so the screen and the .md download can
   never drift apart. */
function mdInline(text, keyPrefix) {
  const parts = String(text).split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) => {
    const k = `${keyPrefix}-${i}`;
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={k} className="font-mono text-[11px] bg-jj-gray-01 border border-jj-gray-02 rounded px-1 py-0.5">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <b key={k} className="font-medium text-jj-gray-08">{part.slice(2, -2)}</b>;
    }
    return <span key={k}>{part}</span>;
  });
}

function renderMarkdown(md, codeTheme = 'dark') {
  const lines = String(md).split('\n');
  const out = [];
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    const text = para.join(' ');
    out.push(
      <p key={`p-${out.length}`} className="font-johnson-text text-xs text-jj-gray-07 leading-relaxed max-w-[86ch] mt-2">
        {mdInline(text, `p-${out.length}`)}
      </p>
    );
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      flushPara();
      const dark = codeTheme === 'dark';
      const paneClass = dark ? 'bg-jj-gray-08 text-jj-gray-02' : 'bg-jj-gray-01 border border-jj-gray-02 text-jj-gray-08';
      out.push(
        <pre
          key={`code-${out.length}`}
          className={`rounded-lg px-4 py-3 font-mono text-[11px] leading-relaxed overflow-x-auto mt-3 ${paneClass}`}
        >
          {buf.join('\n')}
        </pre>
      );
      continue;
    }

    if (line.trim().startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(lines[i].trim());
        i += 1;
      }
      flushPara();
      const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const header = cells(rows[0]);
      const body = rows.slice(rows[1] && /^[\s|:-]+$/.test(rows[1]) ? 2 : 1).map(cells);
      out.push(
        <div key={`tbl-${out.length}`} className="border border-jj-gray-02 rounded-lg overflow-hidden mt-3 max-w-[86ch]">
          <div className="flex bg-jj-gray-01 border-b border-jj-gray-02">
            {header.map((h, hi) => (
              <span key={hi} className="flex-1 px-4 py-2.5 font-johnson-text text-[11px] text-jj-gray-06">{h}</span>
            ))}
          </div>
          {body.map((r, ri) => (
            <div key={ri} className="flex border-b border-jj-gray-01 last:border-0">
              {r.map((c, ci) => (
                <span key={ci} className="flex-1 px-4 py-2.5 font-johnson-text text-xs text-jj-gray-07">
                  {mdInline(c, `t-${ri}-${ci}`)}
                </span>
              ))}
            </div>
          ))}
        </div>
      );
      continue;
    }

    if (line.startsWith('- ')) {
      const items = [];
      while (i < lines.length && lines[i].startsWith('- ')) {
        items.push(lines[i].slice(2));
        i += 1;
      }
      flushPara();
      out.push(
        <ul key={`ul-${out.length}`} className="list-disc pl-5 mt-2 space-y-1 max-w-[86ch]">
          {items.map((it, ii) => (
            <li key={ii} className="font-johnson-text text-xs text-jj-gray-07 leading-relaxed">
              {mdInline(it, `li-${ii}`)}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    if (line.startsWith('### ')) {
      flushPara();
      out.push(<h5 key={`h5-${out.length}`} className="font-johnson-display text-sm text-jj-gray-08 mt-5">{line.slice(4)}</h5>);
      i += 1;
      continue;
    }
    if (line.startsWith('## ')) {
      flushPara();
      out.push(<h4 key={`h4-${out.length}`} className="font-johnson-display text-base text-jj-gray-08 mt-7">{line.slice(3)}</h4>);
      i += 1;
      continue;
    }
    if (line.startsWith('# ')) {
      flushPara();
      out.push(<h3 key={`h3-${out.length}`} className="font-johnson-display text-2xl text-jj-gray-08">{line.slice(2)}</h3>);
      i += 1;
      continue;
    }
    if (line.trim() === '---') {
      flushPara();
      out.push(<hr key={`hr-${out.length}`} className="border-0 border-t border-jj-gray-02 my-6" />);
      i += 1;
      continue;
    }
    if (!line.trim()) {
      flushPara();
      i += 1;
      continue;
    }

    para.push(line);
    i += 1;
  }

  flushPara();
  return out;
}

function DocsPanel({ detail, publicId, tables, showToast, codeTheme }) {
  const filename = `SmartHub_${(detail.name || 'data-api').replace(/\s+/g, '_')}_API_guide.md`;
  const markdown = useMemo(
    () => buildConsumerDoc(detail.name || 'Data API Export', publicId, detail.api_key, tables),
    [detail.name, detail.api_key, publicId, tables]
  );

  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl overflow-hidden">
      <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-jj-gray-01">
        <div>
          <h3 className="font-johnson-display text-[15px] text-jj-gray-08">Consumer guide</h3>
          <p className="font-johnson-text text-xs text-jj-gray-06 mt-0.5">
            Exactly what the download contains — generated from this API&apos;s aliases and limits.
          </p>
        </div>
        <button
          onClick={() => { downloadTextFile(filename, markdown); showToast('Consumer guide downloaded.'); }}
          className="px-4 py-2.5 rounded-lg bg-jj-gray-08 text-white font-johnson-text text-sm hover:bg-jj-gray-07 transition-colors flex-shrink-0"
        >
          Download .md
        </button>
      </div>
      <div className="px-6 py-6">{renderMarkdown(markdown, codeTheme)}</div>
    </div>
  );
}

function UsageChart({ buckets, days, onDaysChange }) {
  const peak = Math.max(...buckets.map((b) => b.calls), 0);
  const silent = peak === 0;

  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl px-5 py-4">
      <div className="flex items-baseline justify-between mb-4">
        <h3 className="font-johnson-display text-[15px] text-jj-gray-08">Requests per day</h3>
        <div className="flex items-center gap-3">
          {!silent && <span className="font-johnson-text text-[11px] text-jj-gray-05">peak {peak.toLocaleString()}/day</span>}
          <div className="flex gap-1">
            {[7, 14, 30].map((d) => (
              <button
                key={d}
                onClick={() => onDaysChange(d)}
                className={`px-2 py-0.5 rounded font-johnson-text text-[11px] transition-colors ${
                  days === d ? 'bg-jj-gray-08 text-white' : 'text-jj-gray-06 hover:bg-jj-gray-01'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="relative flex items-end gap-[3px] h-[104px]">
        {buckets.map((b, i) => (
          <div
            key={b.date}
            title={`${dayLabel(b.date)} — ${b.calls.toLocaleString()} call${b.calls === 1 ? '' : 's'}, ${b.rows_served.toLocaleString()} rows`}
            className={`flex-1 rounded-t ${i === buckets.length - 1 ? 'bg-jj-red' : 'bg-jj-blue-03'}`}
            style={{
              height: silent ? 2 : `${Math.max(2, Math.round((b.calls / peak) * 100))}%`,
              opacity: silent ? 0.25 : i === buckets.length - 1 ? 1 : 0.3 + (b.calls / peak) * 0.5,
            }}
          />
        ))}
        {silent && (
          <p className="absolute inset-0 flex items-center justify-center font-johnson-text text-xs text-jj-gray-05">
            No calls in this window
          </p>
        )}
      </div>

      <div className="flex justify-between mt-2 font-johnson-text text-[11px] text-jj-gray-05">
        <span>{buckets.length ? dayLabel(buckets[0].date) : ''}</span>
        <span>today · Eastern days</span>
      </div>
    </div>
  );
}

/* Consumer calls carry no identity beyond the key, so the user-agent is the
   only hint about what made the call. Map the common ones to something
   readable instead of printing a truncated UA string. */
const describeAgent = (ua) => {
  if (!ua) return '';
  const s = ua.toLowerCase();
  if (s.includes('powerbi') || s.includes('power bi')) return 'Power BI';
  if (s.includes('msoffice') || s.includes('excel')) return 'Excel / Power Query';
  if (s.includes('tableau')) return 'Tableau';
  if (s.includes('postman')) return 'Postman';
  if (s.startsWith('curl')) return 'curl';
  if (s.includes('python-requests') || s.includes('httpx') || s.includes('urllib')) return 'Python script';
  if (s.includes('java/') || s.includes('okhttp')) return 'Java client';
  if (s.includes('node') || s.includes('axios')) return 'Node script';
  if (s.includes('databricks') || s.includes('spark')) return 'Databricks';

  if (s.includes('mozilla')) {
    const browser = s.includes('edg/') ? 'Edge'
      : s.includes('chrome/') ? 'Chrome'
      : s.includes('firefox/') ? 'Firefox'
      : s.includes('safari/') ? 'Safari'
      : 'Browser';
    const os = s.includes('windows') ? 'Windows'
      : s.includes('mac os') ? 'macOS'
      : s.includes('linux') ? 'Linux'
      : '';
    return os ? `${browser} on ${os}` : browser;
  }
  return ua.length > 32 ? `${ua.slice(0, 32)}…` : ua;
};

const describeIp = (ip) =>
  ip === '127.0.0.1' || ip === '::1' || ip === 'localhost' ? `${ip} (this machine)` : ip;

function CallersCard({ callers, loggingSince }) {
  return (
    <div className="bg-white border border-jj-gray-02 rounded-xl px-5 py-4">
      <h3 className="font-johnson-display text-[15px] text-jj-gray-08 mb-1">Callers</h3>
      <p className="font-johnson-text text-[11px] text-jj-gray-05 mb-3">
        One row per client, grouped by machine — the key is the only credential these endpoints take.
      </p>
      {callers.length === 0 ? (
        <p className="font-johnson-text text-xs text-jj-gray-05 py-3">
          {loggingSince ? 'No calls in this window.' : 'No calls recorded yet.'}
        </p>
      ) : (
        callers.map((c) => {
          const agents = c.agents || [];
          /* One machine with one client stays a single line. Several clients on
             the same machine get the IP as a header with a line each, so they
             read as one caller rather than several. */
          if (agents.length <= 1) {
            return (
              <div key={c.ip} className="flex items-center justify-between gap-3 py-2.5 border-b border-jj-gray-01 last:border-0">
                <div className="min-w-0">
                  <p className="font-mono text-xs text-jj-gray-08 truncate">{describeIp(c.ip)}</p>
                  <p className="font-johnson-text text-[11px] text-jj-gray-06 truncate">
                    {describeAgent(c.user_agent) || 'unknown client'} · last call {relative(c.last_call).toLowerCase()}
                  </p>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="font-mono text-xs text-jj-gray-08">{c.calls.toLocaleString()} call{c.calls === 1 ? '' : 's'}</p>
                  <p className="font-johnson-text text-[11px] text-jj-gray-06">{compactNum(c.rows_served)} rows</p>
                </div>
              </div>
            );
          }
          return (
            <div key={c.ip} className="py-2.5 border-b border-jj-gray-01 last:border-0">
              <div className="flex items-center justify-between gap-3">
                <p className="font-mono text-xs text-jj-gray-08 truncate">{describeIp(c.ip)}</p>
                <p className="font-johnson-text text-[11px] text-jj-gray-06 flex-shrink-0">
                  {c.calls.toLocaleString()} calls · {compactNum(c.rows_served)} rows
                </p>
              </div>
              {agents.map((a) => (
                <div key={a.user_agent} className="flex items-center justify-between gap-3 mt-1.5 pl-3 border-l-2 border-jj-gray-02">
                  <p className="font-johnson-text text-[11px] text-jj-gray-06 truncate">
                    {describeAgent(a.user_agent) || 'unknown client'} · last call {relative(a.last_call).toLowerCase()}
                  </p>
                  <p className="font-johnson-text text-[11px] text-jj-gray-06 flex-shrink-0">
                    {a.calls.toLocaleString()} · {compactNum(a.rows_served)} rows
                  </p>
                </div>
              ))}
            </div>
          );
        })
      )}
    </div>
  );
}

/* ──────────────────────────── create wizard ────────────────────────────── */
const STEPS = ['Details', 'Tables', 'Review'];

function CreateWizard({ onCancel, onCreated, showToast }) {
  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tables, setTables] = useState([]);
  const [creating, setCreating] = useState(false);

  const canContinue = step === 1 ? !!name.trim() : step === 2 ? tables.length > 0 : true;

  const create = async () => {
    setCreating(true);
    try {
      const res = await apiFetch('/data-api/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), description: description.trim(), tables }),
      });
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      onCreated(await res.json());
    } catch (e) {
      showToast(`Could not create API: ${e.message}`, 'error');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-8 py-10">
      <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05 mb-1">New data API</p>
      <h2 className="font-johnson-display text-2xl text-jj-gray-08 mb-6">
        {step === 1 ? 'What is this API for?' : step === 2 ? 'Which tables does it expose?' : 'Review and create'}
      </h2>

      <div className="flex gap-2 mb-6">
        {STEPS.map((label, i) => (
          <div key={label} className="flex-1">
            <div className={`h-[3px] rounded-full ${i + 1 <= step ? 'bg-jj-red' : 'bg-jj-gray-02'}`} />
            <p className={`mt-2 font-johnson-text text-[11px] ${i + 1 <= step ? 'text-jj-gray-08' : 'text-jj-gray-05'}`}>{label}</p>
          </div>
        ))}
      </div>

      <div className="bg-white border border-jj-gray-02 rounded-xl p-6">
        {step === 1 && (
          <>
            <Label>Name</Label>
            <input className={`${FIELD} text-sm mb-4`} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sales team read access" />
            <Label>What it&apos;s for, and who gets the key</Label>
            <textarea
              className={`${FIELD} text-sm`}
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Quarterly territory pulls for commercial analytics"
            />
          </>
        )}

        {step === 2 && (
          <>
            <TablePicker existingAliases={tables.map((t) => t.alias.toLowerCase())} onAdd={(t) => setTables([...tables, t])} />
            <div className="mt-4 border-t border-jj-gray-01 pt-3">
              {tables.length === 0 ? (
                <p className="font-johnson-text text-xs text-jj-gray-05">No tables added yet.</p>
              ) : (
                tables.map((t, i) => (
                  <div key={`${t.alias}-${i}`} className="flex items-center gap-3 py-2.5 border-b border-jj-gray-01 last:border-0">
                    <code className="font-mono text-xs font-medium text-jj-gray-08 w-40">/{t.alias}</code>
                    <span className="flex-1 font-johnson-text text-xs text-jj-gray-07 truncate">{tablePath(t)}</span>
                    <span className="font-johnson-text text-[11px] text-jj-gray-06">{t.row_limit} / page</span>
                    <button onClick={() => setTables(tables.filter((_, j) => j !== i))} className="p-1 rounded hover:bg-jj-gray-01" aria-label={`Remove ${t.alias}`}>
                      <Icon src={closeIcon} className="w-3 h-3" tone="gray" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <dl className="grid grid-cols-[150px_1fr] gap-y-2.5 gap-x-4 font-johnson-text text-sm">
              <dt className="text-jj-gray-06">Name</dt><dd className="text-jj-gray-08">{name}</dd>
              <dt className="text-jj-gray-06">Description</dt><dd className="text-jj-gray-07">{description || 'No description'}</dd>
              <dt className="text-jj-gray-06">Base URL</dt>
              <dd><code className="font-mono text-xs text-jj-blue-03">/data-api/{slugify(name)}</code></dd>
              <dt className="text-jj-gray-06">Tables</dt>
              <dd className="text-jj-gray-08">{tables.length} alias{tables.length === 1 ? '' : 'es'}: {tables.map((t) => `/${t.alias}`).join(', ')}</dd>
              <dt className="text-jj-gray-06">Key</dt>
              <dd className="text-jj-gray-07">Generated on create and shown once. Rotate it anytime.</dd>
            </dl>
            <div className="mt-5 flex items-start gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-4 py-3">
              <Icon src={alertTriangleIcon} className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <p className="font-johnson-text text-xs text-jj-gray-07 leading-relaxed">
                These endpoints are reachable without a SmartHub login. Only the aliases above are exposed, read-only.
              </p>
            </div>
          </>
        )}
      </div>

      <div className="flex items-center gap-2 mt-5">
        <button onClick={onCancel} className="px-3 py-2 font-johnson-text text-sm text-jj-gray-07 hover:text-jj-red transition-colors">Cancel</button>
        <span className="flex-1" />
        {step > 1 && <GhostButton onClick={() => setStep(step - 1)} className="text-sm px-4">Back</GhostButton>}
        <RedButton
          disabled={!canContinue || creating}
          onClick={() => (step < 3 ? setStep(step + 1) : create())}
        >
          {step < 3 ? 'Continue' : creating ? 'Creating…' : 'Create API'}
        </RedButton>
      </div>
    </div>
  );
}

/* ─────────────────────── key hand-off (after create) ───────────────────── */
function KeyHandoff({ payload, onDone, showToast }) {
  const publicId = payload.slug || payload.id;
  const tables = payload.tables || [];
  return (
    <div className="max-w-3xl mx-auto px-8 py-12">
      <div className="flex items-center gap-2.5 mb-2">
        <Icon src={checkCircleIcon} className="w-5 h-5" tone="green" />
        <h2 className="font-johnson-display text-2xl text-jj-gray-08">{payload.name} is live</h2>
      </div>
      <p className="font-johnson-text text-sm text-jj-gray-07 mb-6 max-w-[70ch] leading-relaxed">
        Copy the key now — it is also available on the API&apos;s Overview tab, but share it over an encrypted channel only.
      </p>

      <div className="bg-white border border-jj-gray-02 rounded-xl p-5">
        <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05 mb-2">API key</p>
        <div className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2.5">
          <code className="flex-1 font-mono text-xs text-jj-gray-08 break-all">{payload.api_key}</code>
          <DarkButton onClick={() => { navigator.clipboard.writeText(payload.api_key); showToast('Key copied.'); }}>Copy</DarkButton>
        </div>

        {tables.length > 0 && (
          <>
            <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05 mt-5 mb-2">Ready-to-share URLs</p>
            <div className="space-y-1.5">
              {tables.map((t) => (
                <div key={t.alias} className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2">
                  <Icon src={linkIcon} className="w-3.5 h-3.5 flex-shrink-0" tone="blue" />
                  <code className="flex-1 font-mono text-[11px] text-jj-gray-07 truncate">
                    {tableUrl(publicId, t.alias)}
                  </code>
                  <GhostButton onClick={() => { navigator.clipboard.writeText(`curl -H \"Authorization: Bearer ${payload.api_key}\" \"${tableUrl(publicId, t.alias)}\"`); showToast('cURL copied with Authorization header.'); }}>
                    Copy
                  </GhostButton>
                </div>
              ))}
            </div>
            <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05 mt-5 mb-2">Browsable view — needs a SmartHub login</p>
            <div className="space-y-1.5">
              {tables.map((t) => (
                <div key={`ui-${t.alias}`} className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2">
                  <Icon src={databaseIcon} className="w-3.5 h-3.5 flex-shrink-0" tone="gray" />
                  <code className="flex-1 font-mono text-[11px] text-jj-gray-07 truncate">
                    {tableUiUrl(publicId, t.alias)}
                  </code>
                  <a
                    href={tableUiUrl(publicId, t.alias)}
                    target="_blank"
                    rel="noreferrer"
                    className="px-3 py-2 rounded-lg bg-jj-gray-08 text-white font-johnson-text text-xs hover:bg-jj-gray-07 transition-colors"
                  >
                    Open
                  </a>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="flex gap-2.5 mt-5">
        <GhostButton
          className="text-sm px-4 py-2.5"
          onClick={() => {
            downloadTextFile(
              `SmartHub_${(payload.name || 'data-api').replace(/\s+/g, '_')}_API_guide.md`,
              buildConsumerDoc(payload.name || 'Data API Export', publicId, payload.api_key, tables)
            );
            showToast('Consumer guide downloaded.');
          }}
        >
          Download consumer guide
        </GhostButton>
        <RedButton onClick={onDone}>Open the API</RedButton>
      </div>
    </div>
  );
}

/* ───────────────────────────── detail pane ─────────────────────────────── */
const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'tables', label: 'Tables' },
  { id: 'endpoints', label: 'Endpoints' },
  { id: 'docs', label: 'Docs' },
  { id: 'activity', label: 'Activity' },
];

function DetailPane({ apiId, summary, tab, onTabChange, onChanged, onDeleted, showToast, onKeyRegenerated }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [usage, setUsage] = useState(null);
  const [codeTheme, setCodeTheme] = useState(readCodeTheme);
  const [usageDays, setUsageDays] = useState(14);
  const [activity, setActivity] = useState(null);
  const [revealed, setRevealed] = useState(false);
  const [addingTable, setAddingTable] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch(`/data-api/${apiId}`);
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      const data = await res.json();
      setDetail(data);
      setName(data.name);
      setDescription(data.description || '');
    } catch (e) {
      showToast(`Could not load API: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [apiId]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickCodeTheme = (next) => {
    setCodeTheme(next);
    try { localStorage.setItem('dataApiCodeTheme', next); } catch { /* private mode */ }
  };

  const loadUsage = useCallback(async () => {
    try {
      const res = await apiFetch(`/data-api/${apiId}/usage?days=${usageDays}`);
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      setUsage(await res.json());
    } catch (e) {
      setUsage({ error: e.message });
    }
  }, [apiId, usageDays]);

  const loadActivity = useCallback(async () => {
    try {
      const res = await apiFetch(`/data-api/${apiId}/activity?limit=60`);
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      setActivity(await res.json());
    } catch (e) {
      setActivity({ error: e.message, events: [] });
    }
  }, [apiId]);

  useEffect(() => {
    setRevealed(false);
    setAddingTable(false);
    setEditing(false);
    setActivity(null);
    load();
  }, [load]);

  useEffect(() => { setUsage(null); loadUsage(); }, [loadUsage]);

  useEffect(() => {
    if (tab === 'activity' && activity === null) loadActivity();
  }, [tab, activity, loadActivity]);

  const patch = async (body, successMessage) => {
    try {
      const res = await apiFetch(`/data-api/${apiId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      if (successMessage) showToast(successMessage);
      onChanged();
      load();
      loadUsage();
      if (tab === 'activity') loadActivity();
      return true;
    } catch (e) {
      showToast(`Could not save: ${e.message}`, 'error');
      return false;
    }
  };

  if (loading || !detail) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-9 w-9 border-b-2 border-jj-red" />
      </div>
    );
  }

  const publicId = detail.slug || apiId;
  const tables = (detail.tables || []).map((t) => ({ ...t, db_schema: t.schema }));
  const endpoints = endpointsFor(publicId, tables, detail.api_key);

  const removeTable = async (index) => {
    const removed = tables[index];
    if (tables.length === 1) {
      showToast('An API needs at least one table — delete the whole API instead.', 'error');
      return;
    }
    if (!window.confirm(`Remove /${removed.alias}? Its URL stops working immediately.`)) return;
    await patch({ tables: tables.filter((_, i) => i !== index) }, `Removed /${removed.alias}.`);
  };

  const addTable = async (t) => {
    const ok = await patch({ tables: [...tables, t] }, `/${t.alias} is live on this API.`);
    if (ok) setAddingTable(false);
  };

  const regenerate = async () => {
    if (!window.confirm('Rotate the key? The old key stops working immediately.')) return;
    try {
      const res = await apiFetch(`/data-api/${apiId}/regenerate-key`, { method: 'POST' });
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      const data = await res.json();
      onKeyRegenerated({ id: apiId, slug: detail.slug, name: detail.name, api_key: data.api_key, tables });
      load();
    } catch (e) {
      showToast(`Could not rotate key: ${e.message}`, 'error');
    }
  };

  const saveMeta = async () => {
    setSaving(true);
    const ok = await patch({ name: name.trim(), description: description.trim() }, 'Details saved.');
    setSaving(false);
    if (ok) setEditing(false);
  };

  const downloadDocs = () => {
    downloadTextFile(
      `SmartHub_${(detail.name || 'data-api').replace(/\s+/g, '_')}_API_guide.md`,
      buildConsumerDoc(detail.name || 'Data API Export', publicId, detail.api_key, tables)
    );
    showToast('Consumer guide downloaded.');
  };

  return (
    <div>
      {/* header */}
      <div className="bg-white border-b border-jj-gray-02 px-8 pt-6">
        <div className="flex items-start gap-5">
          <div className="flex-1 min-w-0">
            {editing ? (
              <div className="max-w-xl">
                <Label>Name</Label>
                <input className={`${FIELD} text-sm mb-3`} value={name} onChange={(e) => setName(e.target.value)} />
                <Label>Description</Label>
                <textarea className={`${FIELD} text-sm`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
                <div className="flex gap-2 mt-3">
                  <DarkButton onClick={saveMeta} disabled={!name.trim() || saving}>{saving ? 'Saving…' : 'Save'}</DarkButton>
                  <GhostButton onClick={() => { setEditing(false); setName(detail.name); setDescription(detail.description || ''); }}>Cancel</GhostButton>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2.5 mb-1.5">
                  <h2 className="font-johnson-display text-[26px] leading-tight text-jj-gray-08 truncate">{detail.name}</h2>
                  <StatusPill active={detail.is_active} />
                  <button onClick={() => setEditing(true)} className="font-johnson-text text-[11px] text-jj-blue-03 hover:text-jj-red transition-colors">
                    Edit
                  </button>
                </div>
                <p className="font-johnson-text text-xs text-jj-gray-07 max-w-[62ch] leading-relaxed">
                  {detail.description || 'No description.'}
                </p>
                <p className="font-johnson-text text-[11px] text-jj-gray-05 mt-1">
                  <code className="font-mono text-jj-blue-03">/data-api/{publicId}</code>
                  {summary?.created_by ? ` · created by ${summary.created_by}` : ''}
                </p>
              </>
            )}
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <CodeThemeToggle value={codeTheme} onChange={pickCodeTheme} />
            <GhostButton onClick={() => patch({ is_active: !detail.is_active }, detail.is_active ? 'API deactivated — calls now return 403.' : 'API reactivated.')}>
              {detail.is_active ? 'Deactivate' : 'Activate'}
            </GhostButton>
            <GhostButton onClick={downloadDocs}>Download guide</GhostButton>
            <button
              onClick={() => onDeleted(apiId, detail.name)}
              aria-label="Delete API"
              className="p-2 rounded-lg border border-jj-gray-03 bg-white hover:border-jj-red hover:bg-jj-red/5 transition-colors"
            >
              <Icon src={deleteIcon} className="w-4 h-4" tone="red" />
            </button>
          </div>
        </div>

        <div className="flex gap-0.5 mt-5">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => onTabChange(t.id)}
              className={`px-4 py-2.5 font-johnson-text text-sm border-b-2 transition-colors ${
                tab === t.id ? 'text-jj-gray-08 border-jj-red font-medium' : 'text-jj-gray-06 border-transparent hover:text-jj-gray-08'
              }`}
            >
              {t.label}{t.id === 'tables' ? ` (${tables.length})` : ''}
            </button>
          ))}
        </div>
      </div>

      {/* body */}
      <div className="px-8 py-6 pb-12">
        {tab === 'overview' && (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5 mb-4">
              {[
                {
                  label: `Requests · ${usageDays}d`,
                  value: usage ? (usage.error ? '—' : compactNum(usage.total_calls)) : '…',
                  note: usage && !usage.error
                    ? `${usage.lifetime_calls.toLocaleString()} since logging began`
                    : usage?.error || 'loading',
                },
                {
                  label: 'Rows served',
                  value: usage ? (usage.error ? '—' : compactNum(usage.total_rows_served)) : '…',
                  note: usage && !usage.error && usage.avg_duration_ms != null
                    ? `avg ${usage.avg_duration_ms} ms per call`
                    : 'row pulls only',
                },
                {
                  label: 'Callers',
                  value: usage ? (usage.error ? '—' : String(usage.callers.length)) : '…',
                  note: usage && !usage.error && usage.errors > 0
                    ? `${usage.errors} rejected call${usage.errors === 1 ? '' : 's'}`
                    : 'distinct machines',
                },
                {
                  label: 'Last call',
                  value: relative(usage && !usage.error ? usage.last_call : summary?.last_used_at),
                  note: (usage && !usage.error ? usage.last_call : summary?.last_used_at)
                    ? absolute(usage && !usage.error ? usage.last_call : summary?.last_used_at)
                    : detail.is_active ? 'key accepting calls' : 'calls return 403',
                },
              ].map((s) => (
                <div key={s.label} className="bg-white border border-jj-gray-02 rounded-xl px-5 py-4">
                  <p className="font-johnson-text text-[11px] uppercase tracking-wider text-jj-gray-05 mb-2">{s.label}</p>
                  <p className="font-johnson-display text-xl text-jj-gray-08 leading-none truncate">{s.value}</p>
                  <p className="font-johnson-text text-[11px] text-jj-gray-06 mt-1.5 truncate">{s.note}</p>
                </div>
              ))}
            </div>

            {usage && !usage.error && (
              <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr] gap-3.5 mb-4">
                <UsageChart buckets={usage.buckets} days={usageDays} onDaysChange={setUsageDays} />
                <CallersCard callers={usage.callers} loggingSince={usage.logging_since} />
              </div>
            )}

            {usage && !usage.error && usage.lifetime_calls === 0 && (
              <div className="flex items-start gap-2 bg-white border border-jj-gray-02 rounded-xl px-5 py-4 mb-4">
                <Icon src={infoIcon} className="w-4 h-4 mt-0.5 flex-shrink-0" tone="blue" />
                <p className="font-johnson-text text-xs text-jj-gray-07 leading-relaxed">
                  Nothing has called this API yet. Counts start filling in as soon as a consumer hits
                  one of the endpoints on the Endpoints tab.
                </p>
              </div>
            )}

            {usage?.error && (
              <div className="flex items-start gap-2 bg-white border border-jj-gray-02 rounded-xl px-5 py-4 mb-4">
                <Icon src={alertTriangleIcon} className="w-4 h-4 mt-0.5 flex-shrink-0" tone="red" />
                <p className="font-johnson-text text-xs text-jj-gray-07 leading-relaxed">
                  Usage history unavailable: {usage.error}
                </p>
              </div>
            )}

            <div className="bg-white border border-jj-gray-02 rounded-xl px-5 py-4">
              <div className="flex items-center gap-2 mb-1.5">
                <Icon src={lockIcon} className="w-4 h-4" tone="dark" />
                <h3 className="font-johnson-display text-[15px] text-jj-gray-08">API key</h3>
              </div>
              <p className="font-johnson-text text-xs text-jj-gray-06 mb-3 max-w-[78ch] leading-relaxed">
                Anyone holding this key can read every table below without signing in. Share it over an encrypted channel and rotate it when a consumer leaves.
              </p>
              <div className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2.5">
                <code className="flex-1 font-mono text-xs text-jj-gray-08 break-all">
                  {revealed ? detail.api_key : maskKey(detail.api_key)}
                </code>
                <GhostButton onClick={() => setRevealed((r) => !r)}>{revealed ? 'Hide' : 'Reveal'}</GhostButton>
                <DarkButton onClick={() => { navigator.clipboard.writeText(detail.api_key); showToast('Key copied — treat it like a password.'); }}>Copy</DarkButton>
                <GhostButton onClick={regenerate} className="flex items-center gap-1.5">
                  <Icon src={refreshIcon} className="w-3 h-3" tone="dark" /> Rotate
                </GhostButton>
              </div>
            </div>
          </>
        )}

        {tab === 'tables' && (
          <div className="bg-white border border-jj-gray-02 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-jj-gray-01">
              <div>
                <h3 className="font-johnson-display text-[15px] text-jj-gray-08">Tables exposed</h3>
                {usage && !usage.error && (
                  <p className="font-johnson-text text-[11px] text-jj-gray-05 mt-0.5">
                    Call counts cover the last {usageDays} days.
                  </p>
                )}
              </div>
              <GhostButton onClick={() => setAddingTable((a) => !a)} className="flex items-center gap-1.5">
                <Icon src={addIcon} className="w-3.5 h-3.5" tone="dark" /> {addingTable ? 'Close' : 'Add table'}
              </GhostButton>
            </div>

            {addingTable && (
              <div className="px-5 py-4 bg-jj-gray-01/60 border-b border-jj-gray-02">
                <TablePicker
                  existingAliases={tables.map((t) => t.alias.toLowerCase())}
                  onAdd={addTable}
                  onCancel={() => setAddingTable(false)}
                />
              </div>
            )}

            {tables.map((t, i) => (
              <div key={t.alias} className="grid grid-cols-[180px_1fr_90px_110px_110px_44px] items-center gap-4 px-5 py-3.5 border-b border-jj-gray-01 last:border-0">
                <div className="flex items-center gap-2 min-w-0">
                  <Icon src={databaseIcon} className="w-4 h-4 flex-shrink-0" tone="gray" />
                  <code className="font-mono text-xs font-medium text-jj-gray-08 truncate">/{t.alias}</code>
                </div>
                <p className="font-johnson-text text-xs text-jj-gray-07 truncate">{tablePath(t)}</p>
                <span className={`justify-self-start font-johnson-text text-[11px] px-2 py-0.5 rounded-full ${
                  (t.env || '').toLowerCase() === 'prod' ? 'bg-jj-blue-03/10 text-jj-blue-03' : 'bg-jj-gray-02 text-jj-gray-07'
                }`}>
                  {(t.env || '—').toUpperCase()}
                </span>
                <span className="font-johnson-text text-xs text-jj-gray-06">{(t.row_limit ?? 1000).toLocaleString()} / page</span>
                <span className="font-johnson-text text-xs text-jj-gray-06">
                  {usage && !usage.error
                    ? `${(usage.by_alias.find((a) => a.alias === t.alias)?.calls || 0).toLocaleString()} calls`
                    : ''}
                </span>
                <button onClick={() => removeTable(i)} aria-label={`Remove ${t.alias}`} className="justify-self-end p-1.5 rounded-lg hover:bg-jj-gray-01 transition-colors">
                  <Icon src={closeIcon} className="w-3.5 h-3.5" tone="gray" />
                </button>
              </div>
            ))}
          </div>
        )}

        {tab === 'docs' && (
          <DocsPanel detail={detail} publicId={publicId} tables={tables} showToast={showToast} codeTheme={codeTheme} />
        )}

        {tab === 'activity' && (
          <div className="bg-white border border-jj-gray-02 rounded-xl px-5 py-2">
            {activity === null ? (
              <div className="flex items-center justify-center py-10">
                <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-jj-red" />
              </div>
            ) : activity.error ? (
              <p className="font-johnson-text text-xs text-jj-gray-07 py-6">Activity unavailable: {activity.error}</p>
            ) : activity.events.length === 0 ? (
              <p className="font-johnson-text text-xs text-jj-gray-05 py-6">Nothing recorded for this API yet.</p>
            ) : (
              activity.events.map((e, i) => (
                <div key={`${e.ts}-${i}`} className="grid grid-cols-[150px_1fr] gap-4 py-3.5 border-b border-jj-gray-01 last:border-0">
                  <div>
                    <p className="font-johnson-text text-xs text-jj-gray-07">{relative(e.ts)}</p>
                    <p className="font-johnson-text text-[11px] text-jj-gray-05">{absolute(e.ts)}</p>
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                        e.status >= 400 ? 'bg-jj-maroon-05' : e.kind === 'management' ? 'bg-jj-blue-03' : 'bg-jj-green-03'
                      }`} />
                      <p className="font-johnson-text text-sm text-jj-gray-08">{e.what}</p>
                    </div>
                    <p className="font-johnson-text text-[11px] text-jj-gray-06 mt-0.5 pl-3.5 truncate">
                      {e.who}{e.details ? ` · ${e.details}` : ''}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === 'endpoints' && (
          <div className="space-y-3">
            {tables.length > 0 && (
              <TryItConsole publicId={publicId} tables={tables} apiKey={detail.api_key} showToast={showToast} codeTheme={codeTheme} />
            )}
            <p className="font-johnson-text text-xs text-jj-gray-06 max-w-[80ch] leading-relaxed">
              Four GET endpoints, one key, no login. URLs below already carry this API&apos;s key — treat
              them as secrets. Run it sends a real request, so it appears in Activity.
            </p>
            {endpoints.map((e) => <EndpointCard key={e.key} endpoint={e} showToast={showToast} codeTheme={codeTheme} />)}
            <div className="bg-white border border-jj-gray-02 rounded-xl p-4">
              <h3 className="font-johnson-display text-[15px] text-jj-gray-08 mb-1.5">Browsable view</h3>
              <p className="font-johnson-text text-xs text-jj-gray-06 mb-3 max-w-[80ch] leading-relaxed">
                A paginated table you can open straight in a browser. Requires signing in to SmartHub — the key alone is not enough here.
              </p>
              <div className="space-y-1.5">
                {tables.map((t) => (
                  <div key={`ui-${t.alias}`} className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-2">
                    <Icon src={databaseIcon} className="w-3.5 h-3.5 flex-shrink-0" tone="gray" />
                    <code className="flex-1 font-mono text-[11px] text-jj-gray-07 truncate">{tableUiUrl(publicId, t.alias)}</code>
                    <a
                      href={tableUiUrl(publicId, t.alias)}
                      target="_blank"
                      rel="noreferrer"
                      className="px-3 py-2 rounded-lg bg-jj-gray-08 text-white font-johnson-text text-xs hover:bg-jj-gray-07 transition-colors"
                    >
                      Open
                    </a>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────── the page ─────────────────────────────── */
function DataAPIExport() {
  const [apis, setApis] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const [handoff, setHandoff] = useState(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all'); // all | active | inactive
  const [toast, setToast] = useState(null);

  const showToast = useCallback((message, type = 'success') => setToast({ message, type }), []);

  /* The URL says what is on screen, so a view can be linked, bookmarked and
     navigated with the browser's own back/forward:
       /data-api                         list, first API selected
       /data-api?api=<slug>              that API, Overview
       /data-api?api=<slug>&tab=docs     straight to a tab
       /data-api?new=1                   the create wizard
     The key hand-off screen is deliberately not addressable — it shows a
     freshly issued key and should not survive a reload or a shared link. */
  const urlApi = searchParams.get('api') || '';
  const urlTab = searchParams.get('tab') || 'overview';
  const tab = TABS.some((t) => t.id === urlTab) ? urlTab : 'overview';
  const creating = searchParams.get('new') === '1';

  const selected = apis.find((a) => a.slug === urlApi || a.id === urlApi) || (urlApi ? null : apis[0]) || null;
  const selectedId = selected?.id ?? null;
  const view = handoff ? 'created' : creating ? 'create' : 'detail';

  const refOf = (a) => a.slug || a.id;
  const openApi = (a) => setSearchParams({ api: refOf(a), tab: 'overview' });
  const openTab = (next) => setSearchParams(selected ? { api: refOf(selected), tab: next } : { tab: next });
  const startCreate = () => setSearchParams({ new: '1' });
  const clearParams = () => setSearchParams({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch('/data-api/list');
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      const data = await res.json();
      const list = data.apis || [];
      setApis(list);
    } catch (e) {
      showToast(`Could not load APIs: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Delete API "${name}"? This can't be undone and its URLs stop working immediately.`)) return;
    try {
      const res = await apiFetch(`/data-api/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error((await readJson(res)).detail || `HTTP ${res.status}`);
      showToast('API deleted.');
      clearParams();
      load();
    } catch (e) {
      showToast(`Could not delete: ${e.message}`, 'error');
    }
  };

  const q = search.trim().toLowerCase();
  const visible = apis
    .filter((a) => (filter === 'all' ? true : filter === 'active' ? a.is_active : !a.is_active))
    .filter((a) => !q || `${a.name} ${a.description || ''} ${a.created_by || ''} ${a.slug || ''}`.toLowerCase().includes(q));

  const activeCount = apis.filter((a) => a.is_active).length;

  return (
    <div className="flex h-screen min-h-0 bg-jj-gray-01">
      {/* ── list column ── */}
      <div className="w-[334px] flex-shrink-0 bg-white border-r border-jj-gray-02 flex flex-col min-h-0">
        <div className="px-5 pt-5 pb-4 border-b border-jj-gray-01">
          <div className="flex items-start justify-between gap-2">
            <h1 className="font-johnson-display text-lg text-jj-gray-08">Data API Export</h1>
            <span className="font-johnson-text text-[11px] text-jj-gray-06 pt-1.5 whitespace-nowrap">
              {activeCount} active / {apis.length}
            </span>
          </div>
          <p className="font-johnson-text text-xs text-jj-gray-06 mt-1 mb-3.5 leading-relaxed">
            Source tables published as key-authenticated read endpoints.
          </p>

          <button
            onClick={startCreate}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-jj-red text-white font-johnson-text text-sm font-medium hover:opacity-90 transition-opacity"
          >
            <Icon src={addIcon} className="w-3.5 h-3.5" tone="white" /> New API
          </button>

          <div className="relative mt-3">
            <Icon src={searchIcon} className="w-3.5 h-3.5 absolute left-3 top-2.5" tone="gray" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search APIs, owners, aliases"
              className="w-full pl-8 pr-3 py-2 rounded-lg border border-jj-gray-02 bg-jj-gray-01 font-johnson-text text-xs text-jj-gray-08 focus:outline-none focus:ring-2 focus:ring-jj-blue-03"
            />
          </div>

          <div className="flex gap-1.5 mt-2.5">
            {['all', 'active', 'inactive'].map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`flex-1 py-1.5 rounded-lg border font-johnson-text text-[11px] capitalize transition-colors ${
                  filter === f ? 'bg-jj-gray-08 border-jj-gray-08 text-white' : 'bg-white border-jj-gray-02 text-jj-gray-07 hover:bg-jj-gray-01'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {loading ? (
            <div className="flex items-center justify-center py-14">
              <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-jj-red" />
            </div>
          ) : visible.length === 0 ? (
            <p className="px-5 py-10 text-center font-johnson-text text-xs text-jj-gray-05">
              {apis.length === 0 ? 'No APIs yet. Create one to start sharing tables.' : 'Nothing matches that search.'}
            </p>
          ) : (
            visible.map((a) => {
              const on = a.id === selectedId && view === 'detail';
              return (
                <button
                  key={a.id}
                  onClick={() => openApi(a)}
                  className={`block w-full text-left px-[18px] py-3.5 border-b border-jj-gray-01 transition-colors ${
                    on ? 'bg-jj-gray-01 border-l-[3px] border-l-jj-red pl-[15px]' : 'bg-white hover:bg-jj-gray-01/60'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${a.is_active ? 'bg-jj-green-03' : 'bg-jj-gray-04'}`} />
                    <span className="font-johnson-text text-xs font-medium text-jj-gray-08 truncate">{a.name}</span>
                  </div>
                  <code className="block font-mono text-[11px] text-jj-blue-03 mb-1.5 truncate">/data-api/{a.slug || a.id}</code>
                  <div className="flex items-center gap-2 font-johnson-text text-[11px] text-jj-gray-06">
                    <span>{a.table_count} table{a.table_count === 1 ? '' : 's'}</span>
                    <span className="text-jj-gray-03">|</span>
                    <span className="truncate">
                      {typeof a.requests_7d === 'number'
                        ? `${compactNum(a.requests_7d)} call${a.requests_7d === 1 ? '' : 's'} · 7d`
                        : a.last_used_at ? `used ${relative(a.last_used_at)}` : 'never used'}
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* ── main pane ── */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        {view === 'create' && (
          <CreateWizard
            onCancel={clearParams}
            showToast={showToast}
            onCreated={(payload) => { setHandoff(payload); load(); }}
          />
        )}

        {view === 'created' && handoff && (
          <KeyHandoff
            payload={handoff}
            showToast={showToast}
            onDone={() => { const ref = handoff.slug || handoff.id; setHandoff(null); setSearchParams({ api: ref, tab: 'overview' }); }}
          />
        )}

        {view === 'detail' && (
          selectedId ? (
            <DetailPane
              key={selectedId}
              apiId={selectedId}
              summary={selected}
              tab={tab}
              onTabChange={openTab}
              onChanged={load}
              onDeleted={handleDelete}
              showToast={showToast}
              onKeyRegenerated={(payload) => setHandoff(payload)}
            />
          ) : (
            !loading && (
              <div className="flex flex-col items-center justify-center h-full text-center px-8">
                <Icon src={networkIcon} className="w-10 h-10 mb-3" tone="gray" />
                <p className="font-johnson-display text-lg text-jj-gray-08 mb-1">No API selected</p>
                <p className="font-johnson-text text-xs text-jj-gray-06 max-w-sm leading-relaxed">
                  Create an API to publish source tables as read-only endpoints, then hand the key to whoever needs the data.
                </p>
              </div>
            )
          )
        )}
      </div>

      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}

export default DataAPIExport;
