'use strict';

const { contextBridge, ipcRenderer } = require('electron');

if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('inspirationDesktop', {
    googleSignIn: (next, language) => ipcRenderer.invoke('inspiration:google-sign-in', next, language),
    cancelGoogleSignIn: () => ipcRenderer.invoke('inspiration:google-cancel'),
  });
}
