/**
 * 测量载荷构造器的行为测试（TC-07 的第一批护栏）。
 *
 * 用 Node 内置 `node:test`，零第三方依赖 —— 这样它今天就能跑，不必等
 * 测试基建选型定下来。目录里以后要换 vitest，本文件的断言可以原样搬。
 *
 *   cd web && npm test
 *
 * 断言的是 docs/COORDINATE-CONTRACT.md §4 / §4.1 / §6 / §7.2 的可执行部分：
 * graphic_type 与 graphic_data 长度的对应关系、coord_ref 该有与不该有哪些字段。
 */

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  GRAPHIC_SPEC,
  buildMeasurementCreateBody,
  buildMeasurementPayload,
  measurementTypeOf,
  parseSopInstanceUid,
} from './measurementPayload.js'

const IMAGE_ID = 'wadouri:/dicomweb/studies/1.2.3/series/4.5.6/instances/7.8.9'
const EXPECTED_SOP = '7.8.9'

/** 造一条最小可用的注解。cachedStats 的键是 Cornerstone 的 targetId。 */
function makeAnnotation(toolName, stats, points) {
  return {
    annotationUID: 'uid-test',
    metadata: { toolName, referencedImageId: IMAGE_ID },
    data: { cachedStats: { 'volumeId:vol-1': stats }, handles: { points } },
  }
}

test('length → POLYLINE + 6 值，取两个真实端点（不补点）', () => {
  const payload = buildMeasurementPayload(
    makeAnnotation('Length', { length: 12.3456, unit: 'mm' }, [
      [1, 2, 3],
      [4, 5, 6],
    ]),
  )
  assert.equal(payload.graphicType, 'POLYLINE')
  assert.equal(payload.graphicData.length, GRAPHIC_SPEC.length.length)
  assert.deepEqual(payload.graphicData, [1, 2, 3, 4, 5, 6])
  // 值四舍五入到两位，避免把浮点噪声当精度写进 SR
  assert.equal(payload.value, 12.35)
  assert.equal(payload.unit, 'mm')
  assert.equal(payload.sopInstanceUid, EXPECTED_SOP)
})

test('length 只有一个点时产出不足 6 值，绝不复制点凑数', () => {
  const payload = buildMeasurementPayload(
    makeAnnotation('Length', { length: 1, unit: 'mm' }, [[0, 0, 0]]),
  )
  assert.equal(payload.graphicData.length, 3)
  assert.notEqual(payload.graphicData.length, GRAPHIC_SPEC.length.length)
})

test('angle → POLYLINE + 9 值，顶点在中间（原序输出）', () => {
  const payload = buildMeasurementPayload(
    makeAnnotation('Angle', { angle: 42.5 }, [
      [0, 0, 0],
      [10, 0, 0],
      [10, 10, 0],
    ]),
  )
  assert.equal(payload.graphicType, 'POLYLINE')
  assert.equal(payload.graphicData.length, GRAPHIC_SPEC.angle.length)
  assert.deepEqual(payload.graphicData, [0, 0, 0, 10, 0, 0, 10, 10, 0])
})

test('椭圆 ROI → POINT + 3 值，取四点质心', () => {
  // EllipticalROITool 的点序是 [bottom, top, left, right]
  const payload = buildMeasurementPayload(
    makeAnnotation('EllipticalROI', { area: 100.4, areaUnit: 'mm2' }, [
      [0, -1, 0],
      [0, 1, 0],
      [-2, 0, 0],
      [2, 0, 0],
    ]),
  )
  assert.equal(payload.graphicType, 'POINT')
  assert.equal(payload.graphicData.length, GRAPHIC_SPEC.areaEllipse.length)
  assert.deepEqual(payload.graphicData, [0, 0, 0])
})

test('矩形 ROI → POINT + 3 值，质心等于几何中心（与椭圆点序不同但结果一致）', () => {
  // RectangleROITool 的点序是 [topLeft, topRight, bottomLeft, bottomRight]
  const payload = buildMeasurementPayload(
    makeAnnotation('RectangleROI', { area: 4, areaUnit: 'mm2' }, [
      [-1, -1, 0],
      [1, -1, 0],
      [-1, 1, 0],
      [1, 1, 0],
    ]),
  )
  assert.equal(payload.graphicType, 'POINT')
  assert.deepEqual(payload.graphicData, [0, 0, 0])
})

test('面积类不发 POLYGON（PS3.3 C.18.9.1.2 要求首尾重合且共面）', () => {
  for (const tool of ['EllipticalROI', 'RectangleROI']) {
    const payload = buildMeasurementPayload(
      makeAnnotation(tool, { area: 1, areaUnit: 'mm2' }, [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [1, 1, 0],
      ]),
    )
    assert.notEqual(payload.graphicType, 'POLYGON')
  }
})

test('统计量还没算出来时返回 null（对应快速拖拽的时序竞态）', () => {
  assert.equal(
    buildMeasurementPayload(makeAnnotation('Length', {}, [[0, 0, 0], [1, 1, 1]])),
    null,
  )
})

test('非测量工具不产出测量', () => {
  assert.equal(
    buildMeasurementPayload(makeAnnotation('Pan', { length: 1 }, [])),
    null,
  )
})

test('measurementTypeOf：两种面积 ROI 都归为 area', () => {
  assert.equal(measurementTypeOf('length'), 'length')
  assert.equal(measurementTypeOf('angle'), 'angle')
  assert.equal(measurementTypeOf('areaEllipse'), 'area')
  assert.equal(measurementTypeOf('areaRect'), 'area')
})

