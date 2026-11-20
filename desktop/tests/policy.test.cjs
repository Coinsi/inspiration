'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { resolveOrigin, isInternal, isExternal, isGoogleAuth } = require('../src/policy.cjs');

test('production accepts HTTPS origins only, development allows loopback', () => {
  assert.equal(resolveOrigin(), 'https://coinisi.com');
  assert.equal(resolveOrigin('http://127.0.0.1:5173', true), 'http://127.0.0.1:5173');
  for (const url of ['http://coinisi.com', 'http://127.0.0.1:5173', 'file:///tmp/app', 'https://user@coinisi.com', 'https://coinisi.com/login', 'https://coinisi.com/?token=secret']) {
    assert.throws(() => resolveOrigin(url));
  }
});
test('origin restrictions reject lookalikes and credential-bearing links', () => {
  const origin = resolveOrigin();
  assert.equal(isInternal(`${origin}/projects?id=1`, origin), true);
  for (const url of ['https://coinisi.com.evil.test/', 'https://coinisi.com@evil.test/', 'https://user@coinisi.com/', 'http://coinisi.com/', 'https://coinisi.com:444/', 'javascript:alert(1)', 'file:///C:/Windows/']) {
    assert.equal(isInternal(url, origin), false);
  }
});
test('only web and email links may reach the external-link confirmation', () => {
  for (const url of ['https://example.com/', 'http://localhost:3000/', 'mailto:hello@example.com']) assert.equal(isExternal(url), true);
  for (const url of ['file:///tmp/a', 'javascript:alert(1)', 'powershell:payload', 'ms-settings:windowsupdate', 'data:text/html,hi', 'https://u:p@example.com']) assert.equal(isExternal(url), false);
  assert.equal(isGoogleAuth('https://accounts.google.com/o/oauth2/v2/auth'), true);
  assert.equal(isGoogleAuth('https://accounts.google.com.evil.test/'), false);
});
