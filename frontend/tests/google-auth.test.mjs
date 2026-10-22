import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

const cache = new URL('../node_modules/.cache/google-auth-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('auth.mjs', cache);
await build({
  stdin: {
    contents: 'export * from "./lib/auth"; export * from "./lib/google-auth"; export * from "./lib/api"; export { I18nProvider } from "./lib/i18n"; export { default as GoogleAuthComplete } from "./pages/GoogleAuthComplete";',
    resolveDir: fileURLToPath(new URL('../src/', import.meta.url)),
    loader: 'tsx',
  },
  outfile: fileURLToPath(output), bundle: true, packages: 'external', platform: 'node', format: 'esm', jsx: 'automatic', loader: { '.css': 'empty' },
});
const authModule = await import(output.href);
const { AuthProvider, useAuth, startGoogleAuth, exchangeGoogleSession, safeAuthNext, getGoogleAttempt, googleAuthError, ApiError, I18nProvider, GoogleAuthComplete } = authModule;
const originalGlobals = { fetch: globalThis.fetch, localStorage: globalThis.localStorage, sessionStorage: globalThis.sessionStorage, window: globalThis.window, document: globalThis.document };
afterEach(() => Object.assign(globalThis, originalGlobals));

function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

function browser() {
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  const redirects = [];
  globalThis.window = { location: { assign: url => redirects.push(url) } };
  globalThis.document = { documentElement: { lang: 'zh-CN' } };
  return redirects;
}

const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

test('return targets reject external and normalized off-route URLs while preserving account settings', () => {
  for (const value of ['https://evil.test', '//evil.test/projects', '/projects/../../login', '/projects/%2e%2e/login', '/projects\\evil', '/projects\n', '/projectsevil', '/admin/website/extra']) {
    assert.equal(safeAuthNext(value), '/projects', value);
  }
  assert.equal(safeAuthNext('/projects/123/workbench?account=security#panel'), '/projects/123/workbench?account=security#panel');
  assert.equal(safeAuthNext('/admin/website?tab=brand'), '/admin/website?tab=brand');
});

test('Google start validates the authorization host and sends only the safe return path and mode', async () => {
  const redirects = browser();
  const bodies = [];
  globalThis.fetch = async (_url, request) => {
    bodies.push(JSON.parse(request.body));
    return response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=public-nonce' });
  };
  await startGoogleAuth('/projects?account=security', 'link');
  assert.deepEqual(bodies[0], { next: '/projects?account=security', mode: 'link' });
  assert.deepEqual(getGoogleAttempt(), { next: '/projects?account=security', mode: 'link' });
  assert.equal(redirects.length, 1);
  globalThis.fetch = async () => response({ authorization_url: 'https://evil.test/oauth' });
  await assert.rejects(startGoogleAuth('/projects'), error => error.code === 'google_unavailable');
  assert.equal(redirects.length, 1);
});

test('a remounted completion consumer shares one exchange, clears old user queries and survives an old 401', async () => {
  browser();
  localStorage.setItem('inspiration_token', 'old-token');
  let resolveOldMe;
  let exchangeCalls = 0;
  let newMeCalls = 0;
  let current;
  const signedIn = { user: { id: 'new-user', display_name: 'Google user' }, memberships: [] };
  globalThis.fetch = async (url, request) => {
    if (url.endsWith('/auth/google/start')) return response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth' });
    if (url.endsWith('/auth/google/exchange')) {
      exchangeCalls++;
      return response({ access_token: 'new-token', next: '/projects?account=security', linked: true });
    }
    if (url.endsWith('/me') && request.headers.Authorization === 'Bearer old-token') {
      return new Promise(resolve => { resolveOldMe = resolve; });
    }
    if (url.endsWith('/me') && request.headers.Authorization === 'Bearer new-token') {
      newMeCalls++;
      return response(signedIn);
    }
    throw new Error(`Unexpected request: ${url}`);
  };
  await startGoogleAuth('/projects?account=security', 'link');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['old-user-projects'], ['private']);
  function Probe() { current = useAuth(); return null; }
  const render = key => React.createElement(QueryClientProvider, { client }, React.createElement(AuthProvider, null, React.createElement(Probe, { key })));
  let view;
  await act(async () => { view = create(render('first')); });
  let first;
  let second;
  await act(async () => {
    first = current.completeGoogleLogin();
    second = current.completeGoogleLogin();
    await first;
  });
  assert.equal(first, second);
  assert.equal(exchangeCalls, 1);
  assert.equal(newMeCalls, 1);
  assert.deepEqual(current.me, signedIn);
  assert.equal(client.getQueryData(['old-user-projects']), undefined);
  await act(async () => { view.update(render('remounted')); });
  assert.deepEqual(await current.completeGoogleLogin(), { next: '/projects?account=security', linked: true });
  assert.equal(exchangeCalls, 1);
  assert.equal(newMeCalls, 1);
  await act(async () => { resolveOldMe(response({ error: { code: 'UNAUTHORIZED', message: 'Expired' } }, 401)); });
  assert.equal(localStorage.getItem('inspiration_token'), 'new-token');
  assert.deepEqual(current.me, signedIn);
  await act(async () => { view.unmount(); });
  client.clear();
});

