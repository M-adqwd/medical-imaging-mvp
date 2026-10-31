# 坐标契约（Coordinate Contract）

- **状态**：生效（架构裁决，2026-10-27）
- ** owner**：架构师（高见远）
- **适用范围**：所有跨前后端边界传输的空间坐标 —— 测量端点（`MeasurementCreate.coord_ref`）、
  规划入路点/靶点（`PlanCreate.entry_point` / `target_point`）、以及由它们派生的
  DICOM SR（SCOORD3D）与 DICOM SEG（`PixelMeasuresSequence` / `FrameOfReferenceUID`）。
- **上下游**：本文件关闭 `docs/OPEN-DECISIONS.md` 的 Q-GEOMETRY-NAMING；
  是 TC-05 的验收依据；与 ADR-003（PDF 不进首期）无耦合。

---

## 0. 一句话结论

**Cornerstone3D 的 world 坐标就是 DICOM 患者坐标系（Patient Coordinate System，LPS，单位毫米）。
两端之间不需要坐标转换层，需要的是"每一组坐标必须自带坐标系声明"。**

当前代码缺的不是转换，是声明：`coord_ref` 是自由字典（`server/app/api/v1/schemas.py:64`），
`entry_point` / `target_point` 是裸 `{x, y, z}`（`server/app/api/v1/schemas.py:152-153`）。
坐标系靠"大家都知道"来传递，这是静默逻辑错误的温床 —— 一旦有人把体素索引或 RAS 塞进来，
系统不会报错，只会生成一个空间位置错误的 SR/SEG，而它长得完全正常。

---

## 1. 三个坐标系，只有一个允许跨线

| 编号 | 坐标系 | 原点 | 单位 | 是否允许跨前后端 |
|---|---|---|---|---|
| C1 | 体素索引（IJK） | 体数据第 0 个 voxel | voxel | 不允许 |
| C2 | 图像像素（column,row） | 图像 TLHC 像素 | 像素 | 不允许（2D SCOORD 用，本项目 MPR 不产生） |
| C3 | 参考坐标系（RCS / LPS mm） | Frame of Reference 原点 | **mm** | **唯一允许** |

C3 的定义依据：DICOM PS3.3 C.18.9（3D Spatial Coordinates Macro）——
`Graphic Data (0070,0022)` 是"an ordered set of (x,y,z) triplets (in mm and may be negative)
that define a region of interest in the patient-relative Reference Coordinate System
defined by Referenced Frame of Reference UID (3006,0024)"。

**契约硬规则：任何跨线坐标必须是 C3，且必须携带 `geometry` 声明与所属实例标识。**

---

## 2. 为什么不需要转换层（源码证据）

Cornerstone3D 的 world 坐标由 DICOM 影像头推导，链路如下（版本 4.22.13，均已逐文件核对）：

| 环节 | 位置 | 说明 |
|---|---|---|
| 切片间距 | `web/node_modules/@cornerstonejs/core/dist/esm/utilities/calculateSpacingBetweenImageIds.js:21` | 由相邻切片的 `ImagePositionPatient` 与 `ImageOrientationPatient` 计算 |
| 索引 → 世界 | `.../utilities/imageToWorldCoords.js:8-16`、`.../utilities/transformIndexToWorld.js` | 用 `origin + rowCosines*i*colSpacing + colCosines*j*rowSpacing + normal*k*sliceSpacing` |
| 体数据构建 | `.../cache/classes/ImageVolume.js` | volume 的 `origin / direction / spacing` 全部来自上述推导 |

推导输入是 `ImagePositionPatient (0020,0032)` 与 `ImageOrientationPatient (0020,0037)`，
输出正是 DICOM 定义的患者坐标系，轴向为 LPS（Left / Posterior / Superior），单位为毫米。

因此：

- 前端 `annotation.data.handles.points`（`web/src/components/MprViewport.jsx:350-357`）取到的 world 点，
  可以直接写进 SR 的 `GraphicData`，不需要任何换算。
- 服务端不需要、也不应该再实现一次坐标变换 —— 那只会引入第二个可能出错的事实源。

**可以省掉的是转换，不能省掉的是声明和校验。**

---

## 3. `geometry` 取值：命名收敛裁决

### 3.1 现状（三种写法同时在飞）

