'use strict';

const fs = require('node:fs');
const path = require('node:path');

const CHROME_HEIGHT = 36;
const THEME_CHANNEL = 'inspiration:window-theme';
const WORLD_ID = 1007;
const PALETTES = Object.freeze({
  dark: Object.freeze({ color: '#28282a', symbolColor: '#efeff1' }),
  light: Object.freeze({ color: '#eff1f0', symbolColor: '#1f1f1f' }),
});
const css = fs.readFileSync(path.join(__dirname, 'window-chrome.css'), 'utf8');

function supported(platform) {
  return platform === 'win32' || platform === 'darwin';
}

/** Spread into BrowserWindow options; keep the native frame and resize behavior. */
function getWindowChromeOptions({ platform = process.platform, dark = true } = {}) {
  if (!supported(platform)) return {};
  const palette = PALETTES[dark ? 'dark' : 'light'];
  return {
    titleBarStyle: 'hidden',
    backgroundColor: palette.color,
    ...(platform === 'win32'
      ? { titleBarOverlay: { ...palette, height: CHROME_HEIGHT } }
      : { titleBarOverlay: true, trafficLightPosition: { x: 14, y: 11 } }),
  };
}

function trustedURL(raw, origin, fallback) {
  if (fallback && raw === fallback) return true;
  try {
    const url = new URL(raw);
    return url.origin === origin && !url.username && !url.password;
  } catch { return false; }
}

// Runs in a dedicated isolated world, with DOM access only and no Node/IPC bridge.
// An actual layout row keeps all application controls below the native controls.
function installChrome({ height, platform, fullscreen, recovery }) {
  const html = document.documentElement;
  html.dataset.inspirationChrome = platform;
  html.dataset.inspirationFullscreen = String(fullscreen);
  html.style.setProperty('--inspiration-chrome-height', `${height}px`);
  let strip = document.getElementById('inspiration-window-chrome');
  if (!strip) {
    strip = document.createElement('div');
    strip.id = 'inspiration-window-chrome';
    strip.setAttribute('aria-hidden', 'true');
    document.body.prepend(strip);
  }
  // The recovery document intentionally has no React root. Keep its existing
  // content together in one scrollable region without touching its reconnect URL.
  if (recovery && !document.getElementById('root') && !document.getElementById('inspiration-recovery-content')) {
    const content = document.createElement('div');
    content.id = 'inspiration-recovery-content';
    for (const node of Array.from(document.body.childNodes)) {
      if (node !== strip && !(node.nodeType === 1 && ['SCRIPT', 'STYLE'].includes(node.tagName))) {
        content.append(node);
      }
    }
    document.body.append(content);
  }
}

/**
 * Call before loadURL(). Await .ready before the first win.show() to avoid a
 * frame with unreserved controls. Preload sends only 'dark'/'light' on
 * THEME_CHANNEL after DOMContentLoaded and when the <html> class changes.
 */
function attachWindowChrome(win, { origin, fallback, ipcMain, platform = process.platform } = {}) {
  if (!supported(platform)) return { ready: Promise.resolve(), dispose() {} };
  const contents = win.webContents;
  let disposed = false;
  let generation = 0;
  let theme = 'dark';
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const current = () => !disposed && !win.isDestroyed() && !contents.isDestroyed() &&
    trustedURL(contents.getURL(), origin, fallback);

  function setTheme(value) {
    if (!PALETTES[value] || !current()) return;
    theme = value;
    win.setBackgroundColor(PALETTES[value].color);
    if (platform === 'win32') win.setTitleBarOverlay({ ...PALETTES[value], height: CHROME_HEIGHT });
  }

  async function inject() {
    if (!current()) { resolveReady(); return; }
    const revision = generation;
    try {
      // Author origin, scoped selectors: the remote web UI retains its own style
      // sheet, and only viewport allocation / desktop surface colors are adapted.
      await contents.insertCSS(css);
      if (!current() || revision !== generation) return;
      const args = { height: CHROME_HEIGHT, platform, fullscreen: win.isFullScreen(), recovery: contents.getURL() === fallback };
      await contents.executeJavaScriptInIsolatedWorld(WORLD_ID, [{
        code: `(${installChrome.toString()})(${JSON.stringify(args)});`,
      }]);
      if (current() && revision === generation) setTheme(theme);
    } catch {
      // Navigation may replace this document while injection is pending. The next
      // dom-ready installs the strip; a failed remote load still has local recovery.
    } finally {
      resolveReady();
    }
  }

  function themeMessage(event, value) {
    if (event.sender !== contents || !event.senderFrame ||
      event.senderFrame !== contents.mainFrame ||
      !trustedURL(event.senderFrame.url, origin, fallback) || !['dark', 'light'].includes(value)) return;
    setTheme(value);
  }
  function navigation({ isMainFrame, isSameDocument }) {
    if (isMainFrame && !isSameDocument) generation += 1;
  }
  function fullscreenChanged() {
    if (!current()) return;
    const value = String(win.isFullScreen());
    void contents.executeJavaScriptInIsolatedWorld(WORLD_ID, [{
      code: `document.documentElement.dataset.inspirationFullscreen = ${JSON.stringify(value)};`,
    }]).catch(() => {});
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    resolveReady();
    ipcMain?.removeListener(THEME_CHANNEL, themeMessage);
    contents.removeListener('dom-ready', inject);
    contents.removeListener('did-start-navigation', navigation);
    win.removeListener('enter-full-screen', fullscreenChanged);
    win.removeListener('leave-full-screen', fullscreenChanged);
  }

  ipcMain?.on(THEME_CHANNEL, themeMessage);
  contents.on('did-start-navigation', navigation);
  contents.on('dom-ready', inject);
  win.on('enter-full-screen', fullscreenChanged);
  win.on('leave-full-screen', fullscreenChanged);
  win.once('closed', dispose);
  return { ready, dispose };
}

module.exports = { CHROME_HEIGHT, THEME_CHANNEL, getWindowChromeOptions, attachWindowChrome };
