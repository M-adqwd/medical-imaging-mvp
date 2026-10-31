/**
 * 组装部署包：把「单端口能跑起来的最小集合」拷到 deploy/。
 *
 * 为什么要单独一个目录：平台会扫描待发布目录，遇到 docker-compose.yml 里的
 * PostgreSQL 或依赖清单里的 psycopg 就判定不可发布；而 server/ 同时装着测试、
 * 开发环境与 PostgreSQL 配置。发布目录只留运行时需要的东西，边界清楚。
 *
 * 用法：node scripts/build-deploy.mjs
 */
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const DEPLOY = path.join(ROOT, 'deploy')

function copyDir(src, dest) {
  cpSync(src, dest, {
    recursive: true,
    dereference: true,
    filter: (s) => {
      const base = path.basename(s)
      return base !== '__pycache__' && !base.endsWith('.pyc') && base !== '.pytest_cache'
    },
  })
}

function sizeOf(dir) {
  let total = 0
  for (const entry of readdirSync(dir)) {
    const p = path.join(dir, entry)
    const st = statSync(p)
    total += st.isDirectory() ? sizeOf(p) : st.size
  }
  return total
}

const required = ['main.py', 'requirements.txt', '.env']
for (const f of required) {
  if (!existsSync(path.join(DEPLOY, f))) throw new Error(`deploy/${f} 缺失，先补齐再打包`)
}

if (!existsSync(path.join(ROOT, 'web/dist/index.html'))) {
  throw new Error('web/dist 不存在，先在 web/ 下执行 npm run build')
}
if (!existsSync(path.join(ROOT, 'server/miis.db'))) {
  throw new Error('server/miis.db 不存在，缺少演示病例库')
}

// 1) 后端代码
rmSync(path.join(DEPLOY, 'app'), { recursive: true, force: true })
copyDir(path.join(ROOT, 'server/app'), path.join(DEPLOY, 'app'))

// 2) 前端构建产物
rmSync(path.join(DEPLOY, 'dist'), { recursive: true, force: true })
copyDir(path.join(ROOT, 'web/dist'), path.join(DEPLOY, 'dist'))

// 3) DICOM 归档（内置文件存储，QIDO/WADO 的数据源）
const dicomSrc = path.join(ROOT, 'server/data/dicom')
if (!existsSync(dicomSrc)) throw new Error('server/data/dicom 不存在，影像会全部 404')
rmSync(path.join(DEPLOY, 'data'), { recursive: true, force: true })
mkdirSync(path.join(DEPLOY, 'data/dicom'), { recursive: true })
let dicomCount = 0
for (const f of readdirSync(dicomSrc)) {
  if (!/\.(dcm|ima)$/i.test(f)) continue
  cpSync(path.join(dicomSrc, f), path.join(DEPLOY, 'data/dicom', f))
  dicomCount += 1
}

// 4) 演示库（先裁剪：原始库 220MB，分割掩膜占大头，直接传不现实）
cpSync(path.join(ROOT, 'server/miis.db'), path.join(DEPLOY, 'miis.db'))
const pyCandidates = [
  path.join(ROOT, 'server/.venv/Scripts/python.exe'),
  path.join(ROOT, 'server/.venv/bin/python'),
  'python',
  'python3',
]
let trimmed = false
for (const py of pyCandidates) {
  const r = spawnSync(py, [path.join(ROOT, 'scripts/trim_demo_db.py'), path.join(DEPLOY, 'miis.db')], {
    stdio: 'inherit',
  })
  if (r.status === 0) {
    trimmed = true
    break
  }
}
if (!trimmed) throw new Error('演示库裁剪失败，检查本机 python 是否可用')

const mb = (n) => `${(n / 1048576).toFixed(1)} MB`
console.log(`deploy 就绪：${dicomCount} 个 DICOM 实例，总计 ${mb(sizeOf(DEPLOY))}`)
for (const part of ['app', 'dist', 'data']) {
  console.log(`  ${part.padEnd(5)} ${mb(sizeOf(path.join(DEPLOY, part)))}`)
}
