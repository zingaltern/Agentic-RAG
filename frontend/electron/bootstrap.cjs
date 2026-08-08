/**
 * 桌面壳纯逻辑模块（DEVELOPMENT.md 13.7 桌面段）——端口探测/就绪轮询/进程生命周期。
 * 不依赖 electron，可用 node:test 直接单测。
 */
'use strict'

const { spawn, spawnSync } = require('child_process')
const net = require('net')
const http = require('http')
const fs = require('fs')
const path = require('path')

const DEFAULT_PORT = 8000
const MAX_PORT_TRIES = 5
const READY_TIMEOUT_MS = 30000
const PROBE_TIMEOUT_MS = 2000

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

/** 单次 /api/health 探活：status=ok 才算就绪。 */
function probeHealth(port, host = '127.0.0.1', timeoutMs = PROBE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const req = http.get({ host, port, path: '/api/health', timeout: timeoutMs }, (res) => {
      let body = ''
      res.on('data', (d) => (body += d))
      res.on('end', () => {
        try {
          const json = JSON.parse(body)
          resolve(Boolean(json && json.code === 0 && json.data && json.data.status === 'ok'))
        } catch {
          resolve(false)
        }
      })
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

/** 以 .venv 拉起 uvicorn（cwd=项目根，仅 127.0.0.1 监听）。 */
function spawnBackend(pythonBin, cwd, port) {
  const child = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd,
      env: { ...process.env, HF_ENDPOINT: process.env.HF_ENDPOINT || 'https://hf-mirror.com' },
      stdio: 'pipe',
    },
  )
  return child
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

module.exports = {
  DEFAULT_PORT,
  MAX_PORT_TRIES,
  READY_TIMEOUT_MS,
  isPortInUse,
  findFreePort,
  probeHealth,
  waitReady,
  spawnBackend,
  killBackend,
  readExternalBase,
  writeExternalBase,
}
