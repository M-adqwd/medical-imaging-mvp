# 开源选型 ↔ 实现实况 对照审计报告

- **审计对象**：本仓库根目录
- **基准文档**：《医学影像阅片与手术规划系统 — 开源选型结论》（见 `docs/OSS-SELECTION.md`）
- **审计日期**：2026-10-27
- **审计方式**：读代码 + 起服务 + 真实 HTTP / CDP 实测，不是静态通读

---

## 〇、一句话结论

选型文档的每一个 `✅` 描述的是**开源生态里有没有这个能力**，而本项目此前把它们当成了**我这里已经落地**。  
两者之间隔着一层系统性缺口：**渲染是好的，交互是死的，而所有既有门禁都测不到这件事**。

本轮把「静默失效」这一类缺陷（入口存在、信号为假、实质为空）逐个止血，并补了一条真正能失败的 UI 门禁。

---

## 一、最关键的发现：门禁全绿，交互全死

审计开始时项目状态：契约测试 **45/45**、smoke **38/38**、`vite build` 通过、128 个 DICOM 实例全部可检索可取回、MPR 三视口正常渲染（512×512×128，0.70mm）。README 写着"已实测：128 层…全量加载成功"。

**但六个交互工具全部不可用。**

用 CDP 派发真实鼠标事件实测：按下 → 拖动 → 松手，测量条始终停在提示文案，没有任何数值产出。

### 根因链（逐层验证过源码）

```
cornerstoneTools.init()           只做配置与事件监听，state.tools 保持 {}
        ↓  （缺 addTool）
ToolGroup.addTool(name)           state.tools[name] 不存在 → console.warn 后 return
        ↓
getActiveToolForMouseEvent()      选不中任何工具 → 鼠标事件无人处理
        ↓
界面表现                           渲染正常、按钮高亮正常、无任何报错
```

`init()` 不注册任何工具 —— 这一点从 `store/addTool.js`、`init.js`、`ToolGroup.js` 三处源码逐行确认。

### 为什么门禁测不出来

| 门禁          | 覆盖            | 覆盖不到      |
| ----------- | ------------- | --------- |
| pytest 45 项 | API 契约、状态机、幂等 | 浏览器里发生的一切 |
| smoke 38 项  | HTTP 端到端      | 同上        |
| vite build  | 能否编译          | 运行时行为     |

**结论：README 那句"已实测"验证的是「加载」，不是「交互」。**&#x628A;加载成功写成实测通过，等于用渲染的证据为交互背书。

### 顺带暴露的同源缺陷

| #    | 缺陷                                                 | 事实                                                                                                       |
| ---- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| P0-1 | 6 个工具未注册                                           | 全部交互静默失效                                                                                                 |
| P0-2 | `Enums.Events.ANNOTATION_COMPLETED` 取到 `undefined` | 该事件在 `@cornerstonejs/tools` 的 `Enums.Events` 下，`core` 包没有 → `addEventListener(undefined)` 静默失效，测量结果永远收不到 |
| P0-3 | MIP 开关是空操作                                         | `BlendModes.MIP` / `.DEFAULT` 都是 `undefined`；真实成员是 `MAXIMUM_INTENSITY_BLEND` / `COMPOSITE`               |
| P0-4 | StackScroll 绑了 `bindings: []`                      | 滚轮处理器把 `buttons` 设为 `MouseBindings.Wheel`，绑定数组为空 → 滚轮永不匹配                                                |
| P0-5 | `removeViewports(id, [a,b,c])`                     | 该 API 只收单个 `viewportId`，传数组静默什么都不删                                                                       |
| P0-6 | `evt.detail.imageId`                               | 真实负载是 `{ image }`，imageId 在 `image.imageId` 上                                                            |
| P0-7 | 规划提交恒定 422                                         | 后端要 `entry_point`/`target_point`，前端压根没送这两个字段                                                             |

---

## 二、静默失效 vs 功能缺失（本轮采用的分类口径）

这两类不能混为一谈，处置优先级完全不同。

### 静默失效 —— 入口存在，信号为假，实质为空。**必须止血**

| 缺陷         | 表象                       | 实质                                                                                                        |
| ---------- | ------------------------ | --------------------------------------------------------------------------------------------------------- |
| 工具未注册      | 一切正常                     | 交互全死                                                                                                      |
| PDF 导出     | 返回 `application/pdf` 200 | 正文是纯文本，不是 PDF                                                                                             |
| AI 推理      | 202「已投递」                 | 任务永远停在 running，桩永收敛                                                                                       |
| **SEG 空壳** | confirmed 态、绿色「低风险」标签    | **无 PixelData，风险等级是从一个无关量算出来的**                                                                           |
| 二进制度幂等     | 重放返回 200                 | `@idempotent` 用 `jsonable_encoder` 把 `Response` 拆成了 JSON 信封，重放拿到的是 `{"status_code":200,"media_type":...}` |

