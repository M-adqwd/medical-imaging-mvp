import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  cornerstoneCore,
  cornerstoneTools,
  ensureToolGroup,
  initCornerstone,
  loadSeriesImageIds,
  TOOL_GROUP_ID,
} from '../api/dicom'
import {
  buildMeasurementPayload,
  measurementTypeOf,
  MEASURE_KIND_DEFS,
} from '../lib/measurementPayload'
import { savePng } from '../lib/capture'
import { makeVolumeId } from '../lib/volumeId'

const {
  RenderingEngine,
  Enums,
  eventTarget,
  volumeLoader,
  cache,
  metaData,
  setVolumesForViewports,
} = cornerstoneCore

const {
  ToolGroupManager,
  LengthTool,
  AngleTool,
  EllipticalROITool,
  RectangleROITool,
  MagnifyTool,
  CrosshairsTool,
  WindowLevelTool,
  segmentation,
} = cornerstoneTools

const { SegmentationRepresentations } = cornerstoneTools.Enums

/** 叠加调色板：按分割列表顺序循环取色（ATLAS 式半透明器官着色）。 */
export const SEG_COLORS = [
  [255, 107, 107],
  [77, 171, 247],
  [105, 217, 153],
  [255, 199, 95],
  [186, 140, 255],
  [255, 158, 218],
  [94, 231, 223],
  [255, 232, 105],
]

// 注解事件定义在 @cornerstonejs/tools 的 Enums.Events 里
// （CORNERSTONE_TOOLS_ANNOTATION_COMPLETED），core 包的 Events 没有这个成员 ——
// 用 core 的 Enums.Events.ANNOTATION_COMPLETED 监听会得到 undefined，
// addEventListener 静默失效，测量结果永远收不到。
const { Events: TOOLS_EVENTS } = cornerstoneTools.Enums

/**
 * 规划取点必须同时监听 MOUSE_CLICK 与 MOUSE_UP。
 *
 * cornerstoneTools 的 mouseDownListener 在 mouseup 时按
 *   state.isClickEvent ? MOUSE_CLICK : MOUSE_UP
 * 二选一互斥派发（eventListeners/mouse/mouseDownListener.js:152），而
 * state.isClickEvent 会在按下超过 clickDelay（默认 200ms，同文件 :19/:34）后
 * 被 _preventClickHandler 置 false（:74 注册 / :186 实现）。
 * 因此只听 MOUSE_CLICK 会让"按久一点再松手"静默取不到点 —— 界面无报错、
 * 无提示，用户只会觉得"点不动"。
 *
 * 这条约束目前没有测试护栏（仓库还没有测试框架），所以在这里做启动即失败的
 * 机械断言：将来谁把双注册改回单注册、或事件名被改坏，模块加载阶段直接抛错，
 * 而不是等用户在验收时反馈"点了没反应"。
 */
const PLAN_POINT_EVENT_NAMES = [
  'CORNERSTONE_TOOLS_MOUSE_CLICK',
  'CORNERSTONE_TOOLS_MOUSE_UP',
]

function assertPlanPointEvents(names) {
  const [click, up] = names
  // 断言的是**具体是哪两个事件**，不只是"两个非空且不相等的字符串"：
  // 后者挡得住"改回单个"，挡不住"两个都改成 MOUSE_DOWN / MOUSE_MOVE"。
  // 字面量取自 tools 包 enums/Events.js（MOUSE_CLICK / MOUSE_UP 的真实值），
  // 上游改名会在这里炸出来，比线上静默收不到点好。
  if (click !== PLAN_POINT_EVENT_NAMES[0] || up !== PLAN_POINT_EVENT_NAMES[1]) {
    throw new Error(
      '[cornerstone] 规划取点必须同时监听 MOUSE_CLICK 与 MOUSE_UP：' +
        '按压超过 clickDelay(200ms) 时只派发 MOUSE_UP，只注册一个会静默丢点；' +
        '换成 MOUSE_DOWN / MOUSE_MOVE 等其它事件同样收不到点击。' +
        `期望 ${JSON.stringify(PLAN_POINT_EVENT_NAMES)}，实际 ${JSON.stringify(names)}`,
    )
  }
  return names
}

const PLAN_POINT_EVENTS = assertPlanPointEvents([
  TOOLS_EVENTS.MOUSE_CLICK,
  TOOLS_EVENTS.MOUSE_UP,
])

const VIEWPORTS = [
  { id: 'sagittal', orientation: Enums.OrientationAxis.SAGITTAL, label: '矢状位' },
  { id: 'axial', orientation: Enums.OrientationAxis.AXIAL, label: '轴位' },
  { id: 'coronal', orientation: Enums.OrientationAxis.CORONAL, label: '冠状位' },
]

const BACKGROUND = [0.02, 0.04, 0.05]

/**
 * 测量工具名一致性断言。
 *
 * `lib/measurementPayload.js` 是零依赖的纯模块（TC-07 的 smoke 脚本要在 Node 里
 * 直接 import 它），因此那边的工具名只能是字符串字面量，不能引用 ToolClass。
 * 字面量一旦与真实 toolName 漂移，表现是"测量完全失效、控制台只有一条 warn" ——
 * 正是前面 addTool 那次事故的同一形状。这里在模块加载阶段把它变成硬失败。
 */
function assertMeasureKindToolNames(defs) {
  const classes = {
    length: LengthTool,
    angle: AngleTool,
    areaEllipse: EllipticalROITool,
    areaRect: RectangleROITool,
  }
  defs.forEach((def) => {
    const actual = classes[def.id]?.toolName
    if (actual !== def.tool) {
      throw new Error(
        `[cornerstone] 测量工具名漂移：kind=${def.id} 声明为 "${def.tool}"，` +
          `但实际 ToolClass.toolName 是 "${actual}"。` +
          '请同步 lib/measurementPayload.js 的 MEASURE_KIND_DEFS。',
      )
    }
  })
  return defs
}

