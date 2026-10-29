// Real picker and preview components with deterministic media/capability fixtures; no provider calls.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const out = resolve('../test-results/reference-selection');
await mkdir(out, { recursive: true });
const css = (await readdir(resolve('dist/assets'))).find(name => name.startsWith('index-') && name.endsWith('.css'));
assert.ok(css, 'Run the frontend build before this browser check');
await writeFile(resolve(out, 'production.css'), await readFile(resolve('dist/assets', css)));
await build({ stdin: { contents: `
import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {ConversationAttachmentPicker,MessageAttachments} from './src/components/workbench/ConversationAttachments';
import {I18nProvider} from './src/lib/i18n'; import './src/components/workbench/composer.css';
const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
const models={mixed:{features:['reference','video_reference'],max_reference_images:2,max_reference_videos:1},image:{features:['img2img'],max_reference_images:1},frames:{features:['first_frame','last_frame'],max_reference_images:2}};
const saved={id:'saved-image',title:'已保存人物图',filename:'saved.webp',kind:'image',role:'reference',status:'ready',blob_hash:'image',size_bytes:12,uploaded_bytes:12,chunk_size:12,saved_at:'2026-10-30'};
function App(){const [open,setOpen]=useState(false),[model,setModel]=useState('mixed'),[refs,setRefs]=useState([]),[items,setItems]=useState([]),[reused,setReused]=useState(null);
 const uploads={items,remove:item=>setItems(list=>list.filter(value=>value.client_id!==item.client_id)),addSaved:item=>setItems(list=>[...list,{client_id:item.id,attachment:item,kind:item.kind,role:item.role,state:'ready'}])};
 return <main><h1>参考内容选择检查</h1><label>模型<select aria-label="模型" value={model} onChange={e=>setModel(e.target.value)}>{['mixed','image','frames','loading','error','text'].map(value=><option key={value}>{value}</option>)}</select></label>
 <button onClick={()=>setOpen(true)}>打开选择器</button><button onClick={()=>{setRefs([]);setItems([])}}>清空选择</button><button onClick={()=>setItems(list=>[...list,{client_id:'local',kind:'image',role:'reference',state:'uploading'}])}>加入本地图片</button>
 <MessageAttachments projectId="fixture" attachments={[saved]} references={[{id:'history-video',source_type:'generation',title:'历史视频',kind:'video',blob_hash:'video',thumbnail_hash:'image',role:'reference'}]} onReuse={setReused}/>
 <output aria-label="已选内容">{JSON.stringify([...refs,...items])}</output><output aria-label="再次引用结果">{JSON.stringify(reused)}</output>
 <ConversationAttachmentPicker projectId="fixture" open={open} onClose={()=>setOpen(false)} mode={model==='text'?'text':'video'} capabilities={models[model]} capabilitiesLoading={model==='loading'} capabilitiesError={model==='error'?'offline':null} onRetryCapabilities={()=>setModel('mixed')} uploads={uploads} references={refs} pickReference={item=>setRefs(list=>list.some(value=>value.id===item.id&&value.source_type===item.source_type)?list.filter(value=>!(value.id===item.id&&value.source_type===item.source_type)):[...list,item])}/></main>;
}
createRoot(document.getElementById('root')).render(<I18nProvider><QueryClientProvider client={client}><App/></QueryClientProvider></I18nProvider>);
`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, outfile: resolve(out, 'fixture.js'), format: 'esm', jsx: 'automatic', alias: { '@': resolve('src') }, define: { 'process.env.NODE_ENV': '"development"' } });
await writeFile(resolve(out, 'index.html'), '<!doctype html><html class="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><style>body{margin:0;background:hsl(var(--background));color:hsl(var(--foreground))}main{max-width:900px;margin:auto;padding:24px}main>button,select{border:1px solid #9995;margin:12px;padding:8px}output{display:block;overflow-wrap:anywhere}</style><div id="root"></div><script type="module" src="/fixture.js"></script></html>');
const server = createServer(async (req, res) => { const name = ({ '/fixture.js': 'fixture.js', '/fixture.css': 'fixture.css', '/production.css': 'production.css' })[req.url] || 'index.html'; res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html'); res.end(await readFile(resolve(out, name))); });
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/workbench/references')) return route.fulfill({ json: { items: [
      { id: 'image-one', source_type: 'media_version', title: '人物外观', kind: 'image', blob_hash: 'image' },
      { id: 'image-two', source_type: 'generation', title: '场景构图', kind: 'image', blob_hash: 'image' },
      { id: 'video', source_type: 'generation', title: '动作与运镜', kind: 'video', blob_hash: 'video', thumbnail_hash: 'image' },
      { id: 'audio', source_type: 'media_version', title: '对白音频', kind: 'audio', blob_hash: 'audio' },
    ], has_more: false } });
    if (path.endsWith('/attachments')) return route.fulfill({ json: { items: [{ id: 'saved-image', title: '已保存人物图', filename: 'saved.webp', kind: 'image', role: 'reference', status: 'ready', blob_hash: 'image', size_bytes: 12, uploaded_bytes: 12, chunk_size: 12, saved_at: '2026-10-30' }], has_more: false } });
    return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="180"><rect width="300" height="180" fill="#8caaa0"/><circle cx="150" cy="65" r="30" fill="#e4ece8"/><path d="M90 180v-40a60 60 0 01120 0v40" fill="#d2dfd9"/></svg>' });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByRole('button', { name: '再次引用 已保存人物图', exact: true }).click();
  assert.equal(JSON.parse(await page.getByLabel('再次引用结果').textContent()).filename, 'saved.webp');
  await page.getByRole('button', { name: '再次引用 历史视频', exact: true }).click();
  assert.equal(JSON.parse(await page.getByLabel('再次引用结果').textContent()).source_type, 'generation');
  await page.getByRole('button', { name: '预览 已保存人物图', exact: true }).click();
  assert.equal(await page.getByRole('dialog').getByRole('img', { name: '已保存人物图' }).count(), 1);
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '加入本地图片', exact: true }).click();
  await page.getByRole('button', { name: '打开选择器', exact: true }).click();
  await page.getByRole('button', { name: '引用 人物外观', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '引用 场景构图', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '引用 动作与运镜', exact: true }).isEnabled(), true);
  assert.equal(await page.getByRole('button', { name: '引用 对白音频', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '对话附件', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '已保存人物图', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '素材库', exact: true }).click();
  await page.screenshot({ path: resolve(out, 'desktop-picker.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(out, 'mobile-picker.png'), fullPage: true });
  await page.evaluate(() => { document.documentElement.className = 'dark'; });
  await page.screenshot({ path: resolve(out, 'mobile-dark-picker.png'), fullPage: true });
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByLabel('模型', { exact: true }).selectOption('loading');
  await page.getByRole('button', { name: '打开选择器', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '引用 人物外观', exact: true }).isEnabled(), true);
  assert.equal(await page.getByRole('button', { name: '引用 动作与运镜', exact: true }).isDisabled(), true);
  await page.getByRole('status').filter({ hasText: '正在读取当前模型的参考能力' }).waitFor();
  await page.getByRole('button', { name: '引用 人物外观', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: '清空选择', exact: true }).click();
  await page.getByLabel('模型', { exact: true }).selectOption('frames');
  await page.getByRole('button', { name: '打开选择器', exact: true }).click();
  await page.getByRole('button', { name: '引用 人物外观', exact: true }).click();
  await page.getByRole('button', { name: '引用 场景构图', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  assert.deepEqual(JSON.parse(await page.getByLabel('已选内容').textContent()).map(item => item.role), ['first_frame', 'last_frame']);
  await page.getByRole('button', { name: '清空选择', exact: true }).click();
  await page.getByLabel('模型', { exact: true }).selectOption('text');
  await page.getByRole('button', { name: '打开选择器', exact: true }).click();
  await page.getByRole('button', { name: '引用 对白音频', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  assert.equal(JSON.parse(await page.getByLabel('已选内容').textContent())[0].kind, 'audio');
  assert.deepEqual(errors, []);
  console.log('Picker capability/count preflight, saved/local aggregation, loading-safe removal, frame roles, discussion audio, historical preview/reuse, and 390px light/dark layout passed.');
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
