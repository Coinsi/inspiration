import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const dir = new URL('../node_modules/.cache/upload-tests/', import.meta.url);
await mkdir(dir, { recursive: true });
const output = new URL('api.mjs', dir);
await build({ entryPoints: [fileURLToPath(new URL('../src/lib/api.ts', import.meta.url))], outfile: fileURLToPath(output), bundle: true, platform: 'node', format: 'esm' });
const { apiUpload } = await import(output.href);
const originals = { XMLHttpRequest: globalThis.XMLHttpRequest, localStorage: globalThis.localStorage };
afterEach(() => Object.assign(globalThis, originals));
function setup() {
  let xhr;
  let token = 'current-user';
  globalThis.localStorage = { getItem: () => token, removeItem: () => { token = null; } };
  globalThis.XMLHttpRequest = class {
    upload = {}; headers = {}; status = 200;
    constructor() { xhr = this; }
    open(method, url) { this.url = url; this.method = method; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) { this.body = body; }
    abort() { this.aborted = true; this.onabort?.(); }
    finish(body, status = 200) { this.status = status; this.responseText = JSON.stringify(body); this.onload(); }
  };
  return () => xhr;
}
test('chunk progress is visible before acknowledgement and completion uses server bytes', async () => {
  const current = setup(), fractions = [];
  const p = apiUpload('/library/chunk', new FormData(), { onProgress: n => fractions.push(n) });
  current().upload.onprogress({ lengthComputable: true, loaded: 40, total: 100 });
  assert.deepEqual(fractions, [0.4]);
  assert.equal(current().headers.Authorization, 'Bearer current-user');
  current().finish({ uploaded_bytes: 100 });
  assert.deepEqual(await p, { uploaded_bytes: 100 });
});
test('pause aborts in-flight bytes and rejects promptly for resumable reconciliation', async () => {
  const current = setup(), controller = new AbortController();
  const p = apiUpload('/library/chunk', new FormData(), { signal: controller.signal, onProgress() {} });
  const rejected = assert.rejects(p, { name: 'AbortError' });
  controller.abort();
  await rejected;
  assert.equal(current().aborted, true);
});
test('stall timer resets on progress and also bounds the wait for acknowledgement', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const current = setup();
  const p = apiUpload('/library/chunk', new FormData(), { onProgress() {} });
  const rejected = assert.rejects(p, { code: 'UPLOAD_TIMEOUT' });
  t.mock.timers.tick(50_000);
  current().upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 });
  t.mock.timers.tick(50_000);
  assert.notEqual(current().aborted, true);
  current().upload.onload();
  t.mock.timers.tick(60_000);
  await rejected;
});
test('server errors preserve their message instead of leaving the upload spinning', async () => {
  const current = setup();
  const p = apiUpload('/library/chunk', new FormData(), { onProgress() {} });
  const rejected = assert.rejects(p, { code: 'CONFLICT', message: '上传位置已变化' });
  current().finish({ error: { code: 'CONFLICT', message: '上传位置已变化' } }, 409);
  await rejected;
});
