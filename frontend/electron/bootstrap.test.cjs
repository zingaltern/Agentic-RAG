/**
 * bootstrap 纯逻辑单测（node:test 内建，无第三方框架）。
 * 运行：cd frontend && node --test electron/*.test.js
 */
'use strict'

const test = require('node:test')
const assert = require('node:assert')
const net = require('net')
const http = require('http')
const fs = require('fs')
const os = require('os')
const path = require('path')
const bootstrap = require('./bootstrap.cjs')

function startTcpServer(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(port, '127.0.0.1', () => resolve(server))
    server.on('error', reject)
  })
}

test('findFreePort 返回空闲端口', async () => {
  const server = await startTcpServer(0)
  const usedPort = server.address().port
  const free = await bootstrap.findFreePort(usedPort)
  assert.notEqual(free, usedPort)
  server.close()
})

test('findFreePort 全占用返回 null', async () => {
  const servers = []
  try {
    for (let i = 0; i < 3; i++) {
      const s = await startTcpServer(0)
      servers.push(s)
    }
    const base = servers[0].address().port
    const free = await bootstrap.findFreePort(base, servers.length)
    assert.strictEqual(free, null)
  } finally {
    servers.forEach((s) => s.close())
  }
})

test('isPortInUse 检测占用/空闲', async () => {
  const server = await startTcpServer(0)
  const port = server.address().port
  assert.strictEqual(await bootstrap.isPortInUse(port), true)
  server.close()
  await new Promise((r) => setTimeout(r, 50))
  assert.strictEqual(await bootstrap.isPortInUse(port), false)
})

test('probeHealth 对 ok 返回 true、对错误返回 false', async () => {
  const server = http.createServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({ code: 0, data: { status: 'ok' } }))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  assert.strictEqual(await bootstrap.probeHealth(port), true)
  server.close()

  assert.strictEqual(await bootstrap.probeHealth(1), false, '无服务端口应返回 false')
})

test('parseExternalUrl 解析/拒绝', () => {
  assert.deepStrictEqual(bootstrap.parseExternalUrl('http://127.0.0.1:9000'), {
    protocol: 'http:', host: '127.0.0.1', port: '9000',
  })
  assert.deepStrictEqual(bootstrap.parseExternalUrl('https://kb.example.com'), {
    protocol: 'https:', host: 'kb.example.com', port: '443',
  }, 'https 合法，端口回退 443')
  assert.strictEqual(bootstrap.parseExternalUrl('ftp://127.0.0.1:21'), null, '非 http(s) 协议拒绝')
  assert.strictEqual(bootstrap.parseExternalUrl('not a url'), null, '非法地址拒绝')
})

test('probeHealthUrl 对 ok 返回 true、不可达/非法返回 false', async () => {
  const server = http.createServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({ code: 0, data: { status: 'ok' } }))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  assert.strictEqual(await bootstrap.probeHealthUrl(`http://127.0.0.1:${port}`), true)
  server.close()

  assert.strictEqual(await bootstrap.probeHealthUrl(`http://127.0.0.1:1`), false, '不可达端口应返回 false')
  assert.strictEqual(await bootstrap.probeHealthUrl('ftp://127.0.0.1:21'), false, '非法协议应返回 false')
})

test('waitReadyUrl 超时返回 false', async () => {
  const ok = await bootstrap.waitReadyUrl('http://127.0.0.1:1', 1500, 200)
  assert.strictEqual(ok, false)
})

test('waitReady 超时返回 false', async () => {
  const ok = await bootstrap.waitReady(1, 1500, 200)
  assert.strictEqual(ok, false)
})

test('spawnBackend 拉起并可由 killBackend 终止（无残留）', async () => {
  const fakeBin = path.join(__dirname, 'fixtures', 'fake-backend.cjs')
  const child = bootstrap.spawnBackend(fakeBin, path.join(__dirname, 'fixtures'), 18999)
  assert.ok(child.pid)
  // 子进程写 pid 文件表示已起
  const pidFile = path.join(__dirname, 'fixtures', 'fake.pid')
  const deadline = Date.now() + 5000
  while (Date.now() < deadline && !fs.existsSync(pidFile)) {
    await new Promise((r) => setTimeout(r, 50))
  }
  assert.ok(fs.existsSync(pidFile), '子进程应启动')
  bootstrap.killBackend(child)
  await new Promise((r) => setTimeout(r, 300))
  assert.ok(child.killed, 'killBackend 后子进程应终止')
  fs.rmSync(pidFile, { force: true })
})

test('readExternalBase/writeExternalBase 往返', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rag-desktop-'))
  assert.strictEqual(bootstrap.readExternalBase(dir), null)
  bootstrap.writeExternalBase(dir, 'http://127.0.0.1:9000')
  assert.strictEqual(bootstrap.readExternalBase(dir), 'http://127.0.0.1:9000')
  bootstrap.writeExternalBase(dir, '')
  assert.strictEqual(bootstrap.readExternalBase(dir), null, '清空后视为未配置')
  fs.rmSync(dir, { recursive: true, force: true })
})

test('S7 后端日志文件权限为 0o600', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rag-logperm-'))
  const logFile = path.join(dir, 'backend.log')
  const fakeBin = path.join(__dirname, 'fixtures', 'fake-backend.cjs')
  const child = bootstrap.spawnBackend(fakeBin, path.join(__dirname, 'fixtures'), 18998, logFile)
  const pidFile = path.join(__dirname, 'fixtures', 'fake.pid')
  const deadline = Date.now() + 5000
  while (Date.now() < deadline && !fs.existsSync(pidFile)) {
    await new Promise((r) => setTimeout(r, 50))
  }
  assert.ok(fs.existsSync(logFile), '日志文件应被创建')
  const mode = fs.statSync(logFile).mode & 0o777
  assert.strictEqual(mode, 0o600, '审计日志（含用户查询/回答摘要）权限必须为 0o600')
  bootstrap.killBackend(child)
  fs.rmSync(pidFile, { force: true })
  fs.rmSync(dir, { recursive: true, force: true })
})
