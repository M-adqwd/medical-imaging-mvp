## 医学影像阅片与手术规划系统 — 开源选型结论

---

### 一、数据接入与集成

| 需求 | 开源方案 | 落地情况 |
|---|---|---|
| DICOM 3.0 导入 | Cornerstone3D (`file://` / `wadouri://`) | ✅ 成熟，Web 直接读 DICOM 文件 |
| PACS 调阅 | DCM4CHEE-arc-light（DIMSE C-FIND/C-MOVE/C-GET）| ✅ 企业级生产可用 |
| HL7 / FHIR | DCM4CHEE 内置 HL7 v2；HAPI FHIR Server 对接 FHIR | ✅ DCM4CHEE 的 HL7 很成熟 |
| HIS / EMR / PACS 对接 | Orthanc REST API + 插件机制 | ✅ 轻量级首选 |
| RESTful API / SDK 预留 AI 接口 | Orthanc 原生 REST API；MONAI Deploy | ✅ |

---

### 二、二维阅片与三维重建

| 需求 | 开源方案 | 落地情况 |
|---|---|---|
| 窗宽窗位 / MPR / MIP / MinIP | Cornerstone3D（`ORTHOGRAPHIC` viewport + blend modes）| ✅ WebGL 加速 |
| 长度 / 角度 / 面积测量 | `@cornerstonejs/tools`（LengthTool, AngleTool, AreaTool）| ✅ |
| 缩放 / 平移 / 放大镜 / 多窗口 | OHIF 内置，Cornerstone 事件体系 | ✅ |
| CT/MRI 自动三维重建 VR | Cornerstone `VOLUME_3D`（Web）或 3D Slicer VTK（桌面）| ✅ |
| 旋转 / 缩放 / 透明度 / 多模态融合 | Cornerstone + OHIF VTK 扩展 / 3D Slicer | ✅ |

---

### 三、分割编辑与手术规划

| 需求 | 开源方案 | 落地情况 |
|---|---|---|
| 自动分割（器官/血管/骨骼）| TotalSegmentator（100+ 结构）/ MONAI Label | ✅ 3D Slicer 有现成插件 |
| 半自动 / 手动分割工具 | ITK-SNAP（主动轮廓）/ 3D Slicer SegmentEditor | ✅ 3D Slicer 更强 |
| 裁切 / 平滑 / 布尔编辑 | 3D Slicer SegmentEditor（Logical Operators, Margin, Smoothing）| ✅ |
| 三带入路模拟 | 3D Slicer + SlicerIGT（手术导航工具包）| ✅ SlicerIGT 是专门做这个的 |
| 距离 / 角度 / 体积测量 | 3D Slicer Markups / OHIF measurement-tracking | ✅ |
| 二三维联动定位 | 3D Slicer 原生 / OHIF LinkedScroll + crosshair | ✅ |

---

### 四、报告输出与远程扩展

| 需求 | 开源方案 | 落地情况 |
|---|---|---|
| PDF / DICOM 报告生成 | OHIF dicom-pdf 扩展 / 3D Slicer 报告模块 | ✅ |
| 二三维截图 | Cornerstone `viewport` API / OHIF 内置截图 | ✅ |
| 模型旋转录像 | 3D Slicer ScreenCapture 模块 | ✅ |
| 三维模型远程共享 / 标注 | OHIF measurement-tracking + 自建 WebSocket 同步 | ⚠️ 基础标注可用，完整实时协作需二开 |
| 预留远程协作接口 | Orthanc REST API / DCM4CHEE 插件机制 | ✅ |

---

### 五、高优先级开源项目速查

| 项目 | GitHub | Stars | 核心定位 |
|---|---|---|---|
| **OHIF Viewer** | github.com/OHIF/Viewers | ~4.4k | Web DICOM 全功能阅片 |
| **Cornerstone3D** | github.com/cornerstonejs/core | ~1.2k | WebGL 医学影像渲染引擎 |
| **3D Slicer** | github.com/Slicer/Slicer | ~1.6k | 桌面手术规划金标准 |
| **MONAI** | github.com/Project-MONAI/MONAI | ~8.6k | 医学影像 AI 框架 |
| **nnU-Net** | github.com/MIC-DKFZ/nnUNet | ~8.8k | 自动分割 pipelines |
| **TotalSegmentator** | github.com/wasserth/TotalSegmentator | ~2.3k | 100+ 结构自动分割 |
| **DCM4CHEE** | github.com/dcm4che/dcm4chee-arc-light | ~500 | 企业级 DICOM PACS |
| **Orthanc** | github.com/jodogne/Orthanc | — | 轻量 DICOM PACS + REST |
| **ITK-SNAP** | github.com/pyushkevich/itksnap | ~458 | 交互式分割工具 |
| **dcm4che** | github.com/dcm4che/dcm4che | ~1.5k | DICOM Java 工具库 |

---

### 六、结论

**唯一需要二开的功能**是"三维模型远程协作标注/实时交互"——OHIF 和 3D Slicer 有基础标注能力，但完整的多人实时协作（类似 MR 远程会诊）需要基于现有 REST API + WebSocket 自建同步层，这一块没有开箱即用的开源方案。

其他所有模块都有成熟开源方案可以直接用或通过简单集成落地。

**最低落地成本路线：**

```
Orthanc（轻量 PACS）
    ↓
OHIF / Cornerstone3D（Web 阅片前端）
    ↓
TotalSegmentator（AI 自动分割，3D Slicer 插件）
    ↓
OHIF dicom-pdf（报告输出）
```

约 **2–3 周**可出可演示原型。
