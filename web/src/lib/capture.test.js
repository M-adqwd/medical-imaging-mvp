import assert from 'node:assert/strict'
import test from 'node:test'

import { fileStem, gridLayout, timestamp } from './capture.js'

test('三格布局：宽=三列，高=表头+格+标签+页脚', () => {
  const l = gridLayout(3, 512, 512, { columns: 3 })
  assert.equal(l.cells.length, 3)
  assert.equal(l.width, 14 * 2 + 3 * 512 + 2 * 10)
  // 表头 52 + 页脚 30 + 上下留白 28 + 一行（512 画布 + 24 标签）
  assert.equal(l.height, 52 + 30 + 28 + 512 + 24)
  assert.deepEqual(l.cells[0], { x: 14, y: 52 + 14 + 24, w: 512, h: 512 })
  assert.equal(l.cells[2].x, 14 + 2 * (512 + 10))
})

test('单格布局不折行，且列数被夹到格子数以内', () => {
  const l = gridLayout(1, 400, 300, { columns: 3 })
  assert.equal(l.cells.length, 1)
  assert.equal(l.width, 14 * 2 + 400)
  assert.equal(l.cells[0].y, 52 + 14 + 24)
})

test('六格按三列折成两行', () => {
  const l = gridLayout(6, 200, 200, { columns: 3 })
  assert.equal(l.cells.length, 6)
  assert.equal(l.cells[3].x, l.cells[0].x)
  assert.ok(l.cells[3].y > l.cells[0].y)
  // 第二行起点 = 第一行格子底 + 标签高 + 行间距
  assert.equal(l.cells[0].y + 200 + 24 + 10, l.cells[3].y)
})

test('关闭表头与页脚时高度只由格子决定', () => {
  const l = gridLayout(2, 100, 100, { columns: 2, header: false, footer: false })
  assert.equal(l.height, 28 + 100 + 24)
})

test('文件名去掉路径分隔符与空白，保留中文', () => {
  assert.equal(fileStem(['病例 A', '轴位 2026/10/29']), '病例-A_轴位-2026-10-29')
  assert.equal(fileStem([], 'capture'), 'capture')
  assert.equal(fileStem([null, '  '], 'fallback'), 'fallback')
})

test('时间戳是可排序的紧凑格式', () => {
  const s = timestamp(new Date(2026, 9, 29, 8, 5, 3))
  assert.equal(s, '20261029-080503')
})
