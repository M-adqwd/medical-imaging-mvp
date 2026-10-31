/**
 * 生产构建本地预览：静态托管 dist，并把 /api、/dicomweb、/fhir 代理到后端。
 * 用法：MIIS_API=http://127.0.0.1:8000 PORT=4173 node serve-dist.mjs
 * 注意：start.mjs 会自动探测空闲端口并通过 MIIS_API 传入；手动启动时默认 8000。
 */
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.join(__dirname, 'dist')
const API = process.env.MIIS_API || 'http://127.0.0.1:8000'
const PORT = Number(process.env.PORT || 4173)

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}

function proxy(req, res) {
  const target = new URL(API + req.url)
  const upstream = http.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers)
      up.pipe(res)
    },
  )
  upstream.on('error', (e) => {
    res.writeHead(502, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: { code: 'UPSTREAM_ERROR', message: e.message } }))
  })
  req.pipe(upstream)
}

http
  .createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || '/').split('?')[0])
    if (urlPath.startsWith('/api/') || urlPath.startsWith('/dicomweb') || urlPath.startsWith('/fhir'))
      return proxy(req, res)

    let file = path.join(DIST, urlPath)
    if (!file.startsWith(DIST)) {
      res.writeHead(403).end()
      return
    }
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html')
    const type = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream'
    res.writeHead(200, { 'content-type': type })
    fs.createReadStream(file).pipe(res)
  })
  .listen(PORT, '127.0.0.1', () => {
    console.log(`preview http://127.0.0.1:${PORT}  →  api ${API}`)
    // 启动即探测一次后端，避免"页面能开、影像加载不出来"时无从判断是代理目标写错了
    const probe = http.get(`${API}/healthz`, { timeout: 2500 }, (r) => {
      r.resume()
      if (r.statusCode !== 200) console.warn(`[warn] ${API}/healthz 返回 ${r.statusCode}，接口可能不可用`)
    })
    probe.on('timeout', () => probe.destroy(new Error('timeout')))
    probe.on('error', (e) =>
      console.warn(`[warn] 无法连接后端 ${API}（${e.message}）。请确认后端已启动，或用 MIIS_API 指定正确端口。`),
    )
  })
