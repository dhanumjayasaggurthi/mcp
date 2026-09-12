import React, { useEffect, useId, useRef } from 'react';

const PATHS = {
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  database: 'M4 5c0-4 16-4 16 0s-16 4-16 0m0 0v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0',
  source: 'M8 3v5M16 3v5M5 8h14v3a7 7 0 0 1-7 7v4M5 11a7 7 0 0 0 7 7',
  shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z M8 12l3 3 5-6',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-4M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  bot: 'M6 7h12a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3M12 3v4M9 12v2M15 12v2M8 17h8M1 12v5M23 12v5',
  guard: 'M4 4h16v16H4zM4 9h16M9 9v11M14 13h3M14 17h3',
  layers: 'm12 2 10 5-10 5L2 7zM2 12l10 5 10-5M2 17l10 5 10-5',
  code: 'm8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18',
  activity: 'M2 12h4l3-8 6 16 3-8h4',
  history: 'M3 10a9 9 0 1 1 0 5M3 3v7h7M12 7v5l4 2',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
  key: 'M15 3a6 6 0 1 1-4 10l-8 8H1v-4l8-8a6 6 0 0 1 6-6M16 6h.01',
  search: 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0m-2 6 6 6',
  plus: 'M12 4v16M4 12h16',
  close: 'm6 6 12 12M18 6 6 18',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  chevron: 'm9 5 7 7-7 7',
  refresh: 'M20 8a9 9 0 0 0-15-4L2 7m0-5v5h5M4 16a9 9 0 0 0 15 4l3-3m0 5v-5h-5',
  check: 'm5 12 4 4L19 6',
  warning: 'm12 3 10 18H2zM12 9v4M12 17h.01',
  copy: 'M9 9h12v12H9zM15 9V3H3v12h6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  book: 'M4 3h14a2 2 0 0 1 2 2v16H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3M3 17h17M8 7h8M8 11h6',
  logout: 'M9 3H3v18h6M10 12h12m-5-5 5 5-5 5',
  menu: 'M3 5h18M3 12h18M3 19h18',
  spark: 'm12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
  play: 'm7 3 14 9-14 9z',
};
export function Icon({ name, size = 20, className = '' }) {
  return <svg aria-hidden="true" className={'icon ' + className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"><path d={PATHS[name] || PATHS.grid}/></svg>;
}
export function Button({ children, icon, variant = 'secondary', busy, className = '', ...props }) {
  return <button type="button" {...props} disabled={props.disabled || busy} aria-busy={busy || undefined} className={'button ' + variant + ' ' + className}>{busy ? <span className="spinner"/> : icon && <Icon name={icon} size={17}/>} {children}</button>;
}
export function Badge({ value }) {
  const label = String(value ?? 'unknown');
  const color = ['active','healthy','ready','reachable','allow','succeeded'].includes(label) ? 'teal' :
    ['failed','disabled','down','deny','high','critical','error'].includes(label) ? 'rose' :
    ['draft','building','validating','promoting','degraded','medium','queued','running'].includes(label) ? 'amber' : 'violet';
  return <span className={'badge ' + color}><span className="dot"/>{label.replaceAll('_', ' ')}</span>;
}
export function Card({ title, subtitle, action, children, className = '' }) {
  return <section className={'card ' + className}>{(title || action) && <div className="card-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}
export function Empty({ icon = 'database', title, description, action }) {
  return <div className="empty"><span className="empty-symbol"><Icon name={icon} size={30}/></span><h3>{title}</h3>{description && <p>{description}</p>}{action}</div>;
}
export function ErrorNotice({ error, onRetry }) {
  if (!error) return null;
  const hint = error.status === 409 ? 'This configuration changed. Reload it before saving again.' :
    error.status === 403 ? 'Your identity does not have permission for this operation.' :
    error.status === 429 ? 'Capacity is busy. Retry after ' + (error.retryAfter || 'a few') + ' seconds.' : '';
  return <div className="error-notice" role="alert"><Icon name="warning"/><div><strong>{error.message || String(error)}</strong>{hint && <p>{hint}</p>}{error.traceId && <small>Trace ID: <code>{error.traceId}</code></small>}</div>{onRetry && <Button onClick={onRetry}>Retry</Button>}</div>;
}
export function Skeleton() {
  return <div className="skeleton" role="status" aria-label="Loading"><div/><div/><div/></div>;
}
export function Field({ label, hint, children, ...props }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label>{children ? React.cloneElement(children, { id }) : <input id={id} {...props}/>} {hint && <small>{hint}</small>}</div>;
}
export function JsonView({ value }) {
  return <pre className="json-view" tabIndex="0">{JSON.stringify(value, null, 2)}</pre>;
}
export function Dialog({ title, subtitle, children, onClose, busy, wide = false }) {
  const ref = useRef(null);
  const id = useId();
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.showModal();
    return () => { previous?.focus?.(); };
  }, []);
  return <dialog ref={ref} aria-labelledby={id} className={'dialog ' + (wide ? 'wide' : '')}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-heading"><div><h2 id={id}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><Button aria-label="Close dialog" icon="close" disabled={busy} onClick={onClose}/></div>
    {children}
  </dialog>;
}
export function PageHeading({ eyebrow, title, description, action }) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{action}</div>;
}
export function DownloadButton({ value, filename, label = 'Download JSON' }) {
  return <Button icon="download" onClick={() => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }}>{label}</Button>;
}
