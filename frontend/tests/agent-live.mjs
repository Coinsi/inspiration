// A real configured project text model; only the explicit isolated QA fixture is mutated.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const base=process.env.APP_URL,out=process.env.ARTIFACT_DIR;assert.ok(base&&out);
const fixture=JSON.parse(await readFile(`${out}/fixture.json`,'utf8'));assert.ok(fixture.model_configured);
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1560,height:1080}}),page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));const report={project:fixture.project,model:fixture.model,checks:[]};
try{
 const a=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(a.ok());const token=(await a.json()).access_token,headers={Authorization:`Bearer ${token}`};
 const call=async(method,path,data)=>{const r=await context.request[method](`${base}/api/v1${path}`,{headers,data});assert.ok(r.ok(),await r.text());return r.json()};
 const root=`/projects/${fixture.project}`;assert.equal((await call('get',root)).name,'Agent 验收 · 审阅与恢复');
 await context.addInitScript(token=>{localStorage.setItem('inspiration_token',token);localStorage.setItem('inspiration_lang','zh')},token);
 let run=(await call('get',`${root}/agent/runs`)).find(r=>['thinking','queued','running','waiting_review'].includes(r.status));
 if(run){await page.goto(`${base}${root}/agent?run=${run.id}`)}else{
 await page.goto(`${base}${root}/agent`);await page.getByRole('button',{name:'新的创作任务',exact:true}).click();await page.getByLabel('创作目标',{exact:true}).fill('请先读取我授权的镜头，将标题改为「晨曦海岸」，描述改为「清晨，主角沿金色海岸步行，海风轻拂衣角。」。只做这两处修改，提出审阅后等待我批准；批准后确认实际写入结果并结束，不要生成图片视频。');
 const chosenShot=(await call('get',`${root}/shots`)).find(s=>s.id===fixture.shot);await page.getByRole('button',{name:/^选择创作对象/}).click();await page.getByLabel('搜索创作对象').fill(chosenShot.code);await page.getByRole('dialog').getByRole('button',{name:/^选择对象 /}).click();await page.getByRole('button',{name:'完成选择',exact:true}).click();
 const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/agent/runs'));
 await page.getByRole('button',{name:'开始创作',exact:true}).click();const response=await created;assert.ok(response.ok());run=await response.json();
 }
 for(let i=0;i<180;i++){run=await call('get',`${root}/agent/runs/${run.id}`);if(run.status==='waiting_review'||run.status==='failed'||run.status==='succeeded')break;await page.waitForTimeout(1000)}
 assert.equal(run?.status,'waiting_review',JSON.stringify(run));report.run=run.id;assert.ok(run.steps.some(s=>s.tool==='object.read'&&s.status==='succeeded'));const step=run.steps.find(s=>s.status==='review');assert.equal(step.result.after.fields.title,'晨曦海岸');
 let shots=await call('get',`${root}/shots`);assert.equal(shots.find(s=>s.id===fixture.shot).title,'日落海边');report.checks.push('Real text model reads target and proposes changes without premature writes');
 await page.getByRole('button',{name:'应用修改并继续',exact:true}).waitFor();await page.screenshot({path:`${out}/agent-review-dark.png`,animations:'disabled'});
 await page.reload();await page.getByRole('button',{name:'应用修改并继续',exact:true}).waitFor();const approved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decision'));await page.getByRole('button',{name:'应用修改并继续',exact:true}).click();assert.ok((await approved).ok());
 for(let i=0;i<120;i++){run=await call('get',`${root}/agent/runs/${run.id}`);if(['succeeded','failed','waiting_review','paused'].includes(run.status))break;await page.waitForTimeout(1000)}assert.equal(run.status,'succeeded',JSON.stringify(run));
 shots=await call('get',`${root}/shots`);assert.equal(shots.find(s=>s.id===fixture.shot).title,'晨曦海岸');assert.equal(shots.find(s=>s.id===fixture.shot).description,'清晨，主角沿金色海岸步行，海风轻拂衣角。');report.checks.push('Review survives reload; approval applies exact changes and Agent receives execution feedback');
 assert.ok(run.steps.find(s=>s.id===step.id).applied?.version_id);assert.ok(!run.steps.some(s=>s.tool==='generation.request'));report.checks.push('Immutable edit version retained; no unrequested media generation');
 await page.waitForTimeout(1700);await page.screenshot({path:`${out}/agent-result-dark.png`,animations:'disabled'});await page.evaluate(()=>{document.documentElement.classList.remove('dark');document.documentElement.classList.add('light')});await page.waitForTimeout(250);await page.screenshot({path:`${out}/agent-result-light.png`,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:`${out}/agent-mobile.png`,fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);report.passed=true;report.turns=run.turns;report.steps=run.steps.map(s=>({tool:s.tool,status:s.status,decision:s.decision}));report.errors=errors;await writeFile(`${out}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:`${out}/failure.png`,fullPage:true});await writeFile(`${out}/failure.json`,JSON.stringify({...report,error:String(e),errors},null,2));throw e}finally{await browser.close()}
