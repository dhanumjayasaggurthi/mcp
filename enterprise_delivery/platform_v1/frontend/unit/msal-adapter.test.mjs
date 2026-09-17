import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createMsalAdapter} from '../src/msalAdapter.js';
function fixture(path='/overview') {
  let active; let silent=0; let redirects=0;
  const instance={initialize:async()=>{},handleRedirectPromise:async()=>null,getAllAccounts:()=>[{id:'a'}],getActiveAccount:()=>active,setActiveAccount:a=>active=a,acquireTokenSilent:async request=>{silent++;assert.deepEqual(request.scopes,['api://hub/access_as_user']);return {accessToken:'api-token'};},loginRedirect:async()=>{redirects++;},clearCache:async()=>{active=null;}};
  const location={pathname:path,search:'',origin:'https://hub.example',replace:p=>{location.destination=p;}};
  return {instance,location,adapter:createMsalAdapter(instance,{scopes:['api://hub/access_as_user'],location,storage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}}}),counts:()=>({silent,redirects})};
}
test('concurrent callers share one API token acquisition',async()=>{const f=fixture();await f.adapter.initialize();assert.deepEqual(await Promise.all([f.adapter.getAccessToken(),f.adapter.getAccessToken()]),['api-token','api-token']);assert.equal(f.counts().silent,1);});
test('logout blocks token acquisition and lands on logout',async()=>{const f=fixture();await f.adapter.initialize();await f.adapter.signOut();assert.equal(await f.adapter.getAccessToken(),null);assert.equal(f.location.destination,'/logout');});
test('logout route never acquires tokens or redirects to login',async()=>{const f=fixture('/logout');await f.adapter.initialize();assert.equal(await f.adapter.getAccessToken(),null);assert.deepEqual(f.counts(),{silent:0,redirects:0});});
test('multiple accounts require explicit selection',async()=>{const f=fixture();f.instance.getAllAccounts=()=>[{id:'a'},{id:'b'}];await f.adapter.initialize();assert.equal(await f.adapter.getAccessToken(),null);});
test('interaction required redirects once',async()=>{const f=fixture();f.instance.acquireTokenSilent=async()=>{throw {errorCode:'interaction_required'};};await f.adapter.initialize();await Promise.all([f.adapter.getAccessToken(),f.adapter.getAccessToken()]);assert.equal(f.counts().redirects,1);});