### 功能缺失 —— 真的没做，但没撒谎。可按 SPEC 排期

放大镜、多窗口布局、MinIP、VR 三维重建、截图、录像、PDF 报告。

**理由**：静默失效会让使用者基于假信号做判断（临床上可能直接造成伤害）；功能缺失只会让人发现"这个还没有"。

---

## 三、最危险的一项：无像素 SEG 产出绿色「低风险」

### 事实链

1. `seg.py:build_seg_header()` 只产出元数据骨架 —— 无 `Rows`、无 `Columns`、无 `PixelData`，实测 980 字节空壳。
2. `resolve_base_segment()` 只校验 `state == confirmed`，**不校验有没有像素**。
3. `safe_distance = distance(entry, target)` —— 这是**入点到靶点的直线距离**，与解剖结构毫无关系。
4. `risk_from_distance()` 在 ≥10mm 时返回 `LOW`。

**于是：一条 180mm 的长穿刺路径被判成绿色「低风险」。**

这不是算错，是**算了一个根本不是那个量的东西，还给了它一个安全的颜色**。临床上这是最坏的失效形态。

而且这个错误被**固化进了测试**：`smoke.py` 用 `seg_content: None` 建 SEG → confirmed → 断言 `risk_level == "low"`。错误行为被当成了正确基线。

### 处置

- 新增 `RiskLevel.UNKNOWN`；SEG 无像素时一律 `unknown`
- 新增 `risk_basis` 字段，写明"底层 SEG 无像素数据，无法计算解剖安全距离"
- `Plan.risk_level` 默认值从 `LOW` 改为 `UNKNOWN`（任何未经计算的规划都不该自带低风险标签）
- 前端：`unknown` 渲染为灰色斜体「未分级」+ 黄色说明，**不进绿色分支**
- `SegmentOut` 增加 `has_pixels`，让空壳在 API 层可见
- 同步修正 `smoke.py` 与 `test_contract.py` 里那条固化错误的断言

### 实测证据

```
PASS 无像素 SEG → 风险等级 unknown
PASS 风险依据说明存在
```

### 一个我自己埋的坑（已修）

`has_pixel_data()` 最初用 `stop_before_pixels=True` 加速解析。实测发现：**那样 `"PixelData" in ds` 恒为 False** —— 带像素的合法 SEG（1066 字节）也被判成空壳。会造成假阴性、误杀合法数据。改为完整读取后：空壳 `False` / 带像素 `True` / `None` `False` / 垃圾 `False`。

---

## 四、本轮修复清单

### 后端

| 文件                                           | 修复                                                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `app/dicom/seg.py`                           | 新增 `has_pixel_data()`；修正 `stop_before_pixels` 假阴性                                                      |
| `app/models/enums.py`                        | `RiskLevel.UNKNOWN`；`BusinessCode.NOT_IMPLEMENTED` + 501                                               |
| `app/models/plan.py`                         | 新增 `risk_basis`；`risk_level` 默认改 `UNKNOWN`                                                             |
| `app/services/plans.py`                      | `risk_from_distance(距离, has_pixels)`；`seg_has_pixels()`；`recalculate` / `mark_stale` 同步写入 `risk_basis` |
| `app/core/db.py`                             | 增量列迁移（`plans.risk_basis`、`idempotency_records` 三列）—— `create_all` 不会 ALTER 存量表                         |
| `app/core/idem.py` + `models/idempotency.py` | 二进制响应 base64 快照与重放重建                                                                                   |
| `app/api/v1/deps.py`                         | `Response` 直通 + 二进制幂等快照（修掉二进制度被拆成 JSON 信封）                                                             |
| `app/api/v1/routers/plans.py`                | `format=pdf` 改为 501 + 明确指引；`dicom_sr` 改为**真实 DICOM SR**（`application/dicom`）                           |
| `app/dicom/sr.py`                            | 新增 `build_plan_sr()`；新增 `_scoord3d_item()` 写入测量端点坐标                                                    |
| `app/adapters/ai.py`                         | `mode` / `stub_notice` —— 桩必须自曝                                                                        |
| `app/api/v1/routers/ai.py`                   | 投递与查询响应携带 `mode` / `notice`                                                                            |
| `app/adapters/dicomweb.py` + `main.py`       | `/healthz` 暴露 `dicomweb_mode` / `ai_mode`                                                              |
| `app/api/v1/schemas.py` / `serializers.py`   | `risk_basis`、`has_pixels` 透出                                                                           |

### 前端

