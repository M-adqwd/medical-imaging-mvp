# Spec — 医学影像阅片与手术规划系统 MVP v1.0

> 生成日期：2026-10-22
> 基于：PRD V0.9 + 接口与契约规范 V0.9 + 可行性研究报告 V1.0 + 开源选型结论
> 状态：**已确认**（用户提供三文档且已确认；本 Spec 为内部契约）
> 交付形态：**激活现有参考实现并跑通（本地直跑模式）**

---

## 1. 产品定义

- **一句话描述**：让放射/外科医师在一个系统里完成一例影像检查的调阅、阅片、测量、分割、入路规划与结构化报告输出，全部以合规 DICOM 对象持久化。
- **目标用户**：放射科诊断医师（主读）、外科术前规划医师（规划）、审阅者、规培/研究员、管理员。
- **核心问题**：阅片—后处理—规划—报告被拆在多个工具里，数据反复搬运、口径不一致、过程不可追溯、AI 无法嵌入流程。

## 2. MVP 范围（锁定）

| 优先级 | 功能 | 对应 FR | 验收标准摘要 |
|--------|------|---------|-------------|
| P0 | 病例列表 / 搜索 / 自动归并 | FR-01 | 同 Study UID 复用同一 Case |
| P0 | MPR 三视口阅片（AXIAL/SAGITTAL/CORONAL） | FR-02/03 | 128 层合成 CT 可滚动、可渲染 |
| P0 | 窗宽窗位（软组织/肺窗/骨窗 + 拖拽） | FR-02 | 三视口同步生效 |
| P0 | 长度测量写入 DICOM SR | FR-02 | SR 可下载且 `Modality=SR` |
| P0 | 分割创建 / AI 建议态 / 医师确认 / 修订 | FR-04 | 未确认 SEG 不可被 Plan 引用（409） |
| P0 | 手术规划（入路点、安全距离、风险分级、导出） | FR-05 | 导出内容携带 SEG 版本号 |
| P0 | 结构化报告（必填校验、提交、锁定、DICOM SR） | FR-06 | 缺必填 → 422；锁定后不可撤回 → 423 |
| P0 | 异步共享（访问码、只读/可评论、撤销） | FR-07 | 访问码只返回一次；撤销后 410 GONE |
| P0 | 幂等写 / 统一错误体 / 审计留痕 | §6.2-6.4 | 缺幂等键 → 400；重放返回同一资源 |
| P1 | 桌面端 3D Slicer 联动 | FR-03 | 通过同一 DICOM 事实源读取，不在本版实现 UI |
| P1 | 三维 VR 体绘制视口 | FR-03 | **已实现**（`web/src/components/VrViewport.jsx`）：`VOLUME_3D` viewport + 预设 + 不透明度 + SSR 表面阴影模式 + 鼠标旋转，与 2D 用 CSS 隐藏而非卸载以复用体数据缓存。**未含：多模态融合、2D↔3D 共享 crosshair、截图、录像** |
| P1 | 角度 / 面积 / 放大镜 / 多窗口布局 / MinIP | FR-02 | **已实现**（`dicom.js:57-61` 注册 `AngleTool`/`EllipticalROITool`/`RectangleROITool`/`MagnifyTool`；`MprViewport.jsx:88-114`）。面积与角度按坐标契约 §4.1 落 SR 空间信息 |

## 3. 明确不做（Out-of-Scope — 锁定）

