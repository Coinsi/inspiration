'use strict';
// Real Electron renderer + local fixture: no production account or user data involved.
const { _electron } = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

// Deliberately leaves viewport ownership to the shipping chrome stylesheet.
// A margin on #root used to push the 100dvh body below the fixed drag strip,
// allowing focus/scroll operations to move the workbench header behind it.
const chromeLayoutFixture = `<!doctype html><meta charset="utf-8"><title>Window chrome layout fixture</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body { font: 16px system-ui; background: #202023; color: #efeff1; }
  .studio-shell { height: 100dvh; display: flex; flex-direction: column; }
  #workbench-header { height: 72px; flex: none; display: flex; align-items: center; gap: 16px; padding: 12px; background: #303036; }
  #workbench-header h1 { margin: 0; font-size: 18px; }
  input, button { font: inherit; padding: 8px; }
  #application-panel { flex: 1; min-height: 0; overflow: auto; padding: 16px; }
  #panel-content { min-height: 1800px; display: flex; flex-direction: column; align-items: flex-start; }
  #tail-focus { margin-top: auto; }
</style>
<div id="root"><div class="studio-shell">
  <header id="workbench-header"><h1>Workbench layout fixture</h1>
    <input id="header-focus" aria-label="Header search" placeholder="Header search">
    <button id="header-toolbar" type="button">Toolbar action</button><output id="toolbar-result">0</output>
  </header>
  <main id="application-panel" aria-label="Scrollable application panel"><div id="panel-content">
    <p>Content scrolls inside this panel while the workbench header stays below the native drag strip.</p>
    <button id="tail-focus" type="button">Last item in the application panel</button>
  </div></main>
</div></div>
<script>
  document.querySelector('#header-toolbar').onclick = () => {
    document.querySelector('#toolbar-result').value = String(Number(document.querySelector('#toolbar-result').value) + 1);
    document.querySelector('#header-focus').focus();
  };
</script>`;

