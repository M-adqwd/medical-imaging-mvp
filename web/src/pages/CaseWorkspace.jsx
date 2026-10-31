import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { qidoSeries, unwrap } from '../api/dicom'
import MprViewport, { SEG_COLORS } from '../components/MprViewport'
import VrViewport from '../components/VrViewport'
import MeshViewport from '../components/MeshViewport'
import FhirPanel from '../components/FhirPanel'
import { buildMeasurementCreateBody } from '../lib/measurementPayload'
import { countLabel, caseStatusLabel, permissionLabel, riskLabel, stateLabel } from '../lib/labels'
import { nextVolumeTag } from '../lib/volumeId'

/** 种子点 / 入路点 / 靶点一律自声明坐标系（契约 §5） */
const GEOMETRY = 'patient_lps_mm'
const toLpsPoint = (p) => ({ geometry: GEOMETRY, coordinates: [p.x, p.y, p.z] })

/**
 * 区域生长的 HU 容差。
 *
 * ±8 只适合数值均匀的体模：在真实增强 CT 上点主动脉腔（约 266 HU）时，
 * 腔内噪声与对比剂梯度本身就有 ±20，8 HU 只能长出 2 个体素、体积显示 0.00 mL
 * —— 功能"通过"了，医生拿到的是一个空壳。25 HU 在同一位置得到 8.3 mL，
 * 与管腔实际范围相符；再放宽到 40 就会越过管壁把周围脂肪一起吞进来。
 */
const SEED_HU_TOLERANCE = 25

const segMeshColor = (index) =>
  `#${SEG_COLORS[index % SEG_COLORS.length].map((c) => c.toString(16).padStart(2, '0')).join('')}`

/** 体积累加失败的原因码 → 医生看得懂的一句话 */
const VOLUME_REASON = {
  no_pixel_data: '该分割没有像素数据（仅元数据骨架），无法计算体积',
  missing_pixel_spacing: '缺少像素物理尺寸（PixelSpacing），无法换算体积',
  fractional_segmentation: '分数型分割（概率 / 占位率）的体素不是 0/1 归属，不能按体素计数',
  pixel_decode_failed: '像素解码失败',
  not_computed: '未计算',
}

/** 布尔运算的中文名。差集固定"第一个减第二个"，界面上必须写死这个方向 */
const BOOL_LABELS = {
  union: '并集',
  intersection: '交集',
  difference: '差集',
}

