/**
 * Electron 桌面壳（DEVELOPMENT.md 13.7 桌面段）——三步走：
 * ① 启动：探测端口（默认 8000，占用自动后移）→ 以项目 .venv 拉起 uvicorn（仅 127.0.0.1）；
 *    外部后端模式（userData 已配置 external_base）→ 不拉起，直接连接。
 * ② 就绪：轮询 /api/health（≤30s，带 splash）后才 loadURL；失败弹错误而非白屏。
 * ③ 退出：窗口关闭 → 级联终止后端子进程（killBackend），无残留。
 * S8 安全基线：contextIsolation=true、nodeIntegration=false、sandbox=true、
 *    仅加载 loopback/自托管地址（不加载远程内容）；真实密钥只经环境变量运行时注入。
 */
'use strict'

const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const path = require('path')
const bootstrap = require('./bootstrap.cjs')

const PROJECT_ROOT = path.join(__dirname, '..', '..')        // 项目根（cwd 用）
const PYTHON_BIN = path.join(PROJECT_ROOT, '.venv', 'bin', 'python')

let win = null
let backend = null

function shutdown() {
  bootstrap.killBackend(backend)
  backend = null
}

async function openWindow(url, userDataDir) {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'Agentic RAG 智能知识库',
    show: false,                                // 就绪后再显示（避免白屏）
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,                   // S8
      nodeIntegration: false,                   // S8
      sandbox: true,
    },
  })
  win.once('ready-to-show', () => win.show())
  await win.loadURL(url)                        // S8：仅 loopback/自托管地址
  win.on('closed', () => { win = null; shutdown() })
  ipcMain.on('set-external-base', (_e, url) => bootstrap.writeExternalBase(userDataDir, url))
  ipcMain.handle('get-external-base', () => bootstrap.readExternalBase(userDataDir))
}

async function start() {
  const userDataDir = app.getPath('userData')

  // 外部后端模式：不本机拉起
  const external = bootstrap.readExternalBase(userDataDir)
  if (external) {
    await openWindow(external, userDataDir)
    return
  }

  // ① 端口探测 + 拉起
  const port = await bootstrap.findFreePort()
  if (port === null) {
    dialog.showErrorBox('端口不可用', `127.0.0.1:${bootstrap.DEFAULT_PORT} 起连续 ${bootstrap.MAX_PORT_TRIES} 个端口均被占用`)
    app.quit()
    return
  }
  backend = bootstrap.spawnBackend(PYTHON_BIN, PROJECT_ROOT, port)
  backend.on('error', (err) => {
    dialog.showErrorBox('后端启动失败', String(err))
    app.quit()
  })
  backend.on('exit', (code) => {
    if (win && code !== 0) {
      dialog.showErrorBox('后端进程退出', `uvicorn 异常退出（code=${code}）`)
    }
  })

  // ② 就绪轮询（带 splash 语义：窗口未显示，就绪后才 loadURL）
  const ready = await bootstrap.waitReady(port)
  if (!ready) {
    dialog.showErrorBox('后端未就绪', `30s 内 /api/health 未返回 ok（端口 ${port}）`)
    shutdown()
    app.quit()
    return
  }
  await openWindow(`http://127.0.0.1:${port}`, userDataDir)
}

app.whenReady().then(start)
app.on('window-all-closed', () => { shutdown(); app.quit() })
app.on('before-quit', () => shutdown())