async function checkWindowChromeLayout(client, page, origin) {
  if (!['win32', 'darwin'].includes(process.platform)) return;
  const evidence = path.resolve(process.env.ARTIFACT_DIR || path.join(__dirname, '../test-results'));
  await fs.mkdir(evidence, { recursive: true });
  const samples = [];
  const bounds = await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].getBounds());
  const zoom = await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getZoomFactor());
  const sample = async (phase, verify = true) => {
    const layout = await page.evaluate(() => {
      const root = document.querySelector('#root');
      const strip = document.querySelector('#inspiration-window-chrome');
      const header = document.querySelector('#workbench-header');
      const panel = document.querySelector('#application-panel');
      const rect = element => { const r = element.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height }; };
      return {
        viewportHeight: innerHeight, scrollY, documentScrollTop: document.scrollingElement.scrollTop,
        documentHeight: document.scrollingElement.scrollHeight, documentClientHeight: document.scrollingElement.clientHeight,
        bodyTop: document.body.getBoundingClientRect().top, root: rect(root), strip: rect(strip), header: rect(header),
        rootScrollTop: root.scrollTop, panelScrollTop: panel.scrollTop, panelScrollHeight: panel.scrollHeight,
        panelClientHeight: panel.clientHeight, activeElement: document.activeElement.id,
      };
    });
    samples.push({ phase, ...layout });
    if (!verify) return layout;
    const diagnostic = `${phase}: ${JSON.stringify(layout)}`;
    assert.equal(layout.scrollY, 0, `The outer document must not scroll. ${diagnostic}`);
    assert.equal(layout.documentScrollTop, 0, `Document scrolling must not hide the header. ${diagnostic}`);
    assert.ok(layout.documentHeight <= layout.documentClientHeight + 1, `The outer document must fit the viewport. ${diagnostic}`);
    assert.ok(layout.root.top >= layout.strip.bottom - 1, `The app root must remain below native chrome. ${diagnostic}`);
    assert.ok(layout.header.top >= layout.strip.bottom - 1, `The workbench header must remain below native chrome. ${diagnostic}`);
    assert.ok(layout.root.bottom <= layout.viewportHeight + 1, `The app root must stay within the viewport. ${diagnostic}`);
    assert.equal(layout.rootScrollTop, 0, `Only the application panel should scroll. ${diagnostic}`);
    assert.ok(layout.panelScrollHeight > layout.panelClientHeight, `The fixture must contain real internal overflow. ${diagnostic}`);
    return layout;
  };
  const exercise = async size => {
    // Keep both geometries on failure: before the attempted document scroll and
    // afterwards, when a collapsed margin can put the header behind the strip.
    await sample(`${size}: initial`, false);
    await page.evaluate(() => window.scrollTo(0, 1_000_000));
    await sample(`${size}: window.scrollTo`);
    await page.locator('#tail-focus').focus();
    const tail = await sample(`${size}: focus tail`);
    assert.equal(tail.activeElement, 'tail-focus');
    assert.ok(tail.panelScrollTop > 0, 'Focusing the tail must scroll the application panel');
    await page.locator('#header-focus').focus();
    const header = await sample(`${size}: focus header`);
    assert.equal(header.activeElement, 'header-focus');
    assert.equal(header.panelScrollTop, tail.panelScrollTop, 'Focusing the header must preserve the panel scroll position');
    const before = Number(await page.locator('#toolbar-result').textContent());
    await page.getByRole('button', { name: 'Toolbar action', exact: true }).click();
    assert.equal(Number(await page.locator('#toolbar-result').textContent()), before + 1, 'The toolbar must remain clickable below the drag strip');
    const toolbar = await sample(`${size}: toolbar click`);
    assert.equal(toolbar.activeElement, 'header-focus');
    assert.equal(toolbar.panelScrollTop, tail.panelScrollTop, 'Toolbar actions must preserve the panel scroll position');
    await page.evaluate(() => document.querySelector('#application-panel').scrollTo(0, 0));
    assert.equal((await sample(`${size}: internal scroll reset`)).panelScrollTop, 0);
  };
  try {
    await page.goto(`${origin}/chrome-layout`);
    await page.locator('#inspiration-window-chrome').waitFor();
    await exercise('normal');
    const heightBefore = await page.evaluate(() => innerHeight);
    await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1040, 680));
    await page.waitForFunction(previous => innerHeight !== previous, heightBefore);
    await exercise('compact');
    const widthBefore = await page.evaluate(() => innerWidth);
    await client.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(0.8));
    await page.waitForFunction(previous => innerWidth !== previous, widthBefore);
    await exercise('compact at 80% zoom');
    await page.screenshot({ path: path.join(evidence, 'window-chrome-layout.png') });
    console.log('PASS: native chrome keeps the outer document and workbench header fixed through scroll, focus, toolbar actions, resizing and 80% zoom; application panel scrolling remains independent.');
  } catch (error) {
    await page.screenshot({ path: path.join(evidence, 'window-chrome-layout-failure.png') }).catch(() => {});
    throw error;
  } finally {
    await fs.writeFile(path.join(evidence, 'window-chrome-layout.json'), JSON.stringify({ platform: process.platform, samples }, null, 2));
    await client.evaluate(({ BrowserWindow }, savedZoom) => BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(savedZoom), zoom);
    await client.evaluate(({ BrowserWindow }, savedBounds) => BrowserWindow.getAllWindows()[0].setBounds(savedBounds), bounds);
  }
}

async function main() {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'inspiration-desktop-test-'));
  const server = http.createServer((req, res) => {
    if (req.url === '/api/v1/test') {
      res.setHeader('Content-Type', 'application/json'); res.end('{"ok":true}'); return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    if (new URL(req.url, 'http://localhost').pathname === '/chrome-layout') {
      res.end(chromeLayoutFixture);
      return;
    }
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
    await checkWindowChromeLayout(client, page, `http://127.0.0.1:${port}`);
    await page.goto(`http://127.0.0.1:${port}/projects`);
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