// 主鼠标键（左键）绑定的工具随模式切换；平移 / 缩放 / 滚轮固定。
// 面积测量没有 AreaTool（Cornerstone3D 里不存在该工具），走 ROI 工具的
// cachedStats.area，单位是 areaUnit（通常为 mm²）。
const MEASURE_KINDS = assertMeasureKindToolNames(MEASURE_KIND_DEFS)

const MODES = [
  { id: 'locate', label: '定位', tool: CrosshairsTool.toolName },
  { id: 'window', label: '窗位', tool: WindowLevelTool.toolName },
  { id: 'measure', label: '测量', tool: LengthTool.toolName },
  { id: 'magnify', label: '放大镜', tool: MagnifyTool.toolName },
  { id: 'plan', label: '规划取点', tool: null },
]

// 混合模式：合成 / MIP（最大密度投影）/ MinIP（最小密度投影）
// BlendModes 的真实成员只有 COMPOSITE / MAXIMUM_INTENSITY_BLEND /
// MINIMUM_INTENSITY_BLEND / AVERAGE_INTENSITY_BLEND —— 没有 MIP，也没有 DEFAULT。
const BLEND_MODES = [
  { id: 'composite', label: '合成', blend: Enums.BlendModes.COMPOSITE },
  { id: 'mip', label: 'MIP', blend: Enums.BlendModes.MAXIMUM_INTENSITY_BLEND },
  { id: 'minip', label: 'MinIP', blend: Enums.BlendModes.MINIMUM_INTENSITY_BLEND },
]

// 多窗口布局：1×3 三视图 / 1×1 单视口放大 / 2×2（含三维位由 3D 页承担）
const LAYOUTS = [
  { id: 'triple', label: '三视图', viewports: ['sagittal', 'axial', 'coronal'] },
  { id: 'axial', label: '轴位单屏', viewports: ['axial'] },
  { id: 'dual', label: '轴位+冠位', viewports: ['axial', 'coronal'] },
]

const WL_PRESETS = [
  { label: '软组织', lower: -160, upper: 240 },
  { label: '肺窗', lower: -1000, upper: 400 },
  { label: '骨窗', lower: 300, upper: 1500 },
]

/**
 * 给可能永不 settle 的 Promise 套一层看门狗。
 *
 * 背景：cornerstone 的体数据构建与逐层解码都跑在 web worker 里，worker
 * 异常退出时上层 Promise 既不 resolve 也不 reject —— 界面就永远停在
 * "重建体数据 N 层"，看起来像"图像有时候出不来"。所有关键 await 点必须
 * 有超时兜底，把静默挂起变成可见错误。
 */
