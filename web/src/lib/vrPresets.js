/**
 * VR / SSR 传递函数的纯计算部分。
 *
 * 单独成文件、且不 import 任何 @cornerstonejs/* 包，是为了能在 Node 下直接
 * 跑单元测试（`node --test`）—— Cornerstone 在模块顶层就需要 window/DOM，
 * 引进来测试就跑不了。VrViewport 从这个模块取函数，预设对象本身仍从
 * core 的 CONSTANTS.VIEWPORT_PRESETS 拿。
 */

/**
 * scalarOpacity 的字符串格式是 "N x0 y0 x1 y1 …"，N 是后续数值的个数
 * （**不是点数**）。依据 core/dist/esm/utilities/applyPreset.js：
 *   preset.scalarOpacity.split(' ').splice(1) 之后按 i += 2 成对读 (value, opacity)
 */
export function parseScalarOpacity(str) {
  const values = String(str)
    .split(' ')
    .map(Number)
    .slice(1)
  const points = []
  for (let i = 0; i + 1 < values.length; i += 2) {
    points.push([values[i], values[i + 1]])
  }
  return points
}

const OPACITY_DECIMALS = 10

export function formatScalarOpacity(points) {
  // 保留 10 位而不是 4 位：预设里本来就有 7 位小数的值（如 CT-Fat 的
  // 0.0374849），截到 4 位会让 scaleOpacity(preset, 1) 不再等于原串 ——
  // 默认档位本该原样复现库的预设，而不是拿到一个四舍五入后的近似值。
  // 保留 10 位既能原样往返，又能抹掉浮点乘法产生的尾巴（0.35784300000000003）。
  const values = points.flatMap(([v, o]) => [
    Number(v.toFixed(OPACITY_DECIMALS)),
    Number(o.toFixed(OPACITY_DECIMALS)),
  ])
  return `${values.length} ${values.join(' ')}`
}

/**
 * 由 VR 预设推导 SSR（Shaded Surface Rendering，表面阴影重建）预设。
 *
 * Cornerstone 没有内置 SSR 预设 —— SSR 与 VR 的差别不在渲染管线，而在
 * 传递函数：VR 用渐变斜坡，SSR 用硬阶跃（阈值以下全透明、以上全不透明），
 * 于是体数据里只有等值面被着色，观感即表面重建。阶跃位置取在原传递函数
 * 半高处，也就是 VR 里"看起来是边界"的那个值。
 */
export function toSurfaceShaded(preset) {
  const points = parseScalarOpacity(preset.scalarOpacity)
  if (points.length < 2) return preset

  const maxOpacity = Math.max(...points.map((p) => p[1]))
  const half = maxOpacity * 0.5
  let threshold = points[points.length - 1][0]
  for (let i = 0; i + 1 < points.length; i += 1) {
    const [x1, y1] = points[i]
    const [x2, y2] = points[i + 1]
    if (y1 < half && y2 >= half) {
      const t = (half - y1) / (y2 - y1)
      threshold = x1 + t * (x2 - x1)
      break
    }
  }

  const min = points[0][0]
  const max = points[points.length - 1][0]
  const eps = Math.max(Math.abs(max - min) * 0.002, 1e-3)

  return {
    ...preset,
    name: `${preset.name}-SSR`,
    scalarOpacity: formatScalarOpacity([
      [min, 0],
      [threshold, 0],
      [threshold + eps, 1],
      [max, 1],
    ]),
    // 表面渲染靠高光表达形状，因此强制开启着色并调高镜面反射
    shade: '1',
    ambient: '0.3',
    diffuse: '0.7',
    specular: '0.3',
    specularPower: '20',
  }
}

/** 整体不透明度：把传递函数的每个采样点按系数缩放。 */
export function scaleOpacity(preset, factor) {
  const points = parseScalarOpacity(preset.scalarOpacity)
  return {
    ...preset,
    scalarOpacity: formatScalarOpacity(points.map(([v, o]) => [v, o * factor])),
  }
}
