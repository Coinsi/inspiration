'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { checkForUpdates, selectUpdate, RELEASES_API, RELEASES_PAGE } = require('../src/updates.cjs');

const windows = { currentVersion: '0.1.0', platform: 'win32', arch: 'x64' };
function release(version, { platform = 'win', arch = 'x64', ...changes } = {}) {
  const tag = `desktop-v${version}`;
  const name = `Inspiration-${version}-${platform}-${arch}.${platform === 'win' ? 'exe' : 'dmg'}`;
  return {
    tag_name: tag, draft: false, prerelease: false,
    assets: [{ name, state: 'uploaded', size: 12345, browser_download_url: `${RELEASES_PAGE}/download/${encodeURIComponent(tag)}/${encodeURIComponent(name)}` }],
    ...changes,
  };
}
const response = entries => new Response(JSON.stringify(entries), { headers: { 'Content-Type': 'application/json' } });

test('selects the highest compatible stable desktop version, independently of listing order', () => {
  const result = selectUpdate([
    release('2.0.0', { prerelease: true }),
    release('3.0.0', { draft: true }),
    release('4.0.0-beta.1'),
    release('5.0.0', { tag_name: 'v5.0.0' }),
    release('0.9.9'), release('0.10.0'), release('0.2.0'),
    release('0.11.0', { platform: 'mac', arch: 'arm64' }),
    release('0.12.0', { assets: [] }),
  ], windows);
  assert.equal(result.status, 'available');
  assert.equal(result.version, '0.10.0');
  assert.equal(result.assetName, 'Inspiration-0.10.0-win-x64.exe');
  assert.equal(result.releaseUrl, `${RELEASES_PAGE}/tag/desktop-v0.10.0`);
  assert.equal(result.downloadUrl, `${RELEASES_PAGE}/download/desktop-v0.10.0/Inspiration-0.10.0-win-x64.exe`);
});

test('matches exact OS and architecture and never offers an installer for a different chip', () => {
  const releases = [release('1.0.0'), release('1.1.0', { platform: 'mac', arch: 'x64' }), release('1.2.0', { platform: 'mac', arch: 'arm64' })];
  assert.equal(selectUpdate(releases, windows).version, '1.0.0');
  for (const [arch, version] of [['x64', '1.1.0'], ['arm64', '1.2.0']]) {
    const result = selectUpdate(releases, { ...windows, platform: 'darwin', arch });
    assert.equal(result.version, version);
    assert.equal(result.assetName, `Inspiration-${version}-mac-${arch}.dmg`);
  }
  assert.equal(selectUpdate([release('1.0.0', { platform: 'mac', arch: 'x64' })], { ...windows, platform: 'darwin', arch: 'arm64' }).status, 'invalid');
  assert.equal(selectUpdate(releases, { ...windows, arch: 'arm64' }).reason, 'unsupported-platform');
  assert.equal(selectUpdate(releases, { ...windows, platform: 'linux' }).reason, 'unsupported-platform');
});

test('compares SemVer numbers, ignores build precedence, and upgrades a current prerelease to stable', () => {
  assert.equal(selectUpdate([release('1.0.0')], { ...windows, currentVersion: '1.0.0-rc.2' }).status, 'available');
  assert.equal(selectUpdate([release('1.0.0+new')], { ...windows, currentVersion: '1.0.0+old' }).status, 'up-to-date');
  assert.equal(selectUpdate([release('0.9.0')], { ...windows, currentVersion: '0.10.0' }).status, 'up-to-date');
  assert.equal(selectUpdate([release('9007199254740993.0.0')], { ...windows, currentVersion: '9007199254740992.0.0' }).status, 'available');
  for (const currentVersion of ['01.0.0', '1.0', '1.0.0-01', 'v1.0.0', '', null])
    assert.equal(selectUpdate([], { ...windows, currentVersion }).reason, 'current-version');
});

