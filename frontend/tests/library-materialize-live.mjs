import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const base=process.env.APP_URL,out=process.env.ARTIFACT_DIR;await mkdir(out,{recursive:true});
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1560,height:1080}}),page=await context.newPage(),errors=[],report={checks:[],outputs:[]};page.on('pageerror',e=>errors.push(e.message));
try{
 const login=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(login.ok());const token=(await login.json()).access_token,headers={Authorization:`Bearer ${token}`};
 const call=async(method,path,data)=>{const r=await context.request[method](`${base}/api/v1${path}`,{headers,data});assert.ok(r.ok(),await r.text());return r.json()};
 const p=await call('post','/projects',{code:'MATERIAL-'+Date.now(),name:'素材创作验收 · 原片到剪辑'}),root=`/projects/${p.id}`;report.project=p.id;
 const script=await call('post',root+'/scripts',{title:'从素材开始创作'});await call('post',root+`/scripts/${script.id}/apply-scenes`,{scenes:[{title:'海边',shots:[{title:'素材片段衔接'}]}]});const shot=(await call('get',root+'/shots'))[0];report.shot=shot.id;
 const buffer=await readFile(process.env.VIDEO_FIXTURE),v=await call('post',root+'/library/uploads',{filename:'海边独白原片.mp4',size_bytes:buffer.length,fingerprint:createHash('sha256').update(buffer).digest('hex')});report.version=v.id;report.media=v.media_id;
 const chunk=await context.request.post(`${base}/api/v1${root}/library/versions/${v.id}/chunk?offset=0`,{headers,multipart:{file:{name:'chunk',mimeType:'application/octet-stream',buffer}}});assert.ok(chunk.ok());await call('post',root+`/library/versions/${v.id}/complete`);let ready;
 for(let i=0;i<120;i++){ready=await call('get',root+`/library/versions/${v.id}`);if(['ready','failed'].includes(ready.status))break;await page.waitForTimeout(500)}assert.equal(ready.status,'ready');
 const usage=await call('post',root+'/library/usages',{version_id:v.id,shot_id:shot.id,start_ms:3640,end_ms:7760,purpose:'editing_source'});
 await context.addInitScript(t=>{localStorage.setItem('inspiration_token',t);localStorage.setItem('inspiration_lang','zh')},token);
 await page.goto(`${base}${root}/shots?shot=${shot.id}`);
 for(const [kind,label] of [['image','提取中间参考帧'],['video','制作剪辑片段'],['audio','提取音轨']]){
  const waiting=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`/usages/${usage.id}/materialize`));await page.getByRole('button',{name:label,exact:true}).click();const response=await waiting;assert.ok(response.ok(),await response.text());let job=await response.json();
  for(let i=0;i<120;i++){job=await call('get',root+`/jobs/${job.id}`);if(['succeeded','failed','canceled'].includes(job.status))break;await page.waitForTimeout(500)}assert.equal(job.status,'succeeded',JSON.stringify(job));
  const gens=await call('get',root+`/generations?target_type=shot&target_id=${shot.id}`),g=gens.find(g=>g.job_id===job.id);assert.equal(g.output_type,kind);assert.equal(g.input_refs.library_origin.version_id,v.id);assert.equal(g.input_refs.library_origin.start_ms,3640);assert.equal(g.is_selected,false);report.outputs.push({id:g.id,type:kind,job:job.id,blob:g.output_blob_hash,actual_media:g.input_refs.actual_media});
 }
 await page.screenshot({path:`${out}/reference-actions.png`,animations:'disabled'});
 await page.getByRole('tab',{name:'生成出图',exact:true}).click();await page.getByRole('link',{name:/查看原片/}).first().waitFor();await page.screenshot({path:`${out}/creative-results.png`,animations:'disabled'});
 await page.getByRole('link',{name:/查看原片/}).first().click();await page.getByRole('dialog').waitFor();assert.equal(await page.getByLabel('视频版本',{exact:true}).inputValue(),v.id);await page.screenshot({path:`${out}/source-return.png`,animations:'disabled'});
 report.checks.push('Browser creates real image/video/audio derivatives; source version and range retained; shot selection unchanged; result link opens exact original version');
 let transcript=await call('post',root+'/transcriptions',{version_id:v.id,language:'zh'});
 for(let i=0;i<180;i++){transcript=await call('get',root+`/transcriptions/${transcript.id}`);if(['ready','failed'].includes(transcript.status))break;await page.waitForTimeout(1000)}assert.equal(transcript.status,'ready');await call('post',root+`/transcriptions/${transcript.id}/publish`,{revision:transcript.revision});
 const video=report.outputs.find(g=>g.type==='video'),audio=report.outputs.find(g=>g.type==='audio');
 const timeline=await call('post',root+'/timelines',{name:'素材引用成片'});report.timeline=timeline.id;await call('put',root+`/timelines/${timeline.id}/items`,{items:[{shot_id:shot.id,generation_id:video.id,duration_ms:4120}]});
 await page.goto(`${base}${root}/cuts?timeline=${timeline.id}`);await page.getByLabel('音频素材',{exact:true}).selectOption(audio.id);await page.getByRole('button',{name:'添加音轨',exact:true}).click();await page.getByLabel('静音此轨',{exact:true}).check();
 await page.getByRole('tab',{name:/字幕/}).click();await page.getByRole('button',{name:'匹配原片字幕',exact:true}).click();await page.getByRole('button',{name:'用匹配结果替换字幕草稿',exact:true}).click();await page.getByLabel('字幕文字 1',{exact:true}).waitFor();assert.ok((await page.getByLabel('字幕文字 1',{exact:true}).inputValue()).includes('蓝色'));await page.screenshot({path:`${out}/matched-caption.png`,animations:'disabled'});
 const saveResponse=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/document'));await page.getByRole('button',{name:'保存时间线',exact:true}).click();assert.ok((await saveResponse).ok());let document=await call('get',root+`/timelines/${timeline.id}/document`);assert.equal(document.subtitles[0].start_ms,0);assert.equal(document.subtitles[0].source.version_id,v.id);assert.equal(document.audio[0].duration_ms,4120);
 await page.getByLabel('字幕导出方式',{exact:true}).selectOption('burn');const rendering=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/render'));await page.getByRole('button',{name:'保存并导出 MP4',exact:true}).click();const renderResponse=await rendering;assert.ok(renderResponse.ok(),await renderResponse.text());let filmJob=await renderResponse.json();
 for(let i=0;i<180;i++){filmJob=await call('get',root+`/jobs/${filmJob.id}`);if(['succeeded','failed'].includes(filmJob.status))break;await page.waitForTimeout(500)}assert.equal(filmJob.status,'succeeded',JSON.stringify(filmJob));const film=(await call('get',root+`/generations?target_type=timeline&target_id=${timeline.id}`)).find(g=>g.job_id===filmJob.id);report.film=film.id;const file=await context.request.get(`${base}/api/v1${root}/blobs/${film.output_blob_hash}`,{headers});assert.ok(file.ok());await writeFile(`${out}/source-to-cut.mp4`,await file.body());
 report.checks.push('Real ASR publication maps original sentence to clipped timeline at zero; extracted audio carries usable duration; browser saves and renders MP4 with burned captions; additional audio is muted to retain original voice once');
 assert.deepEqual(errors,[]);report.errors=errors;report.passed=true;await writeFile(`${out}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(e){await page.screenshot({path:`${out}/failure.png`,fullPage:true});await writeFile(`${out}/failure.json`,JSON.stringify({...report,error:String(e),errors},null,2));throw e}finally{await browser.close()}
