// Production workspace components with deterministic API fixtures; no model requests.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const out = resolve('../test-results/conversation-attachments');
await mkdir(out, { recursive: true });
const css = (await readdir(resolve('dist/assets'))).find(name => name.startsWith('index-') && name.endsWith('.css'));
await writeFile(resolve(out, 'production.css'), await readFile(resolve('dist/assets', css)));
await build({ stdin: { contents: `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'; import {BrowserRouter} from 'react-router-dom';
import {AuthProvider} from './src/lib/auth'; import {I18nProvider} from './src/lib/i18n';
import {ConversationWorkspace} from './src/components/workbench/ConversationWorkspace';
const q=new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById('root')).render(<I18nProvider><QueryClientProvider client={q}><AuthProvider><BrowserRouter><ConversationWorkspace projectId="fixture"/></BrowserRouter></AuthProvider></QueryClientProvider></I18nProvider>);
`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, loader: { '.svg': 'dataurl', '.png': 'dataurl' }, outfile: resolve(out, 'fixture.js'), format: 'esm', jsx: 'automatic', alias: { '@': resolve('src') }, define: { 'process.env.NODE_ENV': '"development"' } });
await writeFile(resolve(out, 'index.html'), '<!doctype html><html class="light"><meta charset="utf-8"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><style>body{margin:0}.conversation-workspace{height:100vh!important;min-height:0!important}#root{height:100vh}</style><div id="root"></div><script type="module" src="/fixture.js"></script></html>');
const server = createServer(async (req, res) => { const name = ({ '/fixture.js': 'fixture.js', '/fixture.css': 'fixture.css', '/production.css': 'production.css' })[req.url] || 'index.html'; res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html'); res.end(await readFile(resolve(out, name))); });
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const png = await readFile(resolve('public/images/studio-scenes.webp'));
const uploads = new Map(); const conversations = new Map(); const sends = []; const errors = [];
let failChunk = false;
let deferLookup = false, finishLookup, lookupStarted;
let retryInput = null;
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace('/api/v1/projects/fixture', '');
    const method = route.request().method();
    const data = method === 'POST' && !path.includes('/chunk') ? route.request().postDataJSON() : null;
    const ok = json => route.fulfill({ json });
    if (path === '/providers') return ok([{ provider_name: 'text-model', kind: 'llm', enabled: true, config: { is_default: true, label: '文字模型', model: 'text' } }, { provider_name: 'video-model', kind: 'generation', enabled: true, config: { modality: 'video', model: 'video' } }]);
    if (path.endsWith('/capabilities')) return ok({ modalities: ['video'], features: ['reference', 'first_frame'], max_reference_images: 2, max_reference_videos: 0, param_schema: {} });
    if (path === '/works') return ok([]);
    if (path === '/workbench/references') return ok({ items: [], has_more: false });
    if (path === '/conversations') { if (method === 'POST') { const c = { id: data.id, title: '新的对话', work_id: null, archived: false, updated_at: new Date().toISOString(), messages: [], has_older: false }; conversations.set(c.id, c); return ok(c); } return ok([...conversations.values()]); }
    if (path.startsWith('/conversations/')) { const id = path.split('/')[2]; const c = conversations.get(id); if (path.endsWith('/retry-input')) return ok(retryInput); if (path.endsWith('/messages')) { sends.push(data); c.messages = [{ id: crypto.randomUUID(), ordinal: 1, role: 'user', content: data.content || '请分析附件', status: 'completed', created_at: new Date().toISOString(), context: { attachments: data.attachments.map(r => { const a = uploads.get(r.attachment_id); a.saved_at = new Date().toISOString(); return { ...a, role: r.role }; }), references: [] }, progress: [] }]; return ok(c); } return ok(c); }
    if (path === '/attachments/uploads') { const a = { ...data, id: data.request_key, title: data.filename, filename: data.filename, kind: data.filename.endsWith('.mp3') ? 'audio' : data.filename.endsWith('.mp4') ? 'video' : 'image', status: 'uploading', uploaded_bytes: 0, chunk_size: 8 * 1024 * 1024, blob_hash: null, chunk_hashes: [] }; uploads.set(a.id, a); return ok(a); }
    if (path === '/attachments') return ok({ items: [...uploads.values()].filter(a => a.saved_at), has_more: false });
    if (path.startsWith('/attachments/')) { const a = uploads.get(path.split('/')[2]); if (path.endsWith('/chunk')) { if (failChunk) { failChunk = false; return route.fulfill({ status: 503, json: { error: { message: '网络暂时断开' } } }); } a.uploaded_bytes = a.size_bytes; return ok(a); } if (path.endsWith('/complete')) { a.status = 'ready'; a.blob_hash = 'fixture-image'; return ok(a); } if (method === 'DELETE') { a.status = 'cancelled'; return route.fulfill({ status: 204 }); } if (deferLookup && method === 'GET') { deferLookup = false; await new Promise(resolve => { finishLookup = resolve; lookupStarted(); }); } return ok(a); }
    if (path.startsWith('/blobs/')) return route.fulfill({ body: png, contentType: 'image/webp' });
    throw new Error(`Unexpected API ${method} ${path}`);
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/projects/fixture/workbench`);
  const files = page.locator('.conversation-add-menu input[type=file]');
  await files.setInputFiles({ name: 'character.webp', mimeType: 'image/webp', buffer: png });
  await page.getByRole('button', { name: '发送附件', exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.message-attachments > button').length === 1);
  assert.equal(sends.length, 1); assert.equal(sends[0].content, ''); assert.equal(sends[0].attachments.length, 1);
  assert.equal(await page.locator('.conversation-attachment-card').count(), 0);
  await page.getByRole('button', { name: '预览 character.webp', exact: true }).click();
  await page.getByRole('dialog').waitFor(); await page.getByRole('button', { name: '关闭', exact: true }).click();
  // Clipboard files join the same upload pipeline.
  await page.getByRole('textbox', { name: '对话消息' }).evaluate((el, bytes) => { const data = new DataTransfer(); data.items.add(new File([new Uint8Array(bytes)], 'pasted.webp', { type: 'image/webp' })); el.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, clipboardData: data })); }, [...png]);
  await page.getByRole('button', { name: '预览 pasted.webp', exact: true }).waitFor();
  await page.getByRole('button', { name: '移除 pasted.webp', exact: true }).click();
  await page.locator('.conversation-compose').evaluate((el, bytes) => { const data = new DataTransfer(); data.items.add(new File([new Uint8Array(bytes)], 'dropped.webp', { type: 'image/webp' })); el.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: data })); }, [...png]);
  await page.getByRole('button', { name: '预览 dropped.webp', exact: true }).waitFor();
  await page.getByRole('button', { name: '移除 dropped.webp', exact: true }).click();
  await page.getByRole('button', { name: '添加内容', exact: true }).click();
  await page.getByRole('menuitem', { name: '已有素材与对话附件', exact: true }).click();
  await page.getByRole('button', { name: '对话附件', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'character.webp', exact: true }).click();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: '移除 character.webp', exact: true }).click();
  assert.equal([...uploads.values()].find(a => a.filename === 'character.webp').status, 'ready');
  // Failed draft survives refresh and prompts the original file, never promises background upload.
  failChunk = true;
  await files.setInputFiles({ name: 'resume.webp', mimeType: 'image/webp', buffer: png });
  await page.getByRole('alert').filter({ hasText: '网络暂时断开' }).waitFor();
  await page.reload();
  await page.getByText('重新选择原文件续传', { exact: true }).waitFor();
  const chooser = page.waitForEvent('filechooser'); await page.getByRole('button', { name: '继续上传', exact: true }).click();
  await (await chooser).setFiles({ name: 'resume.webp', mimeType: 'image/webp', buffer: png });
  await page.getByRole('button', { name: '发送附件', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: '移除 resume.webp', exact: true }).click();
  // Saved attachments are reusable, and unsupported references remain visible after mode switch.
  await files.setInputFiles({ name: 'voice.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('audio') });
  await page.getByRole('button', { name: '本轮创作方式' }).click();
  await page.getByRole('option', { name: '视频', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '不支持音频参考' }).first().waitFor();
  assert.equal(await page.getByRole('button', { name: '预览 voice.mp3', exact: true }).count(), 1);
  await page.screenshot({ path: resolve(out, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(out, 'mobile.png'), fullPage: true });
  await page.getByRole('button', { name: '添加内容', exact: true }).click();
  const bounds = await page.getByRole('menu').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await page.screenshot({ path: resolve(out, 'mobile-add-menu.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await page.evaluate(() => { document.documentElement.className = 'dark'; });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.screenshot({ path: resolve(out, 'dark-desktop.png'), fullPage: true });
  // A late materials handoff must not pull the user back after changing conversation.
  const previous = [...conversations.keys()][0], attachmentId = [...uploads.values()].find(a => a.filename === 'character.webp').id;
  deferLookup = true; const pendingLookup = new Promise(resolve => { lookupStarted = resolve; });
  await page.goto(`http://127.0.0.1:${server.address().port}/projects/fixture/workbench?conversation=${previous}&attachment=${attachmentId}`); await pendingLookup;
  await page.getByRole('button', { name: '全部对话', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '新的对话', exact: true }).first().click();
  finishLookup();
  await page.waitForFunction(() => !new URL(location.href).searchParams.has('conversation') && !new URL(location.href).searchParams.has('attachment'));
  assert.equal(await page.locator('.conversation-attachment-card').count(), 0);
  // Restore text-mode reused attachments and mixed local/media sources through the actual retry UI.
  const failed = { id: 'failed', role: 'assistant', ordinal: 2, status: 'failed', content: '', error: '测试失败', created_at: new Date().toISOString(), context: {}, progress: [] };
  const fixtureConversation = id => ({ id, title: '恢复要求', work_id: null, archived: false, messages: [failed], has_older: false });
  conversations.set('retry-text', fixtureConversation('retry-text'));
  retryInput = { content: '继续分析这张参考图', model_provider: 'text-model', scope: [], skills: [], attachments: [], references: [{ attachment_id: attachmentId, role: 'reference' }], media: null };
  await page.goto(`http://127.0.0.1:${server.address().port}/projects/fixture/workbench?conversation=retry-text`);
  await page.getByRole('button', { name: '修改后重试', exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.conversation-attachment-card').length === 1);
  assert.equal(await page.getByRole('textbox', { name: '对话消息' }).inputValue(), retryInput.content);
  uploads.set('other-attachment', { ...uploads.get(attachmentId), id: 'other-attachment', filename: 'other.webp', title: 'other' });
  conversations.set('retry-video', fixtureConversation('retry-video'));
  retryInput = { content: '按这两张参考图生成', model_provider: null, scope: [], skills: [], attachments: [{ attachment_id: attachmentId, role: 'reference' }], references: [], media: { provider: 'video-model', request_type: 'video', provider_params: {}, references: [{ attachment_id: attachmentId, role: 'reference' }, { attachment_id: 'other-attachment', role: 'first_frame' }] } };
  await page.goto(`http://127.0.0.1:${server.address().port}/projects/fixture/workbench?conversation=retry-video`);
  await page.getByRole('button', { name: '修改后重试', exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.conversation-attachment-card').length === 2);
  assert.equal(await page.getByRole('button', { name: '预览 other.webp', exact: true }).count(), 1);
  assert.deepEqual(errors, []);
  console.log('Attachment-only send, history preview, paste/drop, saved reuse, failed-draft refresh/resume, preserved audio capability error, light/dark and mobile menu bounds passed.');
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
