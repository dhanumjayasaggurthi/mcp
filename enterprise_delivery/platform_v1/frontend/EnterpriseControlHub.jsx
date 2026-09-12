import React, { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson, REFERENCE_MODE } from './apiClient.js';
import { canUseSSO, clearAuthentication, completeSignIn, signIn, useAccessToken } from './auth.js';
import { ResourcePanel, Onboarding } from './ui/Resources.jsx';
import { RESOURCES } from './ui/resources.js';
import { Overview, AuditPanel, MonitoringPanel, EnvironmentPanel, AccessPanel, MCPPanel } from './ui/Panels.jsx';
import { Explorer } from './ui/Explorer.jsx';
import { Badge, Button, ErrorNotice, Field, Icon } from './ui/primitives.jsx';

const NAV = [
  ['Workspace', [['overview','Platform overview','grid'],['sources','Sources','source'],['datasets','Data products','database']]],
  ['Governance', [['policies','Policies','shield'],['clients','Consumers','users'],['agents','Agents','bot'],['guardrails','Guardrails','guard']]],
  ['Operate & explore', [['indexes','Search indexes','layers'],['mcp','MCP tools','code'],['explorer','API explorer','play'],['monitoring','Monitoring','activity'],['audit','Audit trail','history']]],
  ['Administration', [['environments','Environment','globe'],['access','My access','key']]],
];
const PAGES = NAV.flatMap(([,items]) => items);
function currentPage() { const id=location.hash.slice(1); return PAGES.some(x=>x[0]===id) ? id : 'overview'; }
function Brand() { return <div className="brand"><span className="brand-symbol"><Icon name="spark" size={27}/></span><div><strong>SmartHub</strong><span>MCP & Agentic Gateway</span></div></div>; }

