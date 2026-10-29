// Actual Workspace regression: delayed intrinsic media size must respect the reader's scroll intent.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium }=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const out=resolve('../test-results/conversation-media-scroll');await mkdir(out,{recursive:true});
const css=(await readdir(resolve('dist/assets'))).find(name=>name.startsWith('index-')&&name.endsWith('.css'));
await writeFile(resolve(out,'production.css'),await readFile(resolve('dist/assets',css)));
await build({stdin:{contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';import {BrowserRouter} from 'react-router-dom';
import {AuthProvider} from './src/lib/auth';import {I18nProvider} from './src/lib/i18n';
import {ConversationWorkspace} from './src/components/workbench/ConversationWorkspace';
const q=new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById('root')).render(<I18nProvider><QueryClientProvider client={q}><AuthProvider><BrowserRouter><ConversationWorkspace projectId="fixture"/></BrowserRouter></AuthProvider></QueryClientProvider></I18nProvider>);
`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,loader:{'.svg':'dataurl','.png':'dataurl'},outfile:resolve(out,'fixture.js'),format:'esm',jsx:'automatic',alias:{'@':resolve('src')},define:{'process.env.NODE_ENV':'"development"'}});
await writeFile(resolve(out,'index.html'),'<!doctype html><html class="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><style>body{margin:0}.conversation-workspace{height:100vh!important;min-height:0!important}#root{height:100vh}</style><div id="root"></div><script type="module" src="/fixture.js"></script></html>');
const server=createServer(async(req,res)=>{const name=({'/fixture.js':'fixture.js','/fixture.css':'fixture.css','/production.css':'production.css'})[new URL(req.url,'http://fixture.local').pathname]||'index.html';res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(await readFile(resolve(out,name)));});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:'chrome'});
const timestamp='2026-10-30T03:00:00Z';
const history=Array.from({length:10},(_,index)=>({id:`history-${index}`,ordinal:index+1,role:index%2?'assistant':'user',content:index%2?'人物走入雨幕，先看见地上的倒影，再听到巷口的脚步。这个镜头保留安静的节奏，等人物停下时再切近景。':'这个场景先保留人物的背影，再讨论雨声和远处的灯光。',status:'completed',created_at:timestamp,context:{},progress:[]}));
const conversation={id:'chat',title:'已有对话的延迟图片',work_id:null,archived:false,updated_at:timestamp,has_older:false,messages:[...history,{id:'media-answer',ordinal:11,role:'assistant',content:'这是刚完成的竖幅画面。',status:'completed',execution_kind:'generation',task_id:'job',created_at:timestamp,context:{media:{request_type:'image'},outputs:[{id:'portrait',type:'image',blob_hash:'portrait'}]},progress:[]}]};
const planRun={id:'plan-run',goal:'安排雨夜镜头制作',engine:'mock',status:'needs_plan_approval',revision:1,event_sequence:1,policy:{budget_points:0},tasks:[],plan:{summary:'请确认人物、镜头和声音的安排后开始制作。',tasks:Array.from({length:6},(_,index)=>({key:`step-${index}`,goal:`第 ${index+1} 步：依据角色参考安排雨夜镜头，先确定人物的位置与动作，再整理镜头中的灯光、雨声和远处的脚步。`}))}};
const planConversation={...conversation,messages:[...history,{id:'plan-answer',ordinal:11,role:'assistant',content:'制作方案已整理好，可以展开查看。',status:'needs_review',execution_kind:'production',task_id:'plan-run',created_at:timestamp,context:{production:{run_id:'plan-run',status:'needs_plan_approval',revision:1,is_current:true}},progress:[]}]};
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="300" height="800"><rect width="300" height="800" fill="#293b44"/><rect x="100" y="180" width="100" height="380" rx="50" fill="#192229"/></svg>';
const reports=[],errors=[],unexpected=[];
try{
 for(const scenario of ['following-latest','manually-reading','restored-reader','expand-details']){
  const manuallyReading=scenario==='manually-reading',restoredReader=scenario==='restored-reader',expandDetails=scenario==='expand-details';
  const currentConversation=expandDetails?planConversation:conversation;
  const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  let releaseImage;const delayedImage=new Promise(resolve=>releaseImage=resolve);
  await page.route('**/api/v1/**',async route=>{
   const path=new URL(route.request().url()).pathname.replace('/api/v1/projects/fixture','');
   const ok=json=>route.fulfill({json});
   if(path==='/providers')return ok([{provider_name:'text',kind:'llm',enabled:true,config:{is_default:true,label:'文字模型',model:'text'}}]);
   if(path==='/works')return ok([]);
   if(path==='/conversations')return ok([currentConversation]);
   if(path==='/conversations/chat')return ok(currentConversation);
   if(path==='/production/runs/plan-run')return ok(planRun);
   if(path==='/blobs/portrait'){await delayedImage;return route.fulfill({body:svg,contentType:'image/svg+xml'});}
   unexpected.push(path);return route.fulfill({status:500,json:{error:{message:'Unexpected fixture endpoint'}}});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/projects/fixture/workbench?conversation=chat${restoredReader?'&message=history-4&ordinal=5':''}`,{waitUntil:'domcontentloaded'});
  await page.locator(expandDetails?'.production-brief-toggle':'#conversation-message-media-answer .conversation-media-result').waitFor();
  const list=page.getByRole('log',{name:'对话内容',exact:true});
  // Establish the user's explicit latest position before metadata arrives.
  if(!restoredReader){
   await list.evaluate(element=>element.scrollTop=element.scrollHeight);
   await page.waitForFunction(()=>{const list=document.querySelector('.conversation-messages');return list&&list.scrollHeight-list.scrollTop-list.clientHeight<25;});
  }else{
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  }
  if(manuallyReading){await list.evaluate(element=>element.scrollTop=200);await page.getByRole('button',{name:'回到最新消息',exact:true}).waitFor();}
  const measure=()=>list.evaluate(element=>({scrollTop:element.scrollTop,scrollHeight:element.scrollHeight,clientHeight:element.clientHeight,gap:element.scrollHeight-element.scrollTop-element.clientHeight,mediaHeight:element.querySelector('.conversation-media-result')?.getBoundingClientRect().height,detailsTop:element.querySelector('.production-brief-toggle')?.getBoundingClientRect().top,returnedMessageTop:element.querySelector('#conversation-message-history-4')?.getBoundingClientRect().top,returnedMessageBottom:element.querySelector('#conversation-message-history-4')?.getBoundingClientRect().bottom,viewportTop:element.getBoundingClientRect().top,viewportBottom:element.getBoundingClientRect().bottom,jumpVisible:!!document.querySelector('.conversation-jump')}));
  const before=await measure();
  await page.screenshot({path:resolve(out,`${scenario}-before.png`),fullPage:true});
  if(expandDetails){
   await page.locator('.production-brief-toggle').click();
   await page.getByRole('heading',{name:'这次的制作方案',exact:true}).waitFor();
  }else{
   releaseImage();
   await page.waitForFunction(()=>{const image=document.querySelector('.conversation-media-result img');return image?.naturalWidth===300&&image?.naturalHeight===800&&getComputedStyle(document.querySelector('.conversation-media-result')).getPropertyValue('--result-ratio').trim()==='0.375';});
  }
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const after=await measure();
  await page.screenshot({path:resolve(out,`${scenario}-after.png`),fullPage:true});
  reports.push({scenario,before,after});
  await context.close();
 }
 await writeFile(resolve(out,'report.json'),JSON.stringify({reports,errors,unexpected},null,2));
 console.log(JSON.stringify({reports,errors,unexpected},null,2));
 assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
 const [pinned,reading,restored,expanded]=reports;
 assert.ok(Math.abs(reading.after.scrollTop-reading.before.scrollTop)<=2,'loading media must not pull a reader away from earlier messages');
 assert.ok(pinned.after.gap<25,`latest follower lost bottom after media layout: ${pinned.after.gap}px gap`);
 assert.ok(Math.abs(restored.after.scrollTop-restored.before.scrollTop)<=2,'restored readers must remain at their returned message after media layout');
 assert.ok(restored.before.returnedMessageBottom>restored.before.viewportTop+40&&restored.before.returnedMessageTop<restored.before.viewportBottom,'the returned message should be visible before media layout');
 assert.ok(Math.abs(expanded.after.detailsTop-expanded.before.detailsTop)<=2,'expanding task details must keep its heading anchored');
 assert.ok(expanded.after.detailsTop>=expanded.after.viewportTop&&expanded.after.detailsTop+32<=expanded.after.viewportBottom,'expanded task heading must stay visible');
 console.log('PASS delayed media follows latest, preserves manual/restored reading positions, and anchors explicitly expanded task details.');
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