| 出现位置 | 值 | 评价 |
|---|---|---|
| 我上一版核查报告中的建议 | `patient_lps_mm` | 表达了空间 + 手性 + 单位 |
| `web/src/pages/CaseWorkspace.jsx:166`（当前代码） | `frame_of_reference` | 只说了"属于某个 FOR"，没说单位和手性 |
| 更早的实现 | `volume_world` | Cornerstone 内部术语，对端不可读 |

这三个值指向**同一个坐标系**，只是命名不同。不收敛就会出现第四种写法。

### 3.2 裁决

**规范值：`"patient_lps_mm"`。**

理由不是"这个名字更好听"，而是它把三个会导致静默误读的维度都写进了值里：

1. `patient` —— 是患者坐标系，不是体素索引、不是图像像素、也不是设备坐标系。
2. `lps` —— 手性。RAS 与 LPS 的 x/z 轴符号相反，把 LPS 坐标当 RAS 读会得到镜像的左右与前后。
   这是医学影像领域最经典的跨端静默错误。
3. `mm` —— 单位。像素索引与毫米差一个 spacing 因子。

`frame_of_reference` 只回答了"属于哪个 FOR"，没回答"数字怎么读"；
FOR 的归属由独立字段 `frame_of_reference_uid` 承载，两者不该混在一个字段里。

### 3.3 兼容方案（一次性收敛，不建长期别名表）

不做"长期别名映射表"这种会持续腐化的东西。做法是 **写入即归一 + 存量回填**：

1. **写入路径**：服务端在持久化前调用一次 `_normalize_coord_ref()`，把
   `frame_of_reference` / `volume_world` 改写为 `patient_lps_mm`。
   数据库里永远只有规范值。
2. **存量回填**：MVP 数据量在千行量级，直接一次 UPDATE 即可。
   ```sql
   UPDATE measurements
   SET coord_ref = json_replace(coord_ref, '$.geometry', 'patient_lps_mm')
   WHERE json_extract(coord_ref, '$.geometry') IN ('frame_of_reference', 'volume_world');
   ```
   回填后跑一条断言查询，残留必须为 0 行：
   ```sql
   SELECT count(*) FROM measurements
   WHERE json_extract(coord_ref, '$.geometry') IS NOT NULL
     AND json_extract(coord_ref, '$.geometry') <> 'patient_lps_mm';
   ```
3. **其他值**：一律 422，错误消息带上允许值列表。**不做"猜一个默认值"的兜底。**

这一步之后 Q-GEOMETRY-NAMING 关闭。

---

## 4. `coord_ref` Schema（测量）

`MeasurementCreate.coord_ref` 从 `dict[str, Any]`（`server/app/api/v1/schemas.py:64`）
收成显式模型。字段定义：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `geometry` | enum | 是 | 规范值 `patient_lps_mm`（见 §3） |
| `graphic_type` | enum | 是 | `POINT` / `MULTIPOINT` / `POLYLINE` / `POLYGON` / `ELLIPSE` / `ELLIPSOID`（PS3.3 C.18.9.1.2） |
| `graphic_data` | number[] | 是 | 扁平 (x,y,z) 三元组序列，单位 mm，可为负。长度必须是 3 的倍数 |
| `study_instance_uid` | string | 是 | 归属检查 |
| `sop_instance_uid` | string | 是 | 生成 `ReferencedSOPSequence` 的依据，也是服务端反查 `FrameOfReferenceUID` 的入口（见 §7.1） |

**不在 `coord_ref` 内的字段：**

| 字段 | 位置 | 必填 | 说明 |
|---|---|---|---|
| `series_instance_uid` | `MeasurementCreate` **顶层** | 是 | `server/app/api/v1/schemas.py:66` 已有该字段。只放顶层，**`coord_ref` 里不再重复携带** —— 放两份就是 §6 反对的第二份事实源。见 §7.2 |
| `frame_of_reference_uid` | 前端**不传** | — | **这个值前端拿得到**（`annotation.metadata.FrameOfReferenceUID`，注意大写 F，见 §4.3）。不传的理由不是拿不到，是**它不是独立事实源**：它来自 `viewport.getFrameOfReferenceUID()`，而后者取自 `imageVolume.metadata.FrameOfReferenceUID`（`core/RenderingEngine/BaseVolumeViewport.js:187-188`、`:916-918`），与服务端 `store.py:163` 从影像头读的是同一处。拿它做交叉校验等于自己校验自己。由服务端按 `sop_instance_uid` 派生，见 §7.1 |
| `row_spacing` / `column_spacing` / `slice_thickness` | 前端**不传** | — | 已从契约删除，理由见 §6 |

