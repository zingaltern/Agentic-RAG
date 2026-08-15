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
const fs = require('fs')
const bootstrap = require('./bootstrap.cjs')

// 打包模式（.app/.dmg，Python 运行时暂不捆绑，§13.7）：项目根经 RAG_PROJECT_DIR 指定；
// dev 模式：项目根 = 仓库根（electron 目录上两级）。
function resolveProjectPaths() {
  if (app.isPackaged) {
    const dir = process.env.RAG_PROJECT_DIR
    if (!dir) return null
    const root = path.resolve(dir)
    return { root, python: path.join(root, '.venv', 'bin', 'python') }
  }
  const root = path.join(__dirname, '..', '..')
  return { root, python: path.join(root, '.venv', 'bin', 'python') }
}

let win = null
let backend = null
let activeUrl = null
let activeUserDataDir = ''

function shutdown() {
  bootstrap.killBackend(backend)
  backend = null
}

function registerIpc() {
  ipcMain.on('set-external-base', (_e, url) => bootstrap.writeExternalBase(activeUserDataDir, url))
  ipcMain.handle('get-external-base', () => bootstrap.readExternalBase(activeUserDataDir))
}
registerIpc()

function createSplash() {
  const splash = new BrowserWindow({
    width: 380,
    height: 220,
    frame: false,
    resizable: false,
    show: true,
    alwaysOnTop: true,
    center: true,
    webPreferences: { sandbox: true },
  })
  splash.loadURL(
    'data:text/html;charset=utf-8,' +
      encodeURIComponent(`<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  html,body{margin:0;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg,#fff7f0,#ffe9da);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#7a4a2a}
  .dot{width:9px;height:9px;border-radius:50%;background:#ff7a45;margin:0 4px;animation:blink 1.2s infinite}
  .dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
  p{margin:14px 0 0;font-size:13px;color:#a3764f}
</style></head><body>
<div style="display:flex"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
<p>正在启动知识库服务…</p>
</body></html>`),
  )
  return splash
}

async function openWindow(url, userDataDir) {
  activeUrl = url
  activeUserDataDir = userDataDir
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
  win.on('closed', () => {
    win = null
    // macOS 惯例：关窗保留 Dock 图标与后端进程，activate 时重建窗口
    if (process.platform !== 'darwin') shutdown()
  })
}

async function start() {
  const userDataDir = app.getPath('userData')

  // 外部后端模式：不本机拉起
  const external = bootstrap.readExternalBase(userDataDir)
  if (external) {
    await openWindow(external, userDataDir)
    return
  }

  const envPaths = resolveProjectPaths()
  let paths = envPaths
  if (paths === null) {
    // 打包模式一选择：优先环境变量 → 上次记住的目录 → 首次引导（文件夹选择器）
    let saved = null
    try {
      saved = bootstrap.readProjectDir(userDataDir)
    } catch {
      saved = null
    }
    if (!saved) {
      const picked = await dialog.showOpenDialog({
        title: '选择 Agentic-RAG 项目目录（含 .venv 的仓库根）',
        properties: ['openDirectory'],
        defaultPath: app.getPath('home'),
      })
      if (picked.canceled || !picked.filePaths.length) {
        app.quit()
        return
      }
      saved = picked.filePaths[0]
      if (!fs.existsSync(path.join(saved, '.venv', 'bin', 'python'))) {
        dialog.showErrorBox('目录无效', `所选目录未包含 ${path.join(saved, '.venv', 'bin', 'python')}\n\n请选择项目根再试，或在设置页配置「外部后端」。`)
        app.quit()
        return
      }
      try {
        bootstrap.writeProjectDir(userDataDir, saved)
      } catch {
        /* 记住失败不阻塞本次启动 */
      }
    }
    paths = { root: saved, python: path.join(saved, '.venv', 'bin', 'python') }
  }
  const { root: PROJECT_ROOT, python: PYTHON_BIN } = paths

  // ① 端口探测 + 拉起
  const port = await bootstrap.findFreePort()
  if (port === null) {
    dialog.showErrorBox('端口不可用', `127.0.0.1:${bootstrap.DEFAULT_PORT} 起连续 ${bootstrap.MAX_PORT_TRIES} 个端口均被占用`)
    app.quit()
    return
  }
  const backendLog = path.join(userDataDir, 'backend.log')
  backend = bootstrap.spawnBackend(PYTHON_BIN, PROJECT_ROOT, port, backendLog)
  const showBackendFailure = (title, detail) => {
    const tail = bootstrap.tailBackendLog(backendLog)
    dialog.showErrorBox(title, `${detail}\n\n——— 后端日志（最近）———\n${tail}`)
  }
  let exited = false
  backend.on('error', (err) => {
    if (!exited) { exited = true; showBackendFailure('后端启动失败', String(err)) }
    app.quit()
  })
  backend.on('exit', (code) => {
    if (code !== 0 && !exited) { exited = true; showBackendFailure('后端进程退出', `uvicorn 异常退出（code=${code}）`) }
  })

  // ② 就绪轮询（splash 提示期间等待；就绪后关闭 splash 并 loadURL）
  const splash = createSplash()
  const ready = await bootstrap.waitReady(port)
  if (!splash.isDestroyed()) splash.close()
  if (!ready) {
    showBackendFailure('后端未就绪', `${bootstrap.READY_TIMEOUT_MS / 1000}s 内 /api/health 未返回 ok（端口 ${port}）`)
    shutdown()
    app.quit()
    return
  }
  await openWindow(`http://127.0.0.1:${port}`, userDataDir)
}

app.whenReady().then(start)
app.on('window-all-closed', () => {
  // macOS 惯例：关闭全部窗口不退出应用（保留 Dock 图标），Cmd+Q 才退出
  if (process.platform !== 'darwin') { shutdown(); app.quit() }
})
app.on('activate', () => {
  if (process.platform === 'darwin' && BrowserWindow.getAllWindows().length === 0 && activeUrl) {
    openWindow(activeUrl, activeUserDataDir)
  }
})
app.on('before-quit', () => shutdown())
