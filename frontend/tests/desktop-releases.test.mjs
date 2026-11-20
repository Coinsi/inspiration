import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const cache = new URL('../node_modules/.cache/desktop-releases-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('releases.mjs', cache);
await build({
  entryPoints: [fileURLToPath(new URL('../src/lib/desktop-releases.ts', import.meta.url))],
  outfile: fileURLToPath(output), bundle: true, platform: 'node', format: 'esm',
});
const { getDesktopRelease, DESKTOP_RELEASES_URL } = await import(output.href);
const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json', ...headers },
});
function release(version, overrides = {}) {
  const tag = `desktop-v${version}`;
  return {
    tag_name: tag, draft: false, prerelease: false,
    html_url: `${DESKTOP_RELEASES_URL}/tag/${tag}`,
    published_at: '2026-11-20T12:00:00Z',
    assets: ['win-x64.exe', 'mac-arm64.dmg', 'mac-x64.dmg'].map((suffix, index) => ({
      name: `Inspiration-${version}-${suffix}`, state: 'uploaded', size: 1000 + index,
      browser_download_url: `${DESKTOP_RELEASES_URL}/download/${tag}/Inspiration-${version}-${suffix}`,
    })),
    ...overrides,
  };
}

test('selects the numerically newest stable published desktop version and its exact artifacts', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'https://api.github.com/repos/Coinsi/inspiration/releases?per_page=100&page=1');
    assert.equal(options.method, 'GET');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, undefined);
    return json([
      release('2.9.9'), release('2.10.0'), release('1.99.0'),
      release('9.0.0', { draft: true }), release('8.0.0', { prerelease: true }),
      release('7.0.0', { published_at: null }), release('6.0.0', { tag_name: 'web-v6.0.0' }),
      release('5.0.0-beta.1'), release('04.0.0'),
    ]);
  };
  const result = await getDesktopRelease();
  assert.equal(result.version, '2.10.0');
  assert.equal(result.releaseUrl, `${DESKTOP_RELEASES_URL}/tag/desktop-v2.10.0`);
  assert.equal(result.publishedAt, '2026-11-20T12:00:00Z');
  assert.deepEqual(result.assets, {
    'windows-x64': { url: `${DESKTOP_RELEASES_URL}/download/desktop-v2.10.0/Inspiration-2.10.0-win-x64.exe`, size: 1000 },
    'mac-arm64': { url: `${DESKTOP_RELEASES_URL}/download/desktop-v2.10.0/Inspiration-2.10.0-mac-arm64.dmg`, size: 1001 },
    'mac-x64': { url: `${DESKTOP_RELEASES_URL}/download/desktop-v2.10.0/Inspiration-2.10.0-mac-x64.dmg`, size: 1002 },
  });
});

test('missing or untrusted assets stay unavailable instead of borrowing older or mismatched packages', async () => {
  const current = release('1.1.0');
  const valid = current.assets[0];
  const arm = current.assets[1];
  current.assets = [
    valid,
    { ...arm, browser_download_url: arm.browser_download_url.replace('github.com/', 'github.com.evil.test/') },
    { ...arm, browser_download_url: `${arm.browser_download_url}?redirect=evil` },
    { ...arm, name: 'Inspiration-1.1.0-mac-x64.dmg' },
    { ...arm, state: 'new' },
    { ...arm, size: 0 },
    { ...arm, size: -1 },
    { ...arm, size: '1000' },
    release('1.0.0').assets[2],
  ];
  globalThis.fetch = async () => json([release('1.0.0'), current]);
  const result = await getDesktopRelease();
  assert.equal(result.version, '1.1.0');
  assert.equal(result.assets['windows-x64'].url, valid.browser_download_url);
  assert.equal(result.assets['mac-arm64'], null);
  assert.equal(result.assets['mac-x64'], null);
});

test('pagination stays on the fixed official API and ordering is numeric across pages', async () => {
  const urls = [];
  globalThis.fetch = async url => {
    urls.push(url);
    return urls.length === 1
      ? json([release('9.0.0')], 200, { Link: '<https://evil.test/collect>; rel="next"' })
      : json([release('10.0.0')]);
  };
  assert.equal((await getDesktopRelease()).version, '10.0.0');
  assert.deepEqual(urls, [1, 2].map(page => `https://api.github.com/repos/Coinsi/inspiration/releases?per_page=100&page=${page}`));
});

test('empty or unpublished releases produce no download, while a published version can have no ready assets', async () => {
  for (const entries of [[], [release('1.0.0', { draft: true })], [release('1.0.0', { html_url: 'https://evil.test/download' })]]) {
    globalThis.fetch = async () => json(entries);
    assert.equal(await getDesktopRelease(), null);
  }
  globalThis.fetch = async () => json([release('1.0.0', { assets: [], published_at: 'invalid-date' })]);
  const result = await getDesktopRelease();
  assert.equal(result.publishedAt, null);
  assert.deepEqual(result.assets, { 'windows-x64': null, 'mac-arm64': null, 'mac-x64': null });
});

test('HTTP, network and malformed API failures throw for the UI fallback and are not cached', async () => {
  for (const status of [403, 404, 429, 503]) {
    globalThis.fetch = async () => json({ message: 'GitHub unavailable' }, status);
    await assert.rejects(getDesktopRelease(), new RegExp(String(status)));
  }
  globalThis.fetch = async () => { throw new TypeError('Network unavailable'); };
  await assert.rejects(getDesktopRelease(), /Network unavailable/);
  globalThis.fetch = async () => json({ unexpected: true });
  await assert.rejects(getDesktopRelease(), /Invalid desktop release response/);
  globalThis.fetch = async () => new Response('<html>Proxy error</html>');
  await assert.rejects(getDesktopRelease());
  globalThis.fetch = async () => json([release('0.1.0')]);
  assert.equal((await getDesktopRelease()).version, '0.1.0');
});

test('cancellation stops pending requests and an incomplete scan never claims a latest version', async () => {
  const controller = new AbortController();
  globalThis.fetch = async (_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
  const request = getDesktopRelease(controller.signal);
  controller.abort();
  await assert.rejects(request, error => error.name === 'AbortError');
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return json([release('0.1.0')], 200, { Link: '<https://api.github.com/ignored>; rel="next"' });
  };
  await assert.rejects(getDesktopRelease(), /history exceeds/);
  assert.equal(calls, 10);
});