### 4.1 `graphic_data` 长度与 `graphic_type` 的对应（按测量类型）

| 测量类型 | 工具 | `graphic_type` | `graphic_data` 长度 | 构造方式 |
|---|---|---|---|---|
| length | LengthTool | `POLYLINE` | 6 | `handles.points` 前两点（`MprViewport.jsx:351-357` 现状，正确） |
| angle | AngleTool | `POLYLINE` | 9 | `handles.points` 三点（顶点在中间） |
| area（矩形 ROI） | RectangleROITool | `POINT` | 3 | 四个 handle 的**质心** |
| area（椭圆 ROI） | EllipticalROITool | `POINT` | 3 | 四个 handle 的**质心** |

关于面积取质心而不是"两 handle 中点"：**两种 ROI 的 `handles.points` 都是 4 个点**
（`RectangleROITool.js:46` 建 4 点；`EllipticalROITool.js:202` 建 4 点，`:273-286` 分别写入
`bottom / top / left / right`），而且**点序不同**：矩形是 `[topLeft, topRight, bottomLeft, bottomRight]`
（`drawingSvg/drawRectByCoordinates.js:15`），椭圆是 `[bottom, top, left, right]`
（`drawingSvg/drawEllipseByCoordinates.js:15`）。

四点质心对两者都等于几何中心：

- 矩形：`topLeft + bottomRight == topRight + bottomLeft`，故四角均值 `== (topLeft + bottomRight) / 2`，
  与 `drawRectByCoordinates.js:21-24` 的 `center` 一致。
- 椭圆：`left.x + right.x == top.x + bottom.x == 2 * cx`（对 y 同理），
  故四角均值 `== ((left+right)/2, (top+bottom)/2)`，与 `drawEllipseByCoordinates.js:20` 的 `center` 一致。

取质心的好处是**不必按工具分支**，也不会因为某个版本改了点序而算错中心。
按索引取"对角中点"（如 `(p0+p3)/2`）对椭圆是错的 —— 椭圆的对角对是 `0/1` 与 `2/3`。

**为什么面积类不写 `POLYGON` / `ELLIPSE`**：

- `MprViewport.jsx:365` 当前对非 length 一律发 `POLYGON`，但只填 2 个点（6 个值）。
  PS3.3 C.18.9.1.2 要求 `POLYGON` "the first and last vertices shall be the same forming a polygon;
  the points shall be coplanar" —— 2 点既不成环也不构成面，产出的是非法的 GraphicData。
- 要在斜位 MPR 下正确还原矩形 / 椭圆的四角世界坐标，需要按相机平面做投影重建，
  工作量与风险都超出首期收益。
- 折中是 `POINT` + ROI 中心点：SR 里仍然有"这条面积测量画在哪"的空间信息，
  且不违反标准。面积标量照常写入 `NUM`，不受影响。

**验收时的硬断言**：`graphic_data` 长度 mod 3 == 0，且长度与 `graphic_type` 的对应关系如上表。
不匹配 → 422，不允许"先存下来再说"。

### 4.2 `sop_instance_uid` 前端如何拿到

**取值来源：`annotation.metadata.referencedImageId`。**

我最初写的是"在 handler 里调 `viewport.getCurrentImageId()`"，这条**不成立**，前端同事用源码证否了：

- `ANNOTATION_COMPLETED` 的事件体里**没有 viewportId**。
  `stateManagement/annotation/helpers/state.js:59-64`：
  ```
  function triggerAnnotationCompleted(annotation) {
      const eventDetail = { annotation };
  ```
  只有 `annotation`（对比同文件 `:50-56` 的 `ANNOTATION_MODIFIED` 才有 `viewportId` / `renderingEngineId`）。
  所以在 COMPLETED 处理器里根本取不到视口对象。
- 而且 `metadata.referencedImageId` 本来就是同一套反查、且更准。`AnnotationDisplayTool.getReferencedImageId`
  （`tools/base/AnnotationDisplayTool.js:104-113`）在 `viewport instanceof BaseVolumeViewport` 分支里
  调用的也是 `utilities.getClosestImageId(imageVolume, worldPos, viewPlaneNormal)`，
  与 `VolumeViewport.getCurrentImageId`（`core/.../RenderingEngine/VolumeViewport.js:47-58`）是同一个函数。
  差别在于：前者按**注解自己的 worldPos** 反查，后者按**当前相机焦点**反查。
  测量画完到 handler 执行之间用户可能已经滚动过层，按注解位置反查更贴切。