function withTimeout(promise, ms, label) {
  let timer = null
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label}：${Math.round(ms / 1000)} 秒内未完成`)), ms)
  })
  return Promise.race([promise, guard]).finally(() => timer && clearTimeout(timer))
}

/** 体数据构建/灌入的超时（单次）。真实病例 107 层在本机约 3-5 秒完成，留足余量。 */
const VOLUME_BUILD_TIMEOUT_MS = 60000
const VOLUME_FILL_TIMEOUT_MS = 120000

/**
 * MPR 三视口（FR-02 / FR-03）。
 *
 * 图像以 DICOMweb WADO-RS 取回，构建体数据后用 ORTHOGRAPHIC 视口做
 * 轴位 / 矢状位 / 冠状位重建；十字线工具保证三视图光标联动。
 * 长度测量走 Cornerstone 原生 LengthTool，毫米值由体素间距换算，
 * 与服务端 SR 口径一致（PRD §6.5）。
 */
export default function MprViewport({
  studyUid,
  seriesUid,
  volumeTag = 'v1',
  measureMode,
  planMode,
  seedMode,
  visible = true,
  overlaySegs = [],
  overlayOn = true,
  onMeasureReady,
  onPlanPoint,
  onSeedPoint,
  onCrosshair,
  captureInfo,
  onStatus,
}) {
  const gridRef = useRef(null)
  const engineRef = useRef(null)
  const volumeIdRef = useRef(null)
  const counterRef = useRef({ loaded: 0, total: 0 })
  // 体数据加载的兜底定时器句柄：卸载时必须清掉，否则组件销毁后仍会 resolve
  const loadTimeoutRef = useRef(null)
  const [status, setStatus] = useState({ state: 'idle', message: '' })
  const [error, setError] = useState(null)
  // 加载失败后的重试：自增即重跑主流程（清空缓存 + 重新构建体数据）
  const [reloadTick, setReloadTick] = useState(0)
  const [mode, setMode] = useState('locate')
  const [preset, setPreset] = useState(WL_PRESETS[0].label)
  const [blendId, setBlendId] = useState('composite')
  const [measureKind, setMeasureKind] = useState('length')
  const [layoutId, setLayoutId] = useState('triple')
  const [spacingText, setSpacingText] = useState('')
  const [draft, setDraft] = useState(null)
  const [shotMsg, setShotMsg] = useState('')
  // 十字线位置的上报节流：拖动时每帧都会触发事件，直接 setState 会让父组件的
  // 三维投影每帧重算一遍，帧率掉到个位数。
  const crosshairTickRef = useRef(0)

  const activeLayout = LAYOUTS.find((l) => l.id === layoutId) || LAYOUTS[0]

  const report = useCallback(
    (patch) => {
      setStatus((prev) => {
        const next = { ...prev, ...patch }
        onStatus?.(next)
        return next
      })
    },
    [onStatus],
  )

  // 测量模式由工作台外部开关驱动
  useEffect(() => {
    if (measureMode) setMode('measure')
    // 种子点生长同样要求"左键不被任何工具吃掉"，所以复用 plan 模式的空绑定
    else if (planMode || seedMode) setMode('plan')
    else if (mode === 'measure' || mode === 'plan') setMode('locate')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measureMode, planMode, seedMode])

  /* ---------------- 主流程：体数据加载 + 视口装配 ---------------- */
  useEffect(() => {
    if (!studyUid || !seriesUid) return undefined

    let cancelled = false
    let engine = null
    const engineId = `mpr-${seriesUid.slice(-12)}`
    // volumeTag 由工作台按"每次进入病例/切换序列"自增，保证体数据 ID 不复用：
    // cornerstone 的体数据对象按 volumeId 存在全局缓存里，上一次挂载遗留的对象
    // 可能停在 loadStatus.loading === true，而 BaseStreamingImageVolume.load()
    // 见到 loading 为真会直接 return —— 既不发起请求也不回调，
    // 表现为永远卡在「灌入体数据」（医生侧就是"图像有时候显示不出来"）。
    const volumeId = makeVolumeId(seriesUid, volumeTag)
    volumeIdRef.current = volumeId

    const onImageLoaded = (evt) => {
      // core 派发的负载是 { image }，imageId 在 image.imageId 上
      const imageId = evt?.detail?.image?.imageId || ''
      if (!imageId.startsWith('wadouri:')) return
      counterRef.current.loaded += 1
      if (!cancelled && counterRef.current.loaded % 12 === 0) {
        report({
          state: 'loading',
          message: `已加载 ${counterRef.current.loaded}/${counterRef.current.total || '?'} 张`,
        })
      }
    }

    async function boot() {
      report({ state: 'loading', message: '初始化渲染引擎' })
      initCornerstone()
      ensureToolGroup()

      const el = gridRef.current
      if (!el) return

      report({ state: 'loading', message: '查询序列实例' })
      const entries = await loadSeriesImageIds(studyUid, seriesUid)
      if (cancelled) return
      if (entries.length === 0) {
        report({ state: 'error', message: '序列为空' })
        return
      }
      counterRef.current = { loaded: 0, total: entries.length }
      eventTarget.addEventListener(Enums.Events.IMAGE_LOADED, onImageLoaded)

      const imageIds = entries.map((e) => e.imageId)
      report({ state: 'loading', message: `重建体数据 ${entries.length} 层` })

      // 构建失败/超时后必须把半成品从缓存里摘掉，否则下一次仍会拿到坏对象。
      // 用 purgeVolumeCache 而不是按 id 删：本应用同一时刻只有一个在用的体数据，
      // 而按 id 删常因「占位条目已存在」而删不干净 —— 残留条目会让
      // cache._putVolumeCommon 提前 return，新建的体数据永远挂不上 loadStatus，
      // 之后 volume.load() 直接抛错，界面就死在「灌入体数据」。
      const purge = () => {
        try {
          cache.purgeVolumeCache()
        } catch {
          /* 缓存已空 */
        }
      }

      // ★ 「图像有时候显示不出来」的真正根因就在这里：
      // createAndCacheVolumeFromImages() 内部先算 imageIdsToLoad = 未被图像缓存
      // 命中的 imageId；**若该序列的每一张图都已在缓存里，它会走
      // createAndCacheVolumeFromImagesSync()**，而那条路径造出来的是一个
      // **没有像素数据的 ImageVolume**（没有 scalarData、也没有 loadStatus）。
      // 于是：既能显示（几何对）又没有任何体素，且 volume.load() 不存在/抛错 ——
      // 界面就永远停在「灌入体数据」。第一次打开正常、再进同一病例就白屏，
      // 正是"有时候显示不出来"的由来。
      // 对策：建体数据前先把这个序列的图从**图像缓存**里摘掉，强制走流式路径，
      // 拿到的才是带 loadStatus 的 StreamingImageVolume（体素边下边填）。
      // 只需摘掉**一张**就能让 imageIdsToLoad 非空、从而走流式路径；
      // 其余张继续留在图像缓存里，灌入时直接命中缓存不重复下载
      // （全摘会导致每次切病例都把整个序列重下一遍）。
      const decacheSeriesImages = () => {
        const last = imageIds[imageIds.length - 1]
        if (!last) return
        try {
          cache.removeImageLoadObject(last, { force: true })
        } catch {
          /* 不在缓存里 */
        }
      }

      // 重建。三类失败都要重试：超时（解码 worker 无响应）、拿到无 loadStatus
      // 的静态空体数据、空体数据。每次重试用新的 volumeId，避免上一次仍在飞行
      // 中的 promise 占住同名条目。
      let volume = null
      {
        const ATTEMPTS = 3
        for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
          const attemptId = attempt === 1 ? volumeId : `${volumeId}#r${attempt}`
          try {
            purge()
            decacheSeriesImages()
            const created = await withTimeout(
              volumeLoader.createAndCacheVolumeFromImages(attemptId, imageIds),
              VOLUME_BUILD_TIMEOUT_MS,
              '体数据重建超时',
            )
            if (!created?.loadStatus || !created.dimensions?.length) {
              throw new Error('体数据对象不完整')
            }
            volume = created
            volumeIdRef.current = attemptId
            break
          } catch (e) {
            if (cancelled) return
            if (attempt === ATTEMPTS) throw e
            decacheSeriesImages()
            report({
              state: 'loading',
              message: `重建失败（${e?.message || e}），正在重试 ${attempt + 1}/${ATTEMPTS}`,
            })
          }
        }
      }
      if (cancelled) return

      // createAndCacheVolumeFromImages 只预取首/中/末三张推导几何，
      // 真正的体素数据要显式 load() 才会流式灌入
      let fillTimedOut = false
      if (!volume.loadStatus?.loaded) {
        report({ state: 'loading', message: `灌入体数据 ${entries.length} 层` })
        fillTimedOut = await new Promise((resolve) => {
          let settled = false
          let poll = null
          const finish = (timedOut = false) => {
            if (settled) return
            settled = true
            if (loadTimeoutRef.current) {
              window.clearTimeout(loadTimeoutRef.current)
              loadTimeoutRef.current = null
            }
            if (poll) window.clearInterval(poll)
            resolve(timedOut)
          }
          // 不能只信 load() 的回调：BaseStreamingImageVolume.load() 在
          // loadStatus.loading === true 时直接 return，既不挂回调也不加载。
          // 因此并行轮询 loadStatus.loaded，任何一条路径到达都算完成。
          const watch = () => {
            if (volume.loadStatus?.loaded) finish(false)
          }
          poll = window.setInterval(watch, 250)
          loadTimeoutRef.current = window.setTimeout(() => finish(true), VOLUME_FILL_TIMEOUT_MS)
          try {
            volume.load(() => finish(false))
          } catch (e) {
            console.error('[MprViewport] volume.load', e)
          }
          watch()
        })
      }
      if (cancelled) return
      // 灌入超时仍然继续渲染：已到达的层可以看，但要明确告知不完整，
      // 不能静默渲染出半个体数据让人误判。
      if (fillTimedOut) {
        report({ state: 'loading', message: `灌入超时（${entries.length} 层未全部到达），按已到达数据渲染` })
      }

      const spacing = volume.spacing || metaData.get('pixelSpacing', imageIds[0]) || [1, 1]
      const dims = volume.dimensions || []
      setSpacingText(`${spacing[0]?.toFixed(2) ?? '—'} mm`)

      engine = new RenderingEngine(engineId)
      engineRef.current = engine

      engine.setViewports(
        VIEWPORTS.map((vp) => ({
          viewportId: vp.id,
          type: Enums.ViewportType.ORTHOGRAPHIC,
          element: el.querySelector(`[data-vp="${vp.id}"] .mpr-canvas`),
          defaultOptions: { orientation: vp.orientation, background: BACKGROUND },
        })),
      )

      await withTimeout(
        setVolumesForViewports(
          engine,
          [{ volumeId, callback: () => {} }],
          VIEWPORTS.map((v) => v.id),
        ),
        30000,
        '视口绑定体数据超时',
      )
      if (cancelled) return

      const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
      if (group) {
        VIEWPORTS.forEach((vp) => group.addViewport(vp.id, engineId))
        applyMode(group, mode)
      }

      applyPreset(WL_PRESETS[0])
      engine.renderViewports(VIEWPORTS.map((v) => v.id))

      report({
        state: 'ready',
        message: `${entries.length} 层 · ${dims[0] ?? '—'}×${dims[1] ?? '—'}×${dims[2] ?? '—'}`,
        count: entries.length,
        rows: dims[0],
        columns: dims[1],
        pixelSpacing: spacing[0],
        spacing,
      })
    }

    boot().catch((e) => {
      if (cancelled) return
      console.error('[MprViewport]', e)
      setError(e?.message || String(e))
      report({ state: 'error', message: e?.message || String(e) })
    })

    return () => {
      cancelled = true
      eventTarget.removeEventListener(Enums.Events.IMAGE_LOADED, onImageLoaded)
      // 卸载时必须清掉体数据加载的超时定时器，否则组件卸载后仍会 resolve
      if (loadTimeoutRef.current) {
        window.clearTimeout(loadTimeoutRef.current)
        loadTimeoutRef.current = null
      }
      const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
      if (group && engine) {
        // removeViewports(renderingEngineId, viewportId) 只接受单个 viewportId，
        // 传数组会导致视口残留、切换序列后重复 addViewport。
        VIEWPORTS.forEach((vp) => {
          try {
            group.removeViewports(engine.id, vp.id)
          } catch {
            /* 已销毁 */
          }
        })
      }
      if (engine) {
        try {
          engine.destroy()
        } catch {
          /* 已销毁 */
        }
      }
      engineRef.current = null
      if (volumeIdRef.current) {
        try {
          // 只摘掉本组件自己建的体数据（VR 共用同一个 id，也是同时卸载的）。
          // 注意 core 的 cache 没有 removeVolume 这个方法，按 id 清就走
          // removeVolumeLoadObject —— 它会顺带 cancelLoading 并释放 imageData。
          cache.removeVolumeLoadObject(volumeIdRef.current)
        } catch {
          /* 缓存里已无该体数据 */
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyUid, seriesUid, volumeTag, reloadTick])

  /* ---------------- 器官叠加（labelmap 彩色着色） ---------------- */
  // 依赖：体数据就绪（status.state==='ready'）、叠加开关、分割列表。
  // 掩膜体素顺序 (帧,行,列) 与 cornerstone 体数据 IJK 一致，逐字节映射即可；
  // 掩膜经服务端 zlib 压缩，浏览器端用 DecompressionStream('deflate') 解。
  const overlayKeyRef = useRef('')
  const scalRef = useRef(null)
  useEffect(() => {
    let cancelled = false
    async function build() {
      const stage = (s) => {
        window.__miisOverlayStage = s
      }
      stage('start')
      const vid = volumeIdRef.current
      const engine = engineRef.current
      if (!vid || !engine) {
        stage('no-engine')
        return
      }
      const segId = 'mpr-overlay'
      if (!overlayOn || !overlaySegs.length) {
        stage('off-or-empty')
        if (overlayKeyRef.current) {
          try {
            segmentation.removeSegmentation(segId)
          } catch {
            /* 已移除 */
          }
          try {
            cache.removeVolume(`${vid}-overlay-labelmap`)
          } catch {
            /* 无缓存 */
          }
          overlayKeyRef.current = ''
          engine.renderViewports(VIEWPORTS.map((v) => v.id))
        }
        return
      }
      const ct = cache.getVolume(vid)
      if (!ct || !ct.dimensions) {
        stage('no-ct-volume')
        return
      }
      const [d0, d1, d2] = ct.dimensions
      const key = `${vid}|${overlaySegs.map((s) => s.seg_uid).join(',')}`
      if (key === overlayKeyRef.current) {
        stage('key-unchanged')
        return
      }
      stage('fetch-masks')

      const labelId = `${vid}-overlay-labelmap`
      // 标量数组自管：createLocalVolume 会把 scalarData 按层 subarray 分给
      // 逐层 image（共享同一 ArrayBuffer），写入我们持有的数组即等于写入体数据；
      // 不走 voxelManager.getScalarData（4.22 对 image 型 voxel manager 会抛
      // "No scalar data available"）。
      let label = cache.getVolume(labelId)
      if (!label) {
        scalRef.current = new Uint8Array(d0 * d1 * d2)
        label = volumeLoader.createLocalLabelmapVolume(
          {
            metadata: structuredClone(ct.metadata),
            dimensions: [d0, d1, d2],
            spacing: [...ct.spacing],
            origin: [...ct.origin],
            direction: [...ct.direction],
            scalarData: scalRef.current,
          },
          labelId,
        )
      }
      const scal = scalRef.current
      scal.fill(0)

      const used = []
      for (let s = 0; s < overlaySegs.length; s++) {
        const seg = overlaySegs[s]
        let m
        try {
          m = await api.segmentMask(seg.seg_uid)
        } catch {
          continue // 无像素（NO_PIXEL_DATA）或已失效：跳过，不做假叠加
        }
        if (cancelled) return
        const [frames, rows, cols] = m.shape
        if (cols !== d0 || rows !== d1 || frames !== d2) continue // 栅格不一致：宁缺毋错
        const buf = await (async () => {
          const bin = Uint8Array.from(atob(m.data), (c) => c.charCodeAt(0))
          if (m.encoding !== 'zlib-deflate') return bin
          const ds = new DecompressionStream('deflate')
          const stream = new Blob([bin]).stream().pipeThrough(ds)
          return new Uint8Array(await new Response(stream).arrayBuffer())
        })()
        if (buf.length !== scal.length) continue
        const v = used.length + 1
        for (let i = 0; i < buf.length; i++) if (buf[i]) scal[i] = v
        used.push({
          segmentIndex: v,
          label: seg.name || `分割 ${s + 1}`,
          color: SEG_COLORS[s % SEG_COLORS.length],
        })
      }
      if (cancelled) return

      const colorLUT = [[0, 0, 0, 0], ...used.map((u) => [...u.color, 255])]
      segmentation.config.color.addColorLUT(colorLUT, 1)
      try {
        segmentation.removeSegmentation(segId)
      } catch {
        /* 首次不存在 */
      }
      segmentation.addSegmentations([
        {
          segmentationId: segId,
          representation: {
            type: SegmentationRepresentations.Labelmap,
            data: { volumeId: labelId },
          },
          config: {
            label: '器官叠加',
            segments: Object.fromEntries(used.map((u) => [u.segmentIndex, { label: u.label }])),
          },
        },
      ])
      VIEWPORTS.forEach((vp) =>
        segmentation.addSegmentationRepresentations(vp.id, [
          {
            segmentationId: segId,
            type: SegmentationRepresentations.Labelmap,
            config: { colorLUTOrIndex: 1 },
          },
        ]),
      )
      overlayKeyRef.current = key
      // 调试句柄：门禁/CDP 验证叠加是否真正落进体数据（门禁 G15 消费）
      stage('done')
      window.__miisOverlay = { key: key.slice(-60), used: used.length, labelId }
      engine.renderViewports(VIEWPORTS.map((v) => v.id))
    }
    build().catch((e) => {
      window.__miisOverlayStage = `error: ${e?.message || e}`
      console.error('[MprViewport overlay]', e)
    })
    return () => {
      cancelled = true
    }
    // status.state 变化驱动重建（体数据就绪后才有 derived labelmap 可挂）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayOn, overlaySegs, status.state, studyUid, seriesUid])

  /* ---------------- 模式切换 ---------------- */
  useEffect(() => {
    const engine = engineRef.current
    const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
    if (!engine || !group) return
    applyMode(group, mode)
  }, [mode, measureKind])

  /* ---------------- 重新可见后重算视口尺寸 ---------------- */
  useEffect(() => {
    if (!visible) return undefined
    const engine = engineRef.current
    if (!engine) return undefined
    // 被隐藏（display:none，见 styles.css 的 .is-hidden）的视口 canvas 尺寸会归零，
    // 重新显示后必须让引擎重算，否则画面停在上一次的尺寸上（表现为空白或拉伸）。
    //
    // 不要把它当死代码删掉：这条 effect 由 prop 驱动（依赖 [layoutId, visible]），
    // 用户每切一次 2D/3D 就必跑一次 —— 也就是说在"隐藏没有真正生效"的那段时间里
    // 它照样执行，只是执行了没用（尺寸本来就对，resize 幂等）。看起来"没效果"
    // 不等于"不执行"，而隐藏真的生效之后，它是唯一能让恢复显示的视口不空白的东西。
    //
    // 延时只是等浏览器把布局稳定下来（effect 已在 DOM 更新后跑，但 ResizeObserver
    // 那层还需要一帧），不是调过参数的阈值；与 VrViewport 侧保持同一个值即可，
    // 别把它当精确数值去"优化"。
    const timer = window.setTimeout(() => {
      try {
        engine.resize(true, true)
      } catch {
        /* 引擎已销毁 */
      }
    }, 60)
    return () => window.clearTimeout(timer)
  }, [layoutId, visible])

  /* ---------------- 测量结果：各测量工具完成事件 ---------------- */
  useEffect(() => {
    // 注解 → 载荷的构造全部在 lib/measurementPayload.js 里（纯函数、零依赖），
    // 组件只负责"什么时候调"。这样 TC-07 的 smoke 脚本能在 Node 里直接调同一个
    // 构造器断言 graphic_type / graphic_data / sop_instance_uid，不必起浏览器。
    const capture = (annotation) => {
      const payload = buildMeasurementPayload(annotation)
      if (!payload) return
      setDraft(payload)
    }

    // ANNOTATION_COMPLETED 是同步派发的（LengthTool._endCallback:119），而
    // cachedStats 只在渲染帧里写（renderAnnotation → _calculateCachedStats），
    // 注解渲染又被 AnnotationRenderingEngine._render() 推给了 requestAnimationFrame。
    // 于是「down → move → up 落在同一帧」时，COMPLETED 派发那一刻 stats 还是 {}，
    // 读到的全是 null —— 快速拉一条线拿不到数值，慢速拖拽反而正常。
    //
    // 补录路径监听 ANNOTATION_MODIFIED，且必须过滤 changeType === 'StatsUpdated'：
    // _calculateCachedStats 写完 cachedStats 后自己补发这一次（LengthTool.js:394-399），
    // 是唯一"stats 已就绪"的确定信号。拖拽中另一次 HandlesUpdated（:161）发生在
    // 渲染之前，不加过滤等于把同一个竞态换个地方犯。
    const onCompleted = (evt) => capture(evt?.detail?.annotation)
    const onModified = (evt) => {
      if (evt?.detail?.changeType !== 'StatsUpdated') return
      capture(evt?.detail?.annotation)
    }
    eventTarget.addEventListener(TOOLS_EVENTS.ANNOTATION_COMPLETED, onCompleted)
    eventTarget.addEventListener(TOOLS_EVENTS.ANNOTATION_MODIFIED, onModified)
    return () => {
      eventTarget.removeEventListener(TOOLS_EVENTS.ANNOTATION_COMPLETED, onCompleted)
      eventTarget.removeEventListener(TOOLS_EVENTS.ANNOTATION_MODIFIED, onModified)
    }
  }, [])

  /* ---------------- 取点：视口点击采集世界坐标 ----------------
     同一段采集逻辑服务两个用途：规划取点（入路 / 靶点）与种子点生长。
     两者都需要"轴位上的一个 LPS 点"，区别只在事后用它做什么；
     复制一份监听会在模式切换时留下两条同时生效的通道。 */
  useEffect(() => {
    if (mode !== 'plan' && !seedMode) return undefined
    const engine = engineRef.current
    if (!engine) return undefined

    const handler = (evt) => {
      const world = evt?.detail?.currentPoints?.world
      if (!world) return
      const point = {
        x: Number(world[0].toFixed(2)),
        y: Number(world[1].toFixed(2)),
        z: Number(world[2].toFixed(2)),
      }
      if (seedMode) onSeedPoint?.(point)
      else onPlanPoint?.(point)
    }

    const canvas = gridRef.current?.querySelector('[data-vp="axial"] .mpr-canvas')
    if (!canvas) return undefined
    // 事件名定义在 tools 包（core 包没有这个成员），必须两个都注册。
    // 为什么是 CLICK + UP 而不是任选其一，见模块顶部 PLAN_POINT_EVENTS 的说明。
    PLAN_POINT_EVENTS.forEach((n) => canvas.addEventListener(n, handler))
    return () => PLAN_POINT_EVENTS.forEach((n) => canvas.removeEventListener(n, handler))
  }, [mode, seedMode, onPlanPoint, onSeedPoint])

  /* ---------------- 十字线 → 三维联动 ----------------
     CrosshairsTool 在中心变化时派发 CROSSHAIR_TOOL_CENTER_CHANGED，
     detail.toolCenter 就是世界坐标（本项目的体数据来自 wado-rs，世界系即
     patient_lps_mm，与种子点/规划取点同一套约定，不做二次换算）。
     节流 60ms：拖动十字线时事件按帧派发，父组件每帧重算三维投影会掉帧。 */
  useEffect(() => {
    if (!onCrosshair) return undefined
    const handler = (evt) => {
      const center = evt?.detail?.toolCenter
      if (!Array.isArray(center) || center.length !== 3) return
      if (!center.every((v) => Number.isFinite(v))) return
      const now = Date.now()
      if (now - crosshairTickRef.current < 60) return
      crosshairTickRef.current = now
      onCrosshair({
        x: Number(center[0].toFixed(2)),
        y: Number(center[1].toFixed(2)),
        z: Number(center[2].toFixed(2)),
      })
    }
    eventTarget.addEventListener(TOOLS_EVENTS.CROSSHAIR_TOOL_CENTER_CHANGED, handler)
    return () =>
      eventTarget.removeEventListener(TOOLS_EVENTS.CROSSHAIR_TOOL_CENTER_CHANGED, handler)
  }, [onCrosshair])

  // 初始位置：工具只在"用户动过"之后派发事件，刚加载完要先主动报一次，
  // 否则三维里直到医生拖一下十字线才会出现准星。
  useEffect(() => {
    if (status.state !== 'ready' || !onCrosshair) return
    const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
    const tool = group?.getToolInstance?.(CrosshairsTool.toolName)
    const center = tool?.toolCenter
    if (!Array.isArray(center) || !center.every((v) => Number.isFinite(v))) return
    onCrosshair({
      x: Number(center[0].toFixed(2)),
      y: Number(center[1].toFixed(2)),
      z: Number(center[2].toFixed(2)),
    })
  }, [status.state, onCrosshair])

  /** 截图：把当前布局里可见的视口拼成一张带信息条的 PNG。 */
  const captureShot = () => {
    const cells = Array.from(gridRef.current?.querySelectorAll('[data-vp]') || []).filter(
      (el) => !el.classList.contains('is-hidden'),
    )
    const canvases = cells.map((el) => el.querySelector('canvas'))
    const labels = cells.map(
      (el) => VIEWPORTS.find((v) => v.id === el.dataset.vp)?.label || el.dataset.vp,
    )
    const info = captureInfo || {}
    const ok = savePng({
      canvases,
      labels,
      columns: canvases.length,
      title: [info.title, info.modality].filter(Boolean).join(' · ') || '阅片截图',
      footer: [
        info.patient ? `患者 ${info.patient}` : '',
        info.series ? `序列 ${info.series}` : '',
        `窗位 ${preset}`,
        blendId === 'composite' ? '合成' : blendId === 'mip' ? 'MIP' : 'MinIP',
        spacingText ? `层厚 ${spacingText}` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      nameParts: [info.caseNo || info.patient || 'case', info.modality || 'mpr', preset],
    })
    setShotMsg(ok ? '截图已保存' : '视口尚未渲染完成，暂无可截取画面')
    window.setTimeout(() => setShotMsg(''), 3000)
  }

  function applyMode(group, next) {
    const target = MODES.find((m) => m.id === next) || MODES[0]
    const primary = cornerstoneTools.Enums.MouseBindings.Primary
    // 测量模式下左键绑定哪个工具，取决于当前选的测量类型
    const measureTool =
      (MEASURE_KINDS.find((k) => k.id === measureKind) || MEASURE_KINDS[0]).tool
    const activeTool = target.id === 'measure' ? measureTool : target.tool

    // setToolPassive 默认会移除工具的主键绑定
    MODES.forEach((m) => {
      if (m.tool) group.setToolPassive(m.tool)
    })
    // 测量类工具不在 MODES 里，必须单独让出主键，否则切换测量类型时
    // 上一个工具仍占着左键（setToolActive 会合并绑定，清不掉）
    MEASURE_KINDS.forEach((k) => group.setToolPassive(k.tool))

    if (activeTool) {
      group.setToolActive(activeTool, { bindings: [{ mouseButton: primary }] })
    } else {
      // 规划取点模式不激活任何工具：取点靠视口点击事件独立采集，
      // 让出全部鼠标键给平移 / 缩放 / 滚轮
      group.setToolPassive(CrosshairsTool.toolName, { removeAllBindings: true })
    }
    if (target.tool !== CrosshairsTool.toolName) {
      // setToolActive 会把新绑定与旧绑定合并，传空数组清不掉已有的 Primary。
      // 十字线在非定位模式下要完全让出鼠标，用 removeAllBindings 彻底静默。
      group.setToolPassive(CrosshairsTool.toolName, { removeAllBindings: true })
    }
  }

  const applyPreset = (p) => {
    const engine = engineRef.current
    if (!engine) return
    VIEWPORTS.forEach((vp) => {
      const viewport = engine.getViewport(vp.id)
      if (!viewport) return
      viewport.setProperties({ voiRange: { lower: p.lower, upper: p.upper } })
    })
    engine.renderViewports(VIEWPORTS.map((v) => v.id))
    setPreset(p.label)
  }

  const applyBlend = (id) => {
    const engine = engineRef.current
    if (!engine) return
    // core 的 BlendModes 真实成员只有 COMPOSITE / MAXIMUM_INTENSITY_BLEND /
    // MINIMUM_INTENSITY_BLEND / AVERAGE_INTENSITY_BLEND —— 没有 MIP。
    // 传 undefined 会让 setBlendMode 变成静默空操作（上一轮就是这么失效的）。
    const target = BLEND_MODES.find((b) => b.id === id) || BLEND_MODES[0]
    VIEWPORTS.forEach((vp) => {
      const viewport = engine.getViewport(vp.id)
      if (!viewport?.setBlendMode) return
      viewport.setBlendMode(target.blend)
    })
    engine.renderViewports(VIEWPORTS.map((v) => v.id))
    setBlendId(target.id)
  }

  const discardDraft = () => {
    const engine = engineRef.current
    const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID)
    if (draft && engine && group) {
      try {
        const viewport = engine.getViewport('axial')
        cornerstoneTools.annotation.state.removeAnnotation(draft.annotationUid)
        viewport?.render()
      } catch {
        /* 标注已被清理 */
      }
    }
    setDraft(null)
  }

  const commitDraft = () => {
    if (!draft) return
    onMeasureReady?.({
      type: measurementTypeOf(draft.kind),
      value: draft.value,
      unit: draft.unit,
      graphicType: draft.graphicType,
      graphicData: draft.graphicData,
      // 体渲染下"这条测量画在哪张图上"由 Cornerstone 反查得出，见 capture() 里的说明。
      // 服务端拿不到它就无法生成 ReferencedSOPSequence（契约 §7.3）—— 传 null 让它
      // 422，不要用假 UID 顶替。
      sopInstanceUid: draft.sopInstanceUid,
    })
    setDraft(null)
  }

  return (
    <div className="viewer">
      <div className="viewer-toolbar">
        <span className={`viewer-status ${status.state}`}>
          <span className="dot" />
          {status.state === 'loading' && status.message}
          {status.state === 'ready' && `${status.message} · ${preset}`}
          {status.state === 'error' && `渲染失败：${status.message}`}
        </span>

        <span className="spacer" />

        {/* 工具组：鼠标左键当前绑定的操作 */}
        <div className="toolgroup" role="group" aria-label="鼠标工具">
          {MODES.map((m) => (
            <button
              key={m.id}
              className={mode === m.id ? 'is-active' : ''}
              aria-pressed={mode === m.id}
              onClick={() => setMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* 工具组：窗宽窗位预设 */}
        <div className="toolgroup" role="group" aria-label="窗宽窗位预设">
          {WL_PRESETS.map((p) => (
            <button
              key={p.label}
              className={preset === p.label ? 'is-active' : ''}
              onClick={() => applyPreset(p)}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* 合成 / MIP / MinIP */}
        <div className="toolgroup" role="group" aria-label="投影模式">
          {BLEND_MODES.map((b) => (
            <button
              key={b.id}
              className={blendId === b.id ? 'is-active' : ''}
              aria-pressed={blendId === b.id}
              onClick={() => applyBlend(b.id)}
            >
              {b.label}
            </button>
          ))}
        </div>

        {/* 多窗口布局 */}
        <div className="toolgroup" role="group" aria-label="窗口布局">
          {LAYOUTS.map((l) => (
            <button
              key={l.id}
              className={layoutId === l.id ? 'is-active' : ''}
              aria-pressed={layoutId === l.id}
              onClick={() => setLayoutId(l.id)}
            >
              {l.label}
            </button>
          ))}
        </div>

        {/* 截图：把当前布局的可见视口拼成一张图，信息条随图烧入 */}
        <button onClick={captureShot} disabled={status.state !== 'ready'}>
          截图
        </button>
        {shotMsg && <span className="shot-msg">{shotMsg}</span>}
      </div>

      {/* 测量类型：长度 / 角度 / 面积 */}
      {mode === 'measure' && (
        <div className="toolgroup toolgroup-sub" role="group" aria-label="测量类型">
          {MEASURE_KINDS.map((k) => (
            <button
              key={k.id}
              className={measureKind === k.id ? 'is-active' : ''}
              aria-pressed={measureKind === k.id}
              onClick={() => setMeasureKind(k.id)}
            >
              {k.label}
            </button>
          ))}
        </div>
      )}

      {/* 布局切换不卸载视口元素：engine.setViewports 已经把 viewport 绑定到
          具体 DOM 节点，卸载会让引擎渲染到游离节点上。改为 CSS 隐藏 +
          切换后 engine.resize() 重算 canvas 尺寸。 */}
      <div className={`mpr-grid layout-${activeLayout.id}`} ref={gridRef}>
        {VIEWPORTS.map((vp) => (
          <div
            key={vp.id}
            className={`mpr-cell${activeLayout.viewports.includes(vp.id) ? '' : ' is-hidden'}`}
            data-vp={vp.id}
          >
            <span className="mpr-label">{vp.label}</span>
            <span className="mpr-info">
              {status.state === 'ready'
                ? vp.id === 'axial'
                  ? `${status.count} 层 · ${spacingText}`
                  : spacingText
                : ''}
            </span>
            <div className="mpr-canvas" />
            <div className="mpr-orient">
              <span>R</span>
              <span>A</span>
              <span>S</span>
            </div>
          </div>
        ))}

        {/* 第四格：影像信息占位。三维容积渲染（VR/SSR）不再依赖桌面端，
            已由 VrViewport 在 Web 端实现，经工具栏「2D / 3D」切换进入 */}
        <div className="mpr-cell mpr-info-cell">
          <span className="mpr-label">检查信息</span>
          <dl className="study-meta">
            <div>
              <dt>层数</dt>
              <dd>{status.count ?? '—'}</dd>
            </div>
            <div>
              <dt>矩阵</dt>
              <dd>{status.rows ?? '—'} × {status.columns ?? '—'}</dd>
            </div>
            <div>
              <dt>体素间距</dt>
              <dd>{spacingText || '—'}</dd>
            </div>
            <div>
              <dt>窗宽窗位</dt>
              <dd>{preset}</dd>
            </div>
            <div>
              <dt>传输语法</dt>
              <dd className="mono-sm">WADO-RS</dd>
            </div>
          </dl>
        </div>
      </div>

      {mode === 'measure' && (
        <div className="measure-bar">
          {draft ? (
            <>
              <span className="mono">
                {draft.value} {draft.unit}
              </span>
              <span>{draft.label}测量结果</span>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="primary" onClick={commitDraft}>写入 DICOM SR</button>
              <button onClick={discardDraft}>清除</button>
            </>
          ) : (
            <span>
              {measureKind === 'angle'
                ? '在轴位视口依次点三点画出夹角（第一点为顶点）'
                : measureKind.startsWith('area')
                  ? '在轴位视口拖拽画出区域，松手后自动换算平方毫米'
                  : '在轴位视口拖拽画出线段，松手后自动换算毫米'}
            </span>
          )}
        </div>
      )}

      {mode === 'magnify' && (
        <div className="measure-bar">
          <span>放大镜：按住左键在视口上拖动，镜内放大倍率随停留时间递增</span>
        </div>
      )}

      {mode === 'plan' && (
        <div className="measure-bar">
          <span>在轴位视口点击拾取入路点与靶点，坐标将填入右侧规划表单</span>
        </div>
      )}

      {error && (
        <div className="error-banner" style={{ margin: 12, alignItems: 'center' }}>
          <span style={{ flex: 1 }}>
            <span className="code">VIEWPORT_ERROR</span> · {error}
          </span>
          <button
            className="ghost sm"
            onClick={() => {
              setError(null)
              report({ state: 'loading', message: '重新加载影像…' })
              setReloadTick((n) => n + 1)
            }}
          >
            重新加载影像
          </button>
        </div>
      )}

      <div className="viewer-hint">
        左键 = 当前工具 · 右键缩放 · 中键平移 · 滚轮切换切片 · 比例尺已开启
      </div>
    </div>
  )
}