function Login({ error, busy, authenticate }) {
  const [token,setToken]=useState('');
  const [localError,setLocalError]=useState(null);
  async function submit(e) { e.preventDefault(); try { useAccessToken(token); setToken(''); setLocalError(null); await authenticate(); } catch(err) { setLocalError(err); } }
  return <main className="login-page"><section className="login-story"><Brand/><div><span className="eyebrow">Your governed data workspace</span><h1>Make your data<br/><em>work together.</em></h1><p>One place to connect sources, publish governed APIs, and equip your agents with trusted data.</p><div className="login-features">{[['source','Connect your sources'],['shield','Control every permission'],['bot','Enable your consumers']].map(([icon,label])=><div key={icon}><Icon name={icon}/>{label}</div>)}</div></div><small>SmartHub MCP & Agentic Gateway</small></section>
    <section className="login-form"><div><span className="icon-tile rose"><Icon name="key" size={25}/></span><h2>Welcome to SmartHub</h2><p>Sign in with an identity authorized by your gateway.</p><ErrorNotice error={localError || error}/>
      {canUseSSO() && <Button variant="primary" className="full-width" busy={busy} onClick={async()=>{try{setLocalError(null);await signIn();await authenticate();}catch(e){setLocalError(e);}}}>Continue with single sign-on</Button>}
      <form onSubmit={submit}><Field label="Bearer access token" hint="Kept only in memory for this browser session."><textarea value={token} onChange={e=>setToken(e.target.value)} rows={3} autoComplete="off" spellCheck="false" aria-label="Bearer access token"/></Field><Button type="submit" variant="primary" className="full-width" busy={busy} disabled={!token.trim()}>Open workspace</Button></form>
      <Button className="full-width" busy={busy} onClick={authenticate}>Retry configured session</Button>
      <p className="login-note">Your gateway validates identity, scopes, and permissions on every request.</p>{REFERENCE_MODE && <Badge value="reference mode"/>}
    </div></section></main>;
}
class RenderBoundary extends React.Component {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed ? <div className="fatal-error" role="alert"><h1>This screen could not be displayed</h1><p>Reload the console to start a fresh session.</p><Button onClick={()=>location.reload()}>Reload console</Button></div> : this.props.children;}
}
function Console() {
  const [session,setSession]=useState(null), [busy,setBusy]=useState(true), [error,setError]=useState(null);
  const [page,setPage]=useState(currentPage), [menu,setMenu]=useState(false), [onboard,setOnboard]=useState(false);
  const [revision,setRevision]=useState(0), [toast,setToast]=useState('');
  const mounted=useRef(true), attempt=useRef(0), menuButton=useRef(null), menuRef=useRef(null), toastTimer=useRef(null);
  const authenticate=useCallback(async()=>{
    const id=++attempt.current; setBusy(true); setError(null);
    try { const value=await requestJson('/v1/control/session'); if(mounted.current && id===attempt.current) { if(!value.is_admin) throw Object.assign(new Error('This identity cannot administer the SmartHub control plane.'),{status:403}); setSession(value); } }
    catch(e){if(mounted.current && id===attempt.current){setError(e);setSession(null);}}
    finally{if(mounted.current && id===attempt.current)setBusy(false);}
  },[]);
  useEffect(()=>{
    mounted.current=true;
    completeSignIn().then(()=>{if(mounted.current)authenticate();}).catch(e=>{if(mounted.current){setError(e);setBusy(false);}});
    const expired=()=>{clearAuthentication();setSession(null);setOnboard(false);setMenu(false);setToast('');};
    const hash=()=>setPage(currentPage());
    window.addEventListener('hashchange',hash);window.addEventListener('smarthub:session-expired',expired);
    return()=>{mounted.current=false;attempt.current++;clearTimeout(toastTimer.current);window.removeEventListener('hashchange',hash);window.removeEventListener('smarthub:session-expired',expired);};
  },[authenticate]);
  useEffect(()=>{if(!menu)return;const oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';menuRef.current?.querySelector('button')?.focus();
    const key=e=>{if(e.key==='Escape'){setMenu(false);menuButton.current?.focus();}
      if(e.key==='Tab'){const nodes=[...menuRef.current.querySelectorAll('button,a[href]')];const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};
    document.addEventListener('keydown',key);return()=>{document.body.style.overflow=oldOverflow;document.removeEventListener('keydown',key);};
  },[menu]);
  function navigate(id){setPage(id);location.hash=id;setMenu(false);}
  function notify(message){setToast(message);clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setToast(''),5000);}
  function signOut(){attempt.current++;clearAuthentication();setSession(null);setError(null);setOnboard(false);setToast('');setBusy(false);setMenu(false);globalThis.edpAuth?.signOut?.();}
  if(!session)return <Login error={error} busy={busy} authenticate={authenticate}/>;
  const title=PAGES.find(x=>x[0]===page)?.[1];
  let content;
  if(RESOURCES[page])content=<ResourcePanel key={page+revision} kind={page} session={session} onOnboard={()=>setOnboard(true)} notify={notify}/>;
  else if(page==='overview')content=<Overview key={revision} session={session} navigate={navigate} onOnboard={()=>setOnboard(true)}/>;
  else if(page==='audit')content=<AuditPanel/>;
  else if(page==='monitoring')content=<MonitoringPanel/>;
  else if(page==='environments')content=<EnvironmentPanel session={session}/>;
  else if(page==='access')content=<AccessPanel session={session} navigate={navigate}/>;
  else if(page==='mcp')content=<MCPPanel navigate={navigate}/>;
  else content=<Explorer/>;
  return <div className="app-shell"><a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById('main-content').focus();}}>Skip to content</a>
    {menu && <div className="nav-scrim" onClick={()=>{setMenu(false);menuButton.current?.focus();}}/>}
    <aside ref={menuRef} className={'sidebar '+(menu?'open':'')} aria-label="Workspace navigation" {...(menu?{role:'dialog','aria-modal':true}:{})}><div className="sidebar-brand"><Brand/><Button className="mobile-only" icon="close" aria-label="Close navigation" onClick={()=>{setMenu(false);menuButton.current?.focus();}}/></div>
      <div className="workspace-label"><span className="workspace-avatar">SH</span><div><strong>Control workspace</strong><span>{session.environment} · {session.runtime_mode}</span></div></div>
      <nav>{NAV.map(([label,items])=><div className="nav-group" key={label}><span className="nav-label">{label}</span>{items.map(([id,label,icon])=><button key={id} aria-current={page===id?'page':undefined} onClick={()=>navigate(id)}><Icon name={icon} size={19}/><span>{label}</span>{page===id&&<span className="active-nav-dot"/>}</button>)}</div>)}</nav>
      <div className="sidebar-foot"><span className="icon-tile rose"><Icon name="shield" size={18}/></span><div><strong>Governed by design</strong><span>Identity-aware access</span></div></div>
    </aside>
    <div className="workspace-main"><header className="topbar"><button ref={menuButton} className="button secondary mobile-only" aria-label="Open navigation" aria-expanded={menu} onClick={()=>setMenu(true)}><Icon name="menu"/></button><div className="breadcrumb">Workspace <Icon name="chevron" size={14}/><strong>{title}</strong></div><div className="topbar-actions"><Badge value={session.environment}/>{session.runtime_mode==='reference'&&<Badge value="reference mode"/>}<span className="identity-avatar">{session.subject.slice(0,2).toUpperCase()}</span><span className="identity-label" title={session.subject}>{session.subject}</span><Button icon="logout" aria-label="Sign out" onClick={signOut}/></div></header>
      <main id="main-content" tabIndex={-1} className="main-content" key={page}>{content}<footer className="page-footer"><span>SmartHub MCP & Agentic Gateway</span><span>{session.release || 'Deployment version not reported'}</span></footer></main>
    </div>
    {onboard&&<Onboarding session={session} notify={notify} onClose={saved=>{setOnboard(false);if(saved)setRevision(x=>x+1);}}/>}
    <div className={'toast '+(toast?'visible':'')} role="status" aria-live="polite">{toast&&<><Icon name="check"/>{toast}<Button icon="close" aria-label="Dismiss notification" onClick={()=>setToast('')}/></>}</div>
  </div>;
}
export default function EnterpriseControlHub(){return <RenderBoundary><Console/></RenderBoundary>;}
