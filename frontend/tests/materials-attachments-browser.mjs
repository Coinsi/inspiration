// Saved attachment management, rendered with the real Materials/MaterialProfile components.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const out = resolve('../test-results/materials-attachments');
await mkdir(out, { recursive: true });
const css = (await readdir(resolve('dist/assets'))).find(name => name.startsWith('index-') && name.endsWith('.css'));
await writeFile(resolve(out, 'production.css'), await readFile(resolve('dist/assets', css)));
await build({ stdin: { contents: `
import React from 'react'; import {createRoot} from 'react-dom/client'; import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {BrowserRouter,Routes,Route,useLocation} from 'react-router-dom'; import {AuthProvider} from './src/lib/auth'; import {I18nProvider} from './src/lib/i18n';
import {ConfirmProvider} from './src/components/ui/confirm'; import {ToastProvider} from './src/components/ui/toast'; import Materials from './src/pages/Materials';
const q=new QueryClient({defaultOptions:{queries:{retry:false}}}); function Location(){const l=useLocation();return <output data-testid="location">{l.pathname+l.search}</output>}
createRoot(document.getElementById('root')).render(<I18nProvider><QueryClientProvider client={q}><AuthProvider><ConfirmProvider><ToastProvider><BrowserRouter><Routes><Route path="/projects/:projectId/materials" element={<Materials/>}/><Route path="*" element={<Location/>}/></Routes></BrowserRouter></ToastProvider></ConfirmProvider></AuthProvider></QueryClientProvider></I18nProvider>);
`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, loader: { '.svg': 'dataurl', '.png': 'dataurl' }, outfile: resolve(out, 'fixture.js'), format: 'esm', jsx: 'automatic', alias: { '@': resolve('src') }, define: { 'process.env.NODE_ENV': '"development"' } });
await writeFile(resolve(out, 'index.html'), '<!doctype html><html class="light"><meta charset="utf-8"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><style>body{margin:0;padding:24px;font-family:system-ui}.studio-page{max-width:1500px;margin:auto}</style><div id="root"></div><script type="module" src="/fixture.js"></script></html>');
const server=createServer(async(req,res)=>{const name=({'/fixture.js':'fixture.js','/fixture.css':'fixture.css','/production.css':'production.css'})[req.url]||'index.html';res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(await readFile(resolve(out,name)));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const image=await readFile(resolve('public/images/studio-scenes.webp'));
const browser=await chromium.launch({headless:true,channel:'chrome'});
const asset={id:'attachment-a',title:'镜头风格参考',filename:'original-scene.webp',kind:'image',status:'ready',blob_hash:'image',thumbnail_hash:null,description:'在对话中上传的构图参考',tags:['构图'],metadata_revision:1,conversation_id:'origin-conversation',message_id:'origin-message',message_ordinal:5,saved_at:new Date().toISOString(),size_bytes:image.length};
let patch=null;const errors=[];
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>localStorage.setItem('inspiration_token','fixture-token'));
 await page.route('**/api/v1/**',async route=>{const u=new URL(route.request().url()),p=u.pathname,method=route.request().method();const ok=json=>route.fulfill({json});
  if(p==='/api/v1/me')return ok({user:{id:'u',username:'fixture'},memberships:[{project_id:'fixture',role:'artist'}]});
  if(p.endsWith('/library/materials'))return ok({total:1,items:[{id:asset.id,source:'attachment',name:asset.title,description:asset.description,tags:asset.tags,metadata_revision:asset.metadata_revision,kind:'image',created_at:asset.saved_at,deleted_at:null,status:'ready',poster_hash:null,blob_hash:asset.blob_hash,preview_hash:null,size_bytes:asset.size_bytes,duration_ms:null,version_id:null,is_test:false,adopted:false}]});
  if(p.endsWith('/attachments/attachment-a')){if(method==='PATCH'){patch=route.request().postDataJSON();assert.equal(patch.expected_revision,asset.metadata_revision);Object.assign(asset,{title:patch.title,description:patch.description,tags:patch.tags,metadata_revision:asset.metadata_revision+1});}return ok(asset);}
  if(p.includes('/blobs/'))return route.fulfill({body:image,contentType:'image/webp'});
  throw new Error(`Unexpected ${method} ${p}`);
 });
 const returnTo='/projects/fixture/workbench?view=chat&conversation=return-conversation&message=old&ordinal=99&attachment=stale';
 const home=`http://127.0.0.1:${server.address().port}/projects/fixture/materials?source=attachment&return=${encodeURIComponent(returnTo)}`;
 await page.goto(home);await page.getByRole('button',{name:'预览 镜头风格参考',exact:true}).waitFor();
 await page.screenshot({path:resolve(out,'catalog-light.png'),fullPage:true});
 await page.getByRole('button',{name:'预览 镜头风格参考',exact:true}).click();
 const detail=page.getByRole('dialog',{name:'对话附件',exact:true});await detail.waitFor();
 await page.screenshot({path:resolve(out,'detail-light.png'),fullPage:true});
 await detail.getByRole('button',{name:'编辑素材资料：镜头风格参考',exact:true}).click();
 const editor=page.getByRole('dialog',{name:'编辑素材资料',exact:true});
 assert.equal(await editor.getByLabel(/素材名称/).inputValue(),'镜头风格参考');
 await editor.getByLabel(/素材名称/).fill('日落场景参考');await editor.getByLabel(/说明与备注/).fill('用来统一后续镜头的光线');await editor.getByPlaceholder('例如：雨夜、城市、空镜；用逗号分隔').fill('日落');
 await page.screenshot({path:resolve(out,'edit-light.png'),fullPage:true});
 await editor.getByRole('button',{name:'保存修改',exact:true}).click();await editor.waitFor({state:'hidden'});
 await detail.getByRole('heading',{name:'日落场景参考',exact:true}).waitFor();
 assert.equal(asset.filename,'original-scene.webp');assert.ok(!('filename' in patch));assert.deepEqual(asset.tags,['构图','日落']);
 const creative=await detail.getByRole('link',{name:'用于创作',exact:true}).getAttribute('href');const parsed=new URL(creative,'http://fixture');assert.equal(parsed.searchParams.get('attachment'),'attachment-a');assert.equal(parsed.searchParams.get('conversation'),'return-conversation');assert.equal(parsed.searchParams.get('message'),null);
 await detail.getByRole('link',{name:'用于创作',exact:true}).click();await page.getByTestId('location').waitFor();assert.match(await page.getByTestId('location').textContent(),/attachment=attachment-a/);
 await page.goBack();const origin=await detail.getByRole('link',{name:'返回来源对话',exact:true}).getAttribute('href');assert.match(origin,/conversation=origin-conversation/);assert.match(origin,/message=origin-message&ordinal=5/);
 await detail.getByRole('link',{name:'返回来源对话',exact:true}).click();await page.getByTestId('location').waitFor();assert.match(await page.getByTestId('location').textContent(),/ordinal=5/);
 await page.goBack();await page.evaluate(()=>{document.documentElement.className='dark'});await page.screenshot({path:resolve(out,'detail-dark.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:resolve(out,'detail-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);console.log('Materials attachment source, card/detail, metadata rename without filename change, tags, creative handoff, original message link and light/dark/mobile layout passed.');
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