| 不做的功能 | 原因 | 何时考虑 |
|------------|------|----------|
| 多方实时音视频会诊 / 光标同步 | PRD Q1 明确 P0 不做 | v2.0，需独立冲突合并模块 |
| 替换院级 PACS / RIS 排班 / 计费 | PRD §1.3 不打算做的事 | 永不 |
| 患者端应用 | PRD §1.3 | v3.0 |
| 医疗器械合规定性（RA 质量体系） | PRD Q2：先按科研/教学立项 | 临床路径独立推进 |
| HL7 v2 对接 | 契约 §2.3；缺院内接口信息，且需要 Mirth/HAPI 集成引擎 | 拿到院内接口信息后 |
| ~~HL7 v2 / FHIR 对接~~ → **FHIR 只读门面已实现**（2026-10-29）：`/fhir/*` 暴露 Patient / ImagingStudy / DiagnosticReport / Observation / Endpoint（R4，只读，无写入）；资源 ID 为不透明哈希，测量类型用本地 code system 不编造 LOINC。**仍未做**：FHIR 写侧（ServiceRequest 接单、Observation 回写）、订阅、bulk export | 写侧仍缺院内接口信息 |
| Plan 导出 PDF | **ADR-003 裁定不进首期**：SR 是唯一事实源，PDF 只是第二份人类可读渲染，两套模板必然漂移；`requirements.txt` 无 PDF 库；中文字体嵌入（约 10MB）属部署形态变更。打印需求由浏览器打印样式表承接 | 出现院外流转的强需求且愿意承担双模板维护成本时（见 `docs/decisions/ADR-003-report-pdf-out-of-scope.md`） |
| 导航系统格式导出 | PRD FR-05 标记 [TBD]，无院内导航设备接口信息 | 明确交付格式后 |
| TotalSegmentator 真机推理 | 需 GPU + 模型权重许可（PRD Q3） | 模型选型确认后 |

## 4. 技术架构（锁定 — 版本锚定）

| 层 | 技术 | 实际版本 | 锁定原因 |
|----|------|----------|----------|
| 前端框架 | React | 18.3.1 | 已装；Cornerstone3D 官方示例基准 |
| 构建 | Vite | 5.4.x | 已装；worker format=es 支持 codec worker |
| 影像渲染 | @cornerstonejs/core / tools / dicom-image-loader | 4.22.x | 开源选型结论：Web 阅片事实标准 |
| DICOM 解析（前端） | dicom-parser | 1.8.x | Cornerstone 生态配套 |
| 路由 | react-router-dom | 6.28.x | 已装 |
| 后端框架 | FastAPI | ≥0.115 | 契约参考实现已完成 |
| ASGI | uvicorn[standard] | ≥0.32 | 已装 |
| 校验/配置 | pydantic + pydantic-settings | ≥2.9 / ≥2.6 | 已装 |
| ORM | SQLAlchemy | ≥2.0.36 | 已装 |
| 数据库 | SQLite（默认 `miis.db`）；PostgreSQL 可选 | — | 开箱即用；生产切 Postgres |
| DICOM 生成 | pydicom | ≥3.0 | SR/SEG 合规生成 |
| 数组计算 | numpy | ≥2.0 | 合成 CT 生成 |
| 运行时 | Python | 3.13.12（托管 venv） | 工作区托管版本 |
| 运行时 | Node | 22.22.2（托管） | 工作区托管版本 |
| 认证（MVP） | `X-User-Id` / `X-User-Roles` 头注入 + RBAC 矩阵 | — | 契约 §1；真实 OIDC 在 v2 |
| 图标系统 | **无 emoji 图标**；UI 以排版与语义色块表达状态 | — | 团队 P0-1 |
| 主视觉 | 医疗影像灰黑板式：深墨底 + 青绿强调色 `--accent:#0A6A70` | — | 与 PRD 文档同源；禁止紫粉渐变 |

## 5. API 端点清单（锁定）

业务 API 前缀 `/api/v1`，写操作必须携带 `Idempotency-Key`；状态变更一律 `:action`。

### 5.1 DICOMweb（`/dicomweb`，无前缀）

| Method | Path | 功能 | 认证 |
|--------|------|------|------|
| GET | `/studies` | QIDO-RS 查 Study（PatientID / ModalitiesInStudy） | 只读 |
| GET | `/studies/{study}/series` | QIDO-RS 查 Series | 只读 |
| GET | `/studies/{study}/series/{series}/instances` | QIDO-RS 查 Instance | 只读 |
| GET | `/studies/{study}/metadata` | WADO-RS Study 元数据 | 只读 |
| GET | `/studies/{study}/series/{series}/metadata` | WADO-RS Series 元数据 | 只读 |
| GET | `/studies/{study}/series/{series}/instances/{sop}` | WADO-RS 单实例 | 只读 |
| GET | `/series/{series}/metadata` | WADO-RS 按 Series UID | 只读 |
| POST | `/studies` | STOW-RS 存储 | 写 |
| POST | `/studies/{study}` | STOW-RS 存到指定 Study | 写 |

### 5.2 业务 API

