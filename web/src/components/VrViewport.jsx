import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cornerstoneCore,
  cornerstoneTools,
  ensureVrToolGroup,
  initCornerstone,
  loadSeriesImageIds,
  VR_TOOL_GROUP_ID,
} from '../api/dicom'
// 传递函数的纯计算部分在 lib 下，便于 node --test 直接测（本模块顶层就需要 DOM）
import { scaleOpacity, toSurfaceShaded } from '../lib/vrPresets'
import { makeVolumeId } from '../lib/volumeId'
import { downloadDataUrl, fileStem, savePng, timestamp } from '../lib/capture'

const {
  RenderingEngine,
  Enums,
  CONSTANTS,
  eventTarget,
  volumeLoader,
  cache,
  setVolumesForViewports,
  convertMapperToNotSharedMapper,
} = cornerstoneCore

const { ToolGroupManager } = cornerstoneTools

const VIEWPORT_ID = 'vr3d'
const BACKGROUND = [0.02, 0.04, 0.05]

/**
 * 把三维相机放到能看清体数据的位置。
 *
 * 4.22 的 resetCamera 对 VOLUME_3D 把相机放在 10× 包围球半径处
 * （core/dist/esm/RenderingEngine/Viewport.js：radius × 10，配合 90° 视角，
 * 体数据只占画面高度约 7%，实际观感是黑屏 + 中心一个点）。这里把相机
 * 拉回 1.2× 半径处，并重算裁剪面 —— setCamera 不会替三维视口更新裁剪
 * 范围，旧裁剪面跟着旧距离走，相机挪近后体数据反而落在裁剪面之外。
 */
function frameVolumeCamera(viewport, volume) {
  if (!viewport || !volume) return
  const [nx, ny, nz] = volume.dimensions || []
  const [sx, sy, sz] = volume.spacing || []
  if (!nx || !sx) return
  const radius = 0.5 * Math.hypot(nx * sx, ny * sy, nz * sz)
  const cam = viewport.getCamera()
  const { focalPoint, viewPlaneNormal, viewAngle } = cam
  const distance = radius * 1.2
  viewport.setCamera({
    focalPoint,
    position: [
      focalPoint[0] + distance * viewPlaneNormal[0],
      focalPoint[1] + distance * viewPlaneNormal[1],
      focalPoint[2] + distance * viewPlaneNormal[2],
    ],
    viewAngle: viewAngle || 90,
  })
  viewport.getRenderer()?.resetCameraClippingRange()
}

/**
 * 已核实的 API 真实名称（Cornerstone3D 4.22.13，逐项来自 node_modules）：
 *
 * - 三维视口类型：Enums.ViewportType.VOLUME_3D（值 'volume3d'）
 *   来源 @cornerstonejs/core/dist/esm/enums/ViewportType.js
 *   —— 不是 'VOLUME' / '3D'，写错会在 setViewports 时抛 unsupported viewport type。
 *
 * - VR 渲染预设：viewport.setProperties({ preset })，内部转发到
 *   setPreset(presetNameOrPresetObject, volumeId, suppressEvents)
 *   来源 @cornerstonejs/core/dist/esm/RenderingEngine/BaseVolumeViewport.js:789 / :885
 *   预设名是字符串且区分大小写，真实取值清单在
 *   @cornerstonejs/core/dist/esm/constants/viewportPresets.js
 *   （'CT-Bone' / 'CT-Soft-Tissue' / 'MR-Default' / 'CT-MIP' …）；
 *   预设对象数组由 core 导出为 CONSTANTS.VIEWPORT_PRESETS。
 *   注意：setProperties 里没有 "opacity" 这个属性名，不透明度只能改写
 *   传递函数（scalarOpacity）实现，见下面的 scaleOpacity。
 *
 * - 旋转工具：cornerstoneTools.TrackballRotateTool，toolName === 'TrackballRotate'
 *   来源 @cornerstonejs/tools/dist/esm/tools/TrackballRotateTool.js:180
 *   （另有 VolumeRotateTool，toolName 'VolumeRotate'，本项目用 Trackball）
 *
 * - 鼠标绑定：cornerstoneTools.Enums.MouseBindings.{Primary,Auxiliary,Secondary,Wheel}
 *   来源 @cornerstonejs/tools/dist/esm/enums/ToolBindings.js
 *
 * - 相机事件：Enums.Events.CAMERA_MODIFIED（core 包，值 'CORNERSTONE_CAMERA_MODIFIED'）
 *   来源 @cornerstonejs/core/dist/esm/enums/Events.js
 *   旋转 / 缩放会触发它，用来重算入路轴线的屏幕投影。
 *   （注解完成等事件才在 @cornerstonejs/tools 的 Enums.Events 里，本组件不涉及）
 *
 * - 世界坐标 → 画布坐标：viewport.worldToCanvas([x, y, z])，返回值已是 CSS 像素
 *   来源 @cornerstonejs/core/dist/esm/RenderingEngine/BaseVolumeViewport.js 的
 *   worldToCanvasTiled / worldToCanvasContextPool（内部已除以 devicePixelRatio）
 */

