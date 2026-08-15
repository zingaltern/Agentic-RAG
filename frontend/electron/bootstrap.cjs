/**
 * 桌面壳纯逻辑模块（DEVELOPMENT.md 13.7 桌面段）——端口探测/就绪轮询/进程生命周期。
 * 不依赖 electron，可用 node:test 直接单测。
 */
'use strict'

const { spawn, spawnSync } = require('child_process')
const net = require('net')
const http = require('http')
const https = require('https')
const fs = require('fs')
const path = require('path')

const DEFAULT_PORT = 8000
const MAX_PORT_TRIES = 5
const READY_TIMEOUT_MS = 60000
const PROBE_TIMEOUT_MS = 20000

function isPortInUse(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(true))
    server.once('listening', () => server.close(() => resolve(false)))
    server.listen(port, host)
  })
}

/** 端口探测：从 start 起最多试 maxTries 个，返回第一个空闲端口；全占用返回 null。 */
async function findFreePort(start = DEFAULT_PORT, maxTries = MAX_PORT_TRIES) {
  for (let i = 0; i < maxTries; i++) {
    const port = start + i
    if (!(await isPortInUse(port))) return port
  }
  return null
}

/** /api/health 响应解析：ok / degraded（模型预热中）即视为已就绪。 */
function resolveHealth(res, resolve) {
  let body = ''
  res.on('data', (d) => (body += d))
  res.on('end', () => {
    try {
      const json = JSON.parse(body)
      const status = json && json.data && json.data.status
      resolve(Boolean(json && json.code === 0 && (status === 'ok' || status === 'degraded')))
    } catch {
      resolve(false)
    }
  })
}

/** 单次 /api/health 探活：ok / degraded（模型预热中）即视为已就绪，加快窗口出现。 */
function probeHealth(port, host = '127.0.0.1', timeoutMs = PROBE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const req = http.get({ host, port, path: '/api/health', timeout: timeoutMs }, (res) => {
      resolveHealth(res, resolve)
    })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

/** 解析外部后端地址为 {protocol, host, port}；非法地址返回 null。 */
function parseExternalUrl(url) {
  try {
    const u = new URL(url)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    if (!u.hostname) return null
    return {
      protocol: u.protocol,
      host: u.hostname,
      port: u.port || (u.protocol === 'https:' ? '443' : '80'),
    }
  } catch {
    return null
  }
}

/** 按完整 URL 探活（http/https 均支持），供外部后端模式在加载页面前预检。 */
function probeHealthUrl(url, timeoutMs = PROBE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const parts = parseExternalUrl(url)
    if (!parts) { resolve(false); return }
    const mod = parts.protocol === 'https:' ? https : http
    const req = mod.get({ host: parts.host, port: parts.port, path: '/api/health', timeout: timeoutMs }, (res) => {
      resolveHealth(res, resolve)
    })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

/** 就绪轮询：≤timeoutMs 内 health=ok 返回 true。 */
async function waitReady(port, timeoutMs = READY_TIMEOUT_MS, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await probeHealth(port)) return true
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}

/** 外部后端就绪轮询（按完整 URL）。 */
async function waitReadyUrl(url, timeoutMs = READY_TIMEOUT_MS, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await probeHealthUrl(url)) return true
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}

/** 以 .venv 拉起 uvicorn（cwd=项目根，仅 127.0.0.1 监听）；日志输出到 logFile（便于诊断）。

S7（V5.1）：日志文件以 0o600 权限创建（审计日志含用户查询/回答摘要，仅本机当前用户可读）。 */
function spawnBackend(pythonBin, cwd, port, logFile = null) {
  const child = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd,
      env: { ...process.env, HF_ENDPOINT: process.env.HF_ENDPOINT || 'https://hf-mirror.com' },
      stdio: 'pipe',
    },
  )
  if (logFile) {
    fs.mkdirSync(path.dirname(logFile), { recursive: true })
    const fd = fs.openSync(logFile, 'a', 0o600)   // S7：权限收紧（rw-------）
    fs.fchmodSync(fd, 0o600)
    const sink = fs.createWriteStream(logFile, { flags: 'a', fd })
    child.stdout.pipe(sink)
    child.stderr.pipe(sink)
  }
  return child
}

/** 读取后端日志尾部（诊断用，最多返回 lastLines 行）。 */
function tailBackendLog(logFile, lastLines = 30) {
  try {
    if (!logFile || !fs.existsSync(logFile)) return '(后端日志尚不存在)'
    const text = fs.readFileSync(logFile, 'utf-8')
    const lines = text.trim().split('\n')
    return lines.slice(-lastLines).join('\n') || '(日志为空)'
  } catch (e) {
    return `(读取日志失败: ${e.message})`
  }
}

/** 级联终止后端子进程（Windows taskkill /T，其他 SIGTERM→SIGKILL 兜底）。 */
function killBackend(child) {
  if (!child || child.killed) return
  try {
    if (process.platform === 'win32' && child.pid) {
      spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'])
    } else {
      child.kill('SIGTERM')
      setTimeout(() => {
        if (child && !child.killed) child.kill('SIGKILL')
      }, 2000).unref()
    }
  } catch {
    /* ignore */
  }
}

/** 外部后端模式（userData 持久化 external_base.json，供渲染器 IPC 写入）。 */
function externalBaseFile(userDataDir) {
  return path.join(userDataDir, 'external_base.json')
}
function readExternalBase(userDataDir) {
  try {
    const p = externalBaseFile(userDataDir)
    if (!fs.existsSync(p)) return null
    const parsed = JSON.parse(fs.readFileSync(p, 'utf-8'))
    return (parsed && parsed.url) || null
  } catch {
    return null
  }
}
function writeExternalBase(userDataDir, url) {
  fs.mkdirSync(userDataDir, { recursive: true })
  fs.writeFileSync(externalBaseFile(userDataDir), JSON.stringify({ url: url || '' }))
}

/** 打包模式项目根（userData 持久化 project_dir.json：首次引导选择后记住）。 */
function projectDirFile(userDataDir) {
  return path.join(userDataDir, 'project_dir.json')
}
function readProjectDir(userDataDir) {
  try {
    const p = projectDirFile(userDataDir)
    if (!fs.existsSync(p)) return null
    const parsed = JSON.parse(fs.readFileSync(p, 'utf-8'))
    const dir = parsed && parsed.dir
    if (!dir) return null
    const py = path.join(dir, '.venv', 'bin', 'python')
    return fs.existsSync(py) ? dir : null      // 目录失效则视为未配置
  } catch {
    return null
  }
}
function writeProjectDir(userDataDir, dir) {
  fs.mkdirSync(userDataDir, { recursive: true })
  fs.writeFileSync(projectDirFile(userDataDir), JSON.stringify({ dir }))
}

module.exports = {
  DEFAULT_PORT,
  MAX_PORT_TRIES,
  READY_TIMEOUT_MS,
  isPortInUse,
  findFreePort,
  probeHealth,
  probeHealthUrl,
  parseExternalUrl,
  waitReady,
  waitReadyUrl,
  spawnBackend,
  killBackend,
  readExternalBase,
  writeExternalBase,
  readProjectDir,
  writeProjectDir,
  tailBackendLog,
}
