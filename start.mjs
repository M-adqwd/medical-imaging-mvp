/**
 * MVP 一键启动器。
 *
 * 职责：
 *   1. 自动避让被占用的端口（8000 常被其它服务占用，硬编码会导致整条链路起不来）
 *   2. 拉起 FastAPI 后端（本机 venv，缺失则自动创建并装依赖）
 *   3. 构建并静态托管前端，把 /api、/dicomweb 代理到后端
 *
 * 用法：node start.mjs [--no-build]   （--no-build 表示复用已有 web/dist）
 */
import { spawn, spawnSync } from 'node:child_process'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SERVER = path.join(__dirname, 'server')
const WEB = path.join(__dirname, 'web')
const isWin = process.platform === 'win32'
const venvPython = path.join(SERVER, isWin ? '.venv/Scripts/python.exe' : '.venv/bin/python')
const npmCmd = isWin ? 'npm.cmd' : 'npm'

const log = (...a) => console.log(...a)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** 端口是否被占用（尝试监听） */
function isFree(port) {
  return new Promise((resolve) => {
    const s = net.createServer()
    s.once('error', () => resolve(false))
    s.once('listening', () => s.close(() => resolve(true)))
    s.listen(port, '127.0.0.1')
  })
}

/** 从 start 开始找第一个空闲端口 */
async function findFreePort(start, tries = 40) {
  for (let p = start; p < start + tries; p++) {
    if (await isFree(p)) return p
  }
  throw new Error(`找不到空闲端口（已尝试 ${start}~${start + tries - 1}）`)
}

function httpOk(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('error', () => resolve(false))
    req.setTimeout(1500, () => {
      req.destroy()
      resolve(false)
    })
  })
}

async function waitHealthy(url, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await httpOk(url)) return true
    await sleep(600)
  }
  return false
}

const children = []
function cleanup() {
  for (const c of children) {
    try {
      c.kill('SIGTERM')
    } catch {
      /* 已退出 */
    }
  }
}
process.on('SIGINT', () => {
  cleanup()
  process.exit(0)
})
process.on('exit', cleanup)

function run(cmd, args, cwd, env) {
  const p = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: 'inherit', shell: isWin })
  children.push(p)
  return p
}

/** 等待进程退出，非 0 退出码视为失败 */
function waitExit(p) {
  return new Promise((resolve) => p.on('exit', (code) => resolve(code ?? 0)))
}

async function main() {
  const noBuild = process.argv.includes('--no-build')

  /* ---------- 后端 ---------- */
  if (!fs.existsSync(venvPython)) {
    log('▸ 创建 Python 虚拟环境…')
    const r = spawnSync(isWin ? 'python' : 'python3', ['-m', 'venv', '.venv'], { cwd: SERVER, stdio: 'inherit', shell: isWin })
    if (r.status !== 0) throw new Error('虚拟环境创建失败')
    log('▸ 安装后端依赖…')
    if (spawnSync(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt'], { cwd: SERVER, stdio: 'inherit', shell: isWin }).status !== 0)
      throw new Error('后端依赖安装失败')
  }

  const backendPort = await findFreePort(Number(process.env.BACKEND_PORT || 8000))
  log(`▸ 启动后端 http://127.0.0.1:${backendPort}`)
  const backend = run(venvPython, ['-u', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)], SERVER)
  backend.on('exit', (code) => log(`✖ 后端退出 (${code})`))

  if (!(await waitHealthy(`http://127.0.0.1:${backendPort}/healthz`))) throw new Error('后端启动超时')
  log('✓ 后端就绪')

  /* ---------- 前端 ---------- */
  if (!noBuild) {
    log('▸ 构建前端…')
    if (spawnSync(npmCmd, ['run', 'build'], { cwd: WEB, stdio: 'inherit', shell: isWin }).status !== 0)
      throw new Error('前端构建失败')
  }
  if (!fs.existsSync(path.join(WEB, 'dist', 'index.html'))) throw new Error('web/dist 不存在')

  const frontPort = await findFreePort(Number(process.env.PORT || 4173))
  log(`▸ 启动前端 http://127.0.0.1:${frontPort}`)
  const front = run(process.execPath, ['serve-dist.mjs'], WEB, { PORT: String(frontPort), MIIS_API: `http://127.0.0.1:${backendPort}` })
  front.on('exit', (code) => log(`✖ 前端退出 (${code})`))

  if (!(await waitHealthy(`http://127.0.0.1:${frontPort}/`, 30000))) throw new Error('前端启动超时')

  log('')
  log('  ✓ MVP 已就绪')
  log(`    阅片工作台：http://127.0.0.1:${frontPort}/`)
  log(`    后端接口：  http://127.0.0.1:${backendPort}/api/v1`)
  log(`    DICOMweb：  http://127.0.0.1:${backendPort}/dicomweb/studies`)
  log('')
  log('  按 Ctrl+C 停止')
}

main().catch((e) => {
  console.error('✖ 启动失败：', e.message)
  cleanup()
  process.exit(1)
})
