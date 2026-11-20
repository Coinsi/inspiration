'use strict';

const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { CLOUD_ORIGIN, resolveOrigin, isInternal, isExternal, isGoogleAuth } = require('./policy.cjs');
const { googleSignIn } = require('./google-sign-in.cjs');

// Packaged apps always use the production origin.
const origin = resolveOrigin(app.isPackaged ? CLOUD_ORIGIN : process.env.INSPIRATION_DESKTOP_ORIGIN, !app.isPackaged);
const home = `${origin}/projects`;
const fallback = pathToFileURL(path.join(__dirname, 'offline.html')).href;
let mainWindow;
let googleNoticeOpen = false;
let googleAttempt;

function loadHome() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  void mainWindow.loadURL(home).catch(() => {}); // did-fail-load renders the recovery page.
}

function isSignInPage(url) {
  return isInternal(url, origin) && ['/login', '/register'].includes(new URL(url).pathname);
}

function isSignInSender(event) {
  const contents = mainWindow?.webContents;
  return contents && contents === event.sender && event.senderFrame &&
    event.senderFrame === contents.mainFrame && isSignInPage(event.senderFrame.url);
}

function installGoogleSignIn() {
  ipcMain.handle('inspiration:google-sign-in', async (event, next, language) => {
    if (!isSignInSender(event) || typeof next !== 'string' || next.length > 2048 ||
      !['zh', 'en'].includes(language)) return { error: 'google_desktop_unavailable' };
    if (googleAttempt && !googleAttempt.signal.aborted) return { error: 'google_desktop_unavailable' };

    const attempt = new AbortController();
    googleAttempt = attempt;
    try {
      const result = await googleSignIn({
        origin, next, language, signal: attempt.signal,
        openBrowser: url => shell.openExternal(url),
      });
      if (attempt.signal.aborted || googleAttempt !== attempt || !isSignInSender(event)) {
        return { error: 'google_cancelled' };
      }
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible() && (app.isPackaged || process.env.INSPIRATION_DESKTOP_SMOKE !== '1')) mainWindow.show();
      if (process.platform === 'darwin' && !result.error) app.focus({ steal: true });
      mainWindow.focus();
      return result;
    } finally {
      if (googleAttempt === attempt) googleAttempt = undefined;
    }
  });
  ipcMain.handle('inspiration:google-cancel', event => {
    if (isSignInSender(event)) googleAttempt?.abort();
  });
}

async function handleExternal(url) {
  if (isGoogleAuth(url)) {
    if (googleNoticeOpen) return;
    googleNoticeOpen = true;
    try {
      const { response } = await dialog.showMessageBox(mainWindow, {
        type: 'info', title: 'Google 登录',
        message: '此页面的 Google 操作需要通过网页版完成。',
        detail: '若要登录桌面客户端，请从客户端登录页发起 Google 登录。账户绑定可在网页版的账户安全中完成。',
        buttons: ['返回客户端', '在浏览器中使用'], defaultId: 0, cancelId: 0,
      });
      if (response === 1) await shell.openExternal(`${origin}/login`);
    } finally { googleNoticeOpen = false; }
    return;
  }
  if (!isExternal(url)) return;
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: 'question', title: '打开外部链接', message: '使用系统浏览器打开此链接？',
    detail: url, buttons: ['取消', '打开'], defaultId: 0, cancelId: 0,
  });
  if (response === 1) await shell.openExternal(url);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440, height: 960, minWidth: 960, minHeight: 640,
    title: 'Inspiration', backgroundColor: '#102d27', show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      partition: 'persist:inspiration',
      nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false,
      navigateOnDragDrop: false,
    },
  });
  const win = mainWindow;
  const contents = win.webContents;
  const ses = contents.session;
  ses.setPermissionRequestHandler((_wc, permission, callback, details) => {
    callback(permission === 'fullscreen' && isInternal(details.requestingUrl, origin));
  });
  ses.setPermissionCheckHandler((_wc, permission, requestingOrigin) =>
    permission === 'fullscreen' && isInternal(requestingOrigin, origin));
  contents.on('will-attach-webview', event => event.preventDefault());
  const guard = (event, url) => {
    if (isInternal(url, origin)) return;
    event.preventDefault();
    if (url === 'https://desktop-retry.invalid/' && contents.getURL() === fallback) loadHome();
    else void handleExternal(url).catch(() => {});
  };
  contents.on('will-navigate', guard);
  contents.on('will-redirect', guard);
  contents.on('did-start-navigation', ({ isSameDocument, isMainFrame }) => {
    if (isMainFrame && !isSameDocument) googleAttempt?.abort();
  });
  contents.on('did-navigate-in-page', (_event, url, isMainFrame) => {
    if (isMainFrame && !isSignInPage(url)) googleAttempt?.abort();
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (isInternal(url, origin)) void contents.loadURL(url).catch(() => {});
    else void handleExternal(url).catch(() => {});
    return { action: 'deny' };
  });
  contents.on('did-fail-load', (_event, code, _description, url, isMainFrame) => {
    if (!isMainFrame || code === -3 || !isInternal(url, origin)) return;
    void win.loadFile(path.join(__dirname, 'offline.html')).catch(() => {});
  });
  contents.on('render-process-gone', () => {
    googleAttempt?.abort();
    void win.loadFile(path.join(__dirname, 'offline.html')).catch(() => {});
  });
  win.once('ready-to-show', () => {
    if (!(process.env.INSPIRATION_DESKTOP_SMOKE === '1' && !app.isPackaged)) win.show();
  });
  win.on('closed', () => { googleAttempt?.abort(); mainWindow = null; });
  // Chromium handles file inputs and blob downloads, including authenticated exports.
  // Always ask for a destination, never execute a downloaded file automatically.
  ses.removeAllListeners('will-download');
  ses.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ title: '保存文件', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
    item.once('done', (_event, state) => {
      if (state === 'interrupted' && mainWindow && !mainWindow.isDestroyed()) {
        void dialog.showMessageBox(mainWindow, { type: 'error', message: '下载中断，请检查网络后重试。' });
      }
    });
  });
  loadHome();
}

function installMenu() {
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: '文件', submenu: [{ label: '打开工作台', click: loadHome }, { type: 'separator' }, { role: process.platform === 'darwin' ? 'close' : 'quit' }] },
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '视图', submenu: [
      { label: '刷新', accelerator: 'CmdOrCtrl+R', click: () => {
        if (!mainWindow) return;
        if (isInternal(mainWindow.webContents.getURL(), origin)) mainWindow.reload(); else loadHome();
      } },
      { label: '返回', accelerator: 'Alt+Left', click: () => {
        const history = mainWindow?.webContents.navigationHistory;
        if (history?.canGoBack()) history.goBack();
      } },
      { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' },
    ] },
    { label: '帮助', submenu: [
      { label: '下载安装包', click: () => shell.openExternal('https://github.com/Coinsi/inspiration/releases') },
      { label: '关于 Inspiration', click: () => dialog.showMessageBox(mainWindow, {
        message: `Inspiration ${app.getVersion()}`,
        detail: '云端桌面客户端。需联网使用，支持邮箱密码和浏览器授权的 Google 登录。',
      }) },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show(); mainWindow.focus();
  });
  app.whenReady().then(() => {
    app.setAppUserModelId('com.coinisi.inspiration');
    installGoogleSignIn(); installMenu(); createWindow();
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
}
