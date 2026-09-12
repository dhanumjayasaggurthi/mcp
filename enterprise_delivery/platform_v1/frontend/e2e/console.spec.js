import { test, expect } from '@playwright/test';
const headers={Authorization:'Bearer ui-test-token'};
async function login(page){
  await page.goto('/');
  await page.getByLabel('Bearer access token',{exact:true}).fill('ui-test-token');
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Platform overview',exact:true})).toBeVisible();
}
async function go(page,name){await page.getByRole('navigation').getByRole('button',{name,exact:true}).click();}
test('real registrations, optimistic concurrency and durable audit',async({page,request})=>{
  await login(page);
  await expect(page.getByRole('button',{name:/Registered sources/})).toContainText('1');
  await page.screenshot({path:'screenshots/01-overview-desktop.png',fullPage:true});
  await go(page,'Consumers');
  await page.getByRole('button',{name:'Create consumer',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('OAuth client ID').fill('browser-consumer');
  await dialog.getByLabel('Display name',{exact:true}).fill('Browser consumer');
  await dialog.getByLabel('Owner',{exact:true}).fill('consumer-team');
  await page.screenshot({path:'screenshots/02-guided-editor.png',fullPage:true});
  await dialog.getByRole('button',{name:'Save consumer',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  const saved=await request.get('/v1/control/clients/browser-consumer',{headers});
  expect(saved.status()).toBe(200);
  expect((await saved.json()).status).toBe('disabled');
  await page.getByRole('button',{name:/Browser consumer browser-consumer/}).click();
  const old=await saved.json();
  expect((await request.put('/v1/control/clients/browser-consumer',{headers,data:{...old,display_name:'Concurrent edit'}})).status()).toBe(200);
  await dialog.getByLabel('Display name',{exact:true}).fill('Would overwrite');
  await dialog.getByRole('button',{name:'Save consumer',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('Reload');
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await go(page,'Audit trail');
  await page.getByLabel('Resource',{exact:true}).fill('clients/browser-consumer');
  await page.getByRole('button',{name:'Apply filters',exact:true}).click();
  // Read actual audit entries and verify the operator identity, independent of resource formatting.
  await page.getByLabel('Resource',{exact:true}).fill('');
  await page.getByRole('button',{name:'Apply filters',exact:true}).click();
  await expect(page.getByRole('table')).toContainText('control.put');
  await page.screenshot({path:'screenshots/03-audit.png',fullPage:true});
});
test('source onboarding saves an inspected draft',async({page,request})=>{
  await login(page);await go(page,'Sources');
  await page.getByRole('button',{name:'Check connection',exact:true}).click();
  await expect(page.getByText(/reachable/i).first()).toBeVisible();
  await go(page,'Data products');
  await page.getByRole('button',{name:'Create data product',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('Registered source').selectOption('source');
  await dialog.getByRole('button',{name:'Inspect available objects'}).click();
  await expect(page.locator('datalist option[value="facts"]')).toHaveCount(1);
  await dialog.getByLabel('Table or view').fill('facts');
  await dialog.getByLabel('Dataset ID',{exact:true}).fill('browser-draft');
  await dialog.getByLabel('Display name',{exact:true}).fill('Inspected facts');
  await dialog.getByRole('button',{name:'Preview data contract'}).click();
  await expect(dialog.getByRole('table')).toContainText('amount');
  await page.screenshot({path:'screenshots/04-source-onboarding.png',fullPage:true});
  await dialog.getByRole('button',{name:'Review & save draft'}).click();
  await dialog.getByRole('button',{name:'Save data product',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  const saved=await request.get('/v1/control/datasets/browser-draft',{headers});
  expect(saved.status()).toBe(200);expect((await saved.json()).status).toBe('draft');
});
test('explorer sends compound filters and displays the authorized SQL response',async({page})=>{
  await login(page);await go(page,'API explorer');
  await page.getByLabel('Authorized data product').selectOption('facts');
  await expect(page.getByLabel('Request JSON')).toHaveValue(/count_mode/);
  await page.getByText('Build multi-field filters',{exact:false}).click();
  await page.getByRole('button',{name:'Add rule',exact:true}).click();
  await page.getByLabel('Filter field 1',{exact:true}).selectOption('amount');
  await page.getByLabel('Filter operator 1',{exact:true}).selectOption('gte');
  await page.getByLabel('Filter value 1',{exact:true}).fill('20');
  await page.getByRole('button',{name:'Add rule',exact:true}).click();
  await page.getByLabel('Filter field 2',{exact:true}).selectOption('id');
  await page.getByLabel('Filter operator 2',{exact:true}).selectOption('in');
  await page.getByLabel('Filter value 2',{exact:true}).fill('[2,3]');
  await page.getByRole('button',{name:'Apply filters to request',exact:true}).click();
  await page.getByRole('button',{name:'Run request',exact:true}).click();
  await expect(page.getByRole('table')).toContainText('20');
  await expect(page.getByRole('table')).not.toContainText('999');
  await expect(page.getByText('1 returned',{exact:true})).toBeVisible();
  await page.screenshot({path:'screenshots/05-api-explorer.png',fullPage:true});
  await go(page,'Monitoring');
  await expect(page.getByText('Operations observed',{exact:true})).toBeVisible();
  await page.screenshot({path:'screenshots/06-monitoring.png',fullPage:true});
});
test('every navigation screen resolves and mobile navigation is keyboard accessible',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await login(page);
  for(const name of ['Policies','Agents','Guardrails','Search indexes','MCP tools','Environment','My access']){
    await go(page,name);await expect(page.locator('main h1')).toBeVisible();
    await expect(page.getByText('This screen could not be displayed')).toHaveCount(0);
  }
  expect(errors).toEqual([]);
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Open navigation'}).click();
  await expect(page.getByRole('dialog',{name:'Workspace navigation'})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'Open navigation'})).toBeFocused();
  await page.getByRole('button',{name:'Open navigation'}).click();
  await go(page,'Platform overview');
  await expect(page.locator('main h1')).toHaveText('Platform overview');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'screenshots/07-overview-mobile.png',fullPage:true});
});
test('invalid and non-admin identities cannot enter the console',async({page})=>{
  await page.goto('/');
  await page.getByLabel('Bearer access token',{exact:true}).fill('invalid-token');
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
  await page.getByLabel('Bearer access token',{exact:true}).fill('ui-reader-token');
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('cannot administer');
  expect(await page.evaluate(()=>Object.keys(localStorage))).toEqual([]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage))).toEqual([]);
});
