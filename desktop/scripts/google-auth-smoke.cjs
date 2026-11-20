'use strict';
// Real built Login UI + Electron main/preload + an independent browser cookie jar.
// The cloud API and Google consent page are local fixtures, not production services.
// Only shell.openExternal is substituted, so no user's browser/profile is opened.
// Build frontend first. Run `npm run test:google-auth` from desktop; install the
// Playwright Chromium browser or set PLAYWRIGHT_CHANNEL=chrome / CHROME_PATH.
// ARTIFACT_DIR selects a fresh output directory for screenshots and report.json.
const { _electron, chromium } = require('playwright');
const { expect } = require('playwright/test');
const assert = require('node:assert/strict');
const { createHash, randomBytes } = require('node:crypto');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const desktopDir = path.join(__dirname, '..');
const distDir = path.resolve(desktopDir, '../frontend/dist');
const artifacts = path.resolve(process.env.ARTIFACT_DIR || path.join(desktopDir, 'test-results/google-auth'));
const opaque = () => randomBytes(32).toString('base64url');
const sha256 = value => createHash('sha256').update(value).digest('base64url');
const next = '/projects?from=desktop-google-fixture';
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };

async function fixtureServer() {
  await fs.access(path.join(distDir, 'index.html')).catch(() => {
    throw new Error('Build the current frontend first: npm --prefix ../frontend run build (from desktop).');
  });
  const attempts = [], requests = [], errors = [], tokens = new Set();
  const ticketMap = new Map(), stateMap = new Map(), codeMap = new Map();
  let origin;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, origin);
    if (url.origin !== origin) { res.writeHead(403); res.end(); return; }
    requests.push({ method: req.method, path: url.pathname, cookie: req.headers.cookie || '', authorization: req.headers.authorization || '', rawURL: url.href });
    const json = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(body)); };
    const read = async () => { let body = ''; for await (const chunk of req) body += chunk; return JSON.parse(body); };
    try {
      if (url.pathname === '/api/v1/auth/providers') return json(200, { registration_enabled: true, providers: [{ id: 'google', name: 'Google', registration_enabled: true }] });
      if (url.pathname === '/api/v1/auth/google/desktop/start') {
        assert.equal(req.method, 'POST');
        assert.equal(req.headers.origin, origin);
        assert.equal(req.headers.cookie, undefined, 'Native start must not borrow either browser cookie jar');
        const body = await read();
        assert.deepEqual(Object.keys(body).sort(), ['challenge', 'next', 'port', 'state']);
        assert.match(body.state, /^[A-Za-z0-9_-]{43}$/);
        assert.match(body.challenge, /^[A-Za-z0-9_-]{43}$/);
        assert.equal(body.next, next);
        assert(Number.isInteger(body.port) && body.port > 0 && body.port < 65536);
        const attempt = { ...body, ticket: opaque(), oauthState: opaque(), browserCookie: opaque(), code: opaque(), consumed: false };
        attempts.push(attempt); ticketMap.set(attempt.ticket, attempt); stateMap.set(attempt.oauthState, attempt);
        return json(200, { browser_url: `${origin}/api/v1/auth/google/desktop/browser?ticket=${attempt.ticket}` });
      }
      if (url.pathname === '/api/v1/auth/google/desktop/browser') {
        const attempt = ticketMap.get(url.searchParams.get('ticket'));
        assert(attempt && !attempt.visited, 'Bootstrap ticket must be fresh');
        assert.match(req.headers.cookie || '', /external_test=browser-only/);
        assert(!req.headers.cookie.includes('desktop_test'), 'External browser must have its own cookie jar');
        attempt.visited = true;
        // Keep the provider fixture local. Playwright routing alone cannot prevent
        // every network request in an HTTP redirect chain.
        const authorization = new URL(`${origin}/fixture-google/authorize`);
        authorization.searchParams.set('state', attempt.oauthState);
        authorization.searchParams.set('redirect_uri', `${origin}/api/v1/auth/google/callback`);
        res.writeHead(302, { Location: authorization.href, 'Set-Cookie': `oauth_google_browser=${attempt.browserCookie}; HttpOnly; SameSite=Lax; Path=/api/v1/auth/google`, 'Cache-Control': 'no-store' });
        return res.end();
      }
      if (url.pathname === '/fixture-google/authorize') {
        const attempt = stateMap.get(url.searchParams.get('state'));
        assert(attempt?.visited, 'Fake provider must be reached from the real bootstrap navigation');
        const callback = `${origin}/api/v1/auth/google/callback?state=${attempt.oauthState}`;
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        return res.end(`<title>Fixture Google consent</title><h1>Fixture Google consent</h1><p>No real account or provider is used.</p><a href="${callback}&code=fixture-provider-code">Continue</a> <a href="${callback}&error=access_denied">Cancel</a>`);
      }
      if (url.pathname === '/api/v1/auth/google/callback') {
        const attempt = stateMap.get(url.searchParams.get('state'));
        assert(attempt?.visited && !attempt.callback, 'Callback must belong to the bootstrap browser');
        assert((req.headers.cookie || '').includes(`oauth_google_browser=${attempt.browserCookie}`));
        assert(!req.headers.cookie.includes('desktop_test'));
        attempt.callback = true;
        const callback = new URL(`http://127.0.0.1:${attempt.port}/oauth/google/${attempt.state}`);
        if (url.searchParams.get('error') === 'access_denied') callback.searchParams.set('error', 'google_cancelled');
        else {
          assert.equal(url.searchParams.get('code'), 'fixture-provider-code');
          codeMap.set(attempt.code, attempt);
          callback.searchParams.set('code', attempt.code);
        }
        res.writeHead(302, { Location: callback.href, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' });
        return res.end();
      }
      if (url.pathname === '/api/v1/auth/google/desktop/exchange') {
        assert.equal(req.method, 'POST'); assert.equal(req.headers.origin, origin);
        assert.equal(req.headers.cookie, undefined, 'Native exchange must not reuse browser cookies');
        const body = await read();
        assert.deepEqual(Object.keys(body).sort(), ['code', 'verifier']);
        const attempt = codeMap.get(body.code);
        assert(attempt?.callback && !attempt.consumed, 'Handoff code must be fresh and single use');
        assert.match(body.verifier, /^[A-Za-z0-9_-]{43}$/);
        assert.equal(sha256(body.verifier), attempt.challenge, 'Only the app that began this attempt can redeem it');
        attempt.verifier = body.verifier; attempt.consumed = true;
        attempt.token = `fixture-session-${opaque()}`; tokens.add(attempt.token);
        return json(200, { access_token: attempt.token, next: attempt.next, linked: false });
      }
      if (url.pathname === '/api/v1/me' || url.pathname === '/api/v1/projects') {
        if (!tokens.has((req.headers.authorization || '').replace(/^Bearer /, ''))) return json(401, { error: { code: 'UNAUTHORIZED', message: 'Fixture requires the redeemed app session' } });
        return json(200, url.pathname.endsWith('/me')
          ? { user: { id: 'desktop-fixture-user', email: 'desktop@example.test', username: 'desktop-fixture', display_name: 'Desktop fixture', is_active: true }, memberships: [] }
          : []);
      }
      if (url.pathname.startsWith('/api/')) throw new Error(`Unmatched fixture API: ${req.method} ${url.pathname}`);
      if (url.pathname === '/fixture-browser-inspect') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end('<title>Isolated browser fixture</title><h1>Isolated browser fixture</h1>'); }
      const requested = path.resolve(distDir, '.' + decodeURIComponent(url.pathname));
      assert(requested === distDir || requested.startsWith(distDir + path.sep), 'Static paths must remain within frontend/dist');
      const file = path.extname(url.pathname) ? requested : path.join(distDir, 'index.html');
      const body = await fs.readFile(file);
      res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
      res.end(body);
    } catch (error) {
      if (error.code === 'ENOENT') { res.writeHead(404); res.end(); return; }
      errors.push(error.message);
      json(500, { error: { code: 'FIXTURE_FAILURE', message: 'Local fixture assertion failed' } });
    }
  });
  // This server is also a deny-only proxy for non-loopback Chrome traffic. It
  // never forwards requests, even if a future fixture accidentally redirects out.
  server.on('connect', (_req, socket) => socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  return { server, origin, attempts, requests, errors, stateMap };
}

