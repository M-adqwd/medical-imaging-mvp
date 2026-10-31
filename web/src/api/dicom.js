/**
 * Cornerstone3D 初始化与 DICOMweb 数据源接入。
 *
 * 契约对应：
 * - §2.1 DICOMweb：图像来自 WADO-RS 实例端点，元数据来自 QIDO-RS
 * - §5.5 系统层不自定义 DICOMweb 字段：这里只做标准 DICOMweb 代理
 *
 * API 基线：@cornerstonejs/* 4.22.x
 */

import * as cornerstoneCore from '@cornerstonejs/core'
import * as cornerstoneTools from '@cornerstonejs/tools'
import * as dicomImageLoader from '@cornerstonejs/dicom-image-loader'
import dicomParser from 'dicom-parser'

const DICOMWEB_BASE = '/dicomweb'

let initialized = false

export function initCornerstone() {
  if (initialized) return
  initialized = true

  cornerstoneCore.init()

  // 现场诊断入口：影像加载异常时可在控制台直接查缓存与体数据状态
  // （生产环境也可用，只挂两个对象引用，不产生额外开销）
  if (typeof window !== 'undefined') {
    window.__miisDebug = { core: cornerstoneCore, tools: cornerstoneTools }
  }

  // 注册 web worker 与解码器（JPEG / JPEG2000 / JPEG-LS / HTJ2K / RLE）
  dicomImageLoader.init()

  // wadouri: 协议 → 交给 dicom-image-loader 的 WADO-URI 加载器，
  // 它对 /dicomweb/... 发起单实例取回请求并解析像素数据
  cornerstoneCore.imageLoader.registerImageLoader(
    'wadouri',
    dicomImageLoader.wadouri.loadImage,
  )

  // 元数据提供者：像素间距、窗宽窗位、方向余弦，供测量与 MPR 换算
  cornerstoneCore.metaData.addProvider(
    dicomImageLoader.wadouri.metaData.metaDataProvider,
    100,
  )

  cornerstoneTools.init()

  // 工具必须先注册进全局 state.tools，ToolGroup.addTool 才会生效。
  // init() 只做配置与事件监听，不注册任何工具 —— 漏掉这步会导致
  // 全部工具静默失效（控制台只有 warn，界面无任何报错）。
  cornerstoneTools.addTool(cornerstoneTools.WindowLevelTool)
  cornerstoneTools.addTool(cornerstoneTools.PanTool)
  cornerstoneTools.addTool(cornerstoneTools.ZoomTool)
  cornerstoneTools.addTool(cornerstoneTools.StackScrollTool)
  cornerstoneTools.addTool(cornerstoneTools.LengthTool)
  cornerstoneTools.addTool(cornerstoneTools.CrosshairsTool)

  // 角度 / 面积 / 放大镜
  // 注意：Cornerstone3D 里没有 AreaTool（选型文档第 22 行写错了）——
  // 面积测量走 EllipticalROI / RectangleROI，它们的 cachedStats 带 area + areaUnit。
  cornerstoneTools.addTool(cornerstoneTools.AngleTool)
  cornerstoneTools.addTool(cornerstoneTools.EllipticalROITool)
  cornerstoneTools.addTool(cornerstoneTools.RectangleROITool)
  cornerstoneTools.addTool(cornerstoneTools.MagnifyTool)
  cornerstoneTools.addTool(cornerstoneTools.ScaleOverlayTool)
}

/** 参与交互的工具清单。自检与注册共用同一份，避免两处漂移。 */
export const REQUIRED_TOOLS = [
  'WindowLevel',
  'Pan',
  'Zoom',
  'StackScroll',
  'Length',
  'Crosshairs',
  'Angle',
  'EllipticalROI',
  'RectangleROI',
  'Magnify',
  // ScaleOverlay 是 AnnotationDisplayTool，不占鼠标键，但同样依赖 addTool
  // 注册成功；漏了它只会少一条比例尺，不会像交互工具那样整体失效。
  'ScaleOverlay',
]