| Method | Path | 功能 | 认证 |
|--------|------|------|------|
| GET | `/healthz` | 健康检查 | 匿名 |
| GET | `/api/v1/contract` | 契约自描述 | 匿名 |
| GET | `/api/v1/cases` | 病例列表 / 搜索 | 登录 |
| POST | `/api/v1/cases` | 创建 Case（同 Study 自动归并） | 主读/规划/规培/管理员 |
| GET | `/api/v1/cases/{id}` | Case 详情（含 counts） | 参与者 |
| POST | `/api/v1/cases/{id}:close` / `:reopen` | 关闭 / 重开 | 主读/规划 |
| POST | `/api/v1/cases/{id}/participants` | 添加参与者 | 主读/规划 |
| POST/GET | `/api/v1/cases/{id}/measurements` | 建/列测量 SR | 主读/规划/规培 |
| PUT | `/api/v1/measurements/{sr}` | 编辑草稿 | 创建者 |
| POST | `/api/v1/measurements/{sr}:save` / `:withdraw` | 保存 / 撤回 | 创建者 |
| GET | `/api/v1/measurements/{sr}/content` | 下载 SR 原文 | 参与者 |
| POST/GET | `/api/v1/cases/{id}/segments` | 建/列分割 SEG | 主读/规划/规培 |
| POST | `/api/v1/cases/{id}/segments/merge` | 多结构合并 | 主读/规划 |
| GET | `/api/v1/segments/{seg}` | 分割详情 | 参与者 |
| POST | `/api/v1/segments/{seg}:confirm` / `:revise` / `:withdraw` | 确认/修订/撤回 | 主读/规划确认 |
| GET | `/api/v1/segments/{seg}/versions/{v}` | 指定版本 | 参与者 |
| POST/GET | `/api/v1/cases/{id}/plans` | 建/列规划 | 主读/规划 |
| PUT/GET | `/api/v1/plans/{plan_id}` | 编辑 / 详情 | 创建者 |
| POST | `/api/v1/plans/{plan_id}:save` / `:export` | 保存 / 导出 | 主读/规划 |
| POST/GET | `/api/v1/cases/{id}/reports` | 建/列报告 | 主读/规划 |
| GET/PUT | `/api/v1/reports/{report_id}` | 详情 / 编辑草稿 | 作者 |
| POST | `/api/v1/reports/{report_id}:submit` / `:lock` / `:withdraw` | 提交/锁定/撤回 | 主读/规划 |
| GET | `/api/v1/reports/{report_id}/content` | 下载报告 SR | 参与者 |
| POST/GET | `/api/v1/cases/{id}/shares` | 建/列共享 | 主读/规划/规培 |
| GET | `/api/v1/shares/{share_id}` | 共享详情（不返回访问码） | 创建者 |
| GET | `/api/v1/shares/{share_id}/access` | 访问码校验进入 | 持有码者 |
| POST | `/api/v1/shares/{share_id}:revoke` | 撤销 | 创建者 |
| GET/POST | `/api/v1/cases/{id}/comments` | 评论列表 / 发表 | 可评论者 |
| POST | `/api/v1/ai/infer` | 投递自动分割任务（202） | 主读/规划/规培 |
| POST | `/api/v1/ai-results` | AI 回调写回（建议态 SEG） | 服务间 |
| GET | `/api/v1/ai/jobs/{job_id}` | 任务状态 | 登录 |

## 6. 数据库表清单（17 张）

| 表名 | 核心字段 | 说明 |
|------|----------|------|
| `cases` | case_id, patient_ref, status, owner_id, study_uids(JSON), modality, title | 聚合根 |
| `case_participants` | case_id, user_id, role | 参与者 |
| `study_refs` | case_id, study_instance_uid, source, modality, series_count | Study 引用 |
| `measurements` / `measurement_versions` | sr_uid, type, value, unit, state, creator_id | DICOM SR |
| `segments` / `segment_versions` | seg_uid, segment_name, algorithm, version, state, confirmed_by | DICOM SEG |
| `plans` / `plan_versions` | plan_id, base_seg_uid, base_seg_version, entry/target point, safe_distance, risk_level, state | 规划 |
| `reports` / `report_versions` | report_id, template_id, findings, impression, refs(JSON), state, author_id | 报告 |
| `shares` | share_id, case_id, permission, scope, access_code_hash, view_count, revoked, expires_at | 共享（加盐 SHA-256，明文只返回一次） |
| `comments` | comment_id, case_id, author_id, body, target | 评论 |
| `ai_jobs` | job_id, study_instance_uid, status, model, stats | AI 任务 |
| `audit_events` | actor, action, target_uid, from_state, to_state, at | 审计留痕 |
| `idempotency_records` | key, scope, response_snapshot, expires_at | 幂等窗口 7 天 |

