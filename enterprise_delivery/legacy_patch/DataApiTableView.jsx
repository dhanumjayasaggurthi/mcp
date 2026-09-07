import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { logAuditEvent } from '../utils/auditLog';
import { apiFetch } from '../utils/apiClient';
import dashboardIcon from '/icons/JJ_Icon_Home_RGB.svg';
import dataApiIcon from '/icons/JJ_Icon_Network_RGB.svg';
import databaseIcon from '/icons/JJ_Icon_Database_RGB.svg';
import tableIcon from '/icons/JJ_Icon_Data_Visualization.svg';
import chevronRightIcon from '/icons/JJ_Icon_Chevron_Right.svg';

const FILTER_RED = 'brightness(0) saturate(100%) invert(17%) sepia(100%) saturate(7426%) hue-rotate(3deg) brightness(92%) contrast(118%)';
const FILTER_GRAY = 'brightness(0) saturate(100%) invert(42%) sepia(6%) saturate(656%) hue-rotate(346deg) brightness(92%) contrast(88%)';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const COL_DEFAULT_WIDTH = 280;
const COL_MIN_WIDTH = 60;
const COL_MAX_WIDTH = 800;
const COL_CHAR_PX = 6.2;      // approx px per char at text-xs (data font)
const HEADER_CHAR_PX = 7.6;   // approx px per char at text-xs, bold mono uppercase (wider)
const COL_H_PADDING = 32;     // left + right cell padding (data cells)
// Header cells reserve extra horizontal room the data-cell padding doesn't
// account for: 12px left + 20px right (space held for the sort icon) plus
// the icon itself (~10px) and its gap (~4px) — all eating into the text's
// available width even when the icon is faded out, not just shown.
const HEADER_H_PADDING = 66;
const COL_SAMPLE_ROWS = 100;  // rows sampled per page for width calc
const WIDTH_SAMPLE_CHARS = 300;
const CELL_DISPLAY_MAX_CHARS = 1000;
// Browser-side CSV assembly is deliberately bounded. Enterprise-scale exports
// must be asynchronous/server-side so the UI never tries to hold millions of
// records in memory.
const BROWSER_EXPORT_MAX_ROWS = 50_000;

function fitColumnWidths(columns, rows) {
  const widths = {};
  const capChars = Math.ceil((COL_MAX_WIDTH - COL_H_PADDING) / COL_CHAR_PX);
  columns.forEach((col, ci) => {
    let maxDataChars = 0;
    const sample = rows.slice(0, COL_SAMPLE_ROWS);
    for (const row of sample) {
      const cell = row[col];
      if (cell === null || cell === undefined) continue;
      const raw = typeof cell === 'string' ? cell : formatCellValue(cell);
      const prefix = raw.length > WIDTH_SAMPLE_CHARS ? raw.slice(0, WIDTH_SAMPLE_CHARS) : raw;
      const longest = prefix.split('\n').reduce((a, b) => (a.length > b.length ? a : b), '');
      if (longest.length > maxDataChars) maxDataChars = longest.length;
      if (maxDataChars >= capChars) break; // already enough to hit COL_MAX_WIDTH — stop scanning this column
    }
    const dataRaw = Math.round(maxDataChars * COL_CHAR_PX + COL_H_PADDING);
    const headerRaw = Math.round(col.length * HEADER_CHAR_PX + HEADER_H_PADDING);
    const raw = Math.max(dataRaw, headerRaw);
    widths[ci] = Math.min(COL_MAX_WIDTH, Math.max(COL_MIN_WIDTH, raw));
  });
  return widths;
}

// Small inline SVGs so this page has zero dependency on the app's icon set —
// it's meant to work standalone, without an authenticated session.
const ChevronLeft = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <polyline points="15 18 9 12 15 6" />
  </svg>
);
const ChevronRight = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <polyline points="9 18 15 12 9 6" />
  </svg>
);
const SortIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M3 7h13M3 12h9M3 17h5" />
  </svg>
);
const AlertIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 9v4m0 4h.01M10.29 3.86l-8.18 14.18A1.5 1.5 0 0 0 3.32 20h17.36a1.5 1.5 0 0 0 1.21-1.96L13.71 3.86a1.5 1.5 0 0 0-2.42 0z" />
  </svg>
);
const WrapIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M3 6h18M3 12h13a3 3 0 1 1 0 6h-4m0 0l2-2m-2 2l2 2M3 18h5" />
  </svg>
);
const DownloadIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16" />
  </svg>
);
const CloseIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M18 6L6 18M6 6l12 12" />
  </svg>
);
const SearchIcon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4.35-4.35" />
  </svg>
);

