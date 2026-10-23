// Standalone rendering of production components; every API response is intercepted.
import assert from "node:assert/strict";
import { build } from "esbuild";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const out = resolve(process.env.ARTIFACT_DIR || "../test-results/reference-media");
await mkdir(out, { recursive: true });
// Include the real compiled Tailwind utilities used by MediaImage/SearchField.
const baseCss=(await readdir(resolve('dist/assets'))).find(name=>name.startsWith('index-')&&name.endsWith('.css'));
assert.ok(baseCss,'Run npm run build before the browser check');
await writeFile(resolve(out,'production.css'),await readFile(resolve('dist/assets',baseCss)));
await build({ stdin: { contents: `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ComposerReferences, ReferencePreview } from './src/components/workbench/ComposerReferences';
import { referenceProblem } from './src/components/workbench/referenceProblem';
import { referenceInput, referenceDetailPath } from './src/components/workbench/referenceInput';
import { I18nProvider } from './src/lib/i18n';
import './src/components/workbench/composer.css';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
function App(){
 const [refs,setRefs]=useState([]); const [video,setVideo]=useState(true);
 const caps={features:video?['reference','video_reference']:['reference'],max_reference_images:1,max_reference_videos:1};
 return <main><h1>参考素材</h1><button onClick={()=>setVideo(v=>!v)}>切换模型能力</button>
 <p role="status">{referenceProblem(caps,refs)||'参考素材可提交'}</p>
 <div className="composer-references">{refs.map(r=><div className="composer-reference" key={r.id}><ReferencePreview projectId="fixture" item={r}/></div>)}</div>
 <ComposerReferences projectId="fixture" includeVideos={video} selected={refs} pick={item=>setRefs(v=>v.some(r=>r.id===item.id)?v.filter(r=>r.id!==item.id):[...v,item])}/>
 <output aria-label="选择结果">{JSON.stringify(refs.map(r=>({id:r.id,kind:r.kind})))}</output>
 <output aria-label="引用请求">{JSON.stringify(refs.map(r=>referenceInput(r)))}</output>
 <output aria-label="恢复引用">{JSON.stringify(refs.map(r=>referenceDetailPath(referenceInput(r))))}</output></main>
}
createRoot(document.getElementById('root')).render(<I18nProvider><QueryClientProvider client={client}><App/></QueryClientProvider></I18nProvider>);
`, resolveDir: process.cwd(), loader: "tsx" }, bundle:true, outfile:resolve(out,"fixture.js"), format:"esm", jsx:"automatic", alias:{"@":resolve("src")}, define:{"process.env.NODE_ENV":'"development"'} });
await writeFile(resolve(out,"index.html"), `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/fixture.css"><style>:root{--surface:0 0% 100%;--muted:220 10% 40%;--primary:215 80% 48%;--elevated:220 20% 96%}*{box-sizing:border-box}body{font:15px system-ui;margin:0;background:#f4f6fa;color:#142138}main{max-width:780px;margin:24px auto;padding:20px;background:white}button,input{font:inherit}button{cursor:pointer}output{display:block;overflow-wrap:anywhere;margin-top:16px}.search-field{display:flex}.search-field input{min-width:0;width:100%}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
const html=await readFile(resolve(out,'index.html'),'utf8');
await writeFile(resolve(out,'index.html'),html.replace('<link rel="stylesheet" href="/fixture.css">','<link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css">'));
const server=createServer(async(req,res)=>{const name=({'/fixture.js':'fixture.js','/fixture.css':'fixture.css','/production.css':'production.css'})[req.url]||'index.html'; res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(await readFile(resolve(out,name)));});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,channel:'chrome'});
try {
 const page=await browser.newPage({viewport:{width:1000,height:760}});
 const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error(e.message);});
 await page.route('**/api/v1/**',async route=>{
  assert.equal(route.request().method(),'GET');
  const url=new URL(route.request().url());
  if(url.pathname.endsWith('/workbench/references')){
   const items=[{id:'image',source_type:'media_version',kind:'image',blob_hash:'image',title:'人物外观'},{id:'video',kind:'video',blob_hash:'video',thumbnail_hash:'poster',title:'动作与运镜'}].filter(x=>url.searchParams.get('media_type')==='all'||x.kind==='image');
   return route.fulfill({json:{items,has_more:false}});
  }
  return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="180"><rect width="300" height="180" fill="#7b98ba"/><circle cx="150" cy="72" r="32" fill="#e8eef7"/></svg>'});
 });
 await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.getByRole('button',{name:'引用 人物外观',exact:true}).click();
 await page.getByRole('button',{name:'引用 动作与运镜',exact:true}).click();
 const selection=JSON.parse(await page.getByLabel('选择结果').textContent());
 assert.deepEqual(selection,[{id:'image',kind:'image'},{id:'video',kind:'video'}]);
 assert.deepEqual(JSON.parse(await page.getByLabel('引用请求').textContent()),[
  {media_version_id:'image',role:'reference'},{generation_id:'video',role:'reference'}]);
 assert.deepEqual(JSON.parse(await page.getByLabel('恢复引用').textContent()),[
  '/workbench/references/image?source_type=media_version','/workbench/references/video']);
 await page.getByRole('status').filter({hasText:'参考素材可提交'}).waitFor();
 await page.screenshot({path:resolve(out,'mixed-desktop.png'),fullPage:true});
 await page.getByRole('button',{name:'切换模型能力'}).click();
 await page.getByRole('status').filter({hasText:'当前模型未启用参考视频'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'引用 动作与运镜',exact:true}).count(),0);
 await page.getByRole('button',{name:'切换模型能力'}).click();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'引用 动作与运镜',exact:true}).waitFor();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:resolve(out,'mixed-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('Mixed reference selection, model capability rejection, preserved selection and 390px layout passed.');
} finally {await browser.close();await new Promise(r=>server.close(r));}