function Banner({ error, notice }) {
  if (error) {
    return (
      <div className="error-banner">
        <span className="code">{error.code}</span> · {error.message}
        {error.requestId && (
          <span style={{ color: 'var(--muted)' }}> · request_id {error.requestId}</span>
        )}
        {error.details?.failed_refs && (
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {error.details.failed_refs.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        )}
        {error.details?.errors?.length > 0 && (
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {error.details.errors.map((e, i) => (
              <li key={i}>
                <span className="mono">{e.loc?.join('.')}</span> — {e.msg}
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }
  if (notice) return <div className="ok-banner">{notice}</div>
  return null
}

export default function CaseWorkspace({ onChanged }) {
  const { caseId } = useParams()
  const navigate = useNavigate()

  const [detail, setDetail] = useState(null)
  const [measurements, setMeasurements] = useState([])
  const [segments, setSegments] = useState([])
  const [plans, setPlans] = useState([])
  const [reports, setReports] = useState([])
  const [shares, setShares] = useState([])
  const [share, setShare] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  // 表单状态
  const [measureForm, setMeasureForm] = useState({ type: 'length', value: '', unit: 'mm', text: '' })
  const [segForm, setSegForm] = useState({ segment_name: '' })
  const [reportForm, setReportForm] = useState({
    template_id: 'ct-chest-default-v3',
    findings: '',
    impression: '',
  })

  // 序列
  const [seriesList, setSeriesList] = useState([])
  const [seriesUid, setSeriesUid] = useState(null)
  // seriesUid 属于哪个 Study。切换病例时 detail 先更新、序列列表后到，
  // 中间会有一瞬 (新 Study, 旧序列) 的错配 —— 拿这对值去请求 QIDO instances
  // 必然 404，且会把视口留在"半初始化"状态，后续正确序列也灌不进体数据。
  const [seriesStudyUid, setSeriesStudyUid] = useState(null)

  // 体数据 ID 的唯一化标记：每次进入病例 / 切换序列都换一个新值。
  // cornerstone 的体数据对象按 volumeId 存在**全局**缓存里，复用同一 ID 时可能
  // 命中上一次遗留、且停在 loadStatus.loading === true 的对象；而
  // BaseStreamingImageVolume.load() 见到 loading 为真就直接 return —— 既不发起
  // 请求也不回调，界面永远停在「灌入体数据」（医生侧的现象就是"图像有时候
  // 显示不出来"）。换标记后每次都是全新对象，从根上避开脏状态命中。
  const volumeTag = useMemo(
    () => nextVolumeTag(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [caseId, seriesUid],
  )

  // 交互模式：三者互斥，同时开会让鼠标点击含义不明
  const [measureMode, setMeasureMode] = useState(false)
  const [planMode, setPlanMode] = useState(false)
  const [seedMode, setSeedMode] = useState(false)

  // 视口下方「文档与产出」面板的当前标签页。
  // 非当前 pane 只用 is-hidden 隐藏、**不卸载**：门禁脚本与用户操作都可能
  // 依赖任意面板里的 DOM（如分割表、测量下拉），条件渲染会让它们凭空消失。
  const [docTab, setDocTab] = useState('plan')

  // 规划点
  const [points, setPoints] = useState({ entry: null, target: null })

  // 二维 / 三维
  const [viewMode, setViewMode] = useState('2d')
  const [threeDReady, setThreeDReady] = useState(false)
  useEffect(() => {
    // 三维引擎首次挂载要编译 shader、建体纹理，只有进过 3D 才渲染它，
    // 否则每次进病例页都白付一次初始化代价
    if (viewMode === '3d') setThreeDReady(true)
  }, [viewMode])

  // 器官网格
  const [meshMode, setMeshMode] = useState(false)
  const [meshItems, setMeshItems] = useState([])
  const [meshBusy, setMeshBusy] = useState(false)

  // 十字线位置（LPS 毫米）：二维视口上报，三维视口用来画准星；
  // 裁切也以它为中心，医生不用心算包围盒坐标
  const [crosshair, setCrosshair] = useState(null)
  const [editSegUid, setEditSegUid] = useState('')
  const [cropRadius, setCropRadius] = useState(30)
  // 平滑默认中值：实测在真实 CTA 上 1mm 形态学开运算只保留 12% 体积，
  // 中值保留 87%，所以"平滑"的默认必须是保薄结构的那一种
  const [smoothMethod, setSmoothMethod] = useState('median')
  const [smoothRadius, setSmoothRadius] = useState(1)
  const [smoothClose, setSmoothClose] = useState(1)
  const [boolSegUid, setBoolSegUid] = useState('')
  const [boolOp, setBoolOp] = useState('union')

  /** 只有真正带像素的分割才能出网格 / 体积；元数据骨架放进去只会得到空壳 */
  const segsWithPixels = useMemo(
    () => (segments || []).filter((s) => s.has_pixels || s.volume?.status === 'ok'),
    [segments],
  )

  /** 当前编辑对象。必须从最新列表里取，不能在状态里存对象副本。 */
  const editSource = segments.find((s) => s.seg_uid === editSegUid) || null

  const loadMeshes = async () => {
    setMeshBusy(true)
    const items = []
    for (let i = 0; i < segsWithPixels.length; i += 1) {
      try {
        const mesh = await api.segmentMesh(segsWithPixels[i].seg_uid)
        items.push({
          seg_uid: mesh.seg_uid,
          name: mesh.name || segsWithPixels[i].segment_name,
          color: segMeshColor(i),
          vertices: mesh.vertices,
          faces: mesh.faces,
          visible: true,
          opacity: 0.92,
        })
      } catch {
        // 单个结构提取失败不该拖垮整批：其余照常显示
      }
    }
    setMeshItems(items)
    setMeshBusy(false)
    setMeshMode(true)
  }

  const patchMesh = (segUid, patch) =>
    setMeshItems((prev) => prev.map((m) => (m.seg_uid === segUid ? { ...m, ...patch } : m)))

  // 编辑对象必须始终指向一个真实存在、且带像素的分割：
  // 刷新后原对象被撤回 / 列表为空时，选择框不能停在一个失效的 UID 上
  useEffect(() => {
    if (segsWithPixels.length === 0) {
      if (editSegUid) setEditSegUid('')
      return
    }
    if (!segsWithPixels.some((s) => s.seg_uid === editSegUid)) {
      setEditSegUid(segsWithPixels[0].seg_uid)
    }
  }, [segsWithPixels, editSegUid])

  const volumeText = (seg) =>
    seg?.volume?.status === 'ok'
      ? `${seg.volume.volume_ml.toFixed(2)} mL`
      : `体积不可用（${VOLUME_REASON[seg?.volume?.reason] || seg?.volume?.reason || '未计算'}）`

  const handleCrop = () => {
    if (!crosshair) return
    runAction(
      () =>
        api.cropSegment(editSegUid, {
          center: toLpsPoint(crosshair),
          radius_mm: cropRadius,
        }),
      (seg) => `已裁切为「${seg.segment_name}」：${volumeText(seg)}`,
    )
  }

  const handleSmooth = () =>
    runAction(
      () =>
        api.smoothSegment(editSegUid, {
          method: smoothMethod,
          radius_mm: smoothRadius,
          closing_mm: smoothMethod === 'morphology' ? smoothClose : 0,
        }),
      (seg) => `已平滑为「${seg.segment_name}」：${volumeText(seg)}`,
    )

  const handleBoolean = () =>
    runAction(
      () =>
        api.mergeSegments(caseId, {
          seg_uids: [editSegUid, boolSegUid],
          result_name: `${editSource?.segment_name || '分割'}·${BOOL_LABELS[boolOp]}`,
          operation: boolOp,
        }),
      (seg) => `布尔结果「${seg.segment_name}」：${volumeText(seg)}`,
    )

  const refresh = useCallback(async () => {
    try {
      const [d, ms, segs, ps, rs, ss] = await Promise.all([
        api.getCase(caseId),
        api.listMeasurements(caseId),
        api.listSegments(caseId),
        api.listPlans(caseId),
        api.listReports(caseId),
        api.listShares(caseId),
      ])
      setDetail(d)
      setMeasurements(ms)
      setSegments(segs)
      setPlans(ps)
      setReports(rs)
      setShares(ss)
      setError(null)
    } catch (e) {
      setError(e)
    }
  }, [caseId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 序列列表从 DICOMweb QIDO 取；Study 下没有序列时给空态而不是空下拉框
  useEffect(() => {
    let cancelled = false
    async function loadSeries() {
      const studyUid = detail?.studies?.[0]?.study_instance_uid
      if (!studyUid) {
        setSeriesList([])
        return
      }
      try {
        const json = await qidoSeries(studyUid)
        if (cancelled) return
        const list = json.map((item) => ({
          uid: unwrap(item, '0020000E'),
          modality: unwrap(item, '00080060') || '',
          description: unwrap(item, '0008103E') || '',
          count: unwrap(item, '00201209') || 0,
        }))
        setSeriesList(list)
        // 默认选 CT：同一 Study 里常有定位片、剂量报告等非断层序列，
        // 拿第一个会落到定位片上，看起来像「体数据坏了」
        const first = list.find((s) => s.modality === 'CT') || list[0]
        setSeriesStudyUid(studyUid)
        setSeriesUid(first ? first.uid : null)
      } catch (e) {
        if (cancelled) return
        setSeriesList([])
        if (/QIDO series 404/.test(String(e?.message ?? e))) {
          setSeriesStudyUid(null)
          setSeriesUid(null)
          return
        }
        setError(e)
      }
    }
    loadSeries()
    return () => {
      cancelled = true
    }
  }, [detail?.studies?.[0]?.study_instance_uid])

  /**
   * 统一操作外壳：置忙、清错误、刷新列表、给成功提示。
   * successNotice 允许传函数（依赖接口返回值算文案），否则按固定文案。
   */
  const runAction = async (fn, successNotice) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await fn()
      await refresh()
      onChanged?.()
      setNotice(typeof successNotice === 'function' ? successNotice(result) : successNotice)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const studyUid = detail?.studies?.[0]?.study_instance_uid
  // 只有"序列确实属于当前 Study"才算选中：切换病例时 detail 先到、序列列表后到，
  // 中间那一瞬若把 (新 Study, 旧序列) 交给视口，QIDO instances 必然 404，
  // 并把视口留在半初始化状态 —— 之后即使拿到正确序列也灌不进体数据。
  const activeSeriesUid = seriesUid && seriesStudyUid === studyUid ? seriesUid : null
  const currentSeries = seriesList.find((s) => s.uid === seriesUid)
  const confirmedSeg = segments.find((s) => s.state === 'confirmed')

  /** 截图信息条：截图离开系统后仍要能自证是哪位患者、哪个序列、什么窗位 */
  const captureInfo = {
    title: detail?.title,
    patient: detail?.patient_ref,
    series: currentSeries?.description,
    modality: currentSeries?.modality,
    caseNo: caseId ? caseId.slice(0, 8) : '',
  }

  /** 报告可引用的对象数：已保存测量 + 已确认分割 + 已保存/已导出规划 */
  const reportRefCount =
    measurements.filter((m) => m.state === 'saved').length +
    segments.filter((s) => s.state === 'confirmed').length +
    plans.filter((p) => ['saved', 'exported'].includes(p.state)).length

  const handleMeasureReady = useCallback(
    async (measurement) => {
      if (!studyUid) return
      await runAction(
        () =>
          api.createMeasurement(
            caseId,
            buildMeasurementCreateBody({
              studyInstanceUid: studyUid,
              seriesInstanceUid: seriesUid,
              measurement,
            }),
          ),
        `已写入 DICOM SR：${measurement.value} ${measurement.unit || 'mm'}`,
      )
      setMeasureMode(false)
    },
    [caseId, studyUid, seriesUid],
  )

  /** 入路点与靶点各自独立拾取：先点的补 entry，后点的补 target */
  const handlePlanPoint = useCallback((point) => {
    setPoints((prev) =>
      prev.entry ? { entry: prev.entry, target: point } : { entry: point, target: prev.target },
    )
  }, [])

  const handleSeedPoint = useCallback(
    (point) => {
      setSeedMode(false)
      runAction(
        () =>
          api.growSegment(caseId, {
            study_instance_uid: studyUid,
            seed: toLpsPoint(point),
            segment_name: segForm.segment_name || '区域生长',
            hu_tolerance: SEED_HU_TOLERANCE,
            radius_mm: 60,
          }),
        (seg) => {
          const v = seg?.volume
          return v?.status === 'ok'
            ? `已生成带像素分割：${v.volume_ml.toFixed(2)} mL`
            : `已生成分割，但体积不可用（${v?.reason || '未知原因'}）`
        },
      )
    },
    [caseId, studyUid, segForm.segment_name],
  )

  if (error?.code === 'NOT_FOUND') {
    return (
      <div style={{ padding: '48px 24px', textAlign: 'center' }}>
        <h2>病例不存在</h2>
        <p className="hint">
          病例 <span className="mono">{caseId}</span> 可能已被清理或链接已过期。
        </p>
        <button onClick={() => navigate('/')}>返回病例列表</button>
      </div>
    )
  }

  return (
    <div>
      <div className="case-head">
        <h1>{detail?.title || '病例'}</h1>
        <p className="hint">
          <span className="mono">{detail?.patient_ref}</span>
          {detail?.modality ? ` · ${detail.modality}` : ''}
          {detail?.study_date ? ` · ${detail.study_date}` : ''}
          <span className={`badge ${detail?.status}`}>{caseStatusLabel(detail?.status)}</span>
        </p>
      </div>

      <div className="case-body">
        <Banner error={error} notice={notice} />

        <div className="split">
          {/* ---------------- 阅片视图 ---------------- */}
          <div>
            {/* 需求编号（FR-02 / FR-03）留在 title 里备查：评审时能对上 PRD，
                日常阅片的医生不需要看见这串编号 */}
            <div className="section-title" title="对应 PRD FR-02 · FR-03">
              阅片视图
              <span className="spacer" />
              <div className="toolgroup" role="group" aria-label="阅片模式">
                <button
                  className={viewMode === '2d' ? 'is-active' : ''}
                  aria-pressed={viewMode === '2d'}
                  onClick={() => setViewMode('2d')}
                >
                  2D
                </button>
                <button
                  className={viewMode === '3d' ? 'is-active' : ''}
                  aria-pressed={viewMode === '3d'}
                  disabled={!studyUid || !seriesUid}
                  onClick={() => {
                    setViewMode('3d')
                    setMeasureMode(false)
                    setPlanMode(false)
                  }}
                >
                  3D
                </button>
              </div>
              {viewMode === '3d' && (
                <div className="toolgroup" role="group" aria-label="三维模式">
                  <button className={meshMode ? '' : 'is-active'} onClick={() => setMeshMode(false)}>
                    VR 重建
                  </button>
                  <button
                    className={meshMode ? 'is-active' : ''}
                    onClick={() => (meshItems.length ? setMeshMode(true) : loadMeshes())}
                    disabled={meshBusy || segsWithPixels.length === 0}
                  >
                    {meshBusy ? '提取网格…' : '器官结构'}
                  </button>
                </div>
              )}
            </div>

            <div className="card" style={{ padding: 'var(--sp-3)' }}>
              <div
                className="row"
                style={{ marginBottom: studyUid && seriesList.length ? 'var(--sp-3)' : 0 }}
              >
                <div style={{ flex: '0 0 260px' }}>
                  <label>序列</label>
                  <select
                    value={seriesUid || ''}
                    onChange={(e) => {
                      setSeriesStudyUid(studyUid)
                      setSeriesUid(e.target.value)
                      setMeasureMode(false)
                    }}
                    disabled={seriesList.length === 0}
                  >
                    {seriesList.length === 0 && <option value="">该检查无序列</option>}
                    {seriesList.map((s) => (
                      <option key={s.uid} value={s.uid}>
                        {s.modality} · {s.description || s.uid.slice(-10)} · {s.count} 张
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {studyUid && activeSeriesUid ? (
                <>
                  <div className={viewMode === '3d' ? 'is-hidden' : ''}>
                    <MprViewport
                      key={activeSeriesUid}
                      visible={viewMode !== '3d'}
                      studyUid={studyUid}
                      seriesUid={activeSeriesUid}
                      volumeTag={volumeTag}
                      overlaySegs={segsWithPixels.map((s) => ({
                        seg_uid: s.seg_uid,
                        name: s.segment_name,
                      }))}
                      overlayOn={viewMode !== '3d'}
                      measureMode={measureMode}
                      planMode={planMode}
                      seedMode={seedMode}
                      onMeasureReady={handleMeasureReady}
                      onPlanPoint={handlePlanPoint}
                      onSeedPoint={handleSeedPoint}
                      onCrosshair={setCrosshair}
                      captureInfo={captureInfo}
                    />
                  </div>

                  {threeDReady && viewMode === '3d' && meshMode && meshItems.length > 0 ? (
                    <div style={{ height: 560 }}>
                      <MeshViewport
                        items={meshItems}
                        marker={crosshair}
                        captureInfo={captureInfo}
                      />
                    </div>
                  ) : (
                    threeDReady && (
                      <div className={viewMode === '3d' ? '' : 'is-hidden'}>
                        <VrViewport
                          key={`vr-${volumeTag}`}
                          visible={viewMode === '3d' && !meshMode}
                          studyUid={studyUid}
                          seriesUid={activeSeriesUid}
                          volumeTag={volumeTag}
                          modality={currentSeries?.modality}
                          entryPoint={points.entry}
                          targetPoint={points.target}
                          focusPoint={crosshair}
                          captureInfo={captureInfo}
                        />
                      </div>
                    )
                  )}

                  {viewMode === '3d' && meshMode && meshItems.length > 0 && (
                    <div
                      className="row"
                      style={{ marginTop: 'var(--sp-3)', gap: 12, flexWrap: 'wrap' }}
                    >
                      {meshItems.map((m) => (
                        <div
                          key={m.seg_uid}
                          className="card"
                          style={{ padding: '8px 12px', minWidth: 210, flex: '1 1 210px' }}
                        >
                          <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <input
                              type="checkbox"
                              checked={m.visible}
                              onChange={(e) => patchMesh(m.seg_uid, { visible: e.target.checked })}
                            />
                            <span
                              style={{
                                width: 10,
                                height: 10,
                                borderRadius: 2,
                                background: m.color,
                                display: 'inline-block',
                                flexShrink: 0,
                              }}
                            />
                            {m.name}
                          </label>
                          <input
                            type="range"
                            min="0.05"
                            max="1"
                            step="0.05"
                            value={m.opacity}
                            style={{ width: '100%', marginTop: 6 }}
                            onChange={(e) => patchMesh(m.seg_uid, { opacity: Number(e.target.value) })}
                            aria-label={`${m.name} 透明度`}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="empty">
                  {studyUid ? '该检查下没有可用序列' : '本病例未关联检查'}
                  <br />
                  影像通过 DICOMweb（WADO-RS）取回，需先在 DICOM 存储中登记检查
                </div>
              )}
            </div>
          </div>

          {/* ---------------- 右侧操作栏：只放阅读影像时顺手用的两块 ---------------- */}
          <div className="dock">
            {/* 测量 */}
            <div className="panel">
              <div className="panel-head">
                <h3>测量</h3>
                <span className="count">{measurements.length}</span>
                <span className="spacer" />
                <button
                  className={measureMode ? 'is-active' : ''}
                  disabled={!seriesUid}
                  onClick={() => {
                    setMeasureMode((v) => !v)
                    setPlanMode(false)
                  }}
                >
                  {measureMode ? '退出测量' : '影像测量'}
                </button>
              </div>

              <div className="panel-body">
                <div className="measure-form">
                  <div className="mf-row">
                    <label>类型</label>
                    <select
                      value={measureForm.type}
                      onChange={(e) => setMeasureForm({ ...measureForm, type: e.target.value })}
                    >
                      <option value="length">长度</option>
                      <option value="angle">角度</option>
                      <option value="area">面积</option>
                      <option value="volume" disabled>
                        体积（由分割计算）
                      </option>
                      <option value="free-text">文字</option>
                      <option value="arrow">箭头</option>
                    </select>
                  </div>
                  <p className="hint" style={{ marginTop: 'var(--sp-1)' }}>
                    体积不在本面板填写：它由带像素的分割（SEG）自动计算， 在分割列表里查看；没有像素时显示「不可用」而不是 0。
                  </p>
                  {['length', 'angle', 'area'].includes(measureForm.type) ? (
                    <div className="mf-row">
                      <label>数值</label>
                      <input
                        type="number"
                        step="0.1"
                        value={measureForm.value}
                        onChange={(e) => setMeasureForm({ ...measureForm, value: e.target.value })}
                      />
                      <label>单位</label>
                      <input
                        value={measureForm.unit}
                        onChange={(e) => setMeasureForm({ ...measureForm, unit: e.target.value })}
                      />
                    </div>
                  ) : (
                    <div className="mf-row">
                      <label>内容</label>
                      <input
                        value={measureForm.text}
                        onChange={(e) => setMeasureForm({ ...measureForm, text: e.target.value })}
                      />
                    </div>
                  )}
                </div>
                <button
                  style={{ width: '100%' }}
                  className="primary"
                  disabled={busy || !studyUid}
                  onClick={() =>
                    runAction(
                      () =>
                        api.createMeasurement(caseId, {
                          study_instance_uid: studyUid,
                          type: measureForm.type,
                          value: measureForm.value === '' ? null : Number(measureForm.value),
                          unit: measureForm.unit,
                          text: measureForm.text,
                          cross_series: false,
                        }),
                      '已创建草稿态测量（SR）',
                    )
                  }
                >
                  新建测量记录
                </button>
              </div>

              <div className="panel-body flush">
                <table>
                  <thead>
                    <tr>
                      <th>值</th>
                      <th>状态</th>
                      <th style={{ width: 108 }}>动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {measurements.length === 0 && (
                      <tr>
                        <td colSpan={3} className="empty">
                          暂无测量
                        </td>
                      </tr>
                    )}
                    {measurements.map((m) => (
                      <tr key={m.sr_uid}>
                        <td className="mono">
                          {m.value ?? m.text} {m.unit}
                        </td>
                        <td>
                          <span className={`badge ${m.state}`}>{stateLabel(m.state)}</span>
                        </td>
                        <td>
                          <div className="actions">
                            {m.state === 'draft' && (
                              <button
                                disabled={busy}
                                onClick={() =>
                                  runAction(() => api.saveMeasurement(m.sr_uid), '测量已保存为正式 SR')
                                }
                              >
                                保存
                              </button>
                            )}
                            {m.state !== 'withdrawn' && (
                              <button
                                className="danger"
                                disabled={busy}
                                onClick={() =>
                                  runAction(() => api.withdrawMeasurement(m.sr_uid), '测量已撤回')
                                }
                              >
                                撤回
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 分割 */}
            <div className="panel">
              <div className="panel-head">
                <h3>分割</h3>
                <span className="count">{segments.length}</span>
              </div>

              <div className="panel-body">
                <label>结构名称</label>
                <input
                  value={segForm.segment_name}
                  placeholder="如：肝脏 / 右肾 / T12 椎体"
                  onChange={(e) => setSegForm({ segment_name: e.target.value })}
                  style={{ marginBottom: 'var(--sp-2)' }}
                />
                <div className="row">
                  <button
                    style={{ flex: 1 }}
                    disabled={busy || !studyUid || !segForm.segment_name}
                    onClick={() =>
                      runAction(async () => {
                        await api.createSegment(caseId, {
                          study_instance_uid: studyUid,
                          segment_name: segForm.segment_name,
                          algorithm: 'manual',
                        })
                        setSegForm({ segment_name: '' })
                      }, '已创建草稿态分割（SEG）')
                    }
                  >
                    新建分割
                  </button>
                  <button
                    style={{ flex: 1 }}
                    disabled={busy || !studyUid}
                    onClick={() =>
                      runAction(
                        () => api.triggerInference(studyUid, []),
                        (job) => {
                          if (job?.mode === 'stub') {
                            return '任务已登记，但 AI 服务未配置（本地桩模式）：任务不会自动完成，需人工回传结果后才会出现建议态分割'
                          }
                          if (job?.mode === 'local') {
                            // 本地 TotalSegmentator 是同步长任务：轮询到终态再提示，
                            // 否则界面停在「已投递」，医生会以为没跑
                            ;(async () => {
                              for (let i = 0; i < 60; i += 1) {
                                await new Promise((r) => setTimeout(r, 10000))
                                try {
                                  const st = await api.aiJobStatus(job.job_id)
                                  if (st.status === 'done') {
                                    await refresh()
                                    setNotice(
                                      `本地自动分割完成（${st.stats?.seg_created ?? '?'} 个结构已入建议态，需医师确认）`,
                                    )
                                    return
                                  }
                                  if (st.status === 'failed') {
                                    await refresh()
                                    setNotice(null)
                                    setError(
                                      new Error(`本地自动分割失败：${st.error?.message ?? '未知原因'}`),
                                    )
                                    return
                                  }
                                } catch {
                                  // 轮询期间的网络抖动不该终止整个等待
                                }
                              }
                            })()
                            return '本地 TotalSegmentator 推理已启动（CPU，预计数分钟）：完成后自动刷新。输出为建议态分割，需医师确认'
                          }
                          return '已投递自动分割任务（AI 输出为建议态，需医师确认）'
                        },
                      )
                    }
                  >
                    自动分割
                  </button>
                </div>
                <div className="row" style={{ marginTop: 'var(--sp-2)' }}>
                  <button
                    className={seedMode ? 'is-active' : ''}
                    style={{ width: '100%' }}
                    disabled={busy || !seriesUid}
                    onClick={() => {
                      setSeedMode((v) => !v)
                      setPlanMode(false)
                      setMeasureMode(false)
                    }}
                  >
                    {seedMode ? '在影像上点击靶点（点击取消）' : '种子点生长（生成带像素分割）'}
                  </button>
                </div>
                <p className="hint" style={{ marginTop: 'var(--sp-1)' }}>
                  区域生长按种子点的 HU 相似度扩散（默认 ±25 HU、半径 60 mm），产出可计算体积的分割。 结果标注为「算法生成、未经医师核准」，需确认后才能用于规划。
                </p>
              </div>

              {/* 分割编辑：裁切 / 平滑 / 布尔。三种操作都产出**新的** draft 分割，
                  源对象不动 —— 已确认的分割可能已被规划引用，原地改会让引用失去依据。 */}
              <div className="panel-body edit-sep">
                <label>编辑对象</label>
                <select
                  value={editSegUid}
                  onChange={(e) => setEditSegUid(e.target.value)}
                  disabled={segsWithPixels.length === 0}
                >
                  {segsWithPixels.length === 0 && (
                    <option value="">没有带像素的分割可编辑</option>
                  )}
                  {segsWithPixels.map((s) => (
                    <option key={s.seg_uid} value={s.seg_uid}>
                      {s.segment_name} · {stateLabel(s.state)}
                    </option>
                  ))}
                </select>

                <div className="edit-row">
                  <label>裁切</label>
                  <input
                    type="number"
                    min="1"
                    max="300"
                    step="1"
                    value={cropRadius}
                    aria-label="裁切半径 mm"
                    onChange={(e) => setCropRadius(Number(e.target.value))}
                  />
                  <span className="hint">mm</span>
                  <button
                    disabled={busy || !editSource || !crosshair}
                    title={crosshair ? '以十字线为中心保留立方体范围' : '请先在二维视口移动十字线'}
                    onClick={handleCrop}
                  >
                    裁切到十字线
                  </button>
                </div>
                <p className="hint">
                  {crosshair
                    ? `中心 ${crosshair.x} · ${crosshair.y} · ${crosshair.z}（LPS mm），盒外体素置 0，几何不变`
                    : '先在二维视口里把十字线移到要保留的位置'}
                </p>

                <div className="edit-row">
                  <label>平滑</label>
                  <select
                    value={smoothMethod}
                    onChange={(e) => setSmoothMethod(e.target.value)}
                    aria-label="平滑方法"
                    style={{ flex: '0 0 108px' }}
                  >
                    <option value="median">中值（保薄结构）</option>
                    <option value="morphology">形态学开/闭</option>
                  </select>
                  <input
                    type="number"
                    min="0.5"
                    max="20"
                    step="0.5"
                    value={smoothRadius}
                    aria-label="平滑半径 mm"
                    onChange={(e) => setSmoothRadius(Number(e.target.value))}
                  />
                  <span className="hint">mm</span>
                  {smoothMethod === 'morphology' && (
                    <>
                      <input
                        type="number"
                        min="0"
                        max="20"
                        step="0.5"
                        value={smoothClose}
                        aria-label="闭运算半径 mm"
                        onChange={(e) => setSmoothClose(Number(e.target.value))}
                      />
                      <span className="hint">mm 闭</span>
                    </>
                  )}
                  <button disabled={busy || !editSource || smoothRadius <= 0} onClick={handleSmooth}>
                    平滑
                  </button>
                </div>
                <p className="hint">
                  中值滤波按邻域多数表决去毛刺，薄壁与细血管基本不变形；
                  形态学开运算要求结构元整个落在掩膜内，薄结构会被削穿（真实 CTA 上实测只剩 12% 体积），
                  只在确实要去掉细小突起时使用。核半径上限 12 体素。
                </p>

                <div className="edit-row">
                  <label>布尔</label>
                  <select
                    value={boolSegUid}
                    onChange={(e) => setBoolSegUid(e.target.value)}
                    aria-label="布尔运算的第二个分割"
                  >
                    <option value="">选择另一个分割</option>
                    {segsWithPixels
                      .filter((s) => s.seg_uid !== editSegUid)
                      .map((s) => (
                        <option key={s.seg_uid} value={s.seg_uid}>
                          {s.segment_name}
                        </option>
                      ))}
                  </select>
                  <select
                    value={boolOp}
                    onChange={(e) => setBoolOp(e.target.value)}
                    aria-label="布尔运算类型"
                  >
                    {Object.entries(BOOL_LABELS).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <button
                    disabled={busy || !editSource || !boolSegUid}
                    onClick={handleBoolean}
                  >
                    执行
                  </button>
                </div>
                <p className="hint">
                  差集是「编辑对象 − 另一个分割」。逐体素运算，要求两者在同一体素栅格上，
                  跨栅格会明确拒绝而不是重采样对齐。
                </p>
              </div>

              <div className="panel-body flush">
                <table>
                  <thead>
                    <tr>
                      <th>结构</th>
                      <th>状态</th>
                      <th style={{ width: 88 }}>体积</th>
                      <th style={{ width: 96 }}>动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {segments.length === 0 && (
                      <tr>
                        <td colSpan={4} className="empty">
                          暂无分割
                        </td>
                      </tr>
                    )}
                    {segments.map((s) => (
                      <tr key={s.seg_uid}>
                        <td>
                          <div style={{ fontWeight: 550 }}>{s.segment_name}</div>
                          <div
                            className="mono"
                            style={{ fontSize: 11, color: 'var(--muted-foreground)' }}
                          >
                            {s.algorithm}
                          </div>
                        </td>
                        <td>
                          <span className={`badge ${s.state}`}>{stateLabel(s.state)}</span>
                        </td>
                        <td>
                          {s.volume?.status === 'ok' ? (
                            <span
                              className="mono"
                              title={`${s.volume.voxel_count} 体素 · ${s.volume.volume_mm3.toFixed(1)} mm³`}
                            >
                              {s.volume.volume_ml.toFixed(2)} mL
                            </span>
                          ) : (
                            <span
                              style={{ fontSize: 12, color: 'var(--muted-foreground)' }}
                              title={VOLUME_REASON[s.volume?.reason] || s.volume?.reason || '未计算'}
                            >
                              不可用
                            </span>
                          )}
                        </td>
                        <td>
                          <div className="actions">
                            {['draft', 'suggested'].includes(s.state) && (
                              <button
                                disabled={busy}
                                onClick={() =>
                                  runAction(
                                    () => api.confirmSegment(s.seg_uid),
                                    '分割已确认，可被规划引用',
                                  )
                                }
                              >
                                确认
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        {/* ---------------- 文档与产出：全宽标签面板（规划/报告/共享/统计/FHIR） ---------------- */}
        <section className="doc-panel">
          <div className="doc-tabs" role="tablist">
            {[
              { id: 'plan', label: '规划', count: plans.length },
              { id: 'report', label: '报告', count: reports.length },
              { id: 'share', label: '共享', count: shares.length },
              { id: 'stats', label: '对象统计', count: null },
              { id: 'fhir', label: '互操作 FHIR', count: null },
            ].map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={docTab === t.id}
                className={docTab === t.id ? 'on' : ''}
                onClick={() => setDocTab(t.id)}
              >
                {t.label}
                {t.count != null && <em>{t.count}</em>}
              </button>
            ))}
          </div>
          <div className="doc-body">

            {/* —— 规划：左列取点与建规划，右列规划台账 —— */}
            <div className={`doc-pane ${docTab === 'plan' ? '' : 'is-hidden'}`}>
              <div className="doc-grid">
                <div className="doc-side">
                  <div className="row">
                    <button
                      className={planMode ? 'is-active' : ''}
                      style={{ flex: 1 }}
                      disabled={!seriesUid}
                      onClick={() => {
                        setPlanMode((v) => !v)
                        setMeasureMode(false)
                      }}
                    >
                      {planMode ? '退出取点' : '影像取点'}
                    </button>
                  </div>
                  <div className="mf-row">
                    <label>入路点</label>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {points.entry
                        ? `x ${points.entry.x} · y ${points.entry.y} · z ${points.entry.z}`
                        : '未拾取'}
                    </span>
                  </div>
                  <div className="mf-row">
                    <label>靶点</label>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {points.target
                        ? `x ${points.target.x} · y ${points.target.y} · z ${points.target.z}`
                        : '未拾取'}
                    </span>
                  </div>
                  <button
                    className="primary"
                    disabled={busy || !confirmedSeg || !points.entry || !points.target}
                    onClick={() =>
                      runAction(
                        () =>
                          api.createPlan(caseId, {
                            base_seg_uid: confirmedSeg.seg_uid,
                            base_seg_version: confirmedSeg.version,
                            entry_point: toLpsPoint(points.entry),
                            target_point: toLpsPoint(points.target),
                          }),
                        '已创建规划草稿',
                      )
                    }
                  >
                    基于已确认分割新建规划
                  </button>
                  {!confirmedSeg && <div className="hint">需先确认至少一个分割，才能建立规划</div>}
                  {confirmedSeg && (!points.entry || !points.target) && (
                    <div className="hint">需在影像上拾取入路点与靶点（契约要求入路点 / 靶点）</div>
                  )}
                </div>

                <div className="doc-main">
                  <table>
                    <thead>
                      <tr>
                        <th>入路长度</th>
                        <th>解剖距离</th>
                        <th>夹角</th>
                        <th>风险</th>
                        <th style={{ width: 72 }}>动作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plans.length === 0 && (
                        <tr>
                          <td colSpan={5} className="empty">
                            暂无规划 —— 在左列拾取入路点与靶点后创建
                          </td>
                        </tr>
                      )}
                      {plans.map((p) => (
                        <tr key={p.plan_id}>
                          <td className="mono">{p.safe_distance} mm</td>
                          <td className="mono">
                            {p.anatomical_distance_mm == null ? (
                              <span style={{ color: 'var(--muted-foreground)' }}>—</span>
                            ) : (
                              `${p.anatomical_distance_mm} mm`
                            )}
                          </td>
                          <td className="mono">{p.angle}°</td>
                          <td>
                            <span className={`risk-${p.risk_level}`} title={p.risk_basis || undefined}>
                              {riskLabel(p.risk_level)}
                            </span>
                            {p.risk_level === 'unknown' && (
                              <div className="hint warn">
                                {p.risk_basis || '底层 SEG 无像素数据，无法评估'}
                              </div>
                            )}
                          </td>
                          <td>
                            <div className="actions">
                              {p.state === 'draft' && (
                                <button
                                  disabled={busy}
                                  onClick={() => runAction(() => api.savePlan(p.plan_id), '规划已保存')}
                                >
                                  保存
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* —— 报告：左列表单，右列报告台账 —— */}
            <div className={`doc-pane ${docTab === 'report' ? '' : 'is-hidden'}`}>
              <div className="doc-grid">
                <div className="doc-side">
                  <div className="field">
                    <label>模板</label>
                    <input
                      value={reportForm.template_id}
                      onChange={(e) => setReportForm({ ...reportForm, template_id: e.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label>临床发现</label>
                    <textarea
                      rows={3}
                      value={reportForm.findings}
                      onChange={(e) => setReportForm({ ...reportForm, findings: e.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label>结论 / 印象</label>
                    <textarea
                      rows={3}
                      value={reportForm.impression}
                      onChange={(e) => setReportForm({ ...reportForm, impression: e.target.value })}
                    />
                  </div>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      runAction(async () => {
                        const refs = [
                          ...measurements
                            .filter((m) => m.state === 'saved')
                            .map((m) => ({ kind: 'measurement', uid: m.sr_uid, version: 1 })),
                          ...segments
                            .filter((s) => s.state === 'confirmed')
                            .map((s) => ({ kind: 'segment', uid: s.seg_uid, version: s.version })),
                          ...plans
                            .filter((p) => ['saved', 'exported'].includes(p.state))
                            .map((p) => ({ kind: 'plan', uid: p.plan_id, version: p.version })),
                        ]
                        await api.createReport(caseId, {
                          template_id: reportForm.template_id,
                          findings: reportForm.findings,
                          impression: reportForm.impression,
                          refs,
                        })
                      }, `已创建报告草稿，引用 ${reportRefCount} 个对象`)}
                  >
                    生成报告草稿
                  </button>
                </div>

                <div className="doc-main">
                  <table>
                    <thead>
                      <tr>
                        <th>模板</th>
                        <th>状态</th>
                        <th style={{ width: 88 }}>动作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reports.length === 0 && (
                        <tr>
                          <td colSpan={3} className="empty">
                            暂无报告 —— 在左列填写内容后生成草稿
                          </td>
                        </tr>
                      )}
                      {reports.map((r) => (
                        <tr key={r.report_id}>
                          <td className="mono" style={{ fontSize: 11 }}>
                            {r.template_id}
                          </td>
                          <td>
                            <span className={`badge ${r.state}`}>{stateLabel(r.state)}</span>
                          </td>
                          <td>
                            <div className="actions">
                              {r.state === 'draft' && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    runAction(
                                      () => api.submitReport(r.report_id),
                                      '报告已提交并生成 DICOM SR',
                                    )
                                  }
                                >
                                  提交
                                </button>
                              )}
                              {r.state === 'submitted' && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    runAction(() => api.lockReport(r.report_id), '报告已锁定')
                                  }
                                >
                                  签收
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* —— 共享：左列创建与访问码，右列共享台账 —— */}
            <div className={`doc-pane ${docTab === 'share' ? '' : 'is-hidden'}`}>
              <div className="doc-grid">
                <div className="doc-side">
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      runAction(async () => {
                        const created = await api.createShare(caseId, {
                          permission: 'readonly+comment',
                          scope: 'full',
                        })
                        setShare(created)
                      }, '共享链接已创建')
                    }
                  >
                    创建共享链接
                  </button>
                  {share && (
                    <div className="share-code">
                      <div className="hint warn">访问码仅出现这一次，请立即复制保存</div>
                      <div className="row">
                        <code className="mono grow">{share.access_code}</code>
                        <button onClick={() => navigator.clipboard?.writeText(share.access_code)}>
                          复制
                        </button>
                      </div>
                      <div className="row">
                        <code className="mono grow ellipsis">{share.url}</code>
                        <button onClick={() => navigator.clipboard?.writeText(share.url)}>
                          复制链接
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                <div className="doc-main">
                  <table>
                    <thead>
                      <tr>
                        <th>权限</th>
                        <th>访问</th>
                        <th>状态</th>
                        <th style={{ width: 72 }} />
                      </tr>
                    </thead>
                    <tbody>
                      {shares.length === 0 && (
                        <tr>
                          <td colSpan={4} className="empty">
                            暂无共享 —— 在左列创建只读共享链接
                          </td>
                        </tr>
                      )}
                      {shares.map((s) => (
                        <tr key={s.share_id}>
                          <td className="mono" style={{ fontSize: 11 }}>
                            {permissionLabel(s.permission)}
                          </td>
                          <td className="mono">{s.view_count}</td>
                          <td>
                            <span className={`badge ${s.revoked ? 'withdrawn' : 'saved'}`}>
                              {stateLabel(s.revoked ? 'revoked' : 'active')}
                            </span>
                          </td>
                          <td>
                            <div className="actions">
                              {!s.revoked && (
                                <button
                                  className="danger"
                                  disabled={busy}
                                  onClick={() =>
                                    runAction(() => api.revokeShare(s.share_id), '链接已撤销')
                                  }
                                >
                                  撤销
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* —— 对象统计：数字网格 —— */}
            <div className={`doc-pane ${docTab === 'stats' ? '' : 'is-hidden'}`}>
              {detail?.counts && Object.keys(detail.counts).length > 0 ? (
                <div className="stat-grid">
                  {Object.entries(detail.counts).map(([k, v]) => (
                    <div className="stat-cell" key={k}>
                      <b>{v}</b>
                      <span>{countLabel(k)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="doc-empty">暂无统计数据</div>
              )}
            </div>

            {/* —— 互操作：同一份数据以 FHIR R4 只读视图暴露给院方集成层 —— */}
            <div className={`doc-pane ${docTab === 'fhir' ? '' : 'is-hidden'}`}>
              {detail?.patient_ref ? (
                <FhirPanel patientRef={detail.patient_ref} />
              ) : (
                <div className="doc-empty">本病例未关联患者，无法生成互操作视图</div>
              )}
            </div>

          </div>
        </section>

      </div>
    </div>
  )
}
