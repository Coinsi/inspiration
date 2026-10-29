import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { File } from 'node:buffer';
import React from 'react';
import { create, act } from 'react-test-renderer';
const cache = new URL('../node_modules/.cache/attachment-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('uploads.mjs', cache);
await build({ stdin: { contents: `export * from './src/components/workbench/attachmentUpload'; export * from './src/lib/library'; export * from './src/components/workbench/referenceProblem'; export * from './src/components/workbench/referenceInput';`, resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'ts' }, outfile: fileURLToPath(output), bundle: true, packages: 'external', platform: 'node', format: 'esm', alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } });
const { uploadConversationAttachment: upload, fileFingerprint, digest, persistedAttachmentDrafts, restoreAttachmentDrafts, referenceProblem, referenceInput, referenceDetailPath, retryAttachmentReferences, validateAttachmentFile } = await import(output.href);
const hookOutput = new URL('hook.mjs', cache);
await build({ entryPoints: [fileURLToPath(new URL('../src/components/workbench/useConversationAttachments.ts', import.meta.url))], outfile: fileURLToPath(hookOutput), bundle: true, packages: 'external', platform: 'node', format: 'esm', alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } });
const { useConversationAttachments } = await import(hookOutput.href);
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
globalThis.localStorage = { getItem: () => null };
const file = new File(['abcdef'], 'scene.png', { type: 'image/png' });
const draft = { client_id: 'request-id', filename: file.name, kind: 'image', size_bytes: file.size, role: 'reference', state: 'preparing' };
let requests, current;
const json = value => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
beforeEach(async () => {
  requests = [];
  current = { id: 'file-id', filename: file.name, title: 'scene', kind: 'image', status: 'uploading', uploaded_bytes: 0, size_bytes: file.size, chunk_size: 3, fingerprint: await fileFingerprint(file), chunk_hashes: [], blob_hash: null };
  globalThis.fetch = async (url, options = {}) => {
    requests.push([url, options.method]);
    if (url.endsWith('/uploads') || url.endsWith('/manifest')) return json(current);
    if (url.includes('/chunk?')) {
      assert.equal(new URL(url, 'http://fixture').searchParams.get('offset'), String(current.uploaded_bytes));
      const chunk = options.body.get('file');
      current = { ...current, uploaded_bytes: current.uploaded_bytes + chunk.size, chunk_hashes: [...current.chunk_hashes, await digest(chunk)] };
      return json(current);
    }
    if (url.endsWith('/complete')) { current = { ...current, status: 'ready', blob_hash: 'hash' }; return json(current); }
    throw new Error(`Unexpected request ${url}`);
  };
});
test('uploads chunks at committed offsets and completes without index dependency', async () => {
  const progress = [];
  const result = await upload('/projects/p/attachments', file, draft, item => progress.push(item.uploaded_bytes), () => false);
  assert.equal(result.status, 'ready');
  assert.deepEqual(progress, [0, 3, 6, 6]);
  assert.equal(requests.filter(([url]) => url.includes('/chunk?')).length, 2);
});
test('resumes only missing chunks after validating committed content', async () => {
  current.uploaded_bytes = 3; current.chunk_hashes = [await digest(file.slice(0, 3))];
  const result = await upload('/attachments', file, { ...draft, attachment: current }, () => {}, () => false);
  assert.equal(result.status, 'ready');
  assert.deepEqual(requests.filter(([url]) => url.includes('/chunk?')).map(([url]) => url), ['/api/v1/attachments/file-id/chunk?offset=3']);
});
test('an idempotent create recovery fetches manifest before resuming', async () => {
  current.uploaded_bytes = 3; current.chunk_hashes = [await digest(file.slice(0, 3))];
  await upload('/attachments', file, draft, () => {}, () => false);
  assert.ok(requests.some(([url]) => url.endsWith('/manifest')));
});
test('rejects another file and corrupted committed prefix without retransmitting', async () => {
  current.uploaded_bytes = 3; current.chunk_hashes = ['wrong-hash'];
  await assert.rejects(upload('/attachments', file, { ...draft, attachment: current }, () => {}, () => false), /已上传部分/);
  assert.equal(requests.length, 1);
  current.fingerprint = 'different';
  await assert.rejects(upload('/attachments', file, { ...draft, attachment: current }, () => {}, () => false), /原附件不同/);
});
test('cancel stops before completing and failed processing can retry without reupload', async () => {
  let stop = false;
  const result = await upload('/attachments', file, draft, item => { if (item.uploaded_bytes === 3) stop = true; }, () => stop);
  assert.equal(result.uploaded_bytes, 3);
  assert.ok(!requests.some(([url]) => url.endsWith('/complete')));
  requests = []; current = { ...current, uploaded_bytes: 6, status: 'failed' };
  assert.equal((await upload('/attachments', file, { ...draft, attachment: current }, () => {}, () => false)).status, 'ready');
  assert.ok(!requests.some(([url]) => url.includes('/chunk?')));
});
test('saved drafts strip object URLs and require file reselection after refresh', () => {
  const saved = persistedAttachmentDrafts([{ ...draft, preview_url: 'blob:temporary', state: 'uploading', attachment: current }]);
  assert.ok(!('preview_url' in saved[0]));
  assert.equal(restoreAttachmentDrafts(JSON.stringify(saved))[0].state, 'paused');
  assert.equal(restoreAttachmentDrafts('broken').length, 0);
});
test('attachment references retain identity and audio is not silently treated as an image', () => {
  assert.deepEqual(referenceInput({ id: 'a', source_type: 'attachment', role: 'first_frame' }), { attachment_id: 'a', role: 'first_frame' });
  assert.equal(referenceDetailPath({ attachment_id: 'a', role: 'reference' }), '/attachments/a');
  assert.match(referenceProblem({ features: ['reference'], max_reference_images: 8 }, [{ kind: 'audio', role: 'reference' }]), /不支持音频/);
  assert.equal(validateAttachmentFile(new File(['audio'], 'voice.mp3')), 'audio');
  assert.throws(() => validateAttachmentFile(new File(['text'], 'wrong.txt')), /图片、视频或音频/);
});
test('editing a text retry restores reused attachments from top-level references', () => {
  assert.deepEqual(retryAttachmentReferences({ attachments: [], media: null, references: [{ attachment_id: 'reused', role: 'reference' }, { media_version_id: 'material', role: 'reference' }] }), [{ attachment_id: 'reused', role: 'reference' }]);
});
test('editing a retry combines distinct uploaded and reused files and deduplicates their ids', () => {
  const local = { attachment_id: 'uploaded', role: 'reference' };
  const reused = { attachment_id: 'reused', role: 'first_frame' };
  assert.deepEqual(retryAttachmentReferences({ attachments: [local], media: { references: [local, reused, { generation_id: 'result', role: 'reference' }] } }), [local, reused]);
  assert.deepEqual(retryAttachmentReferences({ attachments: [local], references: [reused, local] }), [local, reused]);
});
test('a background upload updates only its original conversation after switching drafts', { timeout: 10000 }, async t => {
  const completed = deferred();
  const storage = new Map();
  globalThis.localStorage = {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => {
      storage.set(key, value);
      if (key === 'first.attachments' && JSON.parse(value)[0]?.state === 'ready') completed.resolve();
    },
  };
  globalThis.window = globalThis;
  const normalFetch = globalThis.fetch; let unblock; let chunkStarted;
  const started = new Promise(resolve => { chunkStarted = resolve; });
  globalThis.fetch = async (url, options) => { if (url.includes('/chunk?') && !unblock) { await new Promise(resolve => { unblock = resolve; chunkStarted(); }); } return normalFetch(url, options); };
  let hook, rendered;
  function Probe({ scope }) { hook = useConversationAttachments('p', scope); return null; }
  await act(async () => { rendered = create(React.createElement(Probe, { scope: 'first' })); });
  t.after(async () => { await act(async () => { rendered.unmount(); }); });
  await act(async () => { hook.addFiles([file]); await started; });
  await act(async () => { rendered.update(React.createElement(Probe, { scope: 'second' })); });
  assert.equal(hook.items.length, 0);
  await act(async () => { unblock(); await completed.promise; });
  assert.equal(hook.items.length, 0);
  assert.equal(JSON.parse(storage.get('first.attachments'))[0].state, 'ready');
});
test('leaving the page keeps resumable uploaded bytes without deleting the draft', { timeout: 10000 }, async t => {
  const persistedChunk = deferred();
  const storage = new Map();
  globalThis.localStorage = {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => {
      storage.set(key, value);
      if (key === 'leave.attachments' && JSON.parse(value)[0]?.attachment?.uploaded_bytes === 3) persistedChunk.resolve();
    },
  };
  globalThis.window = globalThis;
  const normalFetch = globalThis.fetch; let unblock; let chunkStarted;
  const started = new Promise(resolve => { chunkStarted = resolve; });
  globalThis.fetch = async (url, options) => { if (url.includes('/chunk?') && !unblock) await new Promise(resolve => { unblock = resolve; chunkStarted(); }); return normalFetch(url, options); };
  let hook, rendered;
  function Probe() { hook = useConversationAttachments('p', 'leave'); return null; }
  await act(async () => { rendered = create(React.createElement(Probe)); });
  t.after(async () => { await act(async () => { rendered.unmount(); }); });
  await act(async () => { hook.addFiles([file]); await started; });
  // Flush unmount cleanup before allowing the in-flight chunk to finish.
  await act(async () => { rendered.unmount(); });
  await act(async () => { unblock(); await persistedChunk.promise; });
  assert.ok(!requests.some(([, method]) => method === 'DELETE'));
  assert.equal(requests.filter(([url]) => url.includes('/chunk?')).length, 1);
  assert.ok(!requests.some(([url]) => url.endsWith('/complete')));
  assert.equal(JSON.parse(storage.get('leave.attachments'))[0].attachment.uploaded_bytes, 3);
  assert.equal(restoreAttachmentDrafts(storage.get('leave.attachments'))[0].state, 'paused');
});
test('explicitly removing an in-flight upload cancels only that draft after the current chunk', { timeout: 10000 }, async t => {
  const deleted = deferred();
  const storage = new Map(); globalThis.localStorage = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) }; globalThis.window = globalThis;
  const normalFetch = globalThis.fetch; let unblock; let chunkStarted;
  const started = new Promise(resolve => { chunkStarted = resolve; });
  globalThis.fetch = async (url, options) => { if (options.method === 'DELETE') { requests.push([url, 'DELETE']); deleted.resolve(); return new Response(null, { status: 204 }); } if (url.includes('/chunk?') && !unblock) await new Promise(resolve => { unblock = resolve; chunkStarted(); }); return normalFetch(url, options); };
  let hook, rendered;
  function Probe() { hook = useConversationAttachments('p', 'remove'); return null; }
  await act(async () => { rendered = create(React.createElement(Probe)); });
  t.after(async () => { await act(async () => { rendered.unmount(); }); });
  await act(async () => { hook.addFiles([file]); await started; });
  await act(async () => { hook.remove(hook.items[0]); unblock(); await deleted.promise; });
  assert.equal(hook.items.length, 0); assert.deepEqual(JSON.parse(storage.get('remove.attachments')), []);
  assert.equal(requests.filter(([, method]) => method === 'DELETE').length, 1);
  assert.equal(current.uploaded_bytes, 3);
  assert.equal(requests.filter(([url]) => url.includes('/chunk?')).length, 1);
  assert.ok(!requests.some(([url]) => url.endsWith('/complete')));
});
