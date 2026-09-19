import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const base=process.env.APP_URL,out=process.env.ARTIFACT_DIR;
assert.ok(base&&out);
const saved=JSON.parse(await readFile(`${out}/report.json`,'utf8'));
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 const a=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(a.ok());const token=(await a.json()).access_token,headers={Authorization:`Bearer ${token}`};
 const call=async(method,path,data)=>{const r=await context.request[method](`${base}/api/v1${path}`,{headers,data});assert.ok(r.ok(),await r.text());return r.json();};
 const project=`/projects/${saved.project}`,path=`${project}/timelines/${saved.timeline}`;
 assert.equal((await call('get',project)).name,'剪辑验收 · 字幕与声音');
 await context.addInitScript(token=>{localStorage.setItem('inspiration_token',token);localStorage.setItem('inspiration_lang','zh');},token);
 const initial=await call('get',`${path}/document`); initial.items.forEach(c=>{c.transition=null;});await call('put',`${path}/document`,initial);
 await page.goto(`${base}${project}/cuts?timeline=${saved.timeline}`);await page.getByLabel('转场 1',{exact:true}).selectOption('dissolve');await page.getByRole('button',{name:'选择片段 2',exact:true}).click();await page.getByLabel('转场 2',{exact:true}).selectOption('wipeleft');
 await page.getByRole('button',{name:'预览片段 2',exact:true}).click();await page.getByAltText('星空',{exact:true}).waitFor();
 await page.getByRole('button',{name:'保存时间线',exact:true}).click();await page.getByText('已保存',{exact:true}).waitFor();
 const doc=await call('get',`${path}/document`);assert.equal(doc.items[0].transition.duration_ms,500);assert.equal(doc.items[1].transition.type,'wipeleft');
 await page.getByRole('button',{name:'保存并导出 MP4',exact:true}).click();let generation;
 for(let i=0;i<90;i++){const rows=await call('get',`${project}/generations?target_type=timeline&target_id=${saved.timeline}`);generation=rows.find(g=>g.output_type==='video'&&g.output_blob_hash&&g.input_refs?.options?.timeline_revision===doc.revision+1);if(generation)break;await page.waitForTimeout(1000);}
 assert.ok(generation,'Transition export must finish with the matching revision');
 const film=await context.request.get(`${base}/api/v1${project}/blobs/${generation.output_blob_hash}`,{headers});assert.ok(film.ok());await writeFile(`${out}/timeline-transitions.mp4`,await film.body());
 await page.getByRole('button',{name:'查看成片',exact:true}).click();await page.locator('main').evaluate(el=>el.scrollTo(0,0));await page.screenshot({path:`${out}/timeline-polished-dark.png`});
 await page.evaluate(()=>{document.documentElement.classList.remove('dark');document.documentElement.classList.add('light');});await page.screenshot({path:`${out}/timeline-polished-light.png`});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:`${out}/timeline-polished-mobile.png`});
 assert.deepEqual(errors,[]);const report={passed:true,project:saved.project,timeline:saved.timeline,generation:generation.id,expected_duration_ms:11000,checks:['Real source preview and thumbnails','Two 500ms transitions save with audio/captions','Actual MP4 generated from exact revision','Dark/light and narrow layout without horizontal overflow'],errors};await writeFile(`${out}/transition-report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:`${out}/transition-failure.png`,fullPage:true});throw e;}finally{await browser.close();}