## 7. 页面清单（锁定）

| 页面 | 路由 | 核心组件 | 对应 API |
|------|------|----------|----------|
| 病例工作台（首页） | `/` | `CaseList` + 搜索框 + 契约基线卡 | `GET /cases` |
| 病例详情工作台 | `/cases/:caseId` | `CaseWorkspace` | 全部业务 API |
| 阅片视图 | 内嵌于工作台 | `MprViewport.jsx`（MPR 三视口）+ 窗宽窗位工具栏 | `/dicomweb` QIDO/WADO |
| 测量层 | 内嵌 | 无独立组件，测量状态与结果条内嵌在 `MprViewport.jsx` 的 `measure-bar` | measurement API |
| 分割 / 规划 / 报告 / 共享面板 | 内嵌于工作台 | 表格 + 表单 | 各自 API |

## 8. 设计 Token（锁定）

沿用 PRD 文档同源的医疗影像灰黑板式：

| Token | 值 | 用途 |
|-------|-----|------|
| `--bg` | `#F4F7F8` | 页面底色 |
| `--surface` | `#FFFFFF` | 卡片 |
| `--ink` | `#0F1D22` | 主文字 |
| `--muted` | `#5B6B72` | 次要文字 |
| `--rule` | `#D7E1E3` | 分隔线 |
| `--accent` | `#0A6A70` | 主强调（按钮/选中） |
| `--accent2` | `#2F5D7C` | 次强调 |
| `--ok` | `#2E7D5B` | 成功 / saved / confirmed |
| `--warn` | `#B4633A` | 警告 / medium 风险 |
| `--bad` | `#9C3B2E` | 错误 / high 风险 / withdrawn |
| `--font-sans` | InstrumentSans + PingFang SC + Microsoft YaHei | 正文 |
| `--font-mono` | IBMPlexMono | UID / 数值 |
| `--radius` | `6px` | 圆角 |

阅片视口背景为深墨 `[0.04, 0.08, 0.09]`，符合放射科读片惯例（低环境光）。
**禁止**：emoji 功能图标、紫→粉渐变、硬编码颜色（除 `#fff` / `#000`）、弹跳缓动。

## 9. 验收标准（EARS 格式）

