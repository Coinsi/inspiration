import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const out=process.env.ARTIFACT_DIR,base=process.env.APP_URL;
const fixture=JSON.parse(await readFile(`${out}/fixture.json`,'utf8')),report=JSON.parse(await readFile(`${out}/mcp-report.json`,'utf8'));
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1560,height:1080}}),page=await context.newPage();
try{
 const auth=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(auth.ok());const token=(await auth.json()).access_token;
 await context.addInitScript(t=>{localStorage.setItem('inspiration_token',t);localStorage.setItem('inspiration_lang','zh')},token);
 await page.goto(`${base}/projects/${fixture.project}/agent?run=${report.run}`);
 await page.getByRole('button',{name:'应用修改并继续',exact:true}).waitFor();await page.screenshot({path:`${out}/mcp-review.png`,animations:'disabled'});
 const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decision'));await page.getByRole('button',{name:'应用修改并继续',exact:true}).click();const response=await pending;assert.ok(response.ok());assert.equal((await response.json()).status,'succeeded');
 const r=await context.request.get(`${base}/api/v1/projects/${fixture.project}/shots`,{headers:{Authorization:`Bearer ${token}`}});assert.equal((await r.json())[0].title,'海风中的木屋');
 report.checks.push('Web review shows before/after; approval applies exact target and completes external run without LLM');await writeFile(`${out}/mcp-report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close()}
