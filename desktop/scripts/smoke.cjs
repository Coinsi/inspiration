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
    res.end('<title>Desktop fixture</title><h1>Workspace fixture</h1><input type="file"><a id="blocked" href="file:///etc/passwd">blocked</a>');
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