| 编号 | 功能 | 验收标准 | 优先级 |
|------|------|----------|--------|
| AC-01 | 服务启动 | When 执行启动脚本，系统**必须**在 `/healthz` 返回 `{"status":"ok"}` | P0 |
| AC-02 | 契约自描述 | When 请求 `/api/v1/contract`，系统**必须**返回 `contract_version == "0.9"` | P0 |
| AC-03 | DICOM 索引 | When 服务启动，系统**必须**索引 `data/dicom` 下全部实例并暴露 ≥1 个 Study | P0 |
| AC-04 | QIDO | When 请求 `/dicomweb/studies/{study}/series`，系统**必须**返回 Series 列表且含 `0020000E` | P0 |
| AC-05 | WADO | When 请求单实例，系统**必须**返回 `application/dicom` 且字节 128..132 == `DICM` | P0 |
| AC-06 | 建 Case | While 提供 patient_ref 与 studies，系统**必须**创建 Case 并返回 201 + `status=open` | P0 |
| AC-07 | 自动归并 | If 同一 Study UID 再次创建，系统**必须**返回同一 `case_id` | P0 |
| AC-08 | 测量 | While 提交 length 34.7mm，系统**必须**生成 draft SR 且 `ContentSequence[0].NumericValue == "34.7"` | P0 |
| AC-09 | AI 非自动生效 | If 引用 draft SEG 建 Plan，系统**必须**返回 409 | P0 |
| AC-10 | 医师确认 | When 主读确认 SEG，系统**必须**置 `confirmed` 且写入 `confirmed_by` | P0 |
| AC-11 | 权限 | If 规培确认已确认的 SEG，系统**必须**返回 403 | P0 |
| AC-12 | 规划 | While 基于 confirmed SEG 建 Plan，系统**必须**计算 safe_distance 与 risk_level | P0 |
| AC-13 | 版本校验 | If `base_seg_version` 不匹配，系统**必须**返回 422 | P0 |
| AC-14 | 导出 | If Plan 为 draft，系统**必须**拒绝导出并返回 409；保存后导出**必须**携带 SEG 版本号 | P0 |
| AC-15 | 报告必填 | If findings/impression 为空，系统**必须**返回 422 并列出失效项 | P0 |
| AC-16 | 报告锁定 | When 报告 locked，系统**必须**拒绝撤回并返回 423 | P0 |
| AC-17 | 共享码 | When 创建共享，系统**必须**返回 8 位访问码且后续读取不再返回 | P0 |
| AC-18 | 共享撤销 | When 撤销后访问，系统**必须**返回 410 + `error.code == "GONE"` | P0 |
| AC-19 | 幂等 | While 携带同一 `Idempotency-Key` 重放，系统**必须**返回同一资源 | P0 |
| AC-20 | 缺幂等键 | If 写操作缺 `Idempotency-Key`，系统**必须**返回 400 + `IDEMPOTENCY_KEY_REQUIRED` | P0 |
| AC-21 | 越权 | If 非参与者访问 Case，系统**必须**返回 404 | P0 |
| AC-22 | 未认证 | If 无 `X-User-Id`，系统**必须**返回 401 + `UNAUTHENTICATED` | P0 |
| AC-23 | 契约测试 | When 执行 `pytest -q`，系统**必须**全部通过（基线 45 项） | P0 |
| AC-24 | 前端构建 | When 执行 `npm run build`，系统**必须**成功产出 `dist/` | P0 |
| AC-25 | 端到端冒烟 | When 服务在跑且执行 `scripts/smoke.py`，系统**必须**全部通过 | P0 |

## 10. 边界与约束

- 不支持 IE；目标 Chromium / Edge / Safari 最近两个大版本。
- 响应式断点：980px（主布局降级为单列）、700px（表格横向滚动）。
- 性能目标：128 层序列首屏渲染 ≤ 8s（本地文件存储）；QIDO 元数据 ≤ 500ms。
- 未配置 `DICOMWEB_BASE_URL` 时退化为内置文件存储（`server/data/dicom`），业务链路完整可用。
- MVP 认证为头注入，禁止用于真实临床环境。
- 风险阈值 `SAFE_DISTANCE_LOW_MM=10` / `MEDIUM_MM=5` 为工程初始假设（见 `docs/OPEN-DECISIONS.md` 的 Q-SAFE-DISTANCE）。
  另：`safe_distance` 当前取的是入点到靶点的直线距离，**并非解剖学安全距离**；
  底层 SEG 无像素数据时 `risk_level` 一律为 `unknown`，不做分级（见 §7 风险口径）。
- **阈值对外表述锁定为「待临床确认的暂定阈值」**，不得写成"实验性阈值"。
  后者读起来是"能用，只是精度待验"，会引导使用者照常采信；
  真实情况是"口径本身未定"，应当引导使用者去确认。
- **阈值状态必须是结构化字段，不是文案匹配**：`PlanOut.threshold_status`，
  取值 `"provisional"` / `"clinically_confirmed"`，默认 `"provisional"`。
  **前端分支依据只能来自该字段，禁止对 `risk_basis` 做子串匹配**（文案改一次就断一次）。
  **事实归服务端，措辞归前端** —— 服务端不返回警示文案字段。
  `threshold_status === "provisional"` 时，**任何 `risk_level`（含 `low` / `medium` / `high`）
  都必须在常驻可见位置显示警示**，不得依赖 hover / click / 展开。
  用枚举而非布尔的理由：布尔可被静默翻转且不留下"谁确认的、凭什么确认的"痕迹；
  枚举选 `clinically_confirmed` 会逼出"谁确认的"这个问题，答案正是 Q-RA-CLASSIFICATION 等的那份 RA 意见。
- **通用规则（由阈值问题上升）**：**结论越像可直接采信的判定，它"前提未确认"的警示必须越显眼。**
  反向案例即缺陷：`unknown`（最不像结论）给了最显眼说明，`high`（最像结论）只给一个 hover tooltip。