export const TOOL_GROUP_ID = 'miis-mpr'

/**
 * 注册自检：把 Cornerstone 的静默失效变成 fail-fast。
 *
 * ToolGroup.addTool 在工具未全局注册时只 console.warn 就 return，
 * 界面照常渲染、按钮照常高亮，但所有鼠标操作都是死的操作 ——
 * 这类缺陷在人工验收里极难发现。这里显式断言并抛出，让问题在
 * 启动阶段就暴露（配合 G1 门禁：控制台出现该告警即失败）。
 */
function assertToolsRegistered(group) {
  const missing = REQUIRED_TOOLS.filter((name) => !group.hasTool(name))
  if (missing.length > 0) {
    throw new Error(
      `[cornerstone] 工具未注册到工具组，交互将全部失效：${missing.join(', ')}。` +
        '请确认 initCornerstone() 中对每个工具都调用了 cornerstoneTools.addTool()。',
    )
  }
}

/** 注册全局工具组。三个正交视口共用同一组工具，实现十字线联动。 */
export function ensureToolGroup() {
  const existing = cornerstoneTools.ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
  if (existing) {
    assertToolsRegistered(existing)
    return existing
  }

  const group = cornerstoneTools.ToolGroupManager.createToolGroup(TOOL_GROUP_ID)
  const {
    WindowLevelTool,
    PanTool,
    ZoomTool,
    StackScrollTool,
    LengthTool,
    CrosshairsTool,
    AngleTool,
    EllipticalROITool,
    RectangleROITool,
    MagnifyTool,
    ScaleOverlayTool,
  } = cornerstoneTools

  group.addTool(WindowLevelTool.toolName)
  group.addTool(PanTool.toolName)
  group.addTool(ZoomTool.toolName)
  group.addTool(StackScrollTool.toolName)
  group.addTool(LengthTool.toolName)
  // 角度 / 面积 / 放大镜
  group.addTool(AngleTool.toolName)
  group.addTool(EllipticalROITool.toolName)
  group.addTool(RectangleROITool.toolName)
  group.addTool(MagnifyTool.toolName)
  // ScaleOverlay 是 AnnotationDisplayTool，不占鼠标键，开启后画比例尺
  group.addTool(ScaleOverlayTool.toolName)
  group.addTool(CrosshairsTool.toolName, {
    getReferenceLineColor: () => 'rgb(10,106,112)',
    getReferenceLineControllable: () => true,
    getReferenceLineDraggableRotatable: () => true,
    getReferenceLineSlabThicknessControlsOn: () => false,
  })

  const primary = cornerstoneTools.Enums.MouseBindings.Primary
  const auxiliary = cornerstoneTools.Enums.MouseBindings.Auxiliary
  const secondary = cornerstoneTools.Enums.MouseBindings.Secondary

  group.setToolActive(WindowLevelTool.toolName, {
    bindings: [{ mouseButton: primary }],
  })
  group.setToolActive(PanTool.toolName, {
    bindings: [{ mouseButton: auxiliary }],
  })
  group.setToolActive(ZoomTool.toolName, {
    bindings: [{ mouseButton: secondary }],
  })
  // 滚轮翻层：StackScroll 必须绑定 Wheel 才会收到滚轮事件。
  // mouseWheel 处理器把 evt.detail.buttons 设为 MouseBindings.Wheel，
  // 只有绑定了 Wheel 的工具才会被选中（见 getActiveToolForMouseEvent）。
  group.setToolActive(StackScrollTool.toolName, {
    bindings: [{ mouseButton: cornerstoneTools.Enums.MouseBindings.Wheel }],
  })
  group.setToolEnabled(ScaleOverlayTool.toolName)

  assertToolsRegistered(group)
  return group
}

/** 三维（VR / SSR）视口的工具组，与 MPR 分组隔离：十字线、翻层这些工具在 3D 下没有意义。 */
export const VR_TOOL_GROUP_ID = 'miis-vr'