/** 默认 VR 预设；名字必须与 constants/viewportPresets.js 完全一致。 */
function basePresetName(modality) {
  const m = (modality || '').toUpperCase()
  if (m.startsWith('MR')) return 'MR-Default'
  if (m === 'PT' || m === 'NM') return 'CT-Soft-Tissue'
  // 不要默认 CT-Bone：它的不透明度斜坡从约 300HU 才起来，体数据里若没有
  // 骨密度组织就整屏全透明 —— 用户看到的是「三维功能坏了」，实际只是预设选错。
  // CT-AAA 是覆盖范围最广的通用预设，作为默认更安全。
  return 'CT-AAA'
}

/** 可选预设。名称直接取自 CONSTANTS.VIEWPORT_PRESETS，不手写字符串。 */
const PRESET_CHOICES = [
  'CT-AAA',
  'CT-Bone',
  'CT-Soft-Tissue',
  'CT-Lung',
  'CT-Chest-Vessels',
  'MR-Default',
  'MR-Angio',
  'MR-T2-Brain',
]

function findPreset(name) {
  return CONSTANTS.VIEWPORT_PRESETS.find((p) => p.name === name)
}

/**
 * 三维容积重建视口（VR / SSR）。
 *
 * 体数据与 MPR 共用同一个 volumeId（见 lib/volumeId.js，按 seriesUid + volumeTag
 * 生成），命中 cache 就复用，不重复下载；交互走独立的 miis-vr 工具组。
 */
