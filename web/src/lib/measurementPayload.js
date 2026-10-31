/**
 * 测量注解 → DICOM SR 载荷的构造。
 *
 * 纯函数模块，**零 import** —— 这不是洁癖，是功能前提：
 * TC-07 的 smoke 脚本要在 Node 里直接 import 本模块，断言 graphic_type /
 * graphic_data / sop_instance_uid 以及 coord_ref 的字段集是否符合
 * docs/COORDINATE-CONTRACT.md。只要这里反向依赖 @cornerstonejs/*（浏览器包，
 * 模块顶层就要 window/document），脚本就加载不起来，"脚本与 UI 共用同一个
 * 构造器"这个目的直接落空 —— 而那条正是 TC-07 存在的理由。
 *
 * 因此工具名在这里以**字符串字面量**出现，不引用工具类。字面量与真实
 * `ToolClass.toolName` 的一致性，由 MprViewport 在模块加载时的
 * assertMeasureKindToolNames() 断言保证（漂移即抛错，不静默）。
 */

/**
 * 测量类型定义。
 *
 * `tool` 必须等于对应 ToolClass.toolName。面积测量没有 AreaTool
 * （@cornerstonejs/tools 里不存在该工具，grep 零命中），走 ROI 工具的
 * cachedStats.area，单位是 areaUnit（通常为 mm²）。
 */
export const MEASURE_KIND_DEFS = [
  { id: 'length', label: '长度', tool: 'Length' },
  { id: 'angle', label: '角度', tool: 'Angle' },
  { id: 'areaEllipse', label: '面积·椭圆', tool: 'EllipticalROI' },
  { id: 'areaRect', label: '面积·矩形', tool: 'RectangleROI' },
]

/**
 * 各测量类型的 graphic_type 与 graphic_data 长度（契约 §4.1）。
 * 供脚本断言"产出长度与类型匹配"，也是本模块自身行为的唯一说明书。
 */
export const GRAPHIC_SPEC = {
  length: { graphicType: 'POLYLINE', length: 6 },
  angle: { graphicType: 'POLYLINE', length: 9 },
  areaEllipse: { graphicType: 'POINT', length: 3 },
  areaRect: { graphicType: 'POINT', length: 3 },
}

/** 测量类型（API 的 type 字段）：两种面积 ROI 都归为 `area`。 */
export function measurementTypeOf(kindId) {
  if (kindId === 'length') return 'length'
  if (kindId === 'angle') return 'angle'
  return 'area'
}

/**
 * 从 imageId 解析 SOP Instance UID。
 *
 * 本项目 imageId 形如
 *   `wadouri:/dicomweb/studies/<study>/series/<series>/instances/<sop>`
 * （见 api/dicom.js 的 instanceToImageId），SOP UID 即最后一段路径。
 *
 * 取不到返回 null —— 调用方不得用假值顶替，缺了就让服务端 422
 * （契约 §7.3：不允许"引用不全也先存下来"）。
 */
export function parseSopInstanceUid(imageId) {
  if (typeof imageId !== 'string' || imageId === '') return null
  const path = imageId.split('?')[0]
  const last = path.slice(path.lastIndexOf('/') + 1)
  return last || null
}

const round2 = (v) => Number(Number(v).toFixed(2))

/**
 * 从一条 Cornerstone 注解构造测量载荷。
 *
 * 返回 null 表示"这条注解不该产出测量"：不是本项目的测量工具，
 * 或者统计量还没算出来（见 MprViewport 里 ANNOTATION_MODIFIED/StatsUpdated
 * 那段时序说明）。
 *
 * **不补点、不造点**：graphic_data 里每个坐标都来自 handles.points 的真实点。
 * 长度不足时宁可让服务端 422，也不在这里凑数（契约 §4.1 / §9 X6）——
 * 前端补点会把"数据不一致"变成"数据看着正常但空间位置是错的"，后者更难查。
 */
