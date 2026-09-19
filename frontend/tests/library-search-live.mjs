// Requires the real-index fixture report created in the isolated preview; never targets user projects.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const base=process.env.APP_URL,out=process.env.ARTIFACT_DIR;
assert.ok(base&&out);
const fixture=JSON.parse(await readFile(`${out}/real-search/report.json`,'utf8'));
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
const errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
try {
 const auth=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(auth.ok());const token=(await auth.json()).access_token,headers={Authorization:`Bearer ${token}`};
 const call=async(method,path,data)=>{const r=await context.request[method](`${base}/api/v1${path}`,{headers,data});assert.ok(r.ok(),await r.text());return r.json();};
 const project=`/projects/${fixture.project}`,library=`${project}/library`;
 assert.equal((await call('get',project)).name,'画面检索验收 · 本地模型');
 let shots=await call('get',`${project}/shots`);
 if(!shots.length){const script=await call('post',`${project}/scripts`,{title:'检索结果引用'});await call('post',`${project}/scripts/${script.id}/apply-scenes`,{scenes:[{title:'检索验收',shots:[{title:'咖啡参考',description:'公开样本检索结果的片段引用验收'}]}]});shots=await call('get',`${project}/shots`);}
 await context.addInitScript(token=>{localStorage.setItem('inspiration_token',token);localStorage.setItem('inspiration_lang','zh');},token);
 await page.goto(`${base}${project}/library`);await page.getByRole('button',{name:'查找画面',exact:true}).click();
 await page.getByLabel('搜索画面描述',{exact:true}).fill('一杯咖啡放在桌上');await page.getByRole('button',{name:'查找片段',exact:true}).click();
 await page.getByText('播放片段并引用',{exact:false}).first().waitFor();assert.equal(await page.getByText('播放片段并引用',{exact:false}).count(),3);
 const candidates=page.locator('button').filter({has:page.getByText('播放片段并引用',{exact:false})});
 assert.ok((await candidates.first().innerText()).includes('公开样本 3.mp4'));
 await page.waitForFunction(()=>[...document.querySelectorAll('button img')].filter(i=>i.alt==='公开样本 3.mp4').some(i=>i.complete&&i.naturalWidth>0));
 await page.screenshot({path:`${out}/search-real-dark.png`});
 await candidates.first().click();const dialog=page.getByRole('dialog');await dialog.waitFor();
 await dialog.getByLabel('入点（秒）',{exact:true}).fill('1.2');await dialog.getByLabel('出点（秒）',{exact:true}).fill('2.4');
 await dialog.getByRole('button',{name:'播放所选片段',exact:true}).click();await page.waitForTimeout(1600);assert.ok(await dialog.locator('video').evaluate(v=>v.currentTime>=1.2&&v.currentTime<=2.7&&v.paused));
 await dialog.getByLabel('目标镜头',{exact:true}).selectOption(shots[0].id);await dialog.getByRole('button',{name:'引用到镜头',exact:true}).click();await dialog.getByText('引用已保存。',{exact:false}).waitFor();
 const usages=await call('get',`${library}/usages?shot_id=${shots[0].id}`);assert.ok(usages.some(u=>u.version_id===fixture.videos.coffee.id&&u.start_ms===1200&&u.end_ms===2400));
 checks.push('Real semantic search returns correct video; per-version collapse and exact-range playback/reference work');
 await dialog.getByText('为所选片段添加检索标注',{exact:true}).click();const mark=`咖啡杯 实测 ${Date.now()}`;
 await dialog.getByLabel('片段检索标注',{exact:true}).fill(mark);await dialog.getByRole('button',{name:'保存标注',exact:true}).click();await dialog.getByText('标注已保存，可在明确标注中检索。',{exact:true}).waitFor();
 const speech=`恐龙台词 实测 ${Date.now()}`;
 await dialog.getByLabel('字幕文件',{exact:true}).setInputFiles({name:'test.srt',mimeType:'application/x-subrip',buffer:Buffer.from(`1\n00:00:00,500 --> 00:00:01,500\n${speech}\n`)});
 await dialog.getByRole('button',{name:'导入SRT',exact:true}).click();await dialog.getByText('已导入 1 条，跳过 0 条重复字幕',{exact:true}).waitFor();
 await dialog.getByTitle('关闭',{exact:true}).click();await page.getByRole('button',{name:'明确标注',exact:true}).click();await page.getByLabel('标注来源',{exact:true}).selectOption('speech');await page.getByLabel('搜索画面描述',{exact:true}).fill(speech);await page.getByRole('button',{name:'查找片段',exact:true}).click();await page.getByText(speech,{exact:true}).waitFor();
 await page.getByLabel('标注来源',{exact:true}).selectOption('visual');await page.getByRole('button',{name:'查找片段',exact:true}).click();await page.getByText('当前检索范围没有匹配片段',{exact:true}).waitFor();
 await page.getByLabel('搜索画面描述',{exact:true}).fill(mark);await page.getByRole('button',{name:'查找片段',exact:true}).click();await page.getByText(mark,{exact:true}).waitFor();
 checks.push('Manual visual annotations and imported SRT are persisted and searched separately; dialogue is not visual evidence');
 await page.evaluate(()=>{document.documentElement.classList.remove('dark');document.documentElement.classList.add('light');});await page.screenshot({path:`${out}/search-real-light.png`});await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:`${out}/search-real-mobile.png`});
 assert.deepEqual(errors,[]);const report={passed:true,project:fixture.project,checks,errors};await writeFile(`${out}/search-live-report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:`${out}/search-live-failure.png`,fullPage:true});throw e;}finally{await browser.close();}
