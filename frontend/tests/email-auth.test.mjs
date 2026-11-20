import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';

const cache = new URL('../node_modules/.cache/email-auth-tests/', import.meta.url);
await mkdir(cache, { recursive: true });
const output = new URL('auth.mjs', cache);
await build({
  stdin: {
    contents: 'export { AuthProvider } from "./lib/auth"; export { I18nProvider } from "./lib/i18n"; export { ThemeProvider } from "./lib/theme"; export { default as Login } from "./pages/Login";',
    resolveDir: fileURLToPath(new URL('../src/', import.meta.url)),
    loader: 'tsx',
  },
  outfile: fileURLToPath(output),
  bundle: true,
  packages: 'external',
  platform: 'node',
  format: 'esm',
  jsx: 'automatic',
  loader: { '.css': 'empty' },
  define: { 'import.meta.env.DEV': 'false' },
  plugins: [{
    name: 'omit-decorative-login-background',
    setup(plugin) {
      plugin.onResolve({ filter: /(?:^|\/)preload-entry$/ }, () => ({ path: 'preload-entry', namespace: 'test-preload' }));
      plugin.onLoad({ filter: /.*/, namespace: 'test-preload' }, () => ({ contents: 'export function preloadEntry() {}' }));
      plugin.onResolve({ filter: /(?:^|\/)LoginBackground$/ }, () => ({ path: 'LoginBackground', namespace: 'test-background' }));
      plugin.onLoad({ filter: /.*/, namespace: 'test-background' }, () => ({ contents: 'export function LoginBackground() { return null; }' }));
    },
  }],
});
const { AuthProvider, I18nProvider, ThemeProvider, Login } = await import(output.href);
const originalGlobals = Object.fromEntries(['fetch', 'localStorage', 'sessionStorage', 'window', 'document'].map(key => [key, globalThis[key]]));
const cleanups = [];

afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
  Object.assign(globalThis, originalGlobals);
});

function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const providers = { registration_enabled: true, providers: [{ id: 'google', name: 'Google', registration_enabled: true }] };
const signedIn = { user: { id: 'email-user', email: 'creator@example.com', display_name: 'Creator' }, memberships: [] };

async function renderLogin(path, handleRequest, options = {}) {
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  const redirects = [];
  globalThis.window = { location: { assign: url => redirects.push(url) }, inspirationDesktop: options.desktop };
  globalThis.document = { documentElement: { lang: 'zh-CN', classList: { add() {}, remove() {} } } };
  const requests = [];
  globalThis.fetch = async (url, requestOptions) => {
    const request = { url, method: requestOptions?.method ?? 'GET', body: requestOptions?.body ? JSON.parse(requestOptions.body) : undefined };
    requests.push(request);
    if (url.endsWith('/auth/providers')) return response(providers);
    if (url.endsWith('/me')) return options.me ? options.me(request) : response(signedIn);
    if (url.endsWith('/projects')) return response([]);
    return handleRequest(request);
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['auth-providers'], providers);
  let destination;
  let navigate;
  function PathProbe() { destination = useLocation(); navigate = useNavigate(); return null; }
  let view;
  await act(async () => {
    view = create(React.createElement(QueryClientProvider, { client }, React.createElement(I18nProvider, null,
      React.createElement(ThemeProvider, null, React.createElement(AuthProvider, null,
        React.createElement(MemoryRouter, { initialEntries: [path], future: { v7_startTransition: true, v7_relativeSplatPath: true } },
          React.createElement(PathProbe),
          React.createElement(Routes, null,
            React.createElement(Route, { path: '/login', element: React.createElement(Login) }),
            React.createElement(Route, { path: '/register', element: React.createElement(Login) }),
            React.createElement(Route, { path: '/projects/*', element: React.createElement('p', null, 'Projects') }),
          ),
        ),
      )),
    )));
  });
  cleanups.push(async () => { await act(async () => view.unmount()); client.clear(); });
  return { view, requests, redirects, navigate: path => navigate(path), get destination() { return destination.pathname + destination.search; } };
}

