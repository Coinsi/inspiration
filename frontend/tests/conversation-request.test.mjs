import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const cache = new URL('../node_modules/.cache/conversation-request/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('request.mjs', cache);
await build({ entryPoints: [fileURLToPath(new URL('../src/components/workbench/conversationRequest.ts', import.meta.url))], outfile: fileURLToPath(output), platform: 'node', format: 'esm' });
const { prepareConversationRequest } = await import(output.href);

test('lost-response retry keeps its original body and key across live run revision changes', () => {
  const body = { content: '悬疑一些', media: null, production: { run_id: 'run', action: 'respond', expected_revision: 4 } };
  const sent = prepareConversationRequest(body, null);
  for (const production of [{ ...body.production, expected_revision: 7 }, null]) {
    const retry = prepareConversationRequest({ ...body, production }, sent.request);
    assert.deepEqual(retry, sent);
  }
});

test('changing the prompt, model or references creates a distinct request', () => {
  const body = { content: '雨夜', media: { provider: 'image', references: ['a'] }, production: null };
  const sent = prepareConversationRequest(body, null);
  for (const changed of [{ ...body, content: '白天' }, { ...body, media: { ...body.media, provider: 'other' } }, { ...body, media: { ...body.media, references: ['b'] } }]) {
    const next = prepareConversationRequest(changed, sent.request);
    assert.notEqual(next.request.key, sent.request.key);
    assert.deepEqual(next.body, changed);
  }
});