/**
 * 三维交互依赖的工具清单。注册与自检共用同一份，避免两处漂移。
 *
 * 刻意不含任何注解类工具（Length / Angle / ROI）。原因不是 3D 下不好用，
 * 而是注解的存储与显示都不按工具组隔离，而 VR 与 MPR 共用同一体数据即同一
 * FOR，挂上同名注解工具就会互相干扰：
 *   - 存储键是 FrameOfReferenceUID → toolName，与 toolGroup、视口无关
 *     （tools/dist/esm/stateManagement/annotation/
 *      FrameOfReferenceSpecificAnnotationManager.js 的 getGroupKey）
 *   - 显示过滤只做几何判断、不做归属判断：MPR 侧走 filterAnnotationsWithinSlice，
 *     只看注解点是否落在当前层法向半间距内，**不读 sliceIndex**（其法向共面
 *     那道闸对 createAnnotation 建的注解不生效 —— 建注解时只传一个点，
 *     updatePlaneRestriction 会把 planeRestriction 的两个向量置 null）；
 *     3D 侧走 BaseVolumeViewport.isReferenceViewable 比对 sliceIndex，而
 *     VolumeViewport3D.getSliceIndex() 恒为 null。
 *   因此后果是单向的：3D 侧画的标注在几何条件满足时会串到 MPR 视口；反向
 *   不会 —— MPR 注解的 sliceIndex 是数字，与 3D 侧的 null 比对必然失败。
 * 将来若要在 3D 侧加注解工具，用 ToolGroup.addToolInstance(toolName,
 * parentClassName) 注册独立工具名，绕开 FOR → toolName 的键冲突。
 */
export const VR_REQUIRED_TOOLS = ['TrackballRotate', 'Pan', 'Zoom']

function assertVrToolsRegistered(group) {
  const missing = VR_REQUIRED_TOOLS.filter((name) => !group.hasTool(name))
  if (missing.length > 0) {
    throw new Error(
      `[cornerstone] 三维工具未注册到工具组，旋转/缩放/平移将全部失效：${missing.join(', ')}。`,
    )
  }
}

/**
 * 注册三维视口工具组（VR / SSR 视图用）。
 *
 * 必须在 initCornerstone() 之后调用。TrackballRotateTool 不在
 * initCornerstone() 的注册清单里，这里显式补注册 —— addTool 对已注册的
 * 工具是幂等的（store/addTool.js：`if (!state.tools[toolName])`），
 * 所以重复调用不会产生副作用，也不必改动 initCornerstone 的函数体。
 */
export function ensureVrToolGroup() {
  const { TrackballRotateTool, PanTool, ZoomTool } = cornerstoneTools
  cornerstoneTools.addTool(TrackballRotateTool)
  cornerstoneTools.addTool(PanTool)
  cornerstoneTools.addTool(ZoomTool)

  const existing = cornerstoneTools.ToolGroupManager.getToolGroup(VR_TOOL_GROUP_ID)
  if (existing) {
    assertVrToolsRegistered(existing)
    return existing
  }

  const group = cornerstoneTools.ToolGroupManager.createToolGroup(VR_TOOL_GROUP_ID)
  group.addTool(TrackballRotateTool.toolName)
  group.addTool(PanTool.toolName)
  group.addTool(ZoomTool.toolName)

  const { MouseBindings } = cornerstoneTools.Enums
  // 左键旋转（TrackballRotate）/ 中键平移 / 右键缩放。
  // 3D 视口没有切片概念，因此不绑定 Wheel 给 StackScroll。
  group.setToolActive(TrackballRotateTool.toolName, {
    bindings: [{ mouseButton: MouseBindings.Primary }],
  })
  group.setToolActive(PanTool.toolName, {
    bindings: [{ mouseButton: MouseBindings.Auxiliary }],
  })
  group.setToolActive(ZoomTool.toolName, {
    bindings: [{ mouseButton: MouseBindings.Secondary }],
  })

  assertVrToolsRegistered(group)
  return group
}

