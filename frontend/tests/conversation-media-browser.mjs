// Actual components with deterministic local media. No provider calls or project writes.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const out = resolve('../test-results/conversation-media');
await mkdir(out, { recursive: true });
const css = (await readdir(resolve('dist/assets'))).find(name => name.startsWith('index-') && name.endsWith('.css'));
await writeFile(resolve(out, 'production.css'), await readFile(resolve('dist/assets', css)));
await build({ stdin: { contents: `
import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom'; import {I18nProvider} from './src/lib/i18n';
import {ConversationGenerationProgress} from './src/components/workbench/ConversationGenerationProgress';
import {ConversationMediaResults} from './src/components/workbench/ConversationMedia';
import {productionBriefStatus} from './src/components/workbench/ProductionTask';
import './src/components/workbench/conversations.css';
window.calls=[]; window.summary=productionBriefStatus;
function Fixture(){
 const [phase,setPhase]=useState('queued'),[kind,setKind]=useState('video');
 window.changePhase=setPhase; window.changeKind=setKind;
 const outputs=[1,2].map(i=>({id:kind+i,type:kind,blob_hash:kind+i}));
 const title=phase==='queued'?'等待生成雨夜镜头':phase==='materializing'?'正在接收雨夜镜头':'正在生成雨夜镜头';
 return <main><p className="fixture-assistant">创作助理</p>
 <ConversationGenerationProgress progress={{kind,phase,title,message:phase==='queued'?'生成请求已提交，正在等待开始。':phase==='materializing'?'生成服务已返回，正在保存并检查媒体文件。':'正在按参考图生成，完成后会显示在这里。',started_at:null,updated_at:null,completed_outputs:0}}/>
 {phase==='completed'&&<ConversationMediaResults projectId="fixture" outputs={outputs} disabled={false}
 reuse={(output,mode)=>window.calls.push({action:'reuse',id:output.id,mode})}
 onRecreate={output=>window.calls.push({action:'recreate',id:output.id})}
 onReference={(output,selection)=>{window.calls.push({action:'reference',id:output.id,...selection});document.getElementById('target-composer')?.focus();}}/>}</main>;
}
createRoot(document.getElementById('root')).render(<I18nProvider><BrowserRouter><Fixture/></BrowserRouter></I18nProvider>);
`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, outfile: resolve(out, 'fixture.js'), format: 'esm', jsx: 'automatic', alias: { '@': resolve('src') }, define: { 'process.env.NODE_ENV': '"development"' } });
await writeFile(resolve(out, 'index.html'), '<!doctype html><html class="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><style>body{margin:0;background:hsl(var(--bg));color:hsl(var(--fg))}main{max-width:880px;margin:48px auto;padding:0 20px}.fixture-assistant{font-size:13px;color:hsl(var(--muted));margin-bottom:24px}</style><div id="root"></div><script type="module" src="/fixture.js"></script></html>');
const film = await readFile(resolve('public/media/home-reference-updream.mp4'));
const svg = index => `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="${index === '1' ? '#263640' : '#3e393b'}"/><path d="M140 450 L290 170 L510 170 L660 450" fill="#6f7f85"/><rect x="350" y="90" width="100" height="250" rx="50" fill="#202529"/></svg>`;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://fixture.local');
  if (url.pathname.includes('/blobs/image')) { res.setHeader('Content-Type', 'image/svg+xml'); res.end(svg(url.pathname.at(-1))); return; }
  if (url.pathname.includes('/blobs/video')) { res.setHeader('Content-Type', 'video/mp4'); res.end(film); return; }
  if (url.pathname.startsWith('/api/')) { res.statusCode = 500; res.end('Unexpected API request'); return; }
  const name = ({'/fixture.js':'fixture.js','/fixture.css':'fixture.css','/production.css':'production.css'})[url.pathname] || 'index.html';
  res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(await readFile(resolve(out, name)));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByText('等待生成雨夜镜头', {exact:true}).waitFor();
  const labels = await page.evaluate(() => ['queued','waiting_job','validating_job','running'].map(status => window.summary({status:status==='queued'?'queued':'running',tasks:[{status,role:'generator'}]}).label));
  assert.deepEqual(labels, ['已提交，等待开始','正在等待生成结果','正在检查生成结果','正在创作']);
  assert.equal(await page.evaluate(() => window.summary({status:'running',tasks:[{role:'impact',status:'running'}]}).label), '正在处理你的补充');
  await page.screenshot({ path: resolve(out, 'waiting-light.png'), fullPage: true });
  await page.evaluate(() => window.changePhase('generating'));
  await page.getByText('正在生成雨夜镜头', {exact:true}).waitFor();
  await page.evaluate(() => document.documentElement.className='dark');
  await page.screenshot({ path: resolve(out, 'waiting-dark.png'), fullPage: true });
  await page.setViewportSize({ width:390,height:844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(out, 'waiting-mobile.png'), fullPage: true });
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.conversation-generation-spin').evaluate(el => getComputedStyle(el).animationName), 'none');
  await page.evaluate(() => window.changePhase('materializing'));
  await page.getByText('正在接收雨夜镜头', {exact:true}).waitFor();
  await page.evaluate(() => {window.changeKind('image');window.changePhase('completed');});
  await page.getByRole('button',{name:'讨论这张',exact:true}).first().waitFor();
  assert.equal(await page.locator('.conversation-generation').count(),0);
  await page.getByRole('button',{name:'讨论这张',exact:true}).first().click();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1)),{action:'reference',id:'image1',index:0});
  await page.locator('.conversation-result-more > summary').first().click();
  await page.getByRole('button',{name:'调整原要求',exact:true}).first().click();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1)),{action:'recreate',id:'image1'});
  await page.getByRole('button',{name:'生成视频',exact:true}).first().click();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1)),{action:'reuse',id:'image1',mode:'video'});
  await page.locator('.conversation-result-more > summary').first().click();
  await page.screenshot({ path: resolve(out, 'results-mobile.png'), fullPage: true });
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(() => document.documentElement.className='light');
  await page.getByRole('button',{name:'对比这两个结果',exact:true}).click();
  await page.getByRole('dialog',{name:'对比本轮结果',exact:true}).waitFor();
  await page.screenshot({ path: resolve(out, 'compare-light.png'), fullPage: true });
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(),0);
  await page.evaluate(() => { const input=document.createElement('input');input.id='target-composer';input.style.position='fixed';input.style.left='-10000px';document.body.append(input); });
  await page.getByRole('button',{name:'对比这两个结果',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'讨论这张',exact:true}).first().click();
  assert.equal(await page.getByRole('dialog').count(),0);
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'target-composer');
  await page.evaluate(() => window.changeKind('video'));
  await page.waitForFunction(() => [...document.querySelectorAll('video')].length === 2 && [...document.querySelectorAll('video')].every(v=>v.readyState >= 1));
  await page.locator('.conversation-result-more > summary').first().click();
  await page.getByRole('button',{name:'选择片段讨论',exact:true}).first().click();
  await page.getByRole('spinbutton',{name:'视频 1 / 2讨论开始秒数',exact:true}).fill('0.1');
  await page.getByRole('spinbutton',{name:'视频 1 / 2讨论结束秒数',exact:true}).fill('0.5');
  await page.getByRole('button',{name:'带入对话',exact:true}).click();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1)),{action:'reference',id:'video1',index:0,startSeconds:0.1,endSeconds:0.5});
  await page.getByRole('button',{name:'选择片段讨论',exact:true}).first().click();
  await page.getByRole('spinbutton',{name:'视频 1 / 2讨论开始秒数',exact:true}).fill('2');
  await page.getByRole('spinbutton',{name:'视频 1 / 2讨论结束秒数',exact:true}).fill('1');
  assert.equal(await page.getByRole('button',{name:'带入对话',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await page.getByRole('button',{name:'对比这两个结果',exact:true}).click();
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.getByRole('dialog').locator('video').count(),2);
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.changePhase('failed'));
  assert.equal(await page.locator('.conversation-generation').count(),0);
  assert.deepEqual(writes,[]); assert.deepEqual(errors,[]);
  console.log('PASS truthful stages, terminal replacement, light/dark/mobile, reduced motion, result discussion/recreate callbacks, image/video comparison, validated video discussion range, no writes.');
} finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