test('failed single-use exchange is shared until a fresh Google flow starts', async () => {
  browser();
  let exchanges = 0;
  globalThis.fetch = async url => {
    if (url.endsWith('/start')) return response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth' });
    exchanges++;
    return response({ error: { code: 'google_exchange_invalid', message: 'Expired' } }, 400);
  };
  await startGoogleAuth('/projects');
  const first = exchangeGoogleSession();
  const second = exchangeGoogleSession();
  assert.equal(first, second);
  await assert.rejects(first, error => error.code === 'google_exchange_invalid');
  assert.equal(exchanges, 1);
  await startGoogleAuth('/projects');
  await assert.rejects(exchangeGoogleSession());
  assert.equal(exchanges, 2);
  assert.match(googleAuthError('google_cancelled', true), /取消/);
  assert.match(googleAuthError(new ApiError('google_email_conflict', 'raw provider payload'), true), /原账号登录/);
  assert.doesNotMatch(googleAuthError(new ApiError('unexpected', 'raw provider payload'), true), /raw provider payload/);
});

test('callback cleanup and remount keep the pending exchange result and complete navigation', async () => {
  browser();
  let exchanges = 0;
  let finishExchange;
  let destination;
  globalThis.fetch = async url => {
    if (url.endsWith('/start')) return response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth' });
    if (url.endsWith('/exchange')) {
      exchanges++;
      return new Promise(resolve => { finishExchange = resolve; });
    }
    if (url.endsWith('/me')) return response({ user: { id: 'google-user' }, memberships: [] });
    throw new Error(`Unexpected request: ${url}`);
  };
  await startGoogleAuth('/projects?account=security');
  const client = new QueryClient();
  function PathProbe() { destination = useLocation(); return null; }
  function Content({ show }) {
    return React.createElement(React.Fragment, null, React.createElement(PathProbe), React.createElement(Routes, null,
      React.createElement(Route, { path: '/auth/google/complete', element: show ? React.createElement(GoogleAuthComplete) : null }),
      React.createElement(Route, { path: '/projects', element: React.createElement('p', null, 'Projects') }),
    ));
  }
  const render = show => React.createElement(QueryClientProvider, { client }, React.createElement(I18nProvider, null,
    React.createElement(AuthProvider, null, React.createElement(MemoryRouter, { initialEntries: ['/auth/google/complete'], future: { v7_startTransition: true, v7_relativeSplatPath: true } }, React.createElement(Content, { show }))),
  ));
  let view;
  await act(async () => { view = create(render(true)); });
  assert.equal(exchanges, 1);
  await act(async () => { view.update(render(false)); });
  await act(async () => { view.update(render(true)); });
  assert.equal(exchanges, 1);
  await act(async () => { finishExchange(response({ access_token: 'callback-token', next: '/projects?account=security', linked: false })); });
  assert.equal(localStorage.getItem('inspiration_token'), 'callback-token');
  assert.equal(destination.pathname + destination.search, '/projects?account=security');
  await act(async () => { view.unmount(); });
  client.clear();
});

test('a cancelled Google callback offers recovery without exchanging a cookie', async () => {
  browser();
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error('No request expected'); };
  const client = new QueryClient();
  let view;
  await act(async () => {
    view = create(React.createElement(QueryClientProvider, { client }, React.createElement(I18nProvider, null,
      React.createElement(AuthProvider, null, React.createElement(MemoryRouter, { initialEntries: ['/auth/google/complete?error=google_cancelled'], future: { v7_startTransition: true, v7_relativeSplatPath: true } }, React.createElement(GoogleAuthComplete))),
    )));
  });
  assert.equal(requests, 0);
  assert.match(view.root.findByProps({ role: 'alert' }).children.join(''), /取消/);
  assert.equal(view.root.findAllByType('button').length, 1);
  assert.match(view.root.findByType('a').props.href, /^\/login\?next=/);
  await act(async () => { view.unmount(); });
  client.clear();
});
