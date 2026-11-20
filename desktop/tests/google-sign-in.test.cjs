'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { googleSignIn } = require('../src/google-sign-in.cjs');

for (const status of [404, 405]) {
  test(`an older cloud returning ${status} ends native sign-in without opening the browser`, async () => {
    const originalFetch = globalThis.fetch;
    let opened = false;
    let port;
    try {
      globalThis.fetch = async (url, request) => {
        assert.equal(url, 'https://cloud.example.test/api/v1/auth/google/desktop/start');
        port = JSON.parse(request.body).port;
        return new Response(JSON.stringify({ detail: 'Not Found' }), { status });
      };
      const result = await googleSignIn({
        origin: 'https://cloud.example.test', next: '/projects', language: 'zh',
        signal: new AbortController().signal,
        openBrowser: async () => { opened = true; },
      });
      assert.deepEqual(result, { error: 'google_desktop_server_outdated' });
      assert.equal(opened, false);
      await assert.rejects(originalFetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) }));
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
}