| 文件                           | 修复                                                                                                        |
| ---------------------------- | --------------------------------------------------------------------------------------------------------- |
| `api/dicom.js`               | 补 6 个 `addTool`；StackScroll 改绑 `MouseBindings.Wheel`；新增 `assertToolsRegistered()` 注册自检                    |
| `components/MprViewport.jsx` | 改用 tools 包的 `Enums.Events`；BlendModes 修正；IMAGE_LOADED 负载修正；清理逻辑重写（逐 id 移除视口 + 清定时器）；新增「规划取点」模式；测量结果携带端点坐标 |
| `pages/CaseWorkspace.jsx`    | 规划取点与影像测量互斥；提交 `entry_point`/`target_point`；风险等级渲染 `unknown`；AI 桩文案；共享访问码改为可复制内联面板                        |
| `pages/SharedCase.jsx`（新增）   | `/s/:shareId` 落地页 —— 后端一直在发这个 URL，前端一直没这条路由                                                               |
| `App.jsx`                    | 注册 `/s/:shareId` 路由                                                                                       |
| `styles.css`                 | `.risk-unknown`（**不用绿色**）、`.hint`、`.share-code`                                                           |

### 新增门禁

`scripts/gate-ui.mjs` —— 前端此前**零测试基建**。用 CDP（Node 22 原生 WebSocket，**零新增依赖**）实测真实交互：

```
G1  控制台无工具注册告警      G1b 无未捕获异常
G2  体数据加载成功            G3  进入测量模式
G4  拖拽产出数值化长度        G5  滚轮翻层改变画面
G6  共享落地页渲染            G7  正确访问码后展示病例
```

**这个门禁必须能失败，否则它就是装饰。**&#x505A;了反向验证：临时移除 `addTool(LengthTool)` → 构建 → 门禁报 **G1/G1b/G2/G4 四项失败、退出码 1**，抛出的信息可直接定位到"哪个工具没注册、该改哪一行"。恢复后 8/8、退出码 0。

---

## 五、当前基线（全部实测，非推断）

| 项            | 结果                                                                       |
| ------------ | ------------------------------------------------------------------------ |
| 契约测试         | **45 / 45 passed**                                                       |
| 端到端 smoke    | **39 / 39**（新增风险依据断言后 38→39）                                             |
| 前端 UI 门禁     | **8 / 8**，退出码 0（反向验证时 2/6、退出码 1）                                         |
| `vite build` | 通过，1503 模块                                                               |
| DICOMweb     | 128 实例可检索、可取回                                                            |
| MPR          | 三视口渲染，512×512×128，0.70mm                                                 |
| 交互           | 测量 170.27 mm 产出；滚轮翻层生效；控制台零 Cornerstone 告警                               |
| 测量 SR        | `SCOORD3D POLYLINE [10.5, 20.25, 30.75, 40.5, 50.25, 60.75]`，ForUID 正确写入 |
| 规划 SR        | `application/dicom`，`DICM` magic 校验通过，`Modality: SR`                     |
| `/healthz`   | `dicomweb_mode: local`、`ai_mode: stub` —— 降级状态自曝                         |

---

## 六、选型文档的两处事实性错误

### 1. 第 22 行「LengthTool, AngleTool, AreaTool」—— `AreaTool` 不存在

在 `@cornerstonejs/tools@4.22.13` 中核实：

- `AngleTool` —— **存在且已导出**，本项目此前未接线
- `AreaTool` —— **不存在**（`dist/esm/tools/annotation/` 下无此文件，`index.d.ts` 未导出）

面积测量的对应实现是 `RectangleROITool` / `EllipticalROITool` / `CircleROITool` / `PlanarFreehandROITool`。

### 2. 第 23 行把「放大镜 / 多窗口」归给 OHIF —— Cornerstone 自己就有

`MagnifyTool` 与 `AdvancedMagnifyTool` 都在 `@cornerstonejs/tools` 里。不引入 OHIF 也能做。这条会误导技术选型（为不必要的能力引入一整个框架）。

### 关于「2–3 周出可演示原型」

路线本身（Orthanc → Cornerstone3D → 分割 → 报告）是合理的，但 "2–3 周" 指的是**可演示**，不是**可用**。审计发现的实际状态：

- 路线里的 **TotalSegmentator 这一环没有实现**，且按 ADR-002 首期明确不接入
- 因此 **SEG 永远是无像素空壳**（契约 §1.4 说 SEG 是"分割的唯一事实源"，但它现在是空的）
- 连带所有依赖 SEG 像素的解剖学计算都无从进行

**可演示 ≠ 可交付。**&#x5EFA;议对外沟通时明确区分这两个口径。

---

## 七、仍未关闭的项

### 已登记到 `docs/OPEN-DECISIONS.md`

