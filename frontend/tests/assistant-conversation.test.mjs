import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { create, act } from 'react-test-renderer';
import { useAui } from '@assistant-ui/react';

const cache = new URL('../node_modules/.cache/assistant-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('adapter.mjs', cache);
await build({ entryPoints: [fileURLToPath(new URL('../src/components/workbench/AssistantConversation.tsx', import.meta.url))], outfile: fileURLToPath(output), bundle: true, packages: 'external', platform: 'node', format: 'esm', jsx: 'automatic' });
const { AssistantConversation, toAssistantMessage } = await import(output.href);
const source = { id: 'saved-message', role: 'assistant', content: '结果', status: 'completed', created_at: '2026-10-21T00:00:00Z' };

test('adapts saved identity, content and task status', () => {
  assert.equal(toAssistantMessage(source).id, source.id);
  assert.deepEqual(toAssistantMessage(source).content, [{ type: 'text', text: '结果' }]);
  assert.equal(toAssistantMessage({ ...source, status: 'running' }).status.type, 'running');
  assert.equal(toAssistantMessage({ ...source, status: 'failed' }).status.reason, 'error');
});

test('external drafts, submission locking, failure recovery and conversation isolation', async () => {
  let runtime;
  let root;
  let calls = 0;
  let finish;
  function Probe() { const aui = useAui(); runtime = { thread: { composer: aui.composer(), getState: () => aui.thread().getState() } }; return null; }
  let props = { messages: [source], draft: '第一条', disabled: false, sendDisabled: false, running: false, loading: false, onSend: () => { calls++; return new Promise(resolve => { finish = resolve; }); } };
  const render = (key = 'first') => React.createElement(AssistantConversation, { ...props, key }, React.createElement(Probe));
  await act(async () => { root = create(render()); });
  assert.equal(runtime.thread.composer.getState().text, '第一条');
  await act(async () => { runtime.thread.composer.send(); runtime.thread.composer.send(); });
  assert.equal(calls, 1);
  await act(async () => { finish(); });
  props = { ...props, draft: '快捷提示', sendDisabled: true };
  await act(async () => { root.update(render()); });
  assert.equal(runtime.thread.composer.getState().text, '快捷提示');
  await act(async () => { runtime.thread.composer.send(); });
  assert.equal(calls, 1);
  props = { ...props, sendDisabled: false, onSend: async () => { throw new Error('offline'); } };
  await act(async () => { root.update(render()); });
  await act(async () => { runtime.thread.composer.send(); });
  assert.equal(runtime.thread.composer.getState().text, '快捷提示');
  props = { ...props, messages: [], draft: '另一条对话', disabled: true };
  await act(async () => { root.update(render('second')); });
  assert.equal(runtime.thread.composer.getState().text, '另一条对话');
  assert.equal(runtime.thread.getState().messages.length, 0);
  assert.equal(runtime.thread.composer.getState().canSend, false);
  await act(async () => { root.unmount(); });
});