export default function VrViewport({
  studyUid,
  seriesUid,
  volumeTag = 'v1',
  modality,
  entryPoint,
  targetPoint,
  focusPoint,
  captureInfo,
  visible = true,
  onStatus,
}) {
  const stageRef = useRef(null)
  const engineRef = useRef(null)
  const loadTimeoutRef = useRef(null)
  const counterRef = useRef({ loaded: 0, total: 0 })
  const recRef = useRef(null)

  const [status, setStatus] = useState({ state: 'idle', message: '' })
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('vr')
  const [opacity, setOpacity] = useState(1)
  const [cameraTick, setCameraTick] = useState(0)
  const [recording, setRecording] = useState(false)
  const [shotMsg, setShotMsg] = useState('')
  // 预设可在界面上改：不同预设的传递函数差异极大，选错就是整屏全透明
  const [presetName, setPresetName] = useState(() => basePresetName(modality))

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

  const basePreset = useMemo(
    () => findPreset(presetName) || CONSTANTS.VIEWPORT_PRESETS[0],
    [presetName],
  )

  /* ---------------- 主流程：体数据（可复用） + 三维视口装配 ---------------- */
  useEffect(() => {
    if (!studyUid || !seriesUid) return undefined

    let cancelled = false
    let engine = null
    const engineId = `vr-${seriesUid.slice(-12)}`
    const volumeId = makeVolumeId(seriesUid, volumeTag)

    const onImageLoaded = (evt) => {
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
      ensureVrToolGroup()

      const el = stageRef.current?.querySelector('.vr-canvas')
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

      // MPR 可能已经把该序列建成体数据并缓存，命中就复用：
      // 体数据是几百 MB 级，同一序列重复构建会直接撑爆显存。
      let volume = cache.getVolume(volumeId)
      if (!volume) {
        report({ state: 'loading', message: `重建体数据 ${entries.length} 层` })
        // 同 MPR：若整个序列都已在图像缓存里，core 会走
        // createAndCacheVolumeFromImagesSync 造一个没有像素数据、也没有
        // loadStatus 的静态 ImageVolume，既画不出东西又不会报错。
        // 先摘掉图像缓存，强制走流式路径。
        // 只摘最后一张即可让 core 走流式路径，其余张留缓存以免重复下载
        try {
          cache.removeImageLoadObject(imageIds[imageIds.length - 1], { force: true })
        } catch {
          /* 不在缓存里 */
        }
        // 解码 worker 异常退出时该 Promise 可能永不 settle，加超时兜底，
        // 否则界面会静默停在「重建体数据」（同 MPR 视口的老问题）
        volume = await Promise.race([
          volumeLoader.createAndCacheVolumeFromImages(volumeId, imageIds),
          new Promise((_, reject) =>
            window.setTimeout(
              () => reject(new Error('重建体数据超时（解码 worker 无响应）')),
              60000,
            ),
          ),
        ])
      }
      if (cancelled) return

      if (!volume.loadStatus?.loaded) {
        report({ state: 'loading', message: `灌入体数据 ${entries.length} 层` })
        await new Promise((resolve) => {
          let settled = false
          const done = () => {
            if (settled) return
            settled = true
            if (loadTimeoutRef.current) {
              window.clearTimeout(loadTimeoutRef.current)
              loadTimeoutRef.current = null
            }
            resolve()
          }
          volume.load(done)
          loadTimeoutRef.current = window.setTimeout(done, 120000)
        })
      }
      if (cancelled) return

      engine = new RenderingEngine(engineId)
      engineRef.current = engine

      engine.setViewports([
        {
          viewportId: VIEWPORT_ID,
          type: Enums.ViewportType.VOLUME_3D,
          element: el,
          // parallelProjection 显式给 false：VolumeViewport3D 的构造函数
          // 读 this.options.parallelProjection，留空就沿用 vtk 相机默认，
          // 而容积渲染需要透视投影才有正常的深度观感。
          defaultOptions: { background: BACKGROUND, parallelProjection: false },
        },
      ])

      await setVolumesForViewports(engine, [{ volumeId, callback: () => {} }], [
        VIEWPORT_ID,
      ])
      if (cancelled) return

      // 三维视口必须用自己的纹理，不能沿用 cornerstone 的共享体纹理。
      // 共享纹理（vtkSharedVolumeMapper）按 volumeId 全局只有一份 GPU 对象，
      // 第一次在 MPR 引擎的上下文里上传；本视口是另一个 RenderingEngine、
      // 另一个 GL 上下文，跨上下文 bind 同一个纹理对象只会得到
      // "bindTexture: object does not belong to this context"，采样恒为空，
      // 表现为整屏黑。convertMapperToNotSharedMapper 让本视口持有一份
      // 独立纹理（代价是一次 64MB 级的标量数组拷贝，仅做一次）。
      const vrViewport = engine.getViewport(VIEWPORT_ID)
      const vrActor = vrViewport?.getActors?.()[0]?.actor
      if (vrActor?.getMapper?.()?.getClassName?.() === 'vtkSharedVolumeMapper') {
        vrActor.setMapper(convertMapperToNotSharedMapper(vrActor.getMapper()))
      }

      const group = ToolGroupManager.getToolGroup(VR_TOOL_GROUP_ID)
      if (group) group.addViewport(VIEWPORT_ID, engineId)

      const viewport = engine.getViewport(VIEWPORT_ID)
      if (viewport) {
        // 注意：不能把 MPR 的相机常量套到三维视口上。
        // CONSTANTS.MPR_CAMERA_VALUES.coronal 是为二维 MPR 设计的（含
        // viewRight 等），套进来会让 vtk 相机变成正交投影、并被钉在
        // 距体数据 2.6 米的位置上，配合容积渲染就是整屏全黑。
        // 三维视口只需 resetCamera 让引擎按体数据包围盒自行取景。
        viewport.resetCamera({ resetPan: true, resetZoom: true, resetToCenter: true })
        // 但 4.22 的 resetCamera 对 VOLUME_3D 刻意放在 10× 包围球半径处
        // （core/dist/esm/RenderingEngine/Viewport.js：radius × 10），90° 视角下
        // 体数据只占画面高度约 7%，实际观感仍是黑屏 + 中心一个点。
        // 把相机拉回 1.2× 半径处并重算裁剪面，体数据才能充满视口。
        frameVolumeCamera(viewport, volume)
        // 两处会把相机打回 10× 半径的默认位：
        // 1) 画布尺寸稳定后的 ResizeObserver（TrackballRotateTool 自带一个，
        //    触发时直接 viewport.resetCamera()）；
        // 2) ContextPool 引擎 resize 走的 resetCameraForResize。
        // 包一层 resetCamera：每次重置后立即按体数据重新取景。这也让
        // 「重置视角」按钮自动获得同样行为。
        const origResetCamera = viewport.resetCamera.bind(viewport)
        viewport.resetCamera = (...args) => {
          const result = origResetCamera(...args)
          frameVolumeCamera(viewport, volume)
          return result
        }
        viewport.setProperties({ preset: basePreset })
        viewport.render()
      }

      engine.renderViewport(VIEWPORT_ID)

      // 临时诊断出口：把渲染器的真实内部状态暴露给探针。
      // 三维容积渲染失败时画面全黑，仅凭截图无法区分「没挂上体数据」
      // 「相机看向空处」「传递函数把整段数据映射成 0」这几种完全不同的原因，
      // 必须直接读 actor / camera / 标量范围。
      window.__vrDebug = () => {
        const vp = engine.getViewport(VIEWPORT_ID)
        const actors = vp?.getActors?.() || []
        const actor = actors[0]?.actor
        const mapper = actor?.getMapper?.()
        const imgData = mapper?.getInputData?.()
        let mapperInfo = null
        try {
          mapperInfo = {
            className: mapper?.getClassName?.(),
            inputData: !!mapper?.getInputData?.(),
            blendMode: mapper?.getBlendMode?.(),
            sampleDistance: mapper?.getSampleDistance?.(),
          }
        } catch (e) {
          mapperInfo = `err: ${e}`
        }
        let textureInfo = null
        try {
          const tex = vol?.vtkOpenGLTexture
          textureInfo = {
            exists: !!tex,
            className: tex?.getClassName?.(),
            ready: tex?.getReadyState?.(),
            dimensions: tex?.getDimensions?.(),
          }
        } catch (e) {
          textureInfo = `err: ${e}`
        }
        const scalars = imgData?.getPointData?.()?.getScalars?.()
        const vol = cache.getVolume(volumeId)
        let volImgScalars = null
        try {
          const id = vol?.imageData
          const sc = id?.getPointData?.()?.getScalars?.()
          volImgScalars = {
            dims: id?.getDimensions?.() ?? null,
            hasScalars: !!sc,
            tuples: sc?.getNumberOfTuples?.() ?? null,
            range: sc?.getRange?.() ?? null,
          }
        } catch (e) {
          volImgScalars = `err: ${e}`
        }

        // 体数据标量范围：传递函数按绝对 HU 取值，不知道数据落在哪一段
        // 就无法判断"整屏透明"是预设选错还是渲染坏了。
        // 注意不能走 getScalarData()：流式体数据用 VoxelManager 惰性分片
        // 存储，没有连续数组，getScalarData() 会直接抛
        // "No scalar data available"（实测），那会把"有数据"误判成"空"。
        // 改用 voxelManager.getAtIJK 栅格抽样。
        let dataMin = null
        let dataMax = null
        const samples = {}
        try {
          const vm = vol?.voxelManager
          const [nx, ny, nz] = vol?.dimensions || [0, 0, 0]
          if (vm?.getAtIJK && nx) {
            let mn = Infinity
            let mx = -Infinity
            for (let i = 0; i < nx; i += 8) {
              for (let j = 0; j < ny; j += 8) {
                for (let k = 0; k < nz; k += 8) {
                  const v = vm.getAtIJK(i, j, k)
                  if (Number.isFinite(v)) {
                    if (v < mn) mn = v
                    if (v > mx) mx = v
                  }
                }
              }
            }
            dataMin = mn
            dataMax = mx
            // 中心与几个代表点的实际体素值，用来确认数据不是全 0
            samples.center = vm.getAtIJK(nx >> 1, ny >> 1, nz >> 1)
            samples.spine = vm.getAtIJK(nx >> 1, Math.floor(ny * 0.75), nz >> 1)
            samples.corner = vm.getAtIJK(2, 2, 2)
          }
        } catch (e) {
          dataMin = `err: ${e}`
        }

        // 传递函数实际生效的采样点（applyPreset 之后已是绝对 HU）
        let opacityPts = null
        try {
          const f = actor?.getProperty?.()?.getScalarOpacity?.(0)
          if (f?.getSize) {
            const n = f.getSize()
            const pts = []
            for (let i = 0; i < n; i += 1) pts.push(f.getValue?.(i))
            opacityPts = pts
          }
        } catch (e) {
          opacityPts = `err: ${e}`
        }

        return {
          actorCount: actors.length,
          actorUid: actors[0]?.uid,
          actorVisible: actor?.getVisibility?.(),
          mapperHasInput: !!imgData,
          imgExtent: imgData?.getExtent?.() ?? null,
          imgSpacing: imgData?.getSpacing?.() ?? null,
          scalarRange: scalars?.getRange?.() ?? null,
          volumeScalarRange: [dataMin, dataMax],
          volumeDims: vol?.dimensions ?? null,
          volumeSpacing: vol?.spacing ?? null,
          volumeMetadata: vol?.metadata ? Object.keys(vol.metadata) : null,
          camera: vp?.getCamera?.(),
          vtkCamera: (() => {
            const c = vp?.getVtkActiveCamera?.()
            if (!c) return null
            let pmSet = null
            try {
              const pm = c.get('projectionMatrix')?.projectionMatrix
              pmSet = !!pm && pm.length > 0
            } catch (e) {
              pmSet = `err: ${e}`
            }
            return {
              className: c.getClassName?.(),
              viewAngle: c.getViewAngle?.(),
              distance: c.getDistance?.(),
              physicalScale: c.get('physicalScale')?.physicalScale,
              projectionMatrixCached: pmSet,
              clippingRange: c.getClippingRange?.(),
            }
          })(),
          // 传递函数在关键 HU 位置的取值：CT-AAA 在软组织窗（0~60HU）应有
          // 明显不透明度。采样全 0 说明传递函数本身是空的（预设没应用上），
          // 采样正常但画面黑则问题在渲染管线侧。
          opacitySamples: (() => {
            try {
              const f = actor?.getProperty?.()?.getScalarOpacity?.(0)
              if (!f) return null
              return [-1000, -500, -100, 0, 100, 300, 600, 1000].map(
                (x) => [x, Number(f.getValue(x).toFixed(4))],
              )
            } catch (e) {
              return `err: ${e}`
            }
          })(),
          rgbInfo: (() => {
            try {
              const cfun = actor?.getProperty?.()?.getRGBTransferFunction?.(0)
              if (!cfun) return null
              return {
                range: cfun.getRange?.(),
                samples: [-500, 0, 100, 300].map((x) => {
                  const out = [0, 0, 0]
                  cfun.getColor(x, out)
                  return [x, out.map((v) => Number(v.toFixed(3)))]
                }),
              }
            } catch (e) {
              return `err: ${e}`
            }
          })(),
          propertiesPreset: vp?.getProperties?.()?.preset?.name ?? null,
          opacityPts,
          volumeImageData: volImgScalars,
          mapperInfo,
          textureInfo,
          texExists: !!vol?.vtkOpenGLTexture,
          volumeId,
        }
      }

      // 诊断辅助：用任意预设强制覆盖当前传递函数，用来区分
      // "预设选错（数据落在传递函数 0 区间）"与"渲染结构坏了"。
      // 传 {scalarOpacity:'2 -1024 1 714 1'} 即整段体数据不透明灰阶。
      window.__vrForcePreset = (preset) => {
        const vp = engine.getViewport(VIEWPORT_ID)
        if (!vp) return 'no vp'
        vp.setProperties({ preset })
        vp.render()
        return 'ok'
      }
      window.__vrOpacityNodes = () => {
        const actors = engine.getViewport(VIEWPORT_ID)?.getActors?.() || []
        const f = actors[0]?.actor?.getProperty?.()?.getScalarOpacity?.(0)
        if (!f?.getSize) return 'no fn'
        const n = f.getSize()
        const pts = []
        for (let i = 0; i < n; i += 1) {
          const node = f.getNodeValue(i)
          pts.push(Array.isArray(node) ? node : [node, f.getValue(node)])
        }
        return { n, pts }
      }
      // 诊断辅助：reset=再跑一次 resetCamera；传 {position,...} 则直接 setCamera。
      window.__vrCam = (patch) => {
        const vp = engine.getViewport(VIEWPORT_ID)
        if (!vp) return 'no vp'
        if (patch === 'reset') {
          vp.resetCamera({ resetPan: true, resetZoom: true, resetToCenter: true })
        } else if (patch && typeof patch === 'object') {
          vp.setCamera(patch)
        }
        vp.render()
        return vp.getCamera()
      }

      const dims = volume.dimensions || []
      report({
        state: 'ready',
        message: `${entries.length} 层 · ${dims[0] ?? '—'}×${dims[1] ?? '—'}×${dims[2] ?? '—'}`,
        count: entries.length,
      })
    }

    boot().catch((e) => {
      if (cancelled) return
      console.error('[VrViewport]', e)
      setError(e?.message || String(e))
      report({ state: 'error', message: e?.message || String(e) })
    })

    return () => {
      cancelled = true
      eventTarget.removeEventListener(Enums.Events.IMAGE_LOADED, onImageLoaded)
      if (loadTimeoutRef.current) {
        window.clearTimeout(loadTimeoutRef.current)
        loadTimeoutRef.current = null
      }
      const group = ToolGroupManager.getToolGroup(VR_TOOL_GROUP_ID)
      if (group && engine) {
        try {
          group.removeViewports(engine.id, VIEWPORT_ID)
        } catch {
          /* 已销毁 */
        }
      }
      if (engine) {
        try {
          engine.destroy()
        } catch {
          /* 已销毁 */
        }
      }
      engineRef.current = null
      // 体数据不在这里清理：volumeId 与 MPR 共用，删掉会让切回 2D 时重新下载。
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyUid, seriesUid, volumeTag])

  /* ---------------- 重新可见后重算视口尺寸 ---------------- */
  useEffect(() => {
    if (!visible) return undefined
    const engine = engineRef.current
    if (!engine) return undefined
    // 被 display:none 隐藏时 canvas 尺寸归零，重新显示必须让引擎重算，
    // 否则三维画面停在上一次的尺寸上（表现为空白或错位的投影）。
    // 延时只是等浏览器把布局稳定下来（prop 变更后 effect 已在 DOM 更新后跑，
    // 但 ResizeObserver 那一层还需要一帧），不是调过参数的阈值，与 MPR 侧
    // 保持同一个值即可，不要把它当成精确数值去"优化"。
    const timer = window.setTimeout(() => {
      try {
        engine.resize(true, true)
        const vp = engine.getViewport(VIEWPORT_ID)
        if (vp) vp.render()
      } catch {
        /* 引擎已销毁 */
      }
    }, 60)
    return () => window.clearTimeout(timer)
  }, [visible])

  /* ---------------- VR / SSR 与不透明度：重新合成传递函数 ---------------- */
  useEffect(() => {
    const viewport = engineRef.current?.getViewport(VIEWPORT_ID)
    if (!viewport || status.state !== 'ready') return
    const preset = mode === 'ssr' ? toSurfaceShaded(basePreset) : basePreset
    viewport.setProperties({ preset: scaleOpacity(preset, opacity) })
    viewport.render()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, opacity, basePreset, status.state])

  /* ---------------- 相机变化 → 重算入路轴线投影 ---------------- */
  useEffect(() => {
    const el = stageRef.current?.querySelector('.vr-canvas')
    if (!el) return undefined
    const handler = () => setCameraTick((t) => t + 1)
    el.addEventListener(Enums.Events.CAMERA_MODIFIED, handler)
    return () => el.removeEventListener(Enums.Events.CAMERA_MODIFIED, handler)
  }, [])

  /** 入路轴线：入路点 → 靶点，并按同方向向靶点后方延长一段表示穿刺走向 */
  const trajectory = useMemo(() => {
    if (!entryPoint || !targetPoint) return null
    const entry = [entryPoint.x, entryPoint.y, entryPoint.z]
    const target = [targetPoint.x, targetPoint.y, targetPoint.z]
    const d = [target[0] - entry[0], target[1] - entry[1], target[2] - entry[2]]
    const length = Math.hypot(...d)
    if (!Number.isFinite(length) || length < 1e-6) return null
    const dir = d.map((v) => v / length)
    const beyond = target.map((v, i) => v + dir[i] * length * 0.35)
    return { entry, target, beyond, length }
  }, [entryPoint, targetPoint])

  const overlay = useMemo(() => {
    if (!trajectory || status.state !== 'ready') return null
    const viewport = engineRef.current?.getViewport(VIEWPORT_ID)
    if (!viewport?.worldToCanvas) return null
    let projected
    try {
      projected = [trajectory.entry, trajectory.target, trajectory.beyond].map((p) =>
        viewport.worldToCanvas(p),
      )
    } catch {
      // 投影依赖 GL 渲染器状态，视口正在重建时会拿不到 renderer
      return null
    }
    if (!projected.every((p) => Array.isArray(p) && p.every(Number.isFinite))) return null
    const [entry, target, beyond] = projected
    return { entry, target, beyond }
    // cameraTick 不参与换算，只作为"相机变了需要重投影"的触发信号
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trajectory, status.state, cameraTick])

  /** 十字线在三维里的投影：与入路轴线同一套 worldToCanvas，相机一动就重算。 */
  const reticle = useMemo(() => {
    if (!focusPoint || status.state !== 'ready') return null
    const viewport = engineRef.current?.getViewport(VIEWPORT_ID)
    if (!viewport?.worldToCanvas) return null
    let projected
    try {
      projected = viewport.worldToCanvas([focusPoint.x, focusPoint.y, focusPoint.z])
    } catch {
      return null
    }
    if (!Array.isArray(projected) || !projected.every(Number.isFinite)) return null
    return projected
    // cameraTick 只作为"相机变了要重投影"的信号
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusPoint, status.state, cameraTick])

  const info = captureInfo || {}
  const captureTitle = [info.title, modality].filter(Boolean).join(' · ') || '三维重建截图'
  const captureFooter = [
    info.patient ? `患者 ${info.patient}` : '',
    info.series ? `序列 ${info.series}` : '',
    mode === 'ssr' ? 'SSR 表面遮蔽' : 'VR 容积',
    presetName,
    `不透明度 ${opacity.toFixed(2)}`,
  ]
    .filter(Boolean)
    .join(' · ')

  const captureShot = () => {
    const canvas = stageRef.current?.querySelector('.vr-canvas canvas')
    const ok = savePng({
      canvases: [canvas],
      labels: [],
      title: captureTitle,
      footer: captureFooter,
      nameParts: [info.caseNo || info.patient || 'case', '3d', mode],
    })
    setShotMsg(ok ? '截图已保存' : '三维视口尚未渲染完成，暂无可截取画面')
    window.setTimeout(() => setShotMsg(''), 3000)
  }

  /**
   * 环绕录像：脚本化绕患者头脚轴转一圈，同时录 canvas 流。
   *
   * 为什么自己转而不是"录用户的手"：手转出来的视频抖动、速度不均，
   * 且无法预知时长；报告里要的是一段稳定可复现的旋转片段。
   * 不支持 captureStream / MediaRecorder 时明确提示，不静默失败。
   */
  const startRecording = () => {
    if (recording) return
    const canvas = stageRef.current?.querySelector('.vr-canvas canvas')
    const viewport = engineRef.current?.getViewport(VIEWPORT_ID)
    if (!canvas || !viewport) {
      setShotMsg('三维视口尚未就绪')
      window.setTimeout(() => setShotMsg(''), 3000)
      return
    }
    if (typeof canvas.captureStream !== 'function' || typeof MediaRecorder === 'undefined') {
      setShotMsg('当前浏览器不支持画布录制（缺少 captureStream / MediaRecorder）')
      window.setTimeout(() => setShotMsg(''), 5000)
      return
    }
    const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'].find((t) =>
      MediaRecorder.isTypeSupported?.(t),
    )
    if (!mimeType) {
      setShotMsg('当前浏览器不支持 webm 录制')
      window.setTimeout(() => setShotMsg(''), 5000)
      return
    }

    const cam = viewport.getCamera()
    const focal = [...cam.focalPoint]
    const pos = [...cam.position]
    const rx = pos[0] - focal[0]
    const ry = pos[1] - focal[1]
    const radius = Math.hypot(rx, ry) || 1
    const phi0 = Math.atan2(ry, rx)
    const duration = 7000

    const stream = canvas.captureStream(30)
    const chunks = []
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 8_000_000 })
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data)
    }
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop())
      const blob = new Blob(chunks, { type: 'video/webm' })
      if (blob.size === 0) {
        setShotMsg('录制失败：没有采集到画面帧')
        window.setTimeout(() => setShotMsg(''), 5000)
        return
      }
      const url = URL.createObjectURL(blob)
      downloadDataUrl(
        url,
        `${fileStem([info.caseNo || info.patient || 'case', '3d-rotate'])}_${timestamp()}.webm`,
      )
      // 下载动作是同步触发的，延迟释放即可，立刻 revoke 会让部分浏览器拿不到内容
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
      setShotMsg('录像已保存（webm）')
      window.setTimeout(() => setShotMsg(''), 4000)
    }

    const t0 = performance.now()
    setRecording(true)
    recorder.start(120)

    const step = (now) => {
      const t = Math.min(1, (now - t0) / duration)
      const phi = phi0 + t * Math.PI * 2
      viewport.setCamera({
        position: [focal[0] + radius * Math.cos(phi), focal[1] + radius * Math.sin(phi), pos[2]],
        focalPoint: focal,
      })
      // setCamera 不更新裁剪面，相机绕行途中会把体数据裁掉一部分
      viewport.getRenderer()?.resetCameraClippingRange()
      viewport.render()
      if (t < 1) {
        recRef.current = { recorder, raf: requestAnimationFrame(step) }
      } else {
        recRef.current = null
        setRecording(false)
        recorder.stop()
      }
    }
    recRef.current = { recorder, raf: requestAnimationFrame(step) }
  }

  // 卸载时停止录制：否则 MediaRecorder 会在组件销毁后继续持有 canvas 流
  useEffect(
    () => () => {
      const rec = recRef.current
      recRef.current = null
      if (rec) {
        cancelAnimationFrame(rec.raf)
        try {
          if (rec.recorder.state !== 'inactive') rec.recorder.stop()
        } catch {
          /* 已停止 */
        }
      }
    },
    [],
  )

  const resetView = () => {
    const viewport = engineRef.current?.getViewport(VIEWPORT_ID)
    if (!viewport) return
    viewport.resetCamera({ resetPan: true, resetZoom: true, resetToCenter: true })
    frameVolumeCamera(viewport, cache.getVolume(makeVolumeId(seriesUid, volumeTag)))
    viewport.render()
  }

  return (
    <div className="viewer">
      <div className="viewer-toolbar">
        <span className={`viewer-status ${status.state}`}>
          <span className="dot" />
          {status.state === 'loading' && status.message}
          {status.state === 'ready' &&
            `${status.message} · ${mode === 'ssr' ? 'SSR' : 'VR'} · ${presetName}`}
          {status.state === 'error' && `渲染失败：${status.message}`}
        </span>

        <span className="spacer" />

        <div className="toolgroup" role="group" aria-label="渲染方式">
          <button
            className={mode === 'vr' ? 'is-active' : ''}
            aria-pressed={mode === 'vr'}
            onClick={() => setMode('vr')}
          >
            VR
          </button>
          <button
            className={mode === 'ssr' ? 'is-active' : ''}
            aria-pressed={mode === 'ssr'}
            onClick={() => setMode('ssr')}
          >
            SSR
          </button>
        </div>

        <select
          className="vr-preset"
          aria-label="渲染预设"
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
        >
          {PRESET_CHOICES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <div className="vr-opacity">
          <label htmlFor="vr-opacity">不透明度</label>
          <input
            id="vr-opacity"
            type="range"
            min="0.05"
            max="1"
            step="0.05"
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
          <span className="mono">{opacity.toFixed(2)}</span>
        </div>

        <button onClick={resetView}>重置视角</button>
        <button onClick={captureShot} disabled={status.state !== 'ready'}>
          截图
        </button>
        <button onClick={startRecording} disabled={status.state !== 'ready' || recording}>
          {recording ? '录制中…' : '录像'}
        </button>
        {shotMsg && <span className="shot-msg">{shotMsg}</span>}
      </div>

      <div className="vr-stage" ref={stageRef}>
        <div className="vr-canvas" />
        <span className="mpr-label">
          {mode === 'ssr' ? 'SSR · 表面遮蔽重建' : 'VR · 容积重建'}
        </span>
        <span className="mpr-info">
          {status.state === 'ready' ? `${status.count} 层重建` : ''}
        </span>

        {overlay && (
          <svg className="vr-overlay">
            <line
              x1={overlay.entry[0]}
              y1={overlay.entry[1]}
              x2={overlay.beyond[0]}
              y2={overlay.beyond[1]}
              className="vr-axis-dashed"
            />
            <line
              x1={overlay.entry[0]}
              y1={overlay.entry[1]}
              x2={overlay.target[0]}
              y2={overlay.target[1]}
              className="vr-axis"
            />
            <circle
              cx={overlay.entry[0]}
              cy={overlay.entry[1]}
              r={5}
              className="vr-node-entry"
            />
            <circle
              cx={overlay.target[0]}
              cy={overlay.target[1]}
              r={5}
              className="vr-node-target"
            />
          </svg>
        )}

        {overlay && (
          <div className="vr-legend">
            <span>
              <i className="dot-entry" />
              入路点
            </span>
            <span>
              <i className="dot-target" />
              靶点
            </span>
            <span className="mono">{trajectory.length.toFixed(2)} mm</span>
          </div>
        )}

        {/* 十字线准星：与二维视口联动，位置由父组件的 onCrosshair 推过来 */}
        {reticle && (
          <>
            <svg className="vr-overlay vr-reticle">
              <line x1={reticle[0] - 12} y1={reticle[1]} x2={reticle[0] - 4} y2={reticle[1]} />
              <line x1={reticle[0] + 4} y1={reticle[1]} x2={reticle[0] + 12} y2={reticle[1]} />
              <line x1={reticle[0]} y1={reticle[1] - 12} x2={reticle[0]} y2={reticle[1] - 4} />
              <line x1={reticle[0]} y1={reticle[1] + 4} x2={reticle[0]} y2={reticle[1] + 12} />
              <circle cx={reticle[0]} cy={reticle[1]} r={7} />
            </svg>
            <span
              className="vr-reticle-label mono"
              style={{ left: reticle[0] + 14, top: reticle[1] - 10 }}
            >
              {focusPoint.x.toFixed(1)}, {focusPoint.y.toFixed(1)}, {focusPoint.z.toFixed(1)}
            </span>
          </>
        )}

        {recording && (
          <span className="vr-rec">
            <i />
            录制中
          </span>
        )}
      </div>

      {error && (
        <div className="error-banner" style={{ margin: 12 }}>
          <span className="code">VR_VIEWPORT_ERROR</span> · {error}
        </div>
      )}

      <div className="viewer-hint">
        左键拖拽旋转 · 右键缩放 · 中键平移
        {overlay ? ' · 实线为入路轴线，虚线段为靶点后延长' : ''}
      </div>
    </div>
  )
}
