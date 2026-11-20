'use strict';

const { contextBridge, ipcRenderer } = require('electron');

if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('inspirationDesktop', {
    googleSignIn: (next, language) => ipcRenderer.invoke('inspiration:google-sign-in', next, language),
    cancelGoogleSignIn: () => ipcRenderer.invoke('inspiration:google-cancel'),
  });

  window.addEventListener('DOMContentLoaded', () => {
    const reportTheme = () => ipcRenderer.send('inspiration:window-theme',
      document.documentElement.classList.contains('light') ? 'light' : 'dark');
    reportTheme();
    new MutationObserver(reportTheme).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
  }, { once: true });
}