解析方式：imageId 形如 `wadouri:/dicomweb/studies/<study>/series/<series>/instances/<sop>`
（见 `web/src/api/dicom.js` 的 `instanceToImageId`），取最后一段路径。
**取不到就传 `null`，不伪造 UID** —— 让服务端按 §9 直接 422。

---

### 4.3 `FrameOfReferenceUID` 前端**怎么**拿到（本项目不使用，但记录下来）

键名是 **`FrameOfReferenceUID`**（大写 F），不是 `frameOfReferenceUID`。链路：

```
core/RenderingEngine/Viewport.js:993-1008
getViewReference(viewRefSpecifier) {
    const FrameOfReferenceUID = this.getFrameOfReferenceUID();   // :995
    const target = { FrameOfReferenceUID, cameraFocalPoint, viewPlaneNormal, viewUp, ... };  // :997
}

tools/base/AnnotationDisplayTool.js:87-95
const viewReference = viewport.getViewReference({ points: [worldPos] });   // :87
AnnotationDisplayTool.createAnnotation({
    metadata: { toolName, ...viewReference, referencedImageId, viewUp, cameraPosition },  // :91
})
```

所以读法是 `annotation.metadata.FrameOfReferenceUID`。本项目不使用它，理由见 §4 字段表与 §6。

## 5. 规划点位 Schema

`PlanCreate.entry_point` / `target_point` 当前是 `dict[str, float]`（裸 `{x,y,z}`，
`server/app/api/v1/schemas.py:152-153`）。改为自声明结构：

```json
{
  "geometry": "patient_lps_mm",
  "coordinates": [12.34, -56.78, 90.12]
}
```

配套：

| 字段 | 位置 | 必填 | 说明 |
|---|---|---|---|
| `entry_point` | `PlanCreate` | 是 | `{geometry, coordinates[3]}` |
| `target_point` | `PlanCreate` | 是 | `{geometry, coordinates[3]}` |
| `frame_of_reference_uid` | `PlanCreate` | **不传** | 与 §4 同一理由：前端值不是独立事实源（见 §4.3），交叉校验等于自己校验自己。由服务端从 base SEG / 序列头派生 |

**为什么规划点也要带 `geometry`**：`safe_distance` 现在就是这两个点的欧氏距离
（`server/app/services/plans.py:115-126`）。如果点被当成体素索引传进来，
算出来的"毫米"会差一个 spacing 因子，而界面会照常显示一个绿色数字。
这是 Q-SAFE-DISTANCE 之外又一条可以静默出错的路径，必须在入口堵死。

---

## 6. 派生数据不跨线（spacing / FOR UID 的服务端权威原则）

`web/src/pages/CaseWorkspace.jsx:172-174` 现在把 `row_spacing` / `column_spacing` /
`slice_thickness` 随测量一起发上来。这一条要从契约里删掉，理由：

1. **服务端本来就有这些值，而且是从权威来源读的。**
   `server/app/dicom/store.py:156-163` 在入库时已经把 `ImagePositionPatient`、
   `PixelSpacing`、`FrameOfReferenceUID` 存进实例元数据。
2. **跨线传输可派生数据 = 制造第二份事实源。**
   前端的 spacing 来自 `volume.spacing`（`MprViewport.jsx:213`），
   服务端的来自影像头。两者一旦不一致（重建算法差异、多序列混合、手动改过 spacing），
   系统不会报错，只会产出一个 spacing 自相矛盾的 SEG。PM 在 TC-05 里担心的
   "spacings 必须是与 DICOM 头一致的真实值"，靠"让前端传得更准"是治不好的，
   靠"服务端只认自己读到的"才能治好。

### 6.1 服务端行为约定

| 数据 | 来源 | 前端传了怎么办 |
|---|---|---|
| `PixelSpacing` / 层厚 | 影像头（经 `store.py` 元数据） | 忽略 |
| `FrameOfReferenceUID` | 服务端按 `sop_instance_uid` 从实例元数据派生（`store.py:163`） | **不接收**。前端拿不到可信值（见 §4），传了也只会是 `null` |
| `sop_instance_uid` | 前端（服务端无法从世界坐标反推） | 必须传，缺失 → 422 |

