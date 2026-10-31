import test from 'node:test'
import assert from 'node:assert/strict'
// 走包导出映射里的 "./constants/*" 子路径（package.json exports 中有声明），
// 不要相对路径去碰 node_modules —— 那样会被仓库根目录的 node_modules 抢先解析
import presets from '@cornerstonejs/core/constants/viewportPresets'
import {
  parseScalarOpacity,
  formatScalarOpacity,
  toSurfaceShaded,
  scaleOpacity,
} from './vrPresets.js'

const get = (name) => presets.find((p) => p.name === name)

test('parseScalarOpacity：首个数字是数值个数，不是点数', () => {
  // '8 -3024 0 -16.4458 0 641.385 0.715686 3071 0.705882' → 8 个数值 = 4 个点
  const points = parseScalarOpacity(get('CT-Bone').scalarOpacity)
  assert.equal(points.length, 4)
  assert.deepEqual(points[0], [-3024, 0])
  assert.deepEqual(points[3], [3071, 0.705882])
})

test('parseScalarOpacity / formatScalarOpacity 可往返', () => {
  for (const name of ['CT-Bone', 'CT-AAA', 'MR-Default', 'MR-T2-Brain']) {
    const original = get(name).scalarOpacity
    const round = formatScalarOpacity(parseScalarOpacity(original))
    assert.equal(round, original, `${name} 往返后应与原串一致`)
  }
})

test('toSurfaceShaded：等值面阈值取传递函数半高处', () => {
  // CT-Bone 的斜坡是 (-16.4458, 0) → (641.385, 0.715686)，max=0.715686，
  // 半高 0.357843 落在斜坡中点 → 阈值约 312.47 HU（骨表面）
  const ssr = toSurfaceShaded(get('CT-Bone'))
  const points = parseScalarOpacity(ssr.scalarOpacity)
  assert.equal(points.length, 4)
  assert.deepEqual(points[0], [-3024, 0], '阈值以下必须全透明')
  assert.equal(points[3][1], 1, '阈值以上必须全不透明')
  assert.ok(
    Math.abs(points[1][0] - 312.4696) < 0.01,
    `CT-Bone 阈值应约 312.47，实际 ${points[1][0]}`,
  )
  assert.ok(points[2][0] > points[1][0], '阶跃上沿必须严格高于阈值')
})

test('toSurfaceShaded：不同预设推出各自合理的阈值', () => {
  // MR-Default 的斜坡终点 opacity 0.5，半高 0.25 → 落在 40→220 之间
  const mr = parseScalarOpacity(toSurfaceShaded(get('MR-Default')).scalarOpacity)
  assert.ok(Math.abs(mr[1][0] - 93.3333) < 0.01, `MR-Default 阈值 ${mr[1][0]}`)

  // CT-Soft-Tissue 半高后接近体表
  const st = parseScalarOpacity(toSurfaceShaded(get('CT-Soft-Tissue')).scalarOpacity)
  assert.ok(Math.abs(st[1][0] + 163.505) < 0.01, `CT-Soft-Tissue 阈值 ${st[1][0]}`)
})

test('toSurfaceShaded：强制开启着色', () => {
  const ssr = toSurfaceShaded(get('CT-Bone'))
  assert.equal(ssr.shade, '1')
  assert.ok(Number(ssr.specular) > 0, '表面渲染需要镜面反射才有形状感')
})

test('scaleOpacity：按系数缩放每个采样点，x 不变', () => {
  const scaled = parseScalarOpacity(scaleOpacity(get('CT-Bone'), 0.5).scalarOpacity)
  const original = parseScalarOpacity(get('CT-Bone').scalarOpacity)
  assert.equal(scaled.length, original.length)
  scaled.forEach(([x, o], i) => {
    assert.equal(x, original[i][0], '采样位置不能被动')
    assert.ok(Math.abs(o - original[i][1] * 0.5) < 1e-4, `第 ${i} 点应减半`)
  })
})

test('scaleOpacity(1) 是恒等的，且 SSR 再缩放不会出现负数', () => {
  const name = 'CT-Bone'
  assert.equal(
    scaleOpacity(get(name), 1).scalarOpacity,
    get(name).scalarOpacity,
  )
  const points = parseScalarOpacity(scaleOpacity(toSurfaceShaded(get(name)), 0.05).scalarOpacity)
  assert.ok(points.every(([, o]) => o >= 0 && o <= 1))
})