test('rejects malicious, cross-repository, credential-bearing, Actions and altered installer URLs', () => {
  const valid = release('1.0.0');
  const expected = valid.assets[0].browser_download_url;
  for (const url of [
    expected.replace('github.com/', 'github.com.evil.test/'),
    expected.replace('https://', 'http://'),
    expected.replace('github.com/', 'user@github.com/'),
    expected.replace('Coinsi/inspiration/', 'attacker/inspiration/'),
    `${expected}?download=1`, `${expected}#anything`,
    `${RELEASES_PAGE}/../actions/runs/123/artifacts/456`,
    expected.replace('win-x64', 'win-arm64'),
    expected.replace('desktop-v1.0.0/', 'desktop-v0.9.0/'),
    'file:///C:/Windows/cmd.exe', 'javascript:alert(1)',
  ]) {
    const item = release('1.0.0');
    item.assets[0].browser_download_url = url;
    assert.deepEqual(selectUpdate([item], windows), { status: 'invalid', reason: 'invalid-installer' }, url);
  }
  valid.html_url = 'https://evil.test';
  assert.equal(selectUpdate([valid], windows).releaseUrl, `${RELEASES_PAGE}/tag/desktop-v1.0.0`);
});

test('distinguishes empty/current releases from malformed or incomplete publication data', () => {
  for (const entries of [[], [release('0.1.0')], [release('9.0.0', { draft: true })], [release('9.0.0', { prerelease: true })]])
    assert.deepEqual(selectUpdate(entries, windows), { status: 'up-to-date' });
  for (const entries of [null, { message: 'private error' }, [null], [release('01.0.0')], [release('1.0.0', { draft: undefined })], [release('1.0.0', { assets: null })]])
    assert.equal(selectUpdate(entries, windows).status, 'invalid');
  for (const change of [{ state: 'new' }, { size: 0 }, { size: -1 }]) {
    const item = release('1.0.0');
    Object.assign(item.assets[0], change);
    assert.equal(selectUpdate([item], windows).reason, 'invalid-installer');
  }
  const duplicate = release('1.0.0');
  duplicate.assets.push({ ...duplicate.assets[0] });
  assert.equal(selectUpdate([duplicate], windows).status, 'invalid');
});

test('queries only the fixed public Releases API and paginates past unrelated web releases', async () => {
  const requests = [];
  const result = await checkForUpdates({ ...windows, fetchImpl: async (url, options) => {
    requests.push(url);
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.headers.Authorization, undefined);
    assert.equal(options.headers.Accept, 'application/vnd.github+json');
    return response(requests.length === 1
      ? Array.from({ length: 100 }, (_, i) => ({ tag_name: `web-v${i}`, draft: false, prerelease: false, assets: [] }))
      : [release('0.3.0'), release('0.2.0')]);
  } });
  assert.equal(result.version, '0.3.0');
  assert.deepEqual(requests, [`${RELEASES_API}?per_page=100&page=1`, `${RELEASES_API}?per_page=100&page=2`]);
});

test('network failure and HTTP unavailability are separate from invalid JSON and foreign responses', async () => {
  assert.deepEqual(await checkForUpdates({ ...windows, fetchImpl: async () => { throw new Error('private-network-detail'); } }), { status: 'offline', reason: 'network' });
  assert.equal((await checkForUpdates({ ...windows, fetchImpl: async () => new Response('', { status: 403 }) })).status, 'offline');
  assert.deepEqual(await checkForUpdates({ ...windows, fetchImpl: async () => new Response('not json') }), { status: 'invalid', reason: 'release-data' });
  assert.deepEqual(await checkForUpdates({ ...windows, fetchImpl: async () => ({ ok: true, json: async () => { throw new TypeError('Connection closed'); } }) }), { status: 'offline', reason: 'network' });
  for (const extra of [{ redirected: true }, { url: 'https://evil.test/releases' }]) {
    assert.deepEqual(await checkForUpdates({ ...windows, fetchImpl: async () => ({ ok: true, ...extra, json: async () => [] }) }), { status: 'invalid', reason: 'response-origin' });
  }
});

test('the deadline also covers a stuck fetch or response body that ignores cancellation', async () => {
  for (const phase of ['fetch', 'body']) {
    let signal;
    const pending = new Promise(() => {});
    const result = await checkForUpdates({ ...windows, timeoutMs: 15, fetchImpl: async (_url, options) => {
      signal = options.signal;
      return phase === 'fetch' ? pending : { ok: true, json: () => pending };
    } });
    assert.deepEqual(result, { status: 'offline', reason: 'timeout' });
    assert.equal(signal.aborted, true);
  }
});

test('does not report up-to-date when the bounded release scan is incomplete', async () => {
  let calls = 0;
  const result = await checkForUpdates({ ...windows, fetchImpl: async () => {
    calls++;
    return response(Array.from({ length: 100 }, () => ({ tag_name: 'web-v1', draft: false, prerelease: false, assets: [] })));
  } });
  assert.deepEqual(result, { status: 'invalid', reason: 'release-page-limit' });
  assert.equal(calls, 5);
});