**不一致就报错，不猜。** 写错坐标系的 SR/SEG 会被下游按错误的空间位置解读，
这类错误的检出成本远高于一次 422。

`FrameOfReferenceUID` 是本原则的一个反例修正：我最初让它"前端传 + 服务端交叉校验"，
但实现阶段发现**这个校验没有意义**：前端那个值来自
`viewport.getFrameOfReferenceUID()`（经 `getViewReference()` 展开进 `metadata`，
见 §4.3），而它取自 `imageVolume.metadata.FrameOfReferenceUID`
（`core/RenderingEngine/BaseVolumeViewport.js:187-188`、`:916-918`），
与服务端从影像头读的是**同一处**。两端比对的是同一个值的两份拷贝，
不是两个独立观测 —— 这种校验永远通过，或者在元数据供应源不同
（dicomweb 直连 vs 本地文件存储）时对合法数据误报 422。

正确做法是让服务端自己从影像头派生，前端不参与。

**顺带一条方法论教训（这次踩到的）**：我最初用"全量 grep `frameOfReferenceUID`
字面量"得出"工具从不写该字段"的结论，**这个结论是错的**，而且方法本身就不成立 ——
赋值走的是 `...viewReference` 对象展开（`AnnotationDisplayTool.js:91`），
字面量在 `AnnotationDisplayTool.js` 里一次都不出现，grep 必然漏。
**静态搜索只能证明"这些地方赋了值"，不能证明"没有地方赋值"**；
只要语言支持对象展开 / 动态键 / `setattr`，这个方法就有系统性盲区。
要断言"某字段运行时恒为空"，必须**在运行时观测**（打点读一次实际值），
不能靠 grep 推断。

---

## 7. DICOM SR 落地映射（含对现行代码的标准符合性纠正）

### 7.1 SCOORD3D 内容项（`server/app/dicom/sr.py:173-192`）

| 属性 | Tag | Type | 当前实现 | 纠正 |
|---|---|---|---|---|
| `ReferencedFrameOfReferenceUID` | (3006,0024) | **1** | `sr.py:189-191` `if for_uid:` 条件写入（且前端该字段恒为 `null`，见 §4） | **必填**。FOR UID 缺失时不得生成 SCOORD3D 项，应让整条测量 422。现状是"没 FOR 就写个没归属的坐标"，产出的 SR 不合规且完全看不出来。**取值来源改为服务端按 `sop_instance_uid` 从实例元数据派生**（`server/app/dicom/store.py:163` 已写入 `FrameOfReferenceUID`），不再依赖前端传入 |
| `GraphicData` | (0070,0022) | 1 | `sr.py:187` 已写 | 保持；单位由标准定为 mm |
| `GraphicType` | (0070,0023) | 1 | `sr.py:186` 默认 `POLYLINE` | 按 §4.1 表映射，`POLYGON` 非法情形要挡掉 |
| `ReferencedSOPSequence` | — | — | 放在 NUM 内容项上（`sr.py:141-143`） | **保持放在 NUM 项上，不要挪到 SCOORD3D 项上**。PS3.3 Supplement 162 明确 SCOORD3D "has no children, since its coordinates are patient rather than image-relative" |

关于 PM 在 TC-05 里写的验收项 `GraphicDataUnits=mm`：**标准里没有这个属性。**
SCOORD3D 的单位由 C.18.9 固定为 mm，不存在可写的单位字段。对应验收应改为两条：

1. 数值项的 `MeasurementUnitsCodeSequence` 含 UCUM `mm`（`sr.py:132`，已实现）。
2. `GraphicData` 的值域与影像头 `PixelSpacing` 推导出的解剖尺度一致 ——
   即"两点间距离 / 影像头换算距离 == 1"，这是间接但可执行的单位证明。

### 7.2 SR 的 `SeriesInstanceUID`

`sr.py:95` 与 `sr.py:320` 都是 `series_instance_uid or f"{sr_uid}.1"`。
`f"{sr_uid}.1"` 不是真实序列 —— 它让 SR 在 PACS 里挂在一个凭空捏造的 series 下，
读回时无法与源图像关联。

修法分两步：

1. **前端**：`series_instance_uid` 必须从 `coord_ref` 里挪到 `MeasurementCreate` 顶层
   （`server/app/api/v1/schemas.py:66` 已有这个字段，前端没用）。改一行。
