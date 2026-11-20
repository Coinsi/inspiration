'use strict';
// Real Electron renderer + local fixture: no production account or user data involved.
const { _electron } = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'inspiration-desktop-test-'));
  const server = http.createServer((req, res) => {
    if (req.url === '/api/v1/test') {
      res.setHeader('Content-Type', 'application/json'); res.end('{"ok":true}'); return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    if (new URL(req.url, 'http://localhost').pathname === '/login') {
      res.end(`<title>Legacy login</title><div id="root"><button id="google">Google</button></div>
        <script>
          document.querySelector('#google').onclick = (event) => {
            event.currentTarget.disabled = true;
            location.assign('https://accounts.google.com/o/oauth2/v2/auth');
          };
        </script>`);
      return;
    }
    res.end('<title>Desktop fixture</title><div id="root"><h1>Workspace fixture</h1><input type="file"><a id="blocked" href="file:///etc/passwd">blocked</a></div>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  let client;
  try {
    const launchEnv = { ...process.env, INSPIRATION_DESKTOP_ORIGIN: `http://127.0.0.1:${port}`, INSPIRATION_DESKTOP_SMOKE: '1' };
    delete launchEnv.ELECTRON_RUN_AS_NODE;
    client = await _electron.launch({
      args: [path.join(__dirname, '..'), `--user-data-dir=${profile}`],
      env: launchEnv,
    });
    const page = await client.firstWindow();
    await page.waitForSelector('h1');
    assert.equal(await page.title(), 'Desktop fixture');
    assert.deepEqual(await page.evaluate(() => ({ node: typeof window.require, process: typeof window.process })), { node: 'undefined', process: 'undefined' });
    assert.equal(await page.evaluate(async () => (await (await fetch('/api/v1/test')).json()).ok), true);
    const prefs = await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
    assert.equal(prefs.sandbox, true); assert.equal(prefs.contextIsolation, true); assert.equal(prefs.nodeIntegration, false);
    if (process.platform === 'win32') {
      const menu = await client.evaluate(({ BrowserWindow, Menu }) => {
        const win = BrowserWindow.getAllWindows()[0];
        return {
          visible: win.isMenuBarVisible(),
          available: Menu.getApplicationMenu()?.items.some(item =>
            item.submenu?.items.some(child => child.label === '检查更新…')),
        };
      });
      assert.deepEqual(menu, { visible: false, available: true });
      const strip = page.locator('#inspiration-window-chrome');
      await strip.waitFor();
      const header = await strip.evaluate(element => ({
        height: element.getBoundingClientRect().height,
        overlay: navigator.windowControlsOverlay?.getTitlebarAreaRect().height ?? 0,
        drag: getComputedStyle(element).getPropertyValue('app-region'),
      }));
      assert.ok(header.height > 0 && header.height >= header.overlay - 1, JSON.stringify(header));
      assert.equal(header.drag, 'drag');
      console.log('PASS: Windows native controls are available without the system title and menu rows.');
    }
    await page.evaluate(() => localStorage.setItem('smoke-token', 'fixture'));
    await page.reload();
    assert.equal(await page.evaluate(() => localStorage.getItem('smoke-token')), 'fixture');
    const upload = path.join(profile, 'upload.txt');
    await fs.writeFile(upload, 'upload fixture');
    await page.locator('input').setInputFiles(upload);
    assert.equal(await page.locator('input').evaluate(el => el.files[0].name), 'upload.txt');
    await page.locator('#blocked').click();
    assert.equal(await page.title(), 'Desktop fixture');
    // Exercise the real blob-download pipeline without displaying a native save dialog.
    const saved = path.join(profile, 'export.txt');
    await client.evaluate(({ BrowserWindow }, savePath) => {
      const ses = BrowserWindow.getAllWindows()[0].webContents.session;
      globalThis.downloadResult = new Promise(resolve => ses.once('will-download', (_event, item) => {
        item.setSavePath(savePath); item.once('done', (_event, state) => resolve(state));
      }));
    }, saved);
    await page.evaluate(() => {
      const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['export fixture'])); a.download = 'export.txt'; a.click();
    });
    assert.equal(await client.evaluate(() => globalThis.downloadResult), 'completed');
    assert.equal(await fs.readFile(saved, 'utf8'), 'export fixture');
    await client.evaluate(({ dialog }) => {
      globalThis.realMessageBox = dialog.showMessageBox;
      dialog.showMessageBox = async (_window, options) => {
        globalThis.legacyGoogleNotice = options.message;
        return { response: 0 };
      };
    });
    const legacyLogin = `http://127.0.0.1:${port}/login?next=%2Fprojects`;
    await page.route('https://accounts.google.com/**', route => route.abort());
    await page.goto(legacyLogin);
    await Promise.all([page.waitForEvent('load'), page.getByRole('button', { name: 'Google' }).click()]);
    assert.equal(page.url(), legacyLogin);
    assert.equal(await page.getByRole('button', { name: 'Google' }).isEnabled(), true);
    assert.match(await client.evaluate(() => globalThis.legacyGoogleNotice), /尚未启用桌面 Google 登录/);
    await client.evaluate(({ dialog }) => { dialog.showMessageBox = globalThis.realMessageBox; });
    console.log('PASS: legacy Google navigation explains the server requirement and resets the login form.');
    const updateFilename = `Inspiration-9.0.0-${process.platform === 'win32' ? 'win' : 'mac'}-${process.arch}.${process.platform === 'win32' ? 'exe' : 'dmg'}`;
    const updateURL = `https://github.com/Coinsi/inspiration/releases/download/desktop-v9.0.0/${updateFilename}`;
    if (['win32', 'darwin'].includes(process.platform)) {
      const update = await client.evaluate(async ({ dialog, Menu, shell }, { updateFilename, updateURL }) => {
        const original = { fetch: globalThis.fetch, dialog: dialog.showMessageBox, open: shell.openExternal };
        try {
          let prompt;
          const selected = new Promise(resolve => { shell.openExternal = async url => resolve(url); });
          dialog.showMessageBox = async (_window, options) => { prompt = options; return { response: 0 }; };
          globalThis.fetch = async () => ({ ok: true, json: async () => [{
            tag_name: 'desktop-v9.0.0', draft: false, prerelease: false,
            assets: [{ name: updateFilename, browser_download_url: updateURL, state: 'uploaded', size: 123 }],
          }] });
          const command = Menu.getApplicationMenu().items.find(item => item.label === '帮助')
            .submenu.items.find(item => item.label === '检查更新…');
          command.click();
          const url = await Promise.race([selected, new Promise(resolve => setTimeout(resolve, 3_000))]);
          return { url, message: prompt?.message, button: prompt?.buttons?.[0] };
        } finally {
          globalThis.fetch = original.fetch; dialog.showMessageBox = original.dialog; shell.openExternal = original.open;
        }
      }, { updateFilename, updateURL });
      assert.deepEqual(update, { url: updateURL, message: 'Inspiration 9.0.0 已发布', button: '下载更新' });
      console.log('PASS: checking for updates offers the official installer after confirmation.');
    }
    await page.goto(`http://127.0.0.1:${port}/projects`);
    await new Promise(resolve => server.close(resolve));
    await page.reload().catch(() => {});
    await page.waitForURL('**/offline.html');
    assert.match(await page.locator('h1').innerText(), /暂时无法打开/);
    await fs.mkdir(path.join(__dirname, '../test-results'), { recursive: true });
    await page.screenshot({ path: path.join(__dirname, '../test-results/offline.png') });
    await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
    await page.getByRole('link', { name: '重新连接' }).click();
    await page.waitForURL(`http://127.0.0.1:${port}/projects`);
    assert.equal(await page.title(), 'Desktop fixture');
    console.log('PASS: sandbox, cloud-relative API, session storage, upload, blocked navigation, blob export, offline recovery.');
  } finally {
    if (client) await client.close();
    if (server.listening) await new Promise(resolve => server.close(resolve));
    await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