export async function qidoStudies({ patientId, modality } = {}) {
  const qs = new URLSearchParams()
  if (patientId) qs.set('PatientID', patientId)
  if (modality) qs.set('ModalitiesInStudy', modality)
  const res = await fetch(`${DICOMWEB_BASE}/studies?${qs}`)
  if (!res.ok) throw new Error(`QIDO studies ${res.status}`)
  return res.json()
}

export async function qidoSeries(studyUid) {
  const res = await fetch(`${DICOMWEB_BASE}/studies/${studyUid}/series`)
  if (!res.ok) throw new Error(`QIDO series ${res.status}`)
  return res.json()
}

export async function qidoInstances(studyUid, seriesUid) {
  const res = await fetch(
    `${DICOMWEB_BASE}/studies/${studyUid}/series/${seriesUid}/instances`,
  )
  if (!res.ok) throw new Error(`QIDO instances ${res.status}`)
  return res.json()
}

export function unwrap(json, tag) {
  const el = json[tag]
  return el && el[0] && el[0].Value ? el[0].Value[0] : undefined
}

export function unwrapList(json, tag) {
  const el = json[tag]
  return el && el[0] && el[0].Value ? el[0].Value : undefined
}

export function instanceToImageId(studyUid, seriesUid, sopUid) {
  return `wadouri:${DICOMWEB_BASE}/studies/${studyUid}/series/${seriesUid}/instances/${sopUid}`
}

/**
 * 拉取一个 series 的全部实例，按切片位置排序后返回 Cornerstone imageId 列表。
 *
 * 排序优先级：
 *   1. ImagePositionPatient(00200032) 沿切片前进方向投影 —— DICOM 几何排序
 *   2. 退化到 SliceLocation(00201041) / InstanceNumber(00200013)
 *
 * QIDO 响应已带 00200032，故不必为排序逐张下载实例。
 */
export async function loadSeriesImageIds(studyUid, seriesUid) {
  const instances = await qidoInstances(studyUid, seriesUid)

  const metas = instances.map((inst) => {
    const ipp = (unwrapList(inst, '00200032') || []).map(Number)
    return {
      sopUid: unwrap(inst, '00080018'),
      ipp,
      instanceNumber: Number(unwrap(inst, '00200013') ?? 0),
      sliceLocation: Number(unwrap(inst, '00201041') ?? (ipp.length === 3 ? ipp[2] : NaN)),
    }
  })

  // 用首末两片位移向量判断切片前进方向，沿该向量投影排序
  const first = metas.find((m) => m.ipp.length === 3)
  const last = [...metas].reverse().find((m) => m.ipp.length === 3)
  let axis = [0, 0, 1]
  if (first && last && first !== last) {
    const dv = [
      last.ipp[0] - first.ipp[0],
      last.ipp[1] - first.ipp[1],
      last.ipp[2] - first.ipp[2],
    ]
    const norm = Math.hypot(...dv)
    if (norm > 1e-6) axis = dv.map((v) => v / norm)
  }

  metas.sort((a, b) => {
    if (a.ipp.length === 3 && b.ipp.length === 3) {
      const pa = a.ipp[0] * axis[0] + a.ipp[1] * axis[1] + a.ipp[2] * axis[2]
      const pb = b.ipp[0] * axis[0] + b.ipp[1] * axis[1] + b.ipp[2] * axis[2]
      if (Number.isFinite(pa) && Number.isFinite(pb) && pa !== pb) return pa - pb
    }
    if (Number.isFinite(a.sliceLocation) && Number.isFinite(b.sliceLocation)) {
      return a.sliceLocation - b.sliceLocation
    }
    return a.instanceNumber - b.instanceNumber
  })

  return metas.map((m) => ({
    imageId: instanceToImageId(studyUid, seriesUid, m.sopUid),
    sopInstanceUid: m.sopUid,
    z: m.ipp.length === 3 ? m.ipp[2] : 0,
  }))
}

export { cornerstoneCore, cornerstoneTools, dicomImageLoader, dicomParser }