2. **服务端**：顶层 `series_instance_uid` 缺失时 422，不再回落到 `f"{sr_uid}.1"`。
   报告 SR（`build_report_sr`，`sr.py:233`）用 `new_dicom_uid()` 是合理的 ——
   报告不属于任何影像序列，可以保留。

### 7.3 `ReferencedSOPSequence` 为空

`sr.py:195-209` `_referenced_sop()` 在 `sop_instance_uid` 或 `series_uid` 为空时返回 `[]`，
调用方 `sr.py:140-143` 也不检查 —— 结果是静默生成一份没有源图像引用的测量 SR。

修法：`sop_instance_uid` 已在 §4 列为必填，服务端校验失败即 422。
**不允许"引用不全也先存下来"。**

---

## 8. DICOM SEG 落地映射

`server/app/dicom/seg.py:42-147` 目前是元数据骨架（无 `PixelData`、无 `Rows/Columns`、
`referenced_series` 为空）。坐标相关字段在补像素时必须按此表落：

| 属性 | 来源 | 说明 |
|---|---|---|
| `FrameOfReferenceUID` | 影像头（§6） | 与 SR 的 SCOORD3D 必须指向同一个 FOR，否则两端坐标不可比 |
| `PixelMeasuresSequence.PixelSpacing` | 影像头 `PixelSpacing` | 不得取自前端 |
| `PixelMeasuresSequence.SpacingBetweenSlices` | 影像头或 `ImagePositionPatient` 推导 | 同上 |
| `ReferencedSeriesSequence` | 真实的 source series | 现状为空（`seg.py` referenced_series），补像素时必须填真实序列 |

体积计算（`SegmentVolumeOut.spacing_mm`，`schemas.py:123`）必须来自上述
`PixelMeasuresSequence`，而不是请求体里带的任何值。

---

## 9. 失败语义：本契约涉及的禁止清单

以下行为一律禁止，出现在任何一层都视为缺陷：

| 编号 | 禁止行为 | 现状 | 期望 |
|---|---|---|---|
| X1 | 坐标系缺失时按默认值处理 | `coord_ref` 自由字典，无校验 | 422 + 允许值列表 |
| X2 | FOR UID 缺失时条件省略属性 | `sr.py:189` | 422 |
| X3 | 序列 UID 缺失时造一个 | `sr.py:95` `f"{sr_uid}.1"` | 422 |
| X4 | 源图像引用缺失时空数组 | `sr.py:198-199` | 422 |
| X5 | 前后端各持一份可派生数据且互不校验 | `CaseWorkspace.jsx:172-174` 传 spacing | 服务端只认影像头；传入不一致 → 422 |
| X6 | `graphic_data` 长度与 `graphic_type` 不符时照写 | `MprViewport.jsx:365` 对面积发 `POLYGON` + 2 点 | 422 |

---

## 10. 验收断言（可直接写成测试）

### 10.1 纯函数层（不需要浏览器）

| 编号 | 断言 |
|---|---|
| A1 | `coord_ref.geometry` 为 `frame_of_reference` / `volume_world` 时，持久化后读回为 `patient_lps_mm` |
| A2 | `coord_ref.geometry` 为其他任意值 → 422，错误体含 `patient_lps_mm` |
| A3 | `graphic_data` 长度非 3 的倍数 → 422 |
| A4 | `graphic_type=POLYLINE` 且长度 ≠ 6 → 422 |
| A5 | 缺 `sop_instance_uid` → 422，且不生成 SR |
| A6 | 顶层 `series_instance_uid` 缺失 → 422（不再回落 `f"{sr_uid}.1"`） |
| A7 | `coord_ref` 内**不得**出现 `series_instance_uid`（重复事实源）；顶层缺失 → 422 |
| A8 | 传入的 spacing 与影像头不一致 → 服务端采用影像头的值，返回值等于影像头值 |
| A9 | 服务端能按 `sop_instance_uid` 取到 `FrameOfReferenceUID`；取不到 → 422，不生成 SCOORD3D |

### 10.2 DICOM 产物层

