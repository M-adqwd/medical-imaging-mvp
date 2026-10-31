/**
 * 视口截图：canvas → 带信息条的 PNG。
 *
 * 为什么不用「右键另存画布」了事：一张脱离上下文的截图在会诊和报告里是危险的
 * ——看不出患者、序列、窗位，也没法证明它是哪一次阅片。这里把标题、患者、
 * 序列、时间戳一并烧进像素，图片离开系统后仍然能自证来源。
 *
 * 只做 canvas 层的合成：视口上的方位标（R/A/S）、测量数值是 DOM 叠加层，
 * 不在 canvas 里，因此不会被截进来 —— 这也是有意的，避免把 DOM 的排版
 * 依赖带进导出图；文字信息统一走信息条。
 */

const PAD = 14
const GAP = 10
const HEADER_H = 52
const LABEL_H = 24
const FOOTER_H = 30
const BG = '#05090b'
const FG = '#e8f1f5'
const MUTED = '#8fa6b2'

/** 纯计算：网格尺寸与每格位置。抽出来是为了能在 node --test 里直接断言。 */
export function gridLayout(count, cellW, cellH, { columns = 1, header = true, footer = true } = {}) {
  const n = Math.max(1, Math.floor(count) || 1)
  const cols = Math.max(1, Math.min(Math.floor(columns) || 1, n))
  const rows = Math.ceil(n / cols)
  const headerH = header ? HEADER_H : 0
  const footerH = footer ? FOOTER_H : 0

  const cells = []
  for (let i = 0; i < n; i += 1) {
    const r = Math.floor(i / cols)
    const c = i % cols
    cells.push({
      x: PAD + c * (cellW + GAP),
      y: headerH + PAD + r * (cellH + LABEL_H + GAP) + LABEL_H,
      w: cellW,
      h: cellH,
    })
  }
  return {
    width: PAD * 2 + cols * cellW + (cols - 1) * GAP,
    height: headerH + footerH + PAD * 2 + rows * (cellH + LABEL_H) + (rows - 1) * GAP,
    cells,
    headerH,
    labelH: LABEL_H,
    footerH,
    pad: PAD,
  }
}

/** 文件名安全化：中文保留，路径分隔符与空白替换掉。 */
export function fileStem(parts, fallback = 'capture') {
  const raw = (Array.isArray(parts) ? parts : [parts])
    .filter(Boolean)
    .map((p) => String(p).trim())
    .filter(Boolean)
    .join('_')
  const cleaned = raw
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80)
  return cleaned || fallback
}

export function timestamp(date = new Date()) {
  const p = (n, w = 2) => String(n).padStart(w, '0')
  return (
    `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}` +
    `-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`
  )
}

function fitRect(srcW, srcH, dstW, dstH) {
  const scale = Math.min(dstW / srcW, dstH / srcH)
  const w = Math.round(srcW * scale)
  const h = Math.round(srcH * scale)
  return { x: Math.round((dstW - w) / 2), y: Math.round((dstH - h) / 2), w, h }
}

/**
 * 把若干视口拼成一张图。
 *
 * canvases 里允许出现 null（某个视口还没渲染完），会跳过而不是画一块黑；
 * 但全部为空时返回 null，由调用方明确提示"视口未就绪"，不产出空白图。
 */
export function composeViewports({ canvases, labels = [], title = '', footer = '', columns } = {}) {
  const items = (canvases || [])
    .map((canvas, i) => ({ canvas, label: labels[i] || '' }))
    .filter((it) => it.canvas && it.canvas.width > 0 && it.canvas.height > 0)
  if (items.length === 0) return null

  const cellW = Math.max(...items.map((it) => it.canvas.width))
  const cellH = Math.max(...items.map((it) => it.canvas.height))
  const cols = columns || (items.length === 1 ? 1 : items.length)
  const layout = gridLayout(items.length, cellW, cellH, { columns: cols })

  const out = document.createElement('canvas')
  out.width = layout.width
  out.height = layout.height
  const ctx = out.getContext('2d')
  if (!ctx) return null

  ctx.fillStyle = BG
  ctx.fillRect(0, 0, out.width, out.height)

  const scale = Math.max(1, cellW / 640)
  const f = (px) => `${Math.round(px * scale)}px "Segoe UI", "Microsoft YaHei", system-ui, sans-serif`

  if (title) {
    ctx.fillStyle = FG
    ctx.font = `600 ${f(17)}`
    ctx.textBaseline = 'top'
    ctx.fillText(title, PAD, PAD - 2)
  }
  const stamp = new Date().toLocaleString('zh-CN', { hour12: false })
  ctx.fillStyle = MUTED
  ctx.font = f(12)
  ctx.textAlign = 'right'
  ctx.fillText(stamp, out.width - PAD, PAD)
  ctx.textAlign = 'left'

  items.forEach((it, i) => {
    const cell = layout.cells[i]
    if (it.label) {
      ctx.fillStyle = MUTED
      ctx.font = f(13)
      ctx.textBaseline = 'alphabetic'
      ctx.fillText(it.label, cell.x, cell.y - 7)
    }
    // 视口背景本身就是深色，直接平铺即可；尺寸不一致时居中留边
    const r = fitRect(it.canvas.width, it.canvas.height, cell.w, cell.h)
    ctx.drawImage(it.canvas, cell.x + r.x, cell.y + r.y, r.w, r.h)
  })

  if (footer) {
    ctx.fillStyle = MUTED
    ctx.font = f(12)
    ctx.textBaseline = 'alphabetic'
    ctx.fillText(footer, PAD, out.height - PAD)
  }
  return out.toDataURL('image/png')
}

export function downloadDataUrl(dataUrl, filename) {
  if (!dataUrl) return false
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  return true
}

/** 从容器里找渲染画布：Cornerstone 与 three.js 都把 <canvas> 挂在容器内。 */
export function findCanvas(root) {
  if (!root) return null
  return root.querySelector('canvas')
}

/** 把截图能力封装成"点一下就下载"，返回是否成功，供调用方提示。 */
export function savePng({ canvases, labels, title, footer, columns, nameParts }) {
  const dataUrl = composeViewports({ canvases, labels, title, footer, columns })
  if (!dataUrl) return false
  return downloadDataUrl(dataUrl, `${fileStem(nameParts || [title])}_${timestamp()}.png`)
}