- 测量 SR 的空间信息范围按 `docs/COORDINATE-CONTRACT.md` 执行，坐标系规范值 `patient_lps_mm`。
  **面积类测量（矩形 / 椭圆 ROI）在 SR 中只落 `GraphicType = POINT` + 四个 handle 的质心，不落完整轮廓**——
  斜位 MPR 下还原四角世界坐标需按相机平面做投影重建，超出首期收益。
  两点必须写清：
  1. 面积测量的**权威数值**是 `NUM` 内容项里的 `value` + UCUM `mm2`；`POINT` 只回答"这个面积画在哪"，不回答轮廓。
  2. 下游**不得**从 `GraphicData` 反推 ROI 形状或重新计算面积。
  **`GraphicType = POINT` 本身就是机器可读的"不可还原轮廓"标记**，不再另加布尔字段
  （多一个字段就多一份可能不同步的状态）。任何引用 SR 面积测量的下游不得假设可从中还原轮廓。
- **单位：展示单位与 UCUM 代码必须分开，UCUM 代码服务端权威**
  `MeasurementUnitsCodeSequence.CodeValue` 必须是**合法 UCUM 代码**，按测量类型固定为
  `length→mm`、`angle→deg`、`area→mm2`、`volume→mm3`。
  **不得**把展示单位写进去：`°`、`mm²`、`mm³`（带度数符号与上标）、`degree` 都不是 UCUM 代码。
  客户端的 `unit` 字段只作**界面展示**，**不得**被写入 DICOM，也**不得**作为 UCUM 代码来源——
  与 spacing 同一条原则：**派生数据不跨线**，服务端按测量类型派生，不采信客户端。
  判据：`CodeValue` **不得**等于请求体 `unit` 的原值（一旦相等，说明服务端在透传而非派生）。
- **静默失效门禁条款**：任何向用户呈现"成功"信号的行为（成功提示、成功态样式、
  声明某产物格式），其背后必须有可断言的真实产物。产物为空或不可解析的，
  一律计为缺陷并优先于"功能缺失"项处理。门禁不得只测 API，必须覆盖 UI 实际发出的 payload
  （见 `docs/TASK-CARDS.md` TC-07）。
- **错误必须可定位，不只是可感知**：任何 4xx 响应用于界面展示时，字段级 `loc` / `msg`
  必须可见。只渲染一句顶层 `message` 的界面，视为**未满足错误可定位要求**。
  服务端 `_clean_errors`（`app/main.py:141-147`）已经把 `loc` / `msg` / `type` 打包好，
  界面不显示等于把排障成本转嫁给用户，且转嫁得悄无声息——
  没人会报告"错误提示不够详细"，只会觉得软件难用，因此这类问题永远不进缺陷列表。
  本条适用于测量、规划、报告、导入各条链路，不止坐标校验。
- **"某字段恒为空"必须在运行时验证**：新增跨端字段、或断言某字段不可得时，
  必须以**运行时实测值**作为证据。仅凭源码搜索（grep 字面量）得出的
  "该字段无值 / 恒为空"结论**不予采信**——只要语言支持对象展开 / 动态键 / `setattr`，
  静态搜索就有系统性盲区：它只能证明"这些地方赋了值"，永远不能证明"没有地方赋值"。
  （本轮实例：键名实为大写 F 的 `FrameOfReferenceUID`，赋值走 `...viewReference` 展开，
  字面量在 `AnnotationDisplayTool.js` 里一次都不出现，grep 必然漏。）
- **每一个不可用 reason code 都必须配一条"它不是唯一结果"的断言**：
  `unavailable` / `unknown` 的每个 reason 都要有可见比率指标，
  接入真实能力后该比率必须下降。首期 `no_pixel_data` 与 `risk_level=unknown`
  都应是 100% —— **这个 100% 本身就是要被盯着的数字**，因为它同时意味着"上游能力不存在"。
  注意：降级路径写得越完善越难发现上游从不供给 —— 那是用正确性掩盖缺失。

## 11. 内嵌已知坑