function useDebounced(value, delay = 400) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// VARIANT/OBJECT/ARRAY columns (e.g. Snowflake VARIANT) come back as real
// JSON objects/arrays, not strings — String(obj) would just print
// "[object Object]". Serialize those properly instead.
function formatCellValue(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') {
    try { return JSON.stringify(v); } catch { return String(v); }
  }
  return String(v);
}

function csvEscape(value) {
  const s = formatCellValue(value);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// Case-insensitive substring highlight — matches the backend's search_rows
// behavior (plain LIKE '%term%' across columns), so what lights up here is
// exactly why that row matched. Returns plain text unchanged when there's
// no term, so this is a safe no-op wrapper everywhere else.
const HIGHLIGHT_MAX_TEXT_LEN = 3000;
const HIGHLIGHT_MAX_MATCHES = 50;
function highlightMatch(text, term) {
  if (!term) return text;
  const str = String(text);
  if (str.length > HIGHLIGHT_MAX_TEXT_LEN) return str;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = str.split(new RegExp(`(${escaped})`, 'ig'));
  if (parts.length === 1) return str;
  const termLower = term.toLowerCase();
  let matchCount = 0;
  return parts.map((part, i) => {
    if (part.toLowerCase() !== termLower) return part;
    matchCount++;
    if (matchCount > HIGHLIGHT_MAX_MATCHES) return part;
    return <mark key={i} className="bg-jj-yellow-01 text-jj-gray-08 rounded-sm px-0.5">{part}</mark>;
  });
}

// SCREAMING_SNAKE_CASE column names have no spaces, so the browser treats
// them as one unbreakable word — without a hint, a forced wrap in a narrow
// column falls back to splitting mid-letter. <wbr/> after each underscore
// gives it a sane place to break instead.
function renderColName(col) {
  const parts = col.split('_');
  return parts.map((part, i) => (
    <React.Fragment key={i}>
      {part}
      {i < parts.length - 1 && <>_<wbr /></>}
    </React.Fragment>
  ));
}

function compareRaw(a, b) {
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  const sa = String(a).trim(), sb = String(b).trim();
  const na = parseFloat(sa), nb = parseFloat(sb);
  if (!isNaN(na) && !isNaN(nb) && sa !== '' && sb !== '') return na - nb;
  return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: 'base' });
}