| 编号 | 断言 |
|---|---|
| B1 | 测量 SR 含 SCOORD3D 内容项，且 `ReferencedFrameOfReferenceUID` 存在且非空 |
| B2 | `GraphicData` 长度与 `graphic_type` 匹配（见 §4.1） |
| B3 | `GraphicData` 两点间距离 / 影像头换算距离 == 1（容差 1e-3） |
| B4 | NUM 内容项的 `ReferencedSOPSequence` 长度 ≥ 1，且 `ReferencedSOPInstanceUID` 等于前端传的 `sop_instance_uid` |
| B5 | SR 的 `SeriesInstanceUID` 等于真实序列 UID，不等于 `f"{sr_uid}.1"` |
| B6 | 数值项 `MeasurementUnitsCodeSequence` 的 `CodeValue` 是该测量类型对应的**合法 UCUM 代码**：length→`mm`、angle→`deg`、area→`mm2`、volume→`mm3`。**不允许 `°`（那是 print symbol 不是代码）、不允许 `degree`、不允许上标 `mm²` / `mm³`**（UCUM §3.1 限定 7-bit ASCII，指数必须是紧跟单位的 ASCII 数字，见 §10.4） |
| B8 | 服务端按测量类型校验 `unit` 白名单，白名单外的值 → 422。客户端不得向 UCUM `CodeValue` 注入任意字符串 |
| B7 | SEG 的 `FrameOfReferenceUID` 与同病例 SR 的 `ReferencedFrameOfReferenceUID` 相等 |

### 10.3 跨端一致性（3D Slicer 交叉验证）

| 编号 | 断言 |
|---|---|
| C1 | 在 Cornerstone 视口量一段已知长度（如体模或已知 spacing × 已知 voxel 数），前端上报值与理论值偏差 ≤ 0.5 mm |
| C2 | 导出的 SR 用 3D Slicer 载入，SCOORD3D 点的世界坐标与 Cornerstone 端显示的世界坐标偏差 ≤ 0.5 mm |
| C3 | 同一段线在四个视口（三 MPR + VR）分别量取，结果偏差 ≤ 0.5 mm |

C1/C2 是本契约最终的"证明成立"手段 —— 前面所有断言都只是保证数据格式对，
只有跨软件对齐才能证明**语义**对。

### 10.4 单位代码（UCUM）速查

`MeasurementUnitsCodeSequence` 的 `CodeValue` 必须是 UCUM **交换码**，不是显示符号。
依据 UCUM §3.1（限定 7-bit US-ASCII）、§9（指数为紧跟单位的 ASCII 整数）、§31 表 5。

| 测量类型 | 合法 UCUM | 常见错误写法 | 为什么错 |
|---|---|---|---|
| length | `mm` | — | — |
| angle | `deg` | `°` / `degree` | `°` 是 **print symbol**，UCUM 代码是 `deg`（定义 `[pi].rad/180`）；`degree` 不是任何 UCUM 代码 |
| area | `mm2` | `mm²` | 上标不在 ASCII 范围内，UCUM 交换码禁用 |
| volume | `mm3` | `mm³` | 同上 |

**跨线约束**：`unit` 是测量的固有属性，不是可派生量，所以**前端声明它是对的**
（不属于 §6 的"派生数据不跨线"，那条只约束可派生量）。
但服务端**必须按类型校验白名单**，不得把客户端字符串直接写进 UCUM `CodeValue` ——
`sr.py:139` 现在是直写，这是一个任意字符串注入到标准编码字段的口子，
和 `coord_ref` 曾是自由字典是同一类问题。

---

## 11. 与其他条目的关系

- 关闭 `docs/OPEN-DECISIONS.md` 的 **Q-GEOMETRY-NAMING**（§3 给出收敛方案与回填步骤）。
- 是 **TC-05** 的验收依据；TC-05 中的 `GraphicDataUnits=mm` 一条按 §7.1 修正。
- 不解决 **Q-SAFE-DISTANCE**（那是口径问题，不是坐标问题）。§5 只堵住"坐标被传错"
  这一类失效，堵不住"算的量本身不对"。
- 不解决 **Q-SEG-PIXELS**。SEG 有像素之前，§8 的字段无处落地。
- 与 **ADR-003**（PDF 不进首期）无耦合。

---

## 12. 未决

- 多序列混合（一个病例内存在不同 FOR）时的 SCOORD3D 归属策略：当前按
  `sop_instance_uid` 单点归属，跨 FOR 测量在首期按 §9 X1 直接拒绝。
  是否支持"跨 FOR 测量"留到有真实需求时再开条目。
