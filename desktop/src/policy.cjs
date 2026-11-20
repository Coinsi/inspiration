'use strict';

const CLOUD_ORIGIN = 'https://coinisi.com';

function resolveOrigin(raw = CLOUD_ORIGIN, development = false) {
  const url = new URL(raw);
  const local = development && url.protocol === 'http:' &&
    ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((!local && url.protocol !== 'https:') || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Desktop origin must be an HTTPS origin (localhost HTTP is development-only).');
  }
  return url.origin;
}

function isInternal(raw, origin) {
  try {
    const url = new URL(raw);
    return url.origin === origin && !url.username && !url.password;
  } catch { return false; }
}

function isExternal(raw) {
  try {
    const url = new URL(raw);
    return ['https:', 'http:', 'mailto:'].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; }
}

function isGoogleAuth(raw) {
  try { return new URL(raw).hostname === 'accounts.google.com'; }
  catch { return false; }
}

module.exports = { CLOUD_ORIGIN, resolveOrigin, isInternal, isExternal, isGoogleAuth };
