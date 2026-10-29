// Actual chat components, deterministic API fixtures. No paid provider calls.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const out = resolve('../test-results/production-chat');
await mkdir(out, { recursive: true });
const css = (await readdir(resolve('dist/assets'))).find(n => n.startsWith('index-') && n.endsWith('.css'));
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
await new Promise(r => server.listen(0, '127.0.0.1', r));
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
let run, conversation, rejectSend = false;
const sends = [], controls = [], errors = [];
function scenario(status, taskStatus, error = {}) {
  run = { id: 'run', goal: '内部评估用户指导，只提出有证据的影响范围', engine: 'mock', status, revision: 7, event_sequence: 1, policy: { budget_points: 0 }, tasks: [{ id: 'task', key: 'story', role: 'writer', goal: '整理雨夜开场', status: taskStatus, state: { question: '配乐偏悬疑还是舒缓？' }, error, result: {} }] };
  conversation = { id: 'chat', title: '雨夜开场', work_id: null, archived: false, updated_at: new Date().toISOString(), has_older: false, messages: [
    { id: 'user', role: 'user', ordinal: 1, content: '帮我设计一个雨夜的悬疑开场。', status: 'completed', created_at: new Date().toISOString(), context: {}, progress: [] },
    { id: 'answer', role: 'assistant', ordinal: 2, content: '我会先用雨声和远处的脚步声建立气氛。配乐偏悬疑还是舒缓？', status: status === 'running' ? 'running' : 'needs_review', task_id: 'run', execution_kind: 'production', created_at: new Date().toISOString(), context: { production: { run_id: 'run', status, revision: 7, is_current: true } }, progress: [], error: error.message },
  ] };
}
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/projects/fixture', '');
    const method = route.request().method(), ok = json => route.fulfill({ json });
    if (path === '/providers') return ok([{ provider_name: 'text', kind: 'llm', enabled: true, config: { is_default: true, model: 'text', label: '文字模型' } }]);
    if (path === '/works') return ok([]);
    if (path === '/workbench/references' || path === '/attachments') return ok({ items: [], has_more: false });
    if (path === '/conversations') return ok([conversation]);
    if (path === '/conversations/chat/messages' && method === 'POST') {
      sends.push(route.request().postDataJSON());
      if (rejectSend) return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '制作进展已更新，请查看最新消息后再发送；你的输入已保留' } } });
      return ok(conversation);
    }
    if (path.endsWith('/control') && method === 'POST') { controls.push(route.request().postDataJSON()); return ok(path.startsWith('/production') ? run : conversation); }
    if (path.startsWith('/conversations/chat')) return ok(conversation);
    if (path === '/production/runs/run') return ok(run);
    throw new Error(`Unexpected API ${method} ${path}`);
  });
  const url = `http://127.0.0.1:${server.address().port}/projects/fixture/workbench?conversation=chat`;
  const load = async () => { await page.goto(url); await page.locator('.production-brief-toggle').waitFor(); };
  scenario('needs_attention', 'waiting_input'); await load();
  assert.equal(await page.getByRole('textbox').count(), 1);
  assert.equal(await page.getByText('用量与资源设置', { exact: true }).isVisible(), false);
  assert.equal(await page.getByText(run.goal, { exact: true }).count(), 0);
  assert.ok((await page.locator('.production-task').boundingBox()).height < 60);
  await page.screenshot({ path: resolve(out, 'waiting-light.png'), fullPage: true });
  await page.locator('.production-brief-toggle').click();
  await page.getByRole('button', { name: '回复', exact: true }).click();
  assert.equal(await page.getByRole('textbox').count(), 1);
  assert.ok(await page.getByRole('textbox', { name: '对话消息' }).evaluate(el => document.activeElement === el));
  await page.locator('.production-brief-toggle').click();
  rejectSend = true;
  await page.getByRole('textbox', { name: '对话消息' }).fill('先慢一些');
  await page.getByRole('textbox', { name: '对话消息' }).press('Shift+Enter');
  assert.equal(sends.length, 0);
  assert.ok((await page.getByRole('textbox', { name: '对话消息' }).inputValue()).includes('\n'));
  await page.getByRole('textbox', { name: '对话消息' }).fill('悬疑一些');
  await page.waitForFunction(() => !document.querySelector('[aria-label="发送消息"]').disabled);
  await page.getByRole('textbox', { name: '对话消息' }).press('Enter');
  await page.getByText('制作进展已更新，请查看最新消息后再发送；你的输入已保留', { exact: false }).waitFor();
  assert.deepEqual(sends[0].production, { run_id: 'run', action: 'respond', expected_revision: 7 });
  assert.equal(await page.getByRole('textbox', { name: '对话消息' }).inputValue(), '悬疑一些');
  rejectSend = false;
  scenario('needs_attention', 'failed', { message: '讨论或澄清不能包含修改范围', retryable: true }); await load();
  assert.equal(await page.getByText('讨论或澄清不能包含修改范围', { exact: true }).isVisible(), false);
  await page.locator('.production-brief').getByRole('button', { name: '重试', exact: true }).click();
  assert.equal(controls.at(-1).action, 'retry');
  assert.deepEqual(controls.at(-1).task_ids, ['task']);
  scenario('needs_attention', 'uncertain', { message: '未知提交结果', retryable: false }); await load();
  assert.equal(await page.getByRole('button', { name: '重试', exact: true }).count(), 0);
  scenario('running', 'running'); await load();
  await page.getByRole('button', { name: '停止后续步骤', exact: true }).click();
  assert.equal(controls.at(-1).action, 'cancel');
  await page.evaluate(() => document.documentElement.className = 'dark');
  await page.screenshot({ path: resolve(out, 'running-dark.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(out, 'mobile-dark.png'), fullPage: true });
  scenario('succeeded', 'succeeded'); await load();
  await page.getByRole('button', { name: '执行记录', exact: true }).waitFor();
  assert.ok((await page.locator('.production-task').boundingBox()).height < 60);
  assert.equal(await page.getByRole('button', { name: '停止后续步骤', exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  console.log('PASS compact task UI, single composer reply, version-bound request, preserved draft on conflict, safe retry, uncertain no resend, stop, light/dark/mobile.');
} finally { await browser.close(); await new Promise(r => server.close(r)); }
