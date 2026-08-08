#!/usr/bin/env node
'use strict'
// 桌面壳单测用假后端：启动后写 pid 文件，等待被杀（忽略 -m uvicorn 等 argv）。
const fs = require('fs')
const path = require('path')
fs.writeFileSync(path.join(__dirname, 'fake.pid'), String(process.pid))
setInterval(() => {}, 1000)