export function buildMeasurementPayload(annotation) {
  const toolName = annotation?.metadata?.toolName
  const kind = MEASURE_KIND_DEFS.find((k) => k.tool === toolName)
  if (!annotation || !kind) return null

  // 各工具的统计量键名不同（已逐一对着源码核实）：
  //   LengthTool                   → cachedStats[].length  单位 unit（mm）
  //   AngleTool                    → cachedStats[].angle   单位 deg（不产出 unit）
  //   RectangleROI / EllipticalROI → cachedStats[].area + .areaUnit（mm²）
  //
  // 单个体数据下 cachedStats 只有一个 `volumeId:` 键（BaseVolumeViewport
  // .getViewReferenceId 对三个 MPR 视口返回同一个 key），取 [0] 是确定的。
  // 用 find 只是防御：将来接 stack 视口（逐层一个 `imageId:` 键）或多体数据时，
  // [0] 会静默取到别的层的统计。
  const stats = Object.values(annotation.data?.cachedStats || {}).find(
    (s) => s && (s.length ?? s.angle ?? s.area) != null,
  )
  const raw = stats?.length ?? stats?.angle ?? stats?.area
  if (raw == null || Number.isNaN(raw)) return null
  const unit = stats?.unit || stats?.areaUnit || (stats?.angle != null ? '°' : '')

  // 端点世界坐标 = DICOM 患者坐标系 LPS mm（契约 §2：由 ImagePositionPatient /
  // ImageOrientationPatient 推导，两端之间无需坐标转换层）。
  // 只存标量的话 SR 里没有任何空间信息 —— 事后无法知道这条线画在哪，也无法复核。
  const points = annotation.data?.handles?.points || []
  let graphicType
  let graphicData

  if (kind.id === 'length') {
    graphicType = 'POLYLINE'
    graphicData = points.slice(0, 2).flatMap((p) => p.slice(0, 3).map(round2))
  } else if (kind.id === 'angle') {
    // 顶点在中间：AngleTool.js:63 `const [point1, point2, point3] = handles.points`，
    // :309/:319 画 0→1 与 1→2，:325 `center = canvasCoordinates[1]`。
    graphicType = 'POLYLINE'
    graphicData = points.slice(0, 3).flatMap((p) => p.slice(0, 3).map(round2))
  } else {
    // 面积类不能发 POLYGON：PS3.3 C.18.9.1.2 要求 POLYGON 首尾顶点重合且各点共面，
    // 只填 2 个点既不成环也不构成面，产出的是非法 GraphicData。
    //
    // 折中方案是 POINT + ROI 中心。中心取四个 handle 的质心：矩形
    // （topLeft/topRight/bottomLeft/bottomRight）与椭圆（bottom/top/left/right）
    // 点序不同，但质心都等于几何中心（drawingSvg/drawRectByCoordinates.js:15-21、
    // drawEllipseByCoordinates.js:15-19），所以取质心对两者都成立，不必按工具分支。
    graphicType = 'POINT'
    const n = points.length || 1
    graphicData = [0, 1, 2].map((axis) =>
      round2(points.reduce((sum, p) => sum + (p[axis] || 0), 0) / n),
    )
  }

  return {
    annotationUid: annotation.annotationUID,
    kind: kind.id,
    label: kind.label,
    type: measurementTypeOf(kind.id),
    value: round2(raw),
    unit,
    graphicType,
    graphicData,
    // 体渲染下"这条测量属于哪张图"不天然存在。Cornerstone 的反查结果存在
    // metadata.referencedImageId 里（AnnotationDisplayTool.getReferencedImageId
    // :104-113 → utilities.getClosestImageId(volume, worldPos, viewPlaneNormal)）。
    // 比 viewport.getCurrentImageId() 更贴切：后者按当前相机焦点反查，而
    // ANNOTATION_COMPLETED 的 detail 只有 { annotation }，没有 viewportId，
    // 拿不到"事件发生在哪个视口"。
    sopInstanceUid: parseSopInstanceUid(annotation.metadata?.referencedImageId),
  }
}

/**
 * 组装 `POST /cases/{caseId}/measurements` 的请求体。
 *
 * 把 coord_ref 的字段集收在一处，是为了让契约 §4 的"该有什么 / 不该有什么"
 * 能被脚本直接断言（TC-07），而不是靠人眼扫 JSX。三条硬约束：
 *
 * 1. `series_instance_uid` **只在顶层**，不在 coord_ref 里（契约 §7.2）。
 *    放在 coord_ref 里服务端拿不到 → SR 会挂到 f"{sr_uid}.1" 这个凭空捏造的
 *    序列下，PACS 读回时无法与源图像关联。
 * 2. `spacing` 三项**不传**（契约 §6）：服务端从影像头读更权威
 *    （store.py 入库时已存 PixelSpacing / ImagePositionPatient）。
 *    前端再传一份等于制造第二个事实源，两边不一致时系统不报错，
 *    只会产出一个 spacing 自相矛盾的 SEG。
 * 3. `frame_of_reference_uid` **不传**（契约 §4/§6）：它来自
 *    viewport.getFrameOfReferenceUID()，服务端读的是影像头同一处 ——
 *    两端比的是同一个值的两份拷贝，不是独立观测。当前端走 DICOMWeb 直连、
 *    服务端走本地存储且两边指向不同影像时，这两个"同源"值会合法地不相等，
 *    按原方案会把正常数据 422 掉。
 *
 * `geometry` 用规范值 `patient_lps_mm`（契约 §3.2）：把"患者坐标系 / LPS 手性 /
 * mm 单位"三个会导致静默误读的维度都写进值里。
 */
export function buildMeasurementCreateBody({
  studyInstanceUid,
  seriesInstanceUid,
  measurement,
}) {
  return {
    study_instance_uid: studyInstanceUid,
    series_instance_uid: seriesInstanceUid,
    type: measurement?.type || 'length',
    value: measurement?.value,
    unit: measurement?.unit || 'mm',
    coord_ref: {
      // 契约 §3.2 规范值。服务端虽把历史别名 volume_world 归一，
      // 但前端不该依赖这个兜底——测试 TC-07 直接断言此处字面量。
      geometry: 'patient_lps_mm',
      study_instance_uid: studyInstanceUid,
      // 契约 §4.2 / §7.3：缺它服务端生成不了 ReferencedSOPSequence。
      // 传 null 让服务端 422，不用假 UID 顶替。
      sop_instance_uid: measurement?.sopInstanceUid || null,
      graphic_type: measurement?.graphicType || 'POLYLINE',
      graphic_data: measurement?.graphicData || [],
    },
    cross_series: false,
  }
}
