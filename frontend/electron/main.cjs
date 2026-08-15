/**
 * Electron 桌面壳（DEVELOPMENT.md 13.7 桌面段）——三步走：
 * ① 启动：探测端口（默认 8000，占用自动后移）→ 以项目 .venv 拉起 uvicorn（仅 127.0.0.1）；
 *    外部后端模式（userData 已配置 external_base）→ 不拉起，直接连接。
 * ② 就绪：轮询 /api/health（≤30s，带 splash）后才 loadURL；失败弹错误而非白屏。
 * ③ 退出：窗口关闭 → 级联终止后端子进程（killBackend），无残留。
 * 运行中后端进程意外退出（含被外部信号优雅终止）→ 自动重启并再次等待就绪（带 splash）；
 *    恢复失败才弹错误框，并以 app.exit() 强制退出（避免模态弹窗下 macOS 无法退出/无响应）。
 * S8 安全基线：contextIsolation=true、nodeIntegration=false、sandbox=true、
 *    仅加载 loopback/自托管地址（不加载远程内容）；真实密钥只经环境变量运行时注入。
 */
'use strict'

const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const path = require('path')
const fs = require('fs')
const bootstrap = require('./bootstrap.cjs')

const MAX_RESTART_TRIES = 2   // 运行中后端意外退出后的自动重启上限

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
let backendPort = null
let backendLog = ''
let activeUrl = null
let activeUserDataDir = ''
let projectRoot = ''
let pythonBin = ''
let backendStopping = false   // 主动关闭后端子进程的标志：其 exit 事件不计入"异常退出"
let exited = false            // 已弹错误框并进入退出流程
let restartTries = 0          // 本次会话内自动重启次数（成功就绪后清零）

function showBackendFailure(title, detail) {
  const tail = bootstrap.tailBackendLog(backendLog)
  dialog.showErrorBox(title, `${detail}\n\n——— 后端日志（最近）———\n${tail}`)
}

function shutdown() {
  backendStopping = true
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

/** 拉起本机后端并挂接 error/exit 处理；返回所用端口，null 表示端口无法获取。 */
async function launchBackend() {
  const port = backendPort ?? (await bootstrap.findFreePort())
  if (port === null) return null
  const child = bootstrap.spawnBackend(pythonBin, projectRoot, port, backendLog)
  backend = child
  backendPort = port
  child.on('error', (err) => {
    if (backendStopping || exited) return
    exited = true
    showBackendFailure('后端启动失败', String(err))
    app.exit(1)
  })
  child.on('exit', (code, signal) => onBackendExit(code, signal))
  return port
}

/**
 * 本轮后端就绪轮询（带 splash）。ready=false 时关闭 splash 并弹错强退；
 * 仅当 failTitle 为空（恢复轮询）时交由调用方决定失败行为。
 */
async function waitBackendReady(port, splash, { failTitle = '后端未就绪', } = {}) {
  const ready = await bootstrap.waitReady(port)
  if (!splash.isDestroyed()) splash.close()
  if (!ready && failTitle) {
    showBackendFailure(failTitle, `${bootstrap.READY_TIMEOUT_MS / 1000}s 内 /api/health 未返回 ok（端口 ${port}）`)
    shutdown()
    app.exit(1)
  }
  return ready
}

function onBackendExit(code, signal) {
  if (backendStopping || exited) return
  backend = null
  const detail = `uvicorn 退出（code=${code ?? 'null'}${signal ? `, signal=${signal}` : ''}）`
  // 无窗口（macOS 关窗后常驻）：不做声，下次激活时自动重启
  if (win === null) return
  // 运行中的意外退出（含被外部信号优雅终止）：自动恢复一次，恢复失败先于弹错重试
  if (restartTries < MAX_RESTART_TRIES) {
    restartTries++
    void restartBackend(detail)
    return
  }
  exited = true
  showBackendFailure('后端服务中断', `${detail}\n\n已尝试自动恢复 ${restartTries} 次仍未成功，请稍后重新打开应用。`)
  app.exit(1)
}

async function restartBackend(detail) {
  const splash = createSplash()
  try {
    const port = await launchBackend()
    if (!splash.isDestroyed()) splash.close()
    if (port === null) {
      exited = true
      showBackendFailure('后端服务中断', `${detail}\n\n服务器无可用端口，请稍后重新打开应用。`)
      app.exit(1)
      return
    }
    const ready = await waitBackendReady(port, splash, { failTitle: '' })
    if (!splash.isDestroyed()) splash.close()
    if (!ready) {
      exited = true
      showBackendFailure('后端服务中断', `${detail}\n\n自动重启后 ${bootstrap.READY_TIMEOUT_MS / 1000}s 内仍未就绪，请稍后重新打开应用。`)
      app.exit(1)
      return
    }
    restartTries = 0
  } catch (e) {
    if (!splash.isDestroyed()) splash.close()
    exited = true
    showBackendFailure('后端服务中断', `${detail}\n\n自动恢复失败：${e.message}`)
    app.exit(1)
  }
}

async function start() {
  const userDataDir = app.getPath('userData')

  // 外部后端模式：不本机拉起，但同样等待就绪（splash + /api/health 轮询），
  // 目标不可达时弹错误框而非打开白屏窗口。
  const external = bootstrap.readExternalBase(userDataDir)
  if (external) {
    const splash = createSplash()
    const ready = await bootstrap.waitReadyUrl(external)
    if (!splash.isDestroyed()) splash.close()
    if (!ready) {
      dialog.showErrorBox('外部后端不可达', `${bootstrap.READY_TIMEOUT_MS / 1000}s 内 /api/health 未返回 ok（${external}）\n\n请检查目标后端是否已启动，或在设置页修改「外部后端」地址。`)
      app.quit()
      return
    }
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
  projectRoot = paths.root
  pythonBin = paths.python
  backendLog = path.join(userDataDir, 'backend.log')

  // ① 拉起本机后端
  const port = await launchBackend()
  if (port === null) {
    dialog.showErrorBox('端口不可用', `127.0.0.1:${bootstrap.DEFAULT_PORT} 起连续 ${bootstrap.MAX_PORT_TRIES} 个端口均被占用`)
    app.quit()
    return
  }

  // ② 就绪轮询（splash 提示期间等待；就绪后关闭 splash 并 loadURL）
  const splash = createSplash()
  const ready = await waitBackendReady(port, splash)
  if (!ready) return   // waitBackendReady 已弹错并退出

  await openWindow(`http://127.0.0.1:${port}`, userDataDir)
}

app.whenReady().then(start)
app.on('window-all-closed', () => {
  // macOS 惯例：关闭全部窗口不退出应用（保留 Dock 图标），Cmd+Q 才退出
  if (process.platform !== 'darwin') { shutdown(); app.quit() }
})
app.on('activate', () => {
  // macOS 惯例：Dock 点击重建窗口；后端已退出时先自动拉起再打开页面
  if (process.platform !== 'darwin') return
  if (BrowserWindow.getAllWindows().length > 0 || !activeUrl) return
  if (backend === null && !backendStopping) {
    restartTries = 0
    void (async () => {
      const ok = await waitBackendReady(await launchBackend(), createSplash(), { failTitle: '' })
        .catch(() => false)
      if (ok) await openWindow(activeUrl, activeUserDataDir)
    })()
    return
  }
  openWindow(activeUrl, activeUserDataDir)
})
app.on('before-quit', () => shutdown())