async function main() {
  const fixture = await fixtureServer();
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'inspiration-google-smoke-'));
  let client, browser, page;
  const checks = [], pageErrors = [], blocked = [], browserRequests = [];
  const record = label => { checks.push(label); console.log(`PASS: ${label}`); };
  await fs.mkdir(artifacts, { recursive: true });
  try {
    // Prefer installed Chrome when explicitly selected; CI can install Playwright Chromium.
    const browserOptions = process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {};
    browser = await chromium.launch({ ...browserOptions, proxy: { server: fixture.origin, bypass: '127.0.0.1,localhost' } });
    const external = await browser.newContext({ viewport: { width: 1000, height: 760 }, serviceWorkers: 'block' });
    await external.addCookies([{ name: 'external_test', value: 'browser-only', url: fixture.origin }]);
    external.on('request', req => browserRequests.push({ url: req.url(), body: req.postData() || '', authorization: req.headers().authorization || '' }));
    await external.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === fixture.origin || (url.hostname === '127.0.0.1' && fixture.attempts.some(a => String(a.port) === url.port))) return route.continue();
      blocked.push(`${url.protocol}//${url.host}${url.pathname}`);
      return route.abort('blockedbyclient');
    });
    const launchEnv = { ...process.env, INSPIRATION_DESKTOP_ORIGIN: fixture.origin, INSPIRATION_DESKTOP_SMOKE: '1' };
    delete launchEnv.ELECTRON_RUN_AS_NODE;
    client = await _electron.launch({ args: [desktopDir, `--user-data-dir=${profile}`], env: launchEnv });
    page = await client.firstWindow();
    page.setDefaultTimeout(15_000);
    page.on('pageerror', error => pageErrors.push(error.message));
    await client.evaluate(({ shell, BrowserWindow }, origin) => {
      globalThis.googleExternalURLs = [];
      shell.openExternal = async url => { globalThis.googleExternalURLs.push(url); };
      const session = BrowserWindow.getAllWindows()[0].webContents.session;
      session.webRequest.onBeforeRequest((details, callback) => {
        const url = new URL(details.url);
        callback({ cancel: !['data:', 'blob:', 'devtools:'].includes(url.protocol) && url.origin !== origin });
      });
    }, fixture.origin);
    await client.evaluate(async ({ BrowserWindow }, origin) => {
      await BrowserWindow.getAllWindows()[0].webContents.session.cookies.set({ url: origin, name: 'desktop_test', value: 'app-only' });
    }, fixture.origin);
    await page.goto(`${fixture.origin}/login?next=${encodeURIComponent(next)}`);
    await page.evaluate(() => localStorage.setItem('inspiration_lang', 'zh'));
    await page.reload();
    await expect(page.getByRole('button', { name: '使用 Google 登录', exact: true })).toBeVisible();
    assert.deepEqual(await page.evaluate(() => ({ require: typeof window.require, process: typeof window.process, bridge: Object.keys(window.inspirationDesktop).sort() })), { require: 'undefined', process: 'undefined', bridge: ['cancelGoogleSignIn', 'googleSignIn'] });
    const prefs = await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
    assert.equal(prefs.sandbox, true); assert.equal(prefs.contextIsolation, true); assert.equal(prefs.nodeIntegration, false);
    record('Real Login UI exposes only the narrow bridge and retains the Electron sandbox');

    const start = async () => {
      const index = fixture.attempts.length;
      await page.getByRole('button', { name: '使用 Google 登录', exact: true }).click();
      await expect(page.getByRole('button', { name: '取消登录', exact: true })).toBeVisible();
      await expect.poll(async () => client.evaluate(() => globalThis.googleExternalURLs.length)).toBe(index + 1);
      assert.equal(fixture.attempts.length, index + 1);
      const url = await client.evaluate((_electron, i) => globalThis.googleExternalURLs[i], index);
      const attempt = fixture.attempts[index];
      assert.equal(url, `${fixture.origin}/api/v1/auth/google/desktop/browser?ticket=${attempt.ticket}`);
      assert.equal(new URL(page.url()).pathname, '/login', 'Google must never replace the app renderer');
      assert.equal(await page.evaluate(() => localStorage.getItem('inspiration_token')), null);
      return { attempt, url };
    };
    const consent = async url => {
      const tab = await external.newPage();
      await tab.goto(url);
      await expect(tab.getByRole('heading', { name: 'Fixture Google consent' })).toBeVisible();
      return tab;
    };
    const assertClosed = async attempt => {
      await expect.poll(async () => {
        try {
          await fetch(`http://127.0.0.1:${attempt.port}/oauth/google/${attempt.state}?code=${attempt.code}`, { signal: AbortSignal.timeout(1000) });
          return false;
        } catch (error) { return error.cause?.code === 'ECONNREFUSED'; }
      }, { message: 'Completed/cancelled attempt must close its loopback listener', timeout: 5000 }).toBe(true);
    };

    const localCancel = await start();
    await page.screenshot({ path: path.join(artifacts, 'login-waiting.png') });
    await page.getByRole('button', { name: '取消登录', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('你已取消 Google 授权');
    await expect(page.getByRole('button', { name: '使用 Google 登录', exact: true })).toBeEnabled();
    await assertClosed(localCancel.attempt);
    assert.equal(localCancel.attempt.consumed, false);
    record('Local cancellation clears busy state, leaves the app signed out, and closes the loopback listener');

    const cancelled = await start();
    assert.notEqual(cancelled.attempt.state, localCancel.attempt.state);
    assert.notEqual(cancelled.attempt.challenge, localCancel.attempt.challenge);
    const cancelledTab = await consent(cancelled.url);
    await cancelledTab.getByRole('link', { name: 'Cancel', exact: true }).click();
    await expect(cancelledTab).toHaveTitle('登录未完成 · Inspiration');
    await expect.poll(() => new URL(cancelledTab.url()).pathname + new URL(cancelledTab.url()).search).toBe('/');
    await expect(page.getByRole('alert')).toContainText('你已取消 Google 授权');
    await expect(page.getByRole('button', { name: '使用 Google 登录', exact: true })).toBeEnabled();
    await assertClosed(cancelled.attempt);
    assert.equal(cancelled.attempt.consumed, false);
    await page.screenshot({ path: path.join(artifacts, 'login-cancelled.png') });
    record('Provider cancellation returns through the independent browser and real loopback, with query removed');

    const success = await start();
    const wrongState = await external.request.get(`http://127.0.0.1:${success.attempt.port}/oauth/google/${opaque()}?code=${opaque()}`);
    assert.equal(wrongState.status(), 404);
    assert.equal(success.attempt.consumed, false);
    const tab = await consent(success.url);
    await tab.getByRole('link', { name: 'Continue', exact: true }).click();
    await expect(tab).toHaveTitle('登录完成 · Inspiration');
    await expect.poll(() => new URL(tab.url()).pathname + new URL(tab.url()).search).toBe('/');
    await expect(page).toHaveURL(`${fixture.origin}${next}`);
    await page.getByRole('button', { name: '用户菜单', exact: true }).click();
    await expect(page.getByText('Desktop fixture', { exact: true }).first()).toBeVisible();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => localStorage.getItem('inspiration_token')), success.attempt.token);
    assert.equal(fixture.requests.filter(r => r.path.endsWith('/desktop/exchange')).length, 1);
    assert.equal(fixture.attempts.filter(a => a.consumed).length, 1);
    assert(fixture.requests.some(r => r.path === '/api/v1/me' && r.authorization === `Bearer ${success.attempt.token}`));
    await assertClosed(success.attempt);
    record('Retry survives a wrong-state callback and exchanges exactly once using the app-only verifier');
    await page.screenshot({ path: path.join(artifacts, 'signed-in-projects.png') });
    await tab.screenshot({ path: path.join(artifacts, 'browser-complete.png') });

    const appCookies = await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.session.cookies.get({}));
    const externalCookies = await external.cookies(`${fixture.origin}/api/v1/auth/google/desktop/browser`);
    assert(appCookies.some(c => c.name === 'desktop_test'));
    assert(!appCookies.some(c => c.name === 'external_test' || c.name === 'oauth_google_browser'));
    assert(externalCookies.some(c => c.name === 'external_test'));
    assert(externalCookies.some(c => c.name === 'oauth_google_browser' && c.httpOnly));
    assert(!externalCookies.some(c => c.name === 'desktop_test'));
    assert(!browserRequests.some(r => r.url.includes(success.attempt.verifier) || r.body.includes(success.attempt.verifier) || r.url.includes(success.attempt.token) || r.body.includes(success.attempt.token) || r.authorization));
    const inspection = await external.newPage();
    await inspection.goto(`${fixture.origin}/fixture-browser-inspect`);
    assert.equal(await inspection.evaluate(() => localStorage.getItem('inspiration_token')), null);
    record('Browser and app cookies stay separate; neither verifier nor app access token reaches the browser');

    await page.reload();
    await page.getByRole('button', { name: '用户菜单', exact: true }).click();
    await expect(page.getByText('Desktop fixture', { exact: true }).first()).toBeVisible();
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => localStorage.getItem('inspiration_token')), success.attempt.token);
    const denied = await page.evaluate(() => window.inspirationDesktop.googleSignIn('/projects', 'zh'));
    assert(denied?.error, 'The sign-in bridge must reject calls outside /login and /register');
    assert.equal(fixture.attempts.length, 3);
    assert.deepEqual(fixture.errors, []); assert.deepEqual(pageErrors, []); assert.deepEqual(blocked, []);
    record('Authenticated /me, intended return route, reload persistence, and non-login bridge rejection');
    await fs.writeFile(path.join(artifacts, 'report.json'), JSON.stringify({ passed: checks.length, checks, attempts: fixture.attempts.length, exchanges: 1, pageErrors, blockedRequests: blocked, fixtureErrors: fixture.errors, boundary: 'Real built frontend, Electron main/preload, independent Chrome context, and actual loopback. Cloud API and Google provider are local fixtures. shell.openExternal is captured, so OS default-browser dispatch, live Google, and production backend are not covered.' }, null, 2));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(artifacts, 'failure.png') }).catch(() => {});
    await fs.writeFile(path.join(artifacts, 'failure.json'), JSON.stringify({ checks, fixtureErrors: fixture.errors, pageErrors, blockedRequests: blocked, message: error.message }, null, 2));
    throw error;
  } finally {
    if (client) await client.close();
    if (browser) await browser.close();
    fixture.server.closeAllConnections();
    await new Promise(resolve => fixture.server.close(resolve));
    assert.equal(path.dirname(profile), path.resolve(os.tmpdir()));
    assert(path.basename(profile).startsWith('inspiration-google-smoke-'));
    await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
