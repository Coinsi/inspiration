'use strict';

const { createHash, randomBytes } = require('node:crypto');
const { once } = require('node:events');
const { createServer } = require('node:http');

const API = '/api/v1/auth/google/desktop';
const LOOPBACK = '127.0.0.1';
const OPAQUE = /^[A-Za-z0-9_-]{43}$/;
const ERROR = /^(google_[a-z_]{1,40}|TOO_MANY_ATTEMPTS)$/;
const CLEAR_CALLBACK = "history.replaceState(null,'','/')";
const CLEAR_CALLBACK_HASH = createHash('sha256').update(CLEAR_CALLBACK).digest('base64');

async function post(origin, endpoint, body, signal) {
  const response = await fetch(`${origin}${API}/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify(body),
    credentials: 'omit',
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
  });
  if (response.status === 404 || response.status === 405)
    return { error: 'google_desktop_server_outdated' };
  const data = await response.json().catch(() => null);
  if (response.ok && data) return data;
  const code = data?.error?.code;
  return { error: ERROR.test(code) ? code : 'google_desktop_unavailable' };
}

function browserURL(raw, origin) {
  try {
    const url = new URL(raw);
    if (url.origin === origin && !url.username && !url.password && !url.hash &&
      url.pathname === `${API}/browser` && url.searchParams.size === 1 &&
      OPAQUE.test(url.searchParams.get('ticket'))) return url.href;
  } catch {
    // A stale or malformed server response must not open an arbitrary URL.
  }
  return null;
}

function waitForCallback(server, port, state, signal) {
  const origin = `http://${LOOPBACK}:${port}`;
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    let delivered = false;
    const onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, { once: true });

    server.on('request', (request, response) => {
      let url;
      try { url = new URL(request.url, origin); }
      catch { response.writeHead(400).end(); return; }
      const topLevel = request.headers['sec-fetch-dest'];
      if (request.method !== 'GET' || request.headers.host !== `${LOOPBACK}:${port}` ||
        (topLevel && topLevel !== 'document') || url.origin !== origin ||
        url.pathname !== `/oauth/google/${state}` || url.searchParams.size !== 1) {
        response.writeHead(404).end();
        return;
      }
      const code = url.searchParams.get('code');
      const error = url.searchParams.get('error');
      if (delivered || signal.aborted || (!OPAQUE.test(code) && !ERROR.test(error))) {
        response.writeHead(400).end();
        return;
      }
      delivered = true;
      signal.removeEventListener('abort', onAbort);
      resolve({ code, error, response });
    });
  });
}

function finishBrowser(response, { error }, language) {
  if (!response || response.destroyed) return;
  const zh = language === 'zh';
  const title = error ? (zh ? '登录未完成' : 'Sign-in did not finish') : (zh ? '登录完成' : 'Signed in');
  const message = error
    ? (zh ? '请回到 Inspiration 客户端查看原因或重试。' : 'Return to Inspiration to see the result or try again.')
    : (zh ? '你可以关闭此页，回到 Inspiration 客户端。' : 'You can close this tab and return to Inspiration.');
  response.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': `default-src 'none'; script-src 'sha256-${CLEAR_CALLBACK_HASH}'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'`,
    'X-Content-Type-Options': 'nosniff',
  }).end(`<!doctype html>
    <html lang="${zh ? 'zh-CN' : 'en'}">
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${title} · Inspiration</title>
    <style>body{font:1rem system-ui;margin:4rem auto;padding:0 1.5rem;max-width:34rem;color-scheme:light dark}</style>
    <script>${CLEAR_CALLBACK}</script>
    <main><h1>${title}</h1><p>${message}</p></main></html>`);
}

async function googleSignIn({ origin, next, language, signal, openBrowser }) {
  const timeout = AbortSignal.timeout(5 * 60_000);
  const attempt = new AbortController();
  const active = AbortSignal.any([signal, timeout, attempt.signal]);
  const state = randomBytes(32).toString('base64url');
  const verifier = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  // The listener exists for this attempt only and is not reachable on the LAN.
  const server = createServer({ requestTimeout: 15_000, headersTimeout: 10_000, keepAliveTimeout: 1_000 });
  server.maxConnections = 12;
  let response;
  let result;
  try {
    active.throwIfAborted();
    const listening = once(server, 'listening', { signal: active });
    server.listen(0, LOOPBACK);
    await listening;
    const { port } = server.address();
    const start = await post(origin, 'start', { port, state, challenge, next }, active);
    if (start.error) return start;
    const destination = browserURL(start.browser_url, origin);
    if (!destination) return { error: 'google_desktop_unavailable' };
    const pending = waitForCallback(server, port, state, active).then(callback => {
      response = callback.response;
      return callback;
    });
    // The preinstalled listener also covers a callback arriving during browser launch.
    const [callback] = await Promise.all([pending, openBrowser(destination)]);
    if (callback.error) result = { error: callback.error };
    else {
      const login = await post(origin, 'exchange', { code: callback.code, verifier }, active);
      result = login.error ? login : typeof login.access_token === 'string' && login.access_token &&
        typeof login.next === 'string' && login.linked === false
        ? { access_token: login.access_token, next: login.next, linked: false }
        : { error: 'google_desktop_unavailable' };
    }
    return result;
  } catch (error) {
    if (signal.aborted) result = { error: 'google_cancelled' };
    else if (timeout.aborted) result = { error: 'google_desktop_timeout' };
    else if (error?.cause?.code || error?.name === 'TimeoutError') result = { error: 'google_network_error' };
    else result = { error: 'google_desktop_unavailable' };
    return result;
  } finally {
    attempt.abort();
    finishBrowser(response, result ?? { error: 'google_desktop_unavailable' }, language);
    // Stop accepting callbacks immediately and let any final browser response flush.
    server.close();
    if (!response || response.destroyed) server.closeAllConnections();
  }
}

module.exports = { googleSignIn };
