import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
assert.equal(process.env.TEST_ISOLATED_PREVIEW,'1');
const base=process.env.APP_URL,out=process.env.ARTIFACT_DIR;await mkdir(out,{recursive:true});
const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({headless:true,channel:'chrome'}),context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
try{
 const login=await context.request.post(`${base}/api/v1/auth/login`,{data:{username:'demo',password:'demo1234'}});assert.ok(login.ok());const token=(await login.json()).access_token,headers={Authorization:`Bearer ${token}`};
 const call=async(method,path,data)=>{const r=await context.request[method](`${base}/api/v1${path}`,{headers,data});assert.ok(r.ok(),await r.text());return r.json()};
 const p=await call('post','/projects',{code:'SCRIPT-'+Date.now(),name:'剧本保护验收 · 草稿与并发'}),root=`/projects/${p.id}`,s=await call('post',root+'/scripts',{title:'海边小屋'}),path=root+`/scripts/${s.id}`;
 let script=await call('get',path);script=await call('put',path+'/blocks',{expected_revision:script.content_revision,blocks:[{block_type:'scene_heading',text:'内景 小屋 清晨'},{block_type:'action',text:'林舟推开窗户。'}]});
 await context.addInitScript(t=>{localStorage.setItem('inspiration_token',t);localStorage.setItem('inspiration_lang','zh')},token);await page.goto(`${base}${path}`);const action=page.getByPlaceholder('动作',{exact:true});await action.fill('我的未保存改稿');await page.waitForTimeout(400);await page.reload();await page.getByRole('button',{name:'恢复剧本草稿',exact:true}).click();assert.equal(await action.inputValue(),'我的未保存改稿');
 // A concurrent writer updates the server; the browser's stale save must not overwrite either draft.
 await call('put',path+'/blocks',{expected_revision:script.content_revision,blocks:script.content_blocks.map(b=>b.block_type==='action'?{...b,text:'另一位作者的新稿'}:b)});
 const rejected=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/blocks'));await page.getByRole('button',{name:'保存',exact:true}).click();assert.equal((await rejected).status(),409);await page.getByRole('button',{name:'载入新版本',exact:true}).waitFor();assert.equal(await action.inputValue(),'我的未保存改稿');assert.equal((await call('get',path)).content_blocks[1].text,'另一位作者的新稿');await page.screenshot({path:`${out}/script-conflict.png`,animations:'disabled'});
 await page.getByRole('button',{name:'载入新版本',exact:true}).click();await page.getByRole('alertdialog').getByRole('button',{name:'确定',exact:true}).click();assert.equal(await action.inputValue(),'另一位作者的新稿');
 await action.fill('核对后的合并稿');const saved=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/blocks'));await page.keyboard.press('Control+s');assert.ok((await saved).ok());await page.waitForTimeout(300);assert.equal((await call('get',path)).content_blocks[1].text,'核对后的合并稿');await page.reload();assert.equal(await page.getByRole('button',{name:'恢复剧本草稿',exact:true}).count(),0);assert.equal(await action.inputValue(),'核对后的合并稿');
 await page.screenshot({path:`${out}/script-saved.png`,animations:'disabled'});assert.deepEqual(errors,[]);await writeFile(`${out}/report.json`,JSON.stringify({passed:true,project:p.id,script:s.id,checks:['Unsaved draft survives reload','Stale save returns conflict and preserves both local and server text','Explicit reload followed by current-revision save succeeds'],errors},null,2));console.log('Script draft persistence and concurrent editing passed');
}catch(e){await page.screenshot({path:`${out}/failure.png`,fullPage:true});throw e}finally{await browser.close()}