// This page opens standalone (shared link, no sidebar/nav), so this is the
// only orientation cue and the only way back into the app. apiName is
// fetched from the backend and null until it resolves — falls back to the
// raw id rather than blocking the crumb on it.
function Breadcrumb({ apiId, apiName, alias }) {
  const crumbs = [
    { label: 'SmartHub', icon: dashboardIcon, to: '/dashboard' },
    { label: 'Data API', icon: dataApiIcon, to: '/data-api' },
    { label: apiName || apiId, icon: databaseIcon, to: '/data-api' },
    { label: alias, icon: tableIcon, current: true },
  ];

  return (
    <nav
      aria-label="Breadcrumb"
      className="flex-shrink-0 flex items-center px-5 py-2.5 border-b border-jj-gray-02 bg-white shadow-sm overflow-x-auto"
    >
      <ol className="flex items-center gap-1 min-w-0">
        {crumbs.map((c, i) => (
          <li key={i} className="flex items-center gap-1 min-w-0">
            {i > 0 && (
              <img src={chevronRightIcon} alt="" className="w-3 h-3 mx-1 flex-shrink-0" style={{ filter: FILTER_GRAY, opacity: 0.5 }} />
            )}
            {c.current ? (
              <span
                title={c.label}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-jj-red bg-opacity-10 font-johnson-text text-xs font-bold text-jj-red max-w-[240px]"
              >
                <img src={c.icon} alt="" className="w-3.5 h-3.5 flex-shrink-0" style={{ filter: FILTER_RED }} />
                <span className="truncate">{c.label}</span>
              </span>
            ) : (
              <Link
                to={c.to}
                title={c.label}
                className="group flex items-center gap-1.5 px-2.5 py-1 rounded-full font-johnson-text text-xs font-semibold text-jj-gray-06 hover:text-jj-red hover:bg-jj-gray-01 transition-colors max-w-[220px]"
              >
                <img
                  src={c.icon}
                  alt=""
                  className="w-3.5 h-3.5 flex-shrink-0 opacity-70 group-hover:opacity-100 transition-opacity"
                  style={{ filter: FILTER_GRAY }}
                />
                <span className="truncate">{c.label}</span>
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

function DataApiTableView() {
  const { apiId, alias } = useParams();
  const [searchParams] = useSearchParams();
  // Backward-compatible URL key fallback. New links omit the key and the
  // SSO-gated management detail call supplies it instead. Consumer data calls
  // always send the credential in the Authorization header.
  const [apiKey, setApiKey] = useState(searchParams.get('api_key') || '');
  const { user } = useAuth();
  const userEmail = user?.email || user?.username || null;

  // Display name for the breadcrumb — this page has no sidebar/nav of its
  // own (it's opened via a shared link), so the crumb is the only way back
  // into the app and the only clue which API this table belongs to.
  const [apiName, setApiName] = useState(null);
  useEffect(() => {
    let cancelled = false;
    apiFetch(`/data-api/${apiId}`)
      .then(res => (res.ok ? res.json() : null))
      .then(data => {
        if (!cancelled && data) {
          setApiName(data.name);
          if (data.api_key) setApiKey(data.api_key);
        }
      })
      .catch(() => {}); // breadcrumb falls back to the raw id — non-critical
    return () => { cancelled = true; };
  }, [apiId]);

  // Page is now SSO-gated, so this is the audit trail's only chance to tie
  // a view of this table to a real person — the rows/columns/count calls
  // below stay on api_key auth and never see who's logged in.
  // Guarded with a ref (not just the effect's own dep array) because
  // React 18 StrictMode mounts every component twice in dev, which would
  // otherwise write two identical "viewed" rows for one real page load.
  const loggedViewKeyRef = useRef(null);
  useEffect(() => {
    if (!userEmail) return;
    const key = `${apiId}/${alias}`;
    if (loggedViewKeyRef.current === key) return;
    loggedViewKeyRef.current = key;
    logAuditEvent('data_api_table_viewed', userEmail, key, `Opened browsable table view for '${alias}'`);
  }, [userEmail, apiId, alias]);

  const [pageSize, setPageSize] = useState(100);
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState([]);
  const [totalRows, setTotalRows] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(null);
  const [effectiveLimit, setEffectiveLimit] = useState(pageSize);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounced(search);
  const [searchColumn, setSearchColumn] = useState(''); // '' = search every column
  const [knownColumns, setKnownColumns] = useState([]); // survives empty result sets so the dropdown doesn't go blank

  const [sortColIdx, setSortColIdx] = useState(null);
  const [sortDir, setSortDir] = useState('asc');

  const [wordWrap, setWordWrap] = useState(false);
  const [cellModal, setCellModal] = useState(null); // { column, value } | null
  const [downloading, setDownloading] = useState(false);
  const [downloadPct, setDownloadPct] = useState(0);
  const [colWidths, setColWidths] = useState({}); // { [columnIndex]: px }
  const dragRef = useRef(null);

  const [uiBusy, setUiBusy] = useState(false);
  const busyRef = useRef(false);

  const base = `${API_BASE_URL}/data-api/${apiId}/${alias}`;

  // A new search term (or a change of which column it applies to) always
  // starts back at page 1.
  useEffect(() => { setOffset(0); }, [debouncedSearch, searchColumn]);

  // Only log once the person has actually typed something and it's settled —
  // not on every keystroke, and not on the initial empty value. Ref-guarded
  // for the same StrictMode double-invoke reason as the view log above.
  const loggedSearchRef = useRef(null);
  useEffect(() => {
    if (!userEmail || !debouncedSearch) return;
    const key = `${apiId}/${alias}/${debouncedSearch}`;
    if (loggedSearchRef.current === key) return;
    loggedSearchRef.current = key;
    const where = searchColumn ? ` in column "${searchColumn}"` : '';
    logAuditEvent('data_api_table_searched', userEmail, `${apiId}/${alias}`, `Searched for "${debouncedSearch}"${where}`);
  }, [userEmail, apiId, alias, debouncedSearch, searchColumn]);

  const load = useCallback(async () => {
    if (!apiKey) {
      setError('No API credential is available for this table view.');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const searchParam = debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : '';
      const columnParam = (debouncedSearch && searchColumn) ? `&column=${encodeURIComponent(searchColumn)}` : '';
      const res = await fetch(`${base}/rows?limit=${pageSize}&offset=${offset}${searchParam}${columnParam}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const commit = () => {
        setRows(data.rows || []);
        if (data.rows && data.rows.length) setKnownColumns(Object.keys(data.rows[0]));
        setHasMore(!!data.has_more);
        setNextOffset(data.next_offset ?? null);
        setEffectiveLimit(Number(data.limit) > 0 ? Number(data.limit) : pageSize);
        setTotalRows(data.total_rows != null ? data.total_rows : (offset === 0 ? null : totalRows));
        setSortColIdx(null);
        setSortDir('asc');
      };
      if (knownColumns.length > 0) {
        setLoading(false);
        busyRef.current = true;
        setUiBusy(true);
        requestAnimationFrame(() => requestAnimationFrame(commit));
      } else {
        commit();
        setLoading(false);
      }
    } catch (e) {
      setError(e.message);
      setRows([]);
      setLoading(false);
    }
  }, [base, apiKey, pageSize, offset, debouncedSearch, searchColumn]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);

  // Pulls the whole table (not just the current page) in chunks and triggers
  // a CSV download once every row has been fetched.
  const downloadCsv = async () => {
    if (downloading || !apiKey) return;
    setDownloading(true);
    setDownloadPct(0);
    try {
      if (totalRows != null && totalRows > BROWSER_EXPORT_MAX_ROWS) {
        throw new Error(`Result has ${totalRows.toLocaleString()} rows. Browser CSV export is capped at ${BROWSER_EXPORT_MAX_ROWS.toLocaleString()} rows; use the managed async export service for larger extracts.`);
      }
      const chunk = 2000;
      let off = 0;
      let more = true;
      let total = totalRows;
      let all = [];
      const searchParam = debouncedSearch ? `&search=${encodeURIComponent(debouncedSearch)}` : '';
      const columnParam = (debouncedSearch && searchColumn) ? `&column=${encodeURIComponent(searchColumn)}` : '';
      while (more) {
        const res = await fetch(`${base}/rows?limit=${chunk}&offset=${off}${searchParam}${columnParam}`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        const pageRows = data.rows || [];
        if (all.length + pageRows.length > BROWSER_EXPORT_MAX_ROWS) {
          throw new Error(`Browser CSV export exceeded ${BROWSER_EXPORT_MAX_ROWS.toLocaleString()} rows; use the managed async export service.`);
        }
        all = all.concat(pageRows);
        if (data.total_rows != null) total = data.total_rows;
        more = !!data.has_more;
        if (more) {
          const serverNext = Number(data.next_offset);
          if (!Number.isFinite(serverNext) || serverNext <= off) {
            throw new Error('Server returned an invalid next_offset; export stopped to prevent duplicate or skipped rows.');
          }
          off = serverNext;
        }
        setDownloadPct(total ? Math.min(99, Math.round((all.length / total) * 100)) : 0);
      }
      const cols = all.length ? Object.keys(all[0]) : knownColumns;
      const lines = [cols.map(csvEscape).join(',')];
      for (const r of all) lines.push(cols.map(c => csvEscape(r[c])).join(','));
      const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
      const filename = debouncedSearch ? `${alias}_filtered.csv` : `${alias}.csv`;
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename });
      a.click();
      URL.revokeObjectURL(a.href);
      setDownloadPct(100);
      if (userEmail) {
        logAuditEvent('data_api_table_downloaded', userEmail, `${apiId}/${alias}`, `Downloaded ${filename} (${all.length} rows)`);
      }
    } catch (e) {
      setError(`Download failed: ${e.message}`);
    } finally {
      setDownloading(false);
    }
  };

  const columns = useMemo(() => (rows.length ? Object.keys(rows[0]) : []), [rows]);

  // Auto-fit widths recompute every time the page's data changes; manual
  // drags (colWidths) take priority over them per column.
  const autoWidths = useMemo(() => fitColumnWidths(columns, rows), [columns, rows]);
  const widthFor = (ci) => colWidths[ci] ?? autoWidths[ci] ?? COL_DEFAULT_WIDTH;

  // New schema (different columns) invalidates any manual widths from before
  // so auto-fit takes back over for the new set of columns.
  const prevColsKey = useRef('');
  useEffect(() => {
    const key = columns.join('|');
    if (key !== prevColsKey.current) {
      prevColsKey.current = key;
      setColWidths({});
    }
  }, [columns]);

  const onResize = (e, ci) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = widthFor(ci);
    dragRef.current = { ci, startX, startWidth };
    const onMove = (ev) => {
      if (!dragRef.current) return;
      const { ci: _ci, startX: _sx, startWidth: _sw } = dragRef.current;
      const next = Math.min(COL_MAX_WIDTH, Math.max(COL_MIN_WIDTH, _sw + (ev.clientX - _sx)));
      setColWidths(prev => ({ ...prev, [_ci]: next }));
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const sortedRows = useMemo(() => {
    if (sortColIdx === null) return rows;
    const col = columns[sortColIdx];
    return [...rows].sort((a, b) => {
      const cmp = compareRaw(a[col], b[col]);
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [rows, sortColIdx, sortDir, columns]);

  // Sorting/rewrapping a big page (up to 500 rows) is synchronous work that
  // blocks the main thread for a beat with no visual feedback otherwise.
  // Clears uiBusy once that re-render has committed.
  useEffect(() => {
    if (busyRef.current) {
      busyRef.current = false;
      setUiBusy(false);
    }
  }, [sortedRows, wordWrap]);

  const runHeavyUpdate = (fn) => {
    busyRef.current = true;
    setUiBusy(true);
    requestAnimationFrame(() => requestAnimationFrame(fn));
  };

  // Real header height (it can wrap to 2 lines for long SCREAMING_SNAKE_CASE
  // names) — the skeleton overlay starts exactly here so it never covers or
  // gaps under the real header row.
  const headerRowRef = useRef(null);
  const [headerHeight, setHeaderHeight] = useState(40);
  useEffect(() => {
    const el = headerRowRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) setHeaderHeight(entry.contentRect.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Tracks the scroll pane's width so the last column can stretch to fill
  // any leftover space instead of leaving a bare gap when the columns
  // don't add up to the full pane width.
  const scrollPaneRef = useRef(null);
  const [scrollPaneWidth, setScrollPaneWidth] = useState(0);
  useEffect(() => {
    const el = scrollPaneRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) setScrollPaneWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const lastColIdx = columns.length - 1;
  const tableContentWidth = 40 + columns.reduce((sum, _, ci) => sum + widthFor(ci), 0);
  const stretchSlack = Math.max(0, scrollPaneWidth - tableContentWidth);
  // Only stretch the last column, and only if nobody's manually resized it.
  const renderWidthFor = (ci) =>
    ci === lastColIdx && colWidths[lastColIdx] == null ? widthFor(ci) + stretchSlack : widthFor(ci);

  const toggleSort = (ci) => {
    runHeavyUpdate(() => {
      if (sortColIdx === ci) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
      else { setSortColIdx(ci); setSortDir('asc'); }
    });
  };

  const toggleWordWrap = () => runHeavyUpdate(() => setWordWrap(w => !w));

  const pageStep = Math.max(1, effectiveLimit || pageSize);
  const totalPages = totalRows != null ? Math.max(1, Math.ceil(totalRows / pageStep)) : null;
  const currentPage = Math.floor(offset / pageStep) + 1;
  const rangeStart = rows.length ? offset + 1 : 0;
  const rangeEnd = offset + rows.length;

  const goFirst = () => setOffset(0);
  const goPrev = () => setOffset(o => Math.max(0, o - pageStep));
  const goNext = () => { if (nextOffset != null) setOffset(nextOffset); };
  const goLast = () => { if (totalRows != null) setOffset(Math.max(0, (totalPages - 1) * pageStep)); };

  // "Jump to page" box — free text while typing, only clamped and applied
  // on submit so the person can freely backspace/retype a number.
  const [pageJump, setPageJump] = useState('');
  useEffect(() => { setPageJump(String(currentPage)); }, [currentPage]);
  const submitPageJump = () => {
    const n = parseInt(pageJump, 10);
    if (!Number.isFinite(n)) { setPageJump(String(currentPage)); return; }
    const clamped = totalPages ? Math.min(Math.max(1, n), totalPages) : Math.max(1, n);
    setOffset((clamped - 1) * pageStep);
  };

  if (error && !rows.length && !loading) {
    return (
      <div className="min-h-screen bg-jj-gray-01 flex flex-col">
        <Breadcrumb apiId={apiId} apiName={apiName} alias={alias} />
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="bg-white rounded-2xl shadow-lg p-8 max-w-md text-center">
            <AlertIcon className="w-10 h-10 mx-auto mb-3 text-jj-maroon-05" />
            <p className="font-johnson-display text-lg text-jj-gray-08 mb-1">Can't load this table</p>
            <p className="font-johnson-text text-sm text-jj-gray-06">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-jj-gray-01">
      <Breadcrumb apiId={apiId} apiName={apiName} alias={alias} />

      {/* Header — title/count, search, download & word-wrap actions */}
      <div className="flex-shrink-0 flex items-center gap-3 px-5 py-3 border-b border-jj-gray-02 bg-jj-gray-01 flex-wrap">
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <span className="font-johnson-display text-sm font-bold text-jj-gray-08">/{alias}</span>
          {totalRows != null && (
            <span className="font-johnson-text text-xs text-jj-gray-05 whitespace-nowrap">
              {totalRows.toLocaleString()} {debouncedSearch ? 'matching' : 'total'} row{totalRows !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        <div className="relative flex-shrink-0" style={{ minWidth: '220px' }}>
          <SearchIcon className="w-3.5 h-3.5 text-jj-gray-05 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search this table…"
            className="w-full pl-8 pr-7 py-1.5 rounded-lg border border-jj-gray-03 bg-white font-johnson-text text-xs focus:outline-none focus:ring-2 focus:ring-jj-blue-03"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-jj-gray-01 transition-colors"
            >
              <CloseIcon className="w-3 h-3 text-jj-gray-06" />
            </button>
          )}
        </div>

        {/* Restricting search to one column skips casting/scanning every
            other column on the table — much faster on huge or wide tables. */}
        <select
          value={searchColumn}
          onChange={e => setSearchColumn(e.target.value)}
          title="Search only this column (faster on large tables)"
          className="flex-shrink-0 py-1.5 px-2 rounded-lg border border-jj-gray-03 bg-white font-johnson-text text-xs focus:outline-none focus:ring-2 focus:ring-jj-blue-03"
        >
          <option value="">All columns</option>
          {knownColumns.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <div className="flex-1" />

        {!downloading ? (
          <button
            onClick={downloadCsv}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-johnson-text text-xs font-bold text-jj-gray-07 bg-white border border-jj-gray-03 hover:bg-jj-gray-01 transition-colors flex-shrink-0"
          >
            <DownloadIcon className="w-3.5 h-3.5 text-jj-gray-07" /> Download CSV
          </button>
        ) : (
          <div className="flex items-center gap-2 bg-jj-gray-01 border border-jj-gray-02 rounded-lg px-3 py-1.5 min-w-[140px] flex-shrink-0">
            <div className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-jj-red flex-shrink-0" />
            <span className="font-johnson-text text-xs text-jj-gray-08">{downloadPct}%</span>
          </div>
        )}

        <button
          onClick={toggleWordWrap}
          title={wordWrap ? 'Collapse to single line' : 'Wrap cell text'}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-johnson-text text-xs font-bold transition-colors flex-shrink-0 ${
            wordWrap ? 'bg-jj-red text-white' : 'bg-white border border-jj-gray-03 text-jj-gray-07 hover:bg-jj-gray-01'
          }`}
        >
          <WrapIcon className={`w-3.5 h-3.5 ${wordWrap ? 'text-white' : 'text-jj-gray-07'}`} /> Word wrap
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 min-h-0 relative">
        {loading && (
          <div className="absolute inset-0 bg-white bg-opacity-80 flex items-center justify-center z-20">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-jj-red" />
          </div>
        )}
        {!loading && uiBusy && (
          // Starts below the real header (left visible — column names and the
          // active sort arrow don't change during a sort/wrap, only the rows
          // do) and only covers the body.
          <div className="absolute left-0 right-0 bottom-0 bg-white z-20 overflow-hidden" style={{ top: `${headerHeight}px` }}>
            {Array.from({ length: 18 }).map((_, ri) => (
              <div key={ri} className="flex border-b border-jj-gray-02" style={{ height: '33px', backgroundColor: ri % 2 === 0 ? '#ffffff' : '#fafaf9' }}>
                <div className="flex-shrink-0 border-r border-jj-gray-04 flex items-center justify-center" style={{ width: '40px' }}>
                  <div className="h-2 w-3 rounded bg-jj-gray-02 animate-pulse" />
                </div>
                {columns.map((c, ci) => (
                  <div key={ci} className="flex-shrink-0 border-r border-jj-gray-02 px-3 py-2 flex items-center" style={{ width: `${renderWidthFor(ci)}px` }}>
                    <div
                      className="h-2.5 rounded bg-jj-gray-02 animate-pulse"
                      style={{ width: `${40 + ((ri * 7 + ci * 13) % 50)}%`, animationDelay: `${(ri * 20) % 300}ms` }}
                    />
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
        <div className="h-full overflow-auto" ref={scrollPaneRef}>
          {!loading && rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 p-8">
              <p className="font-johnson-display text-lg text-jj-gray-08">
                {debouncedSearch ? `No rows match "${debouncedSearch}".` : 'No rows.'}
              </p>
            </div>
          ) : (
            <table style={{ tableLayout: 'fixed', width: `${40 + columns.reduce((sum, _, ci) => sum + renderWidthFor(ci), 0)}px`, borderCollapse: 'separate', borderSpacing: 0 }}>
              <colgroup>
                <col style={{ width: '40px' }} />
                {columns.map((c, ci) => <col key={ci} style={{ width: `${renderWidthFor(ci)}px` }} />)}
              </colgroup>
              <thead>
                <tr ref={headerRowRef} style={{ position: 'sticky', top: 0, zIndex: 2 }}>
                  <th
                    style={{ position: 'sticky', left: 0, zIndex: 3, borderBottom: '2px solid #eb1700', width: '40px', minWidth: '40px' }}
                    className="bg-jj-gray-01 border-r border-jj-gray-04 px-2 py-2.5 text-center"
                  >
                    <span className="font-mono text-[10px] text-jj-gray-04">#</span>
                  </th>
                  {columns.map((col, ci) => (
                    <th
                      key={col}
                      style={{ borderBottom: '2px solid #eb1700', width: `${renderWidthFor(ci)}px` }}
                      className="bg-jj-gray-01 border-r border-jj-gray-04 px-0 py-0 relative select-none group/col"
                    >
                      <div
                        className="px-3 py-2.5 cursor-pointer hover:bg-jj-gray-02 transition-colors"
                        style={{ paddingRight: '1.25rem' }}
                        onClick={() => toggleSort(ci)}
                      >
                        <div className="flex items-start gap-1 min-w-0">
                          <span
                            className="font-mono text-xs font-bold uppercase tracking-wide block whitespace-normal break-words flex-1 min-w-0"
                            title={col}
                            style={{ color: sortColIdx === ci ? '#eb1700' : '#312c2a' }}
                          >
                            {renderColName(col)}
                          </span>
                          <span className={`flex-shrink-0 transition-opacity mt-0.5 ${sortColIdx === ci ? 'opacity-100' : 'opacity-0 group-hover/col:opacity-40'}`}>
                            {sortColIdx === ci && sortDir === 'asc'
                              ? <ChevronLeft className="w-2.5 h-2.5 -rotate-90 text-jj-red" />
                              : sortColIdx === ci && sortDir === 'desc'
                                ? <ChevronLeft className="w-2.5 h-2.5 rotate-90 text-jj-red" />
                                : <SortIcon className="w-2.5 h-2.5 text-jj-gray-06" />
                            }
                          </span>
                        </div>
                      </div>

                      {/* Drag-to-resize handle — separate from the sort click above */}
                      <div
                        onMouseDown={e => onResize(e, ci)}
                        className="absolute top-0 right-0 h-full w-2 cursor-col-resize group/handle flex items-center justify-center z-10"
                      >
                        <div className="w-0.5 h-4 bg-jj-gray-03 rounded-full group-hover/handle:bg-jj-red group-hover/handle:h-full transition-all duration-150" />
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row, i) => {
                  const isEven = i % 2 === 0;
                  return (
                    <tr key={i} className="hover:bg-red-50 transition-colors" style={{ backgroundColor: isEven ? '#ffffff' : '#fafaf9' }}>
                      <td
                        style={{ position: 'sticky', left: 0, zIndex: 1, backgroundColor: isEven ? '#f1efed' : '#e8e6e3' }}
                        className={`border-b border-r border-jj-gray-04 px-2 py-2.5 text-center select-none ${wordWrap ? 'align-top' : ''}`}
                      >
                        <span className="font-mono text-[10px] text-jj-gray-05 leading-none">{offset + i + 1}</span>
                      </td>
                      {columns.map((col, ci) => {
                        const v = row[col];
                        const fullText = formatCellValue(v);
                        const isHuge = fullText.length > CELL_DISPLAY_MAX_CHARS;
                        const text = isHuge ? fullText.slice(0, CELL_DISPLAY_MAX_CHARS) + '…' : fullText;
                        const isClipped = isHuge || (text.length * COL_CHAR_PX + COL_H_PADDING) > renderWidthFor(ci);
                        return (
                          <td
                            key={ci}
                            onClick={() => { if (isClipped) setCellModal({ column: col, value: fullText }); }}
                            className={`border-b border-r border-jj-gray-02 px-3 py-2 font-johnson-text text-xs text-jj-gray-08 ${
                              wordWrap ? 'whitespace-normal break-words align-top' : 'truncate'
                            } ${isClipped ? 'cursor-pointer hover:underline hover:text-jj-blue-03' : ''}`}
                            title={isHuge ? undefined : text}
                          >
                            {highlightMatch(text, debouncedSearch)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Footer — row range summary | rows-per-page, pager, page jump */}
      <div className="flex-shrink-0 flex items-center gap-3 px-5 py-2.5 bg-white border-t border-jj-gray-02 flex-wrap">
        <span className="font-johnson-text text-xs text-jj-gray-06 flex-shrink-0">
          {rows.length > 0 && (
            <>Rows <b className="text-jj-gray-08">{rangeStart.toLocaleString()}</b>–<b className="text-jj-gray-08">{rangeEnd.toLocaleString()}</b>
              {totalRows != null && <> of <b className="text-jj-gray-08">{totalRows.toLocaleString()}</b></>}
            </>
          )}
        </span>

        <div className="flex-1" />

        <label className="font-johnson-text text-xs text-jj-gray-06 flex items-center gap-1.5 flex-shrink-0">
          Rows per page
          <select
            value={pageSize}
            onChange={e => { const next = Number(e.target.value); setPageSize(next); setEffectiveLimit(next); setOffset(0); }}
            className="px-2 py-1 rounded border border-jj-gray-03 bg-white font-johnson-text text-xs"
          >
            {[50, 100, 250, 500].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>

        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            disabled={offset === 0 || loading}
            onClick={goFirst}
            className="px-2.5 py-1 rounded border border-jj-gray-03 bg-white font-johnson-text text-xs hover:bg-jj-gray-02 disabled:opacity-40"
          >
            First
          </button>
          <button
            disabled={offset === 0 || loading}
            onClick={goPrev}
            className="px-2 py-1 rounded border border-jj-gray-03 bg-white hover:bg-jj-gray-02 disabled:opacity-40"
          >
            <ChevronLeft className="w-3.5 h-3.5 text-jj-gray-07" />
          </button>

          <span className="font-johnson-text text-xs text-jj-gray-06 flex items-center gap-1.5 px-1 whitespace-nowrap">
            Page
            <input
              type="text"
              inputMode="numeric"
              value={pageJump}
              disabled={loading}
              onChange={e => setPageJump(e.target.value.replace(/[^0-9]/g, ''))}
              onBlur={submitPageJump}
              onKeyDown={e => { if (e.key === 'Enter') { e.currentTarget.blur(); } }}
              className="w-12 px-1.5 py-1 rounded border border-jj-gray-03 bg-white font-johnson-text text-xs text-center focus:outline-none focus:ring-2 focus:ring-jj-blue-03"
            />
            {totalPages ? `of ${totalPages}` : ''}
          </span>

          <button
            disabled={!hasMore || loading}
            onClick={goNext}
            className="px-2 py-1 rounded border border-jj-gray-03 bg-white hover:bg-jj-gray-02 disabled:opacity-40"
          >
            <ChevronRight className="w-3.5 h-3.5 text-jj-gray-07" />
          </button>
          {totalRows != null && (
            <button
              disabled={!hasMore || loading}
              onClick={goLast}
              className="px-2.5 py-1 rounded border border-jj-gray-03 bg-white font-johnson-text text-xs hover:bg-jj-gray-02 disabled:opacity-40"
            >
              Last
            </button>
          )}
        </div>
      </div>

      {/* Full-value modal for clipped cells */}
      {cellModal && (
        <div
          className="fixed inset-0 bg-black bg-opacity-40 z-50 flex items-center justify-center p-4"
          onClick={() => setCellModal(null)}
        >
          <div
            className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[80vh] flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3 border-b border-jj-gray-02 flex-shrink-0">
              <span className="font-mono text-xs font-bold uppercase tracking-wide text-jj-gray-08">{cellModal.column}</span>
              <button onClick={() => setCellModal(null)} className="p-1 rounded hover:bg-jj-gray-01 transition-colors">
                <CloseIcon className="w-4 h-4 text-jj-gray-06" />
              </button>
            </div>
            <div className="p-5 overflow-auto font-johnson-text text-sm text-jj-gray-08 whitespace-pre-wrap break-words">
              {(() => {
                // Pretty-print if it's JSON (objects/arrays render single-line
                // in the cell/CSV, but there's room here to indent it nicely).
                let display = cellModal.value;
                try {
                  const parsed = JSON.parse(cellModal.value);
                  if (parsed && typeof parsed === 'object') display = JSON.stringify(parsed, null, 2);
                } catch { /* not JSON — use raw text as-is */ }
                return highlightMatch(display, debouncedSearch);
              })()}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default DataApiTableView;