test('parseSopInstanceUid：取 imageId 末段，取不到返回 null 而非假值', () => {
  assert.equal(parseSopInstanceUid(IMAGE_ID), EXPECTED_SOP)
  assert.equal(parseSopInstanceUid(`${IMAGE_ID}?frame=1`), EXPECTED_SOP)
  assert.equal(parseSopInstanceUid(undefined), null)
  assert.equal(parseSopInstanceUid(''), null)
  assert.equal(parseSopInstanceUid(123), null)
})

test('请求体：series_instance_uid 只在顶层（契约 §7.2）', () => {
  const body = buildMeasurementCreateBody({
    studyInstanceUid: 'STUDY-1',
    seriesInstanceUid: 'SERIES-1',
    measurement: buildMeasurementPayload(
      makeAnnotation('Length', { length: 3, unit: 'mm' }, [[0, 0, 0], [3, 0, 0]]),
    ),
  })
  assert.equal(body.series_instance_uid, 'SERIES-1')
  assert.equal('series_instance_uid' in body.coord_ref, false)
})

test('请求体：不带 spacing 与 frame_of_reference_uid（契约 §6 派生数据不跨线）', () => {
  const body = buildMeasurementCreateBody({
    studyInstanceUid: 'STUDY-1',
    seriesInstanceUid: 'SERIES-1',
    measurement: buildMeasurementPayload(
      makeAnnotation('Length', { length: 3, unit: 'mm' }, [[0, 0, 0], [3, 0, 0]]),
    ),
  })
  for (const key of ['row_spacing', 'column_spacing', 'slice_thickness']) {
    assert.equal(key in body.coord_ref, false, `coord_ref 不应包含 ${key}`)
  }
  assert.equal('frame_of_reference_uid' in body.coord_ref, false)
})

test('请求体：geometry 为规范值 patient_lps_mm（契约 §3.2）', () => {
  const body = buildMeasurementCreateBody({
    studyInstanceUid: 'STUDY-1',
    seriesInstanceUid: 'SERIES-1',
    measurement: buildMeasurementPayload(
      makeAnnotation('Length', { length: 3, unit: 'mm' }, [[0, 0, 0], [3, 0, 0]]),
    ),
  })
  assert.equal(body.coord_ref.geometry, 'patient_lps_mm')
  assert.equal(body.coord_ref.study_instance_uid, 'STUDY-1')
  assert.equal(body.coord_ref.sop_instance_uid, EXPECTED_SOP)
  assert.equal(body.coord_ref.graphic_type, 'POLYLINE')
  assert.equal(body.cross_series, false)
})

/**
 * 把注释剥掉，只留可执行代码，供下面的"零依赖"断言使用。
 *
 * 不做这一步会误报：本模块的注释里就写着 `window` / `document` / `@cornerstonejs`
 * （解释"为什么不能依赖它们"），直接全文扫会把自己的说明当成违规。
 * 简易状态机足够 —— 本模块没有含 `//` 或 `/*` 的字符串字面量。
 */
function stripComments(source) {
  let out = ''
  let i = 0
  let mode = 'code' // code | line | block
  while (i < source.length) {
    const ch = source[i]
    const next = source[i + 1]
    if (mode === 'code') {
      if (ch === '/' && next === '/') {
        mode = 'line'
        i += 2
        continue
      }
      if (ch === '/' && next === '*') {
        mode = 'block'
        i += 2
        continue
      }
      out += ch
      i += 1
      continue
    }
    if (mode === 'line') {
      if (ch === '\n') {
        mode = 'code'
        out += ch
      }
      i += 1
      continue
    }
    // block
    if (ch === '*' && next === '/') {
      mode = 'code'
      i += 2
      continue
    }
    i += 1
  }
  return out
}

test('模块必须零依赖：否则 Node 里加载不起来，TC-07 的同源前提静默失效', () => {
  const url = new URL('./measurementPayload.js', import.meta.url)
  const code = stripComments(readFileSync(url, 'utf8'))

  // 这一条是 TC-07 的全部前提。若将来有人为了图方便
  // `import { Enums } from '@cornerstonejs/core'`，本模块就再也进不了 Node，
  // 而症状是"没人会报，直到有人真去写 smoke 才发现"。
  assert.doesNotMatch(code, /(^|\n)\s*import\s/, '不得出现 import 语句')
  assert.doesNotMatch(code, /\brequire\s*\(/, '不得出现 require()')
  assert.doesNotMatch(code, /@cornerstonejs/, '不得引用 @cornerstonejs/*')
  assert.doesNotMatch(code, /\bfrom\s+['"]react/, '不得引用 react')
  // 裸用浏览器全局同样会让 Node 里的加载/执行失败
  assert.doesNotMatch(code, /\bwindow\s*\./, '不得使用 window')
  assert.doesNotMatch(code, /\bdocument\s*\./, '不得使用 document')
})

test('请求体：缺 sop_instance_uid 时传 null，不造假 UID（契约 §7.3）', () => {
  const payload = buildMeasurementPayload({
    annotationUID: 'uid-2',
    metadata: { toolName: 'Length' },
    data: {
      cachedStats: { 'volumeId:vol-1': { length: 3, unit: 'mm' } },
      handles: { points: [[0, 0, 0], [3, 0, 0]] },
    },
  })
  assert.equal(payload.sopInstanceUid, null)
  const body = buildMeasurementCreateBody({
    studyInstanceUid: 'STUDY-1',
    seriesInstanceUid: 'SERIES-1',
    measurement: payload,
  })
  assert.equal(body.coord_ref.sop_instance_uid, null)
})