async function fillCredentials(view, email = 'creator@example.com', password = 'a-good-password') {
  await act(async () => {
    view.root.findByProps({ type: 'email' }).props.onChange({ target: { value: email } });
    view.root.findByProps({ type: 'password' }).props.onChange({ target: { value: password } });
  });
}

async function submit(view) {
  await act(async () => { await view.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
}

test('login and registration each require only email and password with appropriate browser autocomplete', async () => {
  for (const [path, passwordAutocomplete] of [['/login', 'current-password'], ['/register', 'new-password']]) {
    const { view } = await renderLogin(path, () => { throw new Error('No submission expected'); });
    const inputs = view.root.findByType('form').findAllByType('input');
    assert.equal(inputs.length, 2, path);
    assert.deepEqual(inputs.map(input => input.props.type), ['email', 'password'], path);
    assert.ok(inputs.every(input => input.props.required), path);
    assert.equal(inputs[0].props.autoComplete, 'email', path);
    assert.equal(inputs[1].props.autoComplete, passwordAutocomplete, path);
    const submitButton = view.root.findAllByType('button').find(button => button.props.type === 'submit');
    assert.equal(submitButton.props.disabled, true, path);
    if (path === '/register') assert.equal(inputs[1].props.minLength, 8);
  }
});

test('email login sends only email and password and returns to the requested project', async () => {
  const page = await renderLogin('/login?next=%2Fprojects%2Fstory%3Ftab%3Dassets', request => {
    assert.equal(request.url, '/api/v1/auth/login');
    return response({ access_token: 'email-login-token' });
  });
  await fillCredentials(page.view, ' creator@example.com ', 'correct-password');
  await submit(page.view);
  assert.deepEqual(page.requests.filter(request => request.method === 'POST'), [{
    url: '/api/v1/auth/login', method: 'POST', body: { email: 'creator@example.com', password: 'correct-password' },
  }]);
  assert.equal(localStorage.getItem('inspiration_token'), 'email-login-token');
  assert.equal(page.destination, '/projects/story?tab=assets');
});

test('registration submits two fields then signs in automatically without requesting a nickname', async () => {
  const page = await renderLogin('/register?next=https%3A%2F%2Fevil.example', request => {
    if (request.url.endsWith('/auth/register')) return response({ user_id: 'email-user' }, 201);
    if (request.url.endsWith('/auth/login')) return response({ access_token: 'email-registration-token' });
    throw new Error(`Unexpected request: ${request.url}`);
  });
  await fillCredentials(page.view, ' creator@example.com ', 'new-password-123');
  await submit(page.view);
  assert.deepEqual(page.requests.filter(request => request.method === 'POST'), [
    { url: '/api/v1/auth/register', method: 'POST', body: { email: 'creator@example.com', password: 'new-password-123' } },
    { url: '/api/v1/auth/login', method: 'POST', body: { email: 'creator@example.com', password: 'new-password-123' } },
  ]);
  assert.equal(localStorage.getItem('inspiration_token'), 'email-registration-token');
  assert.equal(page.destination, '/projects');
});

test('login uses the returned profile and does not wait for a second profile request', async () => {
  const page = await renderLogin('/login', () => response({ access_token: 'fast-token', me: signedIn }));
  await fillCredentials(page.view);
  await submit(page.view);
  assert.equal(page.destination, '/projects');
  assert.equal(page.requests.filter(r => r.url.endsWith('/me')).length, 0);
  assert.equal(localStorage.getItem('inspiration_token'), 'fast-token');
});

test('an unsuccessful registration preserves the two inputs and shows the server error without logging in', async () => {
  const page = await renderLogin('/register', request => {
    assert.equal(request.url, '/api/v1/auth/register');
    return response({ error: { code: 'EMAIL_EXISTS', message: '这个邮箱已注册，请直接登录' } }, 409);
  });
  await fillCredentials(page.view);
  await submit(page.view);
  assert.match(page.view.root.findByProps({ role: 'alert' }).children.join(''), /这个邮箱已注册/);
  assert.equal(page.view.root.findByProps({ type: 'email' }).props.value, 'creator@example.com');
  assert.equal(page.view.root.findByProps({ type: 'password' }).props.value, 'a-good-password');
  assert.equal(page.requests.filter(request => request.method === 'POST').length, 1);
  assert.equal(localStorage.getItem('inspiration_token'), null);
  assert.equal(page.destination, '/register');
  const submitButton = page.view.root.findAllByType('button').find(button => button.props.type === 'submit');
  assert.equal(submitButton.props.disabled, false);
});

test('Google registration works with the email and password fields empty', async () => {
  const page = await renderLogin('/register?next=%2Fprojects%3Faccount%3Dsecurity', request => {
    assert.equal(request.url, '/api/v1/auth/google/start');
    return response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=test' });
  });
  const googleButton = page.view.root.findAllByType('button').find(button => button.props.className?.includes('google-auth-button'));
  assert.ok(googleButton);
  assert.equal(googleButton.props.type, 'button');
  assert.equal(googleButton.props.disabled, false);
  await act(async () => { googleButton.props.onClick(); });
  assert.deepEqual(page.requests.filter(request => request.method === 'POST'), [{
    url: '/api/v1/auth/google/start', method: 'POST', body: { next: '/projects?account=security', mode: 'login' },
  }]);
  assert.deepEqual(page.redirects, ['https://accounts.google.com/o/oauth2/v2/auth?state=test']);
});

test('password visibility can be toggled without adding a confirmation field or losing the password', async () => {
  const { view } = await renderLogin('/register', () => { throw new Error('No submission expected'); });
  await fillCredentials(view);
  await act(async () => { view.root.findByProps({ 'aria-label': '显示密码' }).props.onClick(); });
  const password = view.root.findByProps({ autoComplete: 'new-password' });
  assert.equal(password.props.type, 'text');
  assert.equal(password.props.value, 'a-good-password');
  assert.equal(view.root.findByType('form').findAllByType('input').length, 2);
  await act(async () => { view.root.findByProps({ 'aria-label': '隐藏密码' }).props.onClick(); });
  assert.equal(view.root.findByProps({ autoComplete: 'new-password' }).props.type, 'password');
});

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function googleButton(view) {
  return view.root.findAllByType('button').find(button => button.props.className?.includes('google-auth-button'));
}

function cancelGoogleButton(view) {
  return view.root.findAllByType('button').find(button => button.children.includes('取消登录'));
}

test('desktop Google login and registration wait for the native bridge and accept the returned session', async () => {
  for (const path of ['/login', '/register']) {
    const completion = deferred();
    const starts = [];
    const page = await renderLogin(`${path}?next=%2Fprojects%2Fstory%3Ftab%3Dassets`, () => {
      throw new Error('Desktop sign-in must not start the browser-cookie flow');
    }, { desktop: {
      googleSignIn: (...args) => { starts.push(args); return completion.promise; },
      cancelGoogleSignIn: async () => {},
    } });
    await act(async () => { googleButton(page.view).props.onClick(); });
    assert.deepEqual(starts, [['/projects/story?tab=assets', 'zh']]);
    assert.equal(googleButton(page.view).props.disabled, true);
    assert.match(page.view.root.findByProps({ role: 'status' }).children.join(''), /浏览器/);
    assert.ok(cancelGoogleButton(page.view));
    assert.equal(localStorage.getItem('inspiration_token'), null);
    await act(async () => { completion.resolve({ access_token: 'desktop-token', next: '/projects/story?tab=assets', linked: false }); });
    assert.equal(localStorage.getItem('inspiration_token'), 'desktop-token');
    assert.equal(page.destination, '/projects/story?tab=assets');
    assert.equal(page.requests.filter(request => request.url.endsWith('/me')).length, 1);
    assert.deepEqual(page.redirects, []);
  }
});

test('cancelled desktop results cannot overwrite a newer sign-in', async () => {
  const attempts = [deferred(), deferred()];
  let starts = 0;
  let cancellations = 0;
  const page = await renderLogin('/login', () => { throw new Error('Unexpected web auth request'); }, { desktop: {
    googleSignIn: () => attempts[starts++].promise,
    cancelGoogleSignIn: async () => { cancellations++; },
  } });
  await act(async () => { googleButton(page.view).props.onClick(); });
  await act(async () => { cancelGoogleButton(page.view).props.onClick(); });
  assert.equal(cancellations, 1);
  assert.equal(googleButton(page.view).props.disabled, false);
  assert.match(page.view.root.findByProps({ role: 'alert' }).children.join(''), /取消/);
  await act(async () => { googleButton(page.view).props.onClick(); });
  await act(async () => { attempts[1].resolve({ access_token: 'current-token', next: '/projects/current', linked: false }); });
  await act(async () => { attempts[0].resolve({ access_token: 'cancelled-token', next: '/projects/stale', linked: false }); });
  assert.equal(localStorage.getItem('inspiration_token'), 'current-token');
  assert.equal(page.destination, '/projects/current');
  assert.equal(page.requests.filter(request => request.url.endsWith('/me')).length, 1);
});

test('cancelling while desktop profile confirmation is pending removes that session and ignores its late reply', async () => {
  const oldProfile = deferred();
  let profiles = 0;
  let starts = 0;
  const page = await renderLogin('/login', () => { throw new Error('Unexpected web auth request'); }, {
    desktop: {
      googleSignIn: async () => ({ access_token: `desktop-token-${++starts}`, next: '/projects', linked: false }),
      cancelGoogleSignIn: async () => {},
    },
    me: () => ++profiles === 1 ? oldProfile.promise : response(signedIn),
  });
  await act(async () => { googleButton(page.view).props.onClick(); });
  assert.equal(localStorage.getItem('inspiration_token'), 'desktop-token-1');
  await act(async () => { cancelGoogleButton(page.view).props.onClick(); });
  assert.equal(localStorage.getItem('inspiration_token'), null);
  await act(async () => { googleButton(page.view).props.onClick(); });
  assert.equal(localStorage.getItem('inspiration_token'), 'desktop-token-2');
  await act(async () => { oldProfile.resolve(response({ error: { code: 'UNAUTHORIZED', message: 'Late reply' } }, 401)); });
  assert.equal(localStorage.getItem('inspiration_token'), 'desktop-token-2');
  assert.equal(page.destination, '/projects');
});

test('desktop timeout and native failure allow a fresh retry and reject external return destinations', async () => {
  let starts = 0;
  const page = await renderLogin('/login?next=https%3A%2F%2Fevil.test', () => { throw new Error('Unexpected web auth request'); }, { desktop: {
    googleSignIn: async next => {
      assert.equal(next, '/projects');
      if (++starts === 1) return { error: 'google_desktop_timeout' };
      if (starts === 2) throw new Error('Private native error');
      return { access_token: 'retry-token', next: 'https://evil.test', linked: false };
    },
    cancelGoogleSignIn: async () => {},
  } });
  await act(async () => { googleButton(page.view).props.onClick(); });
  assert.match(page.view.root.findByProps({ role: 'alert' }).children.join(''), /超时/);
  assert.equal(googleButton(page.view).props.disabled, false);
  await act(async () => { googleButton(page.view).props.onClick(); });
  assert.match(page.view.root.findByProps({ role: 'alert' }).children.join(''), /桌面登录/);
  assert.doesNotMatch(page.view.root.findByProps({ role: 'alert' }).children.join(''), /Private/);
  await act(async () => { googleButton(page.view).props.onClick(); });
  assert.equal(starts, 3);
  assert.equal(localStorage.getItem('inspiration_token'), 'retry-token');
  assert.equal(page.destination, '/projects');
});

test('leaving the login route cancels desktop authorization and ignores the eventual result', async () => {
  const completion = deferred();
  let cancellations = 0;
  const page = await renderLogin('/login', () => { throw new Error('Unexpected web auth request'); }, { desktop: {
    googleSignIn: () => completion.promise,
    cancelGoogleSignIn: async () => { cancellations++; },
  } });
  await act(async () => { googleButton(page.view).props.onClick(); });
  await act(async () => { page.navigate('/register'); });
  assert.equal(cancellations, 1);
  assert.equal(googleButton(page.view).props.disabled, false);
  await act(async () => { completion.resolve({ access_token: 'abandoned-token', next: '/projects', linked: false }); });
  assert.equal(localStorage.getItem('inspiration_token'), null);
  assert.equal(page.destination, '/register');
  assert.equal(page.requests.filter(request => request.url.endsWith('/me')).length, 0);
});