| ID                  | 决策       | 为什么悬着                                                                           |
| ------------------- | -------- | ------------------------------------------------------------------------------- |
| Q-SAFE-DISTANCE     | 安全距离口径   | `safe_distance` 是入路长度，不是解剖距离。需 SEG 有像素后改用 `distance_transform_edt` 算入路到分割表面最小距离 |
| Q-RA-CLASSIFICATION | 临床用途合规定性 | PRD 明写"需 RA 书面意见"，无落地证据。**若判定为临床诊断用途，当前 P2 缺口直接升 P0**                           |
| Q-SEG-PIXELS        | SEG 像素来源 | 目前没有任何路径能产出带像素的 SEG —— 这是 Q-SAFE-DISTANCE 的根因                                   |
| Q-GEOMETRY-NAMING   | 坐标系命名    | `coord_ref` 是自由字典，无 schema 约束，`volume_world` 与 `frame_of_reference` 可能在存量数据并存   |

### 表四第 46 / 47 / 48 行的落地情况（需按此改写原表）

| 行 | 原表口径 | 核实结论 | 依据 |
|---|---|---|---|
| 46 | PDF / DICOM 报告生成 · OHIF dicom-pdf 扩展 · ✅ | **双重失真**。DICOM SR 已实现；PDF **有意不做**（见 ADR-003）；OHIF dicom-pdf **从未被依赖**（OHIF 在 `web/src` 中仅出现于 `styles.css` 的注释里，共 8 处，均为设计语言说明） | `server/app/dicom/sr.py`；`grep -i ohif web/src` 仅 `styles.css` 注释命中 |
| 47 | 二三维截图 · Cornerstone viewport API · ✅ | **未实现**。`web/src` 全量无 `toBlob` / `toDataURL` / 截图导出路径 | 全量 grep 零命中 |
| 48 | 模型旋转录像 · 3D Slicer ScreenCapture · ✅ | **未实现**。无 `MediaRecorder` 或任何录屏路径；且 3D Slicer 按 SPEC §2 为 P1 且不在本版实现 UI | 全量 grep 零命中 |

### 按 SPEC 可延后的功能缺失

**2026-10-27 复核后本条已大幅作废**：放大镜、多窗口布局、MinIP、VR 三维重建**均已实现**
（`MprViewport.jsx:130` MagnifyTool、`:139-140` MIP/MinIP 混合模式、`VrViewport.jsx` 595 行并已挂载到
`CaseWorkspace.jsx:309`），不再是缺失项。我核查时的"VR 未接入"结论已被 fe-3d 的实现覆盖，**以现码为准**。

**仍然成立的功能缺失**（按 SPEC 可延后）：

- 二三维截图（`web/src` 全量无 `toBlob` / `toDataURL`）
- 模型旋转录像（无 `MediaRecorder` 或任何录屏路径）
- PDF 报告（ADR-003 裁定**有意不做**，不是缺失；`format=pdf` 返回 501 并指引 `dicom_sr`）
- 多模态融合 / 2D↔3D 共享 crosshair（VR 视口的未含项）
- 影像上传入口、全量搜索

### 下一道防线建议

1. **把 `gate-ui.mjs` 接进 CI**，与 pytest / smoke 同权。它证明过能抓到 4 项真缺陷。
2. **`coord_ref` 收成显式 schema**，别再让自由字典承载几何语义（Q-GEOMETRY-NAMING）。
3. **`risk_level=unknown` 的占比做成可观测指标**。首期它应该是 100%；一旦接入真实分割，这个数字下降才说明链路真的通了 —— 比任何"已完成"的汇报都可靠。

---

## 八、新增文档

| 文件                                                    | 内容                                      |
| ----------------------------------------------------- | --------------------------------------- |
| `docs/OPEN-DECISIONS.md`                              | 未决决策登记（只追加 + 就地关闭 + 每条 `Resolves when`） |
| `docs/decisions/ADR-001-pacs-orthanc.md`              | 为何 Orthanc 而非 DCM4CHEE                  |
| `docs/decisions/ADR-002-segmentation-out-of-scope.md` | TotalSegmentator 首期不接入，及"桩必须自曝"的强制约束    |
| `docs/decisions/ADR-003-report-pdf-out-of-scope.md`   | 报告导出只出 DICOM SR；PDF 不进首期，及"不得声明 application/pdf 却返回非 PDF"的强制约束 |
| `docs/COORDINATE-CONTRACT.md`                         | 跨端坐标契约：geometry 枚举、SCOORD3D 落地映射、服务端派生数据权威原则、三层验收断言 |

同时修正：`README.md` 选型文档链接 404（缺 UUID 前缀）；`SPEC.md` 三处指向不存在文件的引用（`StackViewport` / `MeasureOverlay` / `StackViewport.jsx` / OPEN-DECISIONS）。