| 坑 | 技术栈指纹 | 根因 | 修法 |
|----|------------|------|------|
| 前端白屏 `does not provide an export named 'default'` | vite + @cornerstonejs/dicom-image-loader | codec 包为 CJS/UMD，ESM 静态 import 失败 | `vite.config.js` 的 `optimizeDeps.include` 预打包 codec 包 + `worker.format='es'` |
| Cornerstone 工具组重复创建 | @cornerstonejs/tools | `createToolGroup` 同名重复调用 | 卸载时逐个 `removeViewports`，见 `MprViewport.jsx` cleanup |
| 切换病例显存累积 | @cornerstonejs/core | image cache 不自动清理 | `purgeCache()` 清 `/dicomweb/` 前缀键 |
| wadouri 前缀误用 | dicom-image-loader | imageId 未带 `wadouri:` 前缀 | `instanceToImageId()` 统一拼接 |
| 合成 CT 重复索引 | pydicom + store | `.dcm` 与 `.ima` 同 SOP UID 双写 | MVP 只索引 `data/dicom/*.dcm`，排除 `data/synthetic-ct/` |

## 12. 端到端验证步骤

```bash
# 0. 准备（首次）
cd server && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd ../web && npm install

# 1. 契约测试
cd server && .venv/Scripts/python -m pytest -q          # 期望：45 passed

# 2. 启动后端（端口 8000）
cd server && .venv/Scripts/python -m uvicorn app.main:app --port 8000

# 3. 启动前端（端口 5173）
cd web && npm run dev

# 4. 健康检查
curl http://localhost:8000/healthz
curl http://localhost:8000/api/v1/contract

# 5. DICOMweb 链
curl http://localhost:8000/dicomweb/studies

# 6. 端到端冒烟（FR-01 → FR-07）
cd server && .venv/Scripts/python scripts/smoke.py      # 期望：全部通过

# 7. 前端构建
cd web && npm run build
```

## 13. 变更记录

| 日期 | 变更内容 | 原因 | 影响范围 |
|------|----------|------|----------|
| 2026-10-22 | 初版 Spec v1.0 | 三文档已确认，激活现有参考实现 | 全量 |
| 2026-10-27 | §3「Plan 导出 PDF」由 `[TBD]` 改为明确不做并链接 ADR-003；导航系统格式单列 | 消除"既没实现、也没声明不做"的中间态 | §3 明确不做表 |
| 2026-10-27 | §10 新增：阈值表述锁定为「待临床确认的暂定阈值」且必须常驻可见；面积测量 SR 只落 `POINT` 的降级取舍；静默失效门禁条款 | PM 一致性核查发现的错误信号类缺陷需要契约层约束 | §10 边界与约束 |
| 2026-10-27 | §2 三维 VR 体绘制视口由「未在 MVP 接入」改为**已实现**并列出未含项；新增一行记录角度/面积/放大镜/多窗口/MinIP 已实现 | 实现已落地，契约不得继续写"未接入"，否则审计会按旧状态判缺口 | §2 MVP 范围 |
| 2026-10-27 | §10 新增三条门禁条款：①**错误必须可定位**（4xx 的字段级 `loc`/`msg` 必须可见，只显示顶层 message 计为不通过）；②**"某字段恒为空"必须运行时验证**（不得由源码 grep 推断）；③**每个不可用 reason code 必须配可见比率指标** | 三条都来自本轮真实踩坑，且都属于"不会进缺陷列表"的隐性质量问题：错误提示不够详细只会被读成"软件难用"；grep 推断错的结论会被当成事实写进报告；降级路径越完善越掩盖上游缺失 | §10 边界与约束 |
| 2026-10-27 | §10 面积 SR 条款补两点：**权威数值是 `NUM` 的 `value` + UCUM `mm2`；下游不得从 `GraphicData` 反推形状或重算面积**；并明确 `GraphicType = POINT` 本身就是"不可还原轮廓"的机器可读标记，不另加布尔字段 | 避免下游误把 POINT 当轮廓起点；避免多一个可能不同步的状态字段 | §10 边界与约束 |
| 2026-10-27 | §10 阈值条款补 `PlanOut.threshold_status` 枚举（`provisional` / `clinically_confirmed`）与"前端禁止对 `risk_basis` 做子串匹配" | 分支依据不能是文案：文案改一次就断一次，且断掉时静默不报错 | §10 边界与约束 |
