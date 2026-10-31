# 需求覆盖一致性核查报告 · 医学影像阅片与手术规划系统 MVP

> 核查人：许清楚（产品经理）
> 核查日期：2026-10-27（**v1.2**，含架构师交付坐标契约与 ADR-003 后的增量）
> 核查对象：`medical-imaging-oss-selection.md`（选型结论）× PRD V0.9 × SPEC v1.0 × 当前实现
> 核查方式：静态代码走读 + 启动后端（端口 8099）做端到端请求复现 + DICOM 二进制解剖
> 明确声明：**本次核查未修改任何代码**（v1.2 阶段补写了 `docs/` 下的文档，仍不涉及 `web/` 与 `server/`）

---

## 0.0.00 v1.3 变更记录（v1.2 之后又有 3 项实现落地，我重新核了一遍代码）

写 v1.2 期间，团队并发完成了 3 项实现（任务 #4 / #5 / #6），并顺带补完了工具链。**本报告的"无入口"类判断已过期，必须更新，否则会误导排期。**

| 变化 | 事实 | 对本报告的影响 |
|---|---|---|
| **VR 三维视口已实现** | `web/src/components/VrViewport.jsx`（595 行）已挂载进 `CaseWorkspace.jsx:309`，`VOLUME_3D` viewport + 预设 + 不透明度 + SSR（表面阴影）模式 + 独立工具组（TrackballRotate/Pan/Zoom） | §3 二表「CT/MRI 自动三维重建 VR → 无入口」**作废**，改为可演示；「二三维联动」也随之改判 |
| **角度 / 面积 / 放大镜 / 多窗口布局已实现** | `dicom.js:57-61` 新增 `AngleTool` / `EllipticalROITool` / `RectangleROITool` / `MagnifyTool` / `ScaleOverlayTool`；`MprViewport.jsx:88-90` 角度 + 面积椭圆 + 面积矩形三种测量模式；`:97` 放大镜模式；`:101-107` 合成 / MIP / MinIP 三档；`:110-114` 三视图 / 轴位单屏 / 轴位+冠位三种布局 | §3 二表「角度 / 面积只能手填」「放大镜无」「多窗口无」「MinIP 无」**全部作废**。**这是我 v1.0 的核心指控之一，必须当场撤回** |
| **共享落地页已实现** | `web/src/pages/SharedCase.jsx`（119 行）+ `App.jsx:108` 的 `/s/:shareId` 路由 | §9.1 第 11 项**关闭**；§6 卡 G 部分解除（评论前端仍缺） |
| **DICOMDIR 导入已是 API** | `server/app/api/v1/routers/imports.py` 支持介质索引与无扩展名递归扫描 | 但前端 `createCase`（`client.js:64`）**仍然零调用**，无 UI 入口 → B6 主体仍成立 |
| **体积计算已实现（但不可达）** | `seg.py:276-312` `compute_volume`：体素计数 × 三轴间距，写法诚实（缺 spacing / 非 BINARY / 无像素一律返回 `unavailable` + reason，**不编数字**） | **但 `build_seg_header`（`seg.py:50-155`）仍不写 `PixelData` / `Rows` / `Columns`**，`referenced_series` 仍是空 → 恒返回 `no_pixel_data`。新增一条缺口（见 §9.1 第 12 项） |

**净结论不变（verdict 仍为 fail），但指控范围收窄了**：v1.0 我列的功能缺失里，
**VR / 角度 / 面积 / 放大镜 / 多窗口 / MinIP / 共享落地页 共 7 项已被真实实现补上**。
剩下真正可演示性缺口的，是"信号造假"那条线（B2–B7）+ 数量更少的真功能缺失（截图、录像、多模态融合、上传入口、搜索、体积闭环）。

---

## 0.0.0 v1.2 变更记录

架构师交付了 `docs/COORDINATE-CONTRACT.md` 与 `docs/decisions/ADR-003-report-pdf-out-of-scope.md`。
两份文档**关闭了两个此前悬空的决策**，并带来 5 条对现有验收项的纠正。

| 变化 | 内容 |
|---|---|
| **PDF 已裁决** | **不进 MVP**（ADR-003）。B2 的"二选一"改为确定动作：先 501 止血，再删 `format` 参数 |
| **选型第 46 行拆分** | 「PDF / DICOM 报告生成」合在一格同打 ✅ 是错的——SR 已实现、PDF 决定不做，处置相反。§3 四表已拆成两格，并纠正"OHIF dicom-pdf 从未真正依赖"（全仓 `OHIF` 只在一处 CSS 注释里） |
| **坐标系命名已裁决** | 规范值 `patient_lps_mm`。`docs/OPEN-DECISIONS.md` 的 Q-GEOMETRY-NAMING **已关闭**，执行项拆为 Q-GEOMETRY-ROLLOUT |
| **TC-05 验收项纠正 5 处→7 处** | 作废 `GraphicDataUnits=mm`（标准无此属性）、"spacing 必须是真实值"（spacing 不该跨线）、"FOR UID 前端传 + 交叉校验"（两端读同一处，等于自己校验自己）、面积取"两 handle 中点"（应为四点质心）。详见 `docs/TASK-CARDS.md` TC-05 的 C1–C7 表 |
| **新增 TC-02 阈值可见性验收** | 措辞定为「待临床确认的暂定阈值」（不用"实验性阈值"）。且发现 `risk_basis` 在非 `unknown` 等级下**只是 `title` 属性**，悬停才可见——阈值警示恰在最需要它时不可见。后续按架构师裁定改为结构化字段 `threshold_status`（`provisional` / `clinically_confirmed`），**禁止前端对 `risk_basis` 做子串匹配** |
| **新增两条门禁条款** | SPEC §10 补「错误必须可定位」（4xx 字段级 `loc`/`msg` 必须可见）与「某字段恒为空必须运行时验证」（不得由 grep 推断） |
| **新增单位合规缺口** | §9.1 第 17 项：`sr.py:140` 把客户端 `unit` 透传成 `CodingScheme="UCUM"` 的 `CodeValue`，导致 `°` / `mm²` / `mm³` / `degree` 这些非 UCUM 值写入 SR。**四类测量里三类受影响**，`length` 的 `mm` 恰好合法所以把问题掩盖了。已落 SPEC §10 单位条款 + TC-05 `AC-2b` |
| **新增潜伏缺陷** | `Q-RISK-FALLBACK`：`plans.py:67-68` 在 `safe_distance is None` 时返回 `MEDIUM`——输入缺失却给出具体等级 |
| **TC-07 进度** | 前端已交付"零 import 的 payload 构造器 + 14 条 `node:test` 用例（零第三方依赖）"；lint / E2E / `buildPlanPayload` 仍未开始。并记录一条设计分叉：**JS 构造器与 Python smoke 之间"同源"不是自动的**，需薄 Node CLI，且 `smoke.py` 必须原样转发、不得二次改写字段 |
| **SPEC 已补 2 处** | §3 PDF 由 `[TBD]` 改为明确不做并链 ADR-003；§10 新增阈值表述、面积 SR 降级取舍、静默失效门禁条款 |

---

## 0.0 v1.1 变更记录

架构师（mvp-dev-expert-team-architect）对本报告 4 项接口层发现做了独立复核，结论**全部成立**。
复核期间有 5 项被并发修复（见 §1.1），同时新发现 1 项 P0（见 B7）、补充 2 项影响面更大的发现。

**v1.1 相对 v1.0 的变化**：

| 变化 | 内容 |
|---|---|
| 新增 B7 | 导出端点幂等重放语义破坏（架构师新发现，我已实测复现） |
| 新增 §8 | 把「静默失效」从「功能缺失」中独立成一类，附优先级矩阵 |
| 新增 §9 | 并发修复后的存活缺口清单（v1.1 实时盘点） |
| 状态更新 | B1 已修复；B2 的「JSON 壳」已修复，但「假 PDF」仍在；B3 的「桩不回调」仍在 |
| 任务卡 | 配套 `docs/TASK-CARDS.md`，7 张 Given/When/Then 验收卡 |

---

## 0. 结论摘要

**verdict: fail**（针对"这份实现已跑通、可对外演示 PRD 全量 FR"这一命题）

需要澄清的是：这份实现的**契约层质量很高**——45 项契约测试全绿（实测 `45 passed`），
状态机、幂等、RBAC、审计留痕、DICOM SR/SEG 生成都是真的。**MPR 三视口阅片是真正能用的**。

问题高度集中在一个地方：**用户点得着的那几个按钮，多数是不通的**。
规划按钮必报 422、AI 分割点了没反应、报告拿不走、搜索框是装饰品、分割是 401 字节的空壳。

一句话总结：**这是一份"契约完备但交互未闭环"的实现。**
后端把该有的都造好了，前端把该连的都漏了，且漏的恰好是演示时最常被点的路径。

---

## 1. 阻塞问题（blocking）

### 1.1 复核期间的并发修复（v1.1 实时盘点）

以下 5 项在本报告发出后被并发修复，已从 blocking 移出：

| 原编号 | 问题 | 修复位置 | 复核结论 |
|---|---|---|---|
| B1 | Plan 创建 422 | `web/src/pages/CaseWorkspace.jsx:519-524` 传 `entry_point`/`target_point`；`MprViewport.jsx:277-283` 采集 world 坐标；`CaseWorkspace.jsx:48` 维护 `planPoints` | ✅ 已修复，架构师实测 201 成功 |
| B2-a | 幂等层把 `Response` 序列化成 JSON 壳 | `server/app/api/v1/deps.py:80-82` 加 `isinstance(result, Response)` 透传 | ✅ 已修复，实测 `ct=application/pdf` + 正确的 `Content-Disposition` |
| — | 工具未注册导致全部静默失效 | `web/src/api/dicom.js:47-52` 补 6 个 `addTool` | ✅ 已修复 |
| — | 测量事件名取错 | `MprViewport.jsx:27/256-257` 改用 `TOOLS_EVENTS` | ✅ 已修复 |
| — | MIP blend mode | `MprViewport.jsx:292` | ✅ 已修复 |

**但 B1 的修复引入了新问题（见 B7），B2 的修复只做了一半——假 PDF 仍在（见 B2）。**

### B7 — 导出端点幂等重放语义被破坏，同 key 重放返回 409 而非同一资源

**问题**
`deps.py:80-82` 的 `Response` 透传分支在 `return` 时**早于 `:88` 的 `idem.remember(...)`**。
后果是二进制响应完全不进幂等记录：同 key 重放时 `idem.lookup` 查不到记录 → 重新执行 `func(...)`
→ 撞上 `services/plans.py:163-169` 的导出状态校验（`只有 saved 态 Plan 可导出`）
→ **第二次返回 409，而不是返回首次的同一份文件**。

这违反 SPEC AC-19：「While 携带同一 `Idempotency-Key` 重放，系统**必须**返回同一资源」。

**证据**（实测，同一 `Idempotency-Key` 连发两次）
```
第 1 次 -> 200  ct: application/pdf  | 前 8 字节: b'PLAN 6f7' | 是 PDF? False
第 2 次 -> 409  ct: application/json  | 前 8 字节: b'{"error"'
  错误体: {"code":"STATE_CONFLICT",
           "message":"只有 saved 态 Plan 可导出，当前为 exported",
           "details":{"current_state":"exported","allowed_states":["saved"]}}
```
对照组（JSON 响应端点，同 key 重放）：第1次 201、第2次 201、`sr_uid` 相同 → 幂等语义正常。
说明缺陷只发生在二进制响应分支。

**期望**
二选一：
1. `idem.remember` 支持二进制快照（存 body bytes + content-type + content-disposition，
   重放时重建 `Response`）——推荐，符合契约 §5.4「同 key + 同 payload 重入返回同一响应」
2. 明确把导出端点移出幂等装饰器，并同步修改 SPEC AC-19 的适用范围 +
   在契约里写明「导出为只读语义，可安全重复调用」

无论选哪条，**不能让同一 key 重放把资源状态搞成 409**——前端网络重试极容易触发。

---

### B1 — 规划功能在 UI 上完全走不通，点"新建规划"必得 422〔v1.0 记录，v1.1 已修复〕

**问题**
`CaseWorkspace.jsx` 的"基于已确认分割新建规划"按钮，构造的 payload 缺 `entry_point` /
`target_point`，而服务端 `PlanCreate` 把这两个字段声明为必填，点击必然 422。
更严重的是：即使绕过 422，界面上也**没有任何可以放置或调整入路点 / 目标点的控件**——
PRD FR-05 的"标记 / 验证 / 实时刷新"三条一条都没实现。

**证据**
- `web/src/pages/CaseWorkspace.jsx:463-478`
  `api.createPlan(caseId, { base_seg_uid, base_seg_version })` —— 只传两个字段
- `server/app/api/v1/schemas.py:128-133`
  `class PlanCreate: base_seg_uid: str; base_seg_version: int; entry_point: dict[str, float]; target_point: dict[str, float]`
  —— 后两个无默认值，必填
- 实测（端口 8099，attending 身份，已确认态 SEG）：

  ```
  POST /api/v1/cases/{cid}/plans
  {"base_seg_uid":"2.25...","base_seg_version":1}
  → HTTP 422
  {"error":{"code":"VALIDATION_ERROR","message":"请求体字段校验失败",
    "details":{"errors":[
      {"type":"missing","loc":["body","entry_point"],"msg":"Field required"},
      {"type":"missing","loc":["body","target_point"],"msg":"Field required"}]}}}
  ```
- 对照组：补上 `entry_point` / `target_point` 后同请求返回 `201`，
  `safe_distance=50.0, angle=0.0, risk_level=low`
- 服务端日志同步留痕：`POST /api/v1/cases/{cid}/plans 422 Unprocessable Content`

**期望**
二选一，或两者都做：
1. 前端补入路点拾取交互（在影像上点两个点，取 Cornerstone 世界坐标送入）
2. `PlanCreate` 给 `entry_point` / `target_point` 默认值（如 `{0,0,0}`），允许后续 `PUT` 调整

无论选哪条，都必须补一个可放置入路点 / 目标点的界面，否则 FR-05 依然为空。

---

### B2 — 报告导出拿不到 PDF，Plan 导出的文件是文本却声明 application/pdf

> **v1.1 状态更新**：`deps.py:80-82` 的 `Response` 透传已修复，「JSON 壳」问题消失。
> 实测现在返回 `ct=application/pdf` + 正确的 `Content-Disposition: attachment; filename="..._v1.pdf"`。
> **但「假 PDF」仍在**：文件内容仍是纯文本，首 8 字节 `b'PLAN 6f7'`，`%PDF` = False。
> 另外导出幂等语义已被这个修复破坏 → 见 B7。

**问题**
三个问题叠加：

1. **PDF 从未实现**。`plans.py` 里 `format=pdf` 分支只是把同一段纯文本
   （`"PLAN xxx v1\nbase_seg: ...\nentry: ...\n"`）套上 `application/pdf` 的 media_type，
   文件内容里连 `%PDF` magic 都没有。仓库依赖里没有任何 PDF 生成库。
2. **导出端点被幂等装饰器破坏**。`POST /plans/{id}:export` 返回 `Response` 对象，
   但 `@idempotent` 装饰器（`deps.py:76-83`）对返回值做了 `jsonable_encoder`，
   于是 `Response` 被序列化成 JSON 壳下发。实测拿到的响应是：

   ```json
   {"status_code":200,"media_type":"application/pdf","background":null,
    "body":"PLAN 388d20a4-... v1\nbase_seg: 1.2.840.100099... \n...",
    "raw_headers":[["content-disposition","attachment; filename=\"..._v1.pdf\""],
                   ["content-length","297"],["content-type","application/pdf"]]}
   ```

   `Content-Disposition` 声明是 `.pdf` 附件，实际下载到的文件是一段 JSON 字符串。
   **这是 `@idempotent` 装饰器的通用缺陷，任何返回 `Response` 的写端点都会中招。**
3. **前端零导出入口**。`web/src/api/client.js` 没有 `exportPlan` 方法；
   规划表格的动作列只有"保存"，没有"导出"；报告表格没有"下载"。
   报告 SR 只能靠手敲 `GET /api/v1/reports/{id}/content` 拿（该端点本身是通的，
   实测 `content-type: application/dicom`、1232 字节、`DICM` magic 正确）。

**证据**
- `server/app/api/v1/routers/plans.py:125-141` —— `body` 是纯文本，`media` 按 format 切换
- `server/app/api/v1/deps.py:76-83` —— `body = jsonable_encoder(result)` 无条件执行
- 实测导出响应体首 4 字节 = `b'{"st'`，`%PDF` = False，`DICM` = False
- `web/src/api/client.js` 全文无 `exportPlan` / `getReportContent` / `getMeasurementContent`

**期望**
1. ~~修 `@idempotent` 对 `Response` / `StreamingResponse` 返回值的短路处理~~〔v1.1 已修复，
   但引入了 B7，需一并处理幂等记录〕→ 改为：**`idem.remember` 支持二进制快照**
2. ~~决定 PDF 是否进 MVP~~〔**v1.2 已决策**：ADR-003 裁定**不进**〕。
   落地动作改为两步（见 `docs/TASK-CARDS.md` TC-03）：
   **先让 `format=pdf` 返回 501 并给出替代路径（止血），再删掉 `format` 参数（终态）。**
   参数留在契约里，读者会照着它排期——一个永远 501 的取值等于对外声明
   "功能存在只是暂时不可用"，而事实是"决定不做"，两句话的行动含义相反。
   同时 PRD FR-06（"也可以是 PDF（用于院内流转）"）必须改为明确的 out-of-scope 表述并链接 ADR-003
3. **无论 PDF 做不做，导出文件的 MIME 必须与真实内容一致**——声明 `application/pdf` 却给纯文本，
   会让客户端 Acrobat 打不开并报"文件已损坏"，这比直接报错更难排查
4. 前端补"下载报告 SR"与"导出规划"两个按钮

---

### B3 — AI 自动分割按钮点了没反应，演示环境永远走不到"建议态 SEG"

**问题**
未配置 `ai_base_url` 时，AI 适配器只返回一个桩响应（`queue_pos: 0, estimated_sec: 30`），
**没有任何进程去调用 `/api/v1/ai-results` 回调**。任务永远停在 `running`，
`result_seg_uid` 永远为 `None`，建议态 SEG 永远不出现。
前端也没有轮询——点完按钮弹一句"已投递自动分割任务"就再无更新。

**证据**
- `server/app/adapters/ai.py:49-53`
  ```python
  # 桩：本地开发直接给出队列位置
  return {"job_id": req.job_id, "queue_pos": 0, "estimated_sec": 30}
  ```
  —— 此路径下不产生任何后续动作，`submit()` 之后没有任何调度
- 实测：
  ```
  POST /api/v1/ai/infer?study_instance_uid=1.2.840.113619.26346837952970&targets=
  → 202 {"job_id":"job-346837952970","queue_pos":0,"estimated_sec":30,"status":"running"}
  3 秒后 GET /api/v1/ai/jobs/job-346837952970
  → status=running, result_seg_uid=None
  该 case 的 segments 列表：无任何 algorithm 以 "ai:" 开头的条目
  ```
- `web/src/` 全库无 `ai/jobs` 引用，无 job 轮询代码

**期望**
桩模式下补一个本地 worker（合成一个 suggested 态 SEG 并走 `/ai-results` 回调路径落库），
让 FR-04 的"AI 输出非自动生效 → 医师确认"这条核心信任链在演示中能被走通。
同时前端加 job 状态轮询；AI 服务未配置时给出明确提示，而不是谎报成功。

> **v1.1 补充（架构师复核）**：`adapters/ai.py:52-54` 的桩分支与
> `routers/ai.py:79-81` 的 `job.status = AiJobStatus.RUNNING` 组合后，
> job **没有任何超时收敛路径**——既不会因超时转 failed，也不会因桩就绪转 done。
> 这是「静默失效」的典型形态：接口返回 202 成功、UI 弹成功提示、任务永远 running。

---

### B4 — 分割是纯元数据空壳，基于它的风险标签是凭空来的

**问题**
`build_seg_header` 只写 SEG 的元数据骨架，**从不写 `PixelData` /
`PerFrameFunctionalGroupsSequence` / `Rows` / `Columns`**，`referenced_series` 传的也是空列表。
前端"新建分割"因此只是新建了一条**空记录**，没有画线、没有涂沫、不能裁切、不能平滑、不能布尔。

后果比"功能没做"更严重：服务端 `plans.py` 的 `safe_distance` 就是
`distance(entry_point, target_point)`（两个坐标点的欧氏距离），
`risk_from_distance` 拿这个距离和 10mm / 5mm 阈值比大小。
**这个距离与任何分割结构、任何血管、任何关键器官都无关。**
实测 `(0,0,0) → (30,40,0)` 得到 `safe_distance=50.0, risk_level=low`，
UI 会把这个 `low` 用绿色标签展示给医师。

**证据**
- `server/app/dicom/seg.py:42-147` —— `build_seg_header` 只写元数据
- `server/app/services/segments.py:265-276` —— `referenced_series=[]`，无像素内容
- `server/app/services/plans.py:115-126, 148-156` —— 距离/角度/风险的算法链路
- 实测解剖 `server/.dicomstore/*.dcm` 中 58 个 SEG 实例：

  ```
  PixelData 存在: False
  PerFrameFunctionalGroupsSequence: False
  Rows / Columns: None None
  ReferencedSeriesSequence: 0
  文件大小: 401 字节
  ```
- 前端：分割面板只有"结构名称"输入框 + "新建分割" + "自动分割"两个按钮，
  无任何分割绘制区；cornerstone 4.22 自带的 `SculptorTool` /
  `LivewireContourSegmentationTool` / `RegionSegmentPlusTool` / `RectangleROITool` 全部未引入

**期望**
1. 至少让手动分割能产出真实 mask（cornerstone 已装好相关工具，前端接入 + SEG 序列化即可）
2. **在 SEG 为空壳时必须禁止 Plan 计算并给出明确错误**，不得展示绿色 `low` 风险标签
   —— 这是一个会让临床医师误判的直接风险

---

### B5 — 测量 SR 丢失全部空间定位信息，"与 3D Slicer 口径一致"无法验证

**问题**
从影像上量出来的长度，前端只送了 `{type, value, unit}` 三个字段：
没有测量点坐标、没有像素间距、没有切片引用、`series_instance_uid` 还被塞错了位置。
服务端照单全收，生成了一份**只有数值、没有坐标**的 SR。

**证据**
- `web/src/components/MprViewport.jsx:305-309`
  `onMeasureReady?.({ type: 'length', value: draft.value, unit: draft.unit })` —— 无坐标
- `web/src/pages/CaseWorkspace.jsx:150-158`
  `coord_ref: { ..., row_spacing: m.spacing?.[0] ?? null, ... }`
  —— `m` 里根本没有 `spacing` 字段，恒为 `null`；
  且 `series_instance_uid` 被塞在 `coord_ref` 内部，
  而 `MeasurementsCreate`（`schemas.py:58-66`）有顶层 `series_instance_uid` 字段，前端没传
- `server/app/dicom/sr.py:140-143` —— `coord_ref.get("sop_instance_uid")` 为空，
  `_referenced_sop()` 返回空列表
- 实测 SR 解剖：

  ```
  NumericValue: 34.7 mm
  ReferencedSOPSequence 长度: 0        <-- 无法定位到具体切片
  有无 GraphicData / 测量点坐标: False
  DerivationDescription: None
  Measurement.series_instance_uid: None
  SR.SeriesInstanceUID: 1.2.840.100099.4.281569881695415467795362048536412376909.1
  （真实 CT 序列 UID 不一致 —— SR 落进了合成 Series，PACS 侧无法把测量归到原检查序列）
  ```

**期望**
从 `LengthTool` 的 annotation 取出 world 坐标与 `sop_instance_uid`，
一并写入 `coord_ref` 与顶层 `series_instance_uid`；
SR 侧写入 `ReferencedSOPSequence` 与 `GraphicData`。
否则 PRD FR-02"口径必须显示并与 3D Slicer 一致"、§6.5 跨端一致性两条都是空话。

---

### B6 — 前端搜索是装饰品，也没有创建病例 / 上传 DICOM 的入口

**问题**
搜索框没有绑定输入值、也没有把查询词传给后端——按 Enter 只会重新拉一次前 50 条，
结果永远不变。后端 `GET /cases` 是支持 `query` / `source` / `modality` 三个参数的，前端没接。
同时前端**没有任何创建病例或上传 DICOM 的入口**，`api.createCase` 定义了但零调用点。
演示时列表内容完全取决于 `seed_demo.py` 造的那一条合成 CT。

**证据**
- `web/src/App.jsx:128-134` —— `<input className="grow" placeholder="按患者名 / ID / 标题搜索（FR-01）" />`
  无 `value`、无 `onChange`，`onKeyDown` 里只有 `setReloadKey(n => n+1)`
- `web/src/App.jsx:42-50` —— `api.listCases({ size: 50 })`，从不传 `query` / `source` / `modality`
- `server/app/api/v1/routers/cases.py:34-36` —— 后端三个 query 参数都支持
- `web/src/api/client.js:64` —— `createCase` 已定义；`web/src/` 全库无调用点
- `web/src/App.jsx:65` 空态文案："用 PACS 检索或上传 DICOM 后自动创建" —— 两者都不存在
- 服务端 `POST /dicomweb/studies`（STOW-RS）已实现（`server/app/api/dicomweb.py:259`），前端未调用

**期望**
接上搜索参数；补一个"新建病例 / 上传 DICOM"入口（服务端 STOW-RS 已就绪）。
PRD FR-01 的上传入口、PACS 实时同步、来源徽标三条目前全无。

---

## 2. 建议项（advisory）

| 编号 | 建议 | 理由 |
|---|---|---|
| A1 | 共享链接落地页 `/s/:shareId` 不存在；评论功能后端有、前端无 | `serializers.py:158` 返回 `url = f"{base_url}/s/{share_id}"`，但 `App.jsx` 只有 `/` 与 `/cases/:caseId` 两条路由。FR-07"接收者首次打开链接必须登录看到内容"链路在演示时是断的——把链接发给同事，同事打开落到首页。评论 API 已实现（`shares.py:99-113`），前端零调用。 |
| A2 | 在 `docs/` 下建 `OPEN-DECISIONS.md`，按知识库 `open-decisions-register` 规范登记 Q2 / Q3 | `SPEC.md:207` 引用了"OPEN-DECISIONS"但该文件不存在。PRD Q2 明写"此结论需 RA 书面意见"=未决，SPEC §3 却直接列为"明确不做"，中间没有 RA 意见的落地证据。可行性报告 §4.1 把 R2（器械属性判定）定为最高风险。规范要求：只追加 + 就地关闭 + 每条写明 `Resolves when`。 |
| A3 | SPEC §7 页面清单的组件名与仓库实际文件不符 | `SPEC.md:143-144` 写 `StackViewport`（MPR 三视口）与 `MeasureOverlay`；`web/src/components/` 下只有 `MprViewport.jsx`。`SPEC.md:214` 的"内嵌已知坑"也指向不存在的 `StackViewport.jsx`。SPEC 是锁定契约，组件名对不上会让架构师按不存在的文件规划工作。建议改为 `MprViewport.jsx` 并注明"测量层当前内嵌在 measure-bar，非独立组件"。 |
| A4 | PRD 明确标注"不得直接进入工程实现"的 TBD 标记，多处已进入实现 | PRD 结尾："所有 TBD 与 Assumption, TBD 标记的内容代表未决或推断状态，不得直接进入工程实现"。但 `plans.py:25-26` 的 `SAFE_DISTANCE_LOW_MM=10` / `MEDIUM_MM=5` 注释标着 `[Assumption, TBD]`，README:152 亦然——**这个未决阈值已经在驱动面向医师的风险标签**（见 B4）。要么由临床顾问给值，要么在 UI 上把该标签标注为"实验性阈值"。 |
| A5 | 补一份"用户视角可演示能力清单"进 README §5 | README「已知边界」只列了 5 条实现边界（VR 未实现 / DICOMweb 未代理 / HL7 / Plan PDF / 风险阈值），未覆盖 AI 桩不回调、搜索未接线、SEG 无像素、角度面积只能手填、MinIP 与放大镜与多窗口缺失、截图录像缺失、评论未接、共享落地页缺失共 8 项。这些是对外演示时最容易被追问的地方。 |
| A6 | PRD P0 → SPEC P1 的降级需要逐条留痕 | PRD FR-03 标 P0，含"实时容积重建""旋转 / 缩放 / 透明度""多模态融合""导出截图 PNG 或短序列录像 MP4"；SPEC.md:30 只把"三维 VR 体绘制视口"降为 P1，其余条目在 SPEC 里没有逐条对应。是范围决策还是遗漏，需要写明，否则后续无法判断 FR-03 剩余部分是否已交付。 |

---

## 3. 三段对照表：选型需求 → PRD → 当前可演示性

图例：`可演示` / `部分` / `无入口` / `明确排除`

### 一、数据接入与集成

| 选型需求 | 开源方案（选型文档） | PRD 对应 | 当前可演示性 | 判定依据 |
|---|---|---|---|---|
| DICOM 3.0 导入 | Cornerstone3D | FR-01 | **可演示** | `MprViewport.jsx` 真实 Cornerstone3D 4.22 容积重建，README:147 记录实测 128 层 / 512×512 / 0.7mm |
| PACS 调阅 | DCM4CHEE-arc-light | FR-01 | **无入口** | 全库无 `dcm4chee`；`infra/docker-compose.yml` 只编排 Orthanc；未配 `DICOMWEB_BASE_URL` 时退化为内置文件存储（README:102） |
| HL7 / FHIR | DCM4CHEE 内置 + HAPI | Q4 | **明确排除** | SPEC §3「明确不做」表；README §五.3 |
| HIS / EMR / PACS 对接 | Orthanc REST API | FR-01 | **部分** | Orthanc 已编排且 STOW-RS 已实现（`dicomweb.py:259`），但前端无上传入口、无新建 Case 入口 |
| RESTful API / 预留 AI 接口 | Orthanc REST + MONAI Deploy | FR-04 | **有 API 无效果** | `/ai/infer` 返回 202 但桩不回调（见 B3） |

### 二、二维阅片与三维重建

| 选型需求 | 开源方案 | PRD 对应 | 当前可演示性 | 判定依据 |
|---|---|---|---|---|
| 窗宽窗位 / MPR / MIP / MinIP | Cornerstone3D | FR-02 | **可演示**〔v1.3 更新〕 | 窗宽窗位三档预设 ✓、MPR 三视口 ✓、MIP ✓、**MinIP ✓**（`MprViewport.jsx:101-107`，合成 / MIP / MinIP 三档） |
| 长度 / 角度 / 面积测量 | `@cornerstonejs/tools` | FR-02 | **可演示**〔v1.3 撤回原指控〕 | **v1.0 我判"角度 / 面积只能手填"，这个判断已作废**：`dicom.js:57-59` 已注册 `AngleTool` / `EllipticalROITool` / `RectangleROITool`，`MprViewport.jsx:88-90` 有"角度 / 面积·椭圆 / 面积·矩形"三种影像内交互模式，且按坐标契约 §4.1 产出 `POLYLINE`/9 与 `POINT`/3。**体积仍无闭环**（见 §9.1 第 12 项） |
| 缩放 / 平移 / 放大镜 / 多窗口 | OHIF 内置 | FR-02 | **可演示**〔v1.3 更新〕 | 缩放 ✓、平移 ✓、**放大镜 ✓**（`MagnifyTool` 已注册 + `MprViewport.jsx:97` 模式）、**多窗口布局 ✓**（`:110-114` 三视图 / 轴位单屏 / 轴位+冠位） |
| CT/MRI 自动三维重建 VR | `VOLUME_3D` / 3D Slicer | FR-03 | **可演示**〔v1.3 撤回"无入口"〕 | `VrViewport.jsx`（595 行）+ `CaseWorkspace.jsx:309` 已挂载，2D/3D 用 CSS 隐藏而非卸载（保住体数据缓存，注释说明了原因）、独立 VR 工具组（TrackballRotate/Pan/Zoom）、预设 + 不透明度 + SSR 模式。**注意：SPEC §2 仍写"Web 端 `VOLUME_3D` 未在 MVP 接入"，与实现不符，需订正** |
| 旋转 / 缩放 / 透明度 / 多模态融合 | Cornerstone + OHIF VTK | FR-03 | **部分**〔v1.3 更新〕 | 旋转 ✓（TrackballRotate）、缩放 ✓、透明度 ✓（`VrViewport.jsx` 的 `scaleOpacity`）；**多模态融合仍无**——`web/src/` 全库无 `fusion` / 多模态叠加，无 VTK |

### 三、分割编辑与手术规划

| 选型需求 | 开源方案 | PRD 对应 | 当前可演示性 | 判定依据 |
|---|---|---|---|---|
| 自动分割（器官/血管/骨骼） | TotalSegmentator / MONAI Label | FR-04 | **无结果** | 按钮可点、返回 202、任务永远 running（见 B3） |
| 半自动 / 手动分割工具 | ITK-SNAP / Slicer SegmentEditor | FR-04 | **无编辑交互** | 前端只有一个文本框 + 按钮；产出的 SEG 是 401 字节空壳（见 B4）。cornerstone 自带 `SculptorTool` / `LivewireContourSegmentationTool` / `RegionSegmentPlusTool` 全未引入 |
| 裁切 / 平滑 / 布尔编辑 | Slicer SegmentEditor | FR-04 | **无入口** | 3D Slicer SegmentEditor 不在 Web 端 |
| 三带入路模拟 | Slicer + SlicerIGT | FR-05 | **UI 不可创建 Plan** | 见 B1（422）；且界面无入路点放置控件 |
| 距离 / 角度 / 体积测量 | Slicer Markups / OHIF | FR-05 | **距离与角度可算，体积不可达**〔v1.3 更新〕 | `safe_distance` = 两点欧氏距离，与分割结构无关（语义问题仍在，见 B4/Q-SAFE-DISTANCE）；`compute_volume` 已实现且写法诚实（`seg.py:276-312`），**但没有任何路径能产出带像素的 SEG，恒返回 `no_pixel_data`**；测量面板里的"体积"仍是手填数字（`CaseWorkspace.jsx:362`） |
| 二三维联动定位 | Slicer / OHIF LinkedScroll | FR-03 | **部分**〔v1.3 更新〕 | 三视口 crosshair 联动 ✓；**3D 视口已存在但为独立页签切换，2D↔3D 无共享 crosshair / 无点选定位** |

### 四、报告输出与远程扩展

| 选型需求 | 开源方案 | PRD 对应 | 当前可演示性 | 判定依据 |
|---|---|---|---|---|
| DICOM 报告生成 | 自研 SR 写出（`app/dicom/sr.py`） | FR-06 | **可演示，但有缺口** | 报告 SR 可下载 ✓（`GET /reports/{id}/content`，实测 `application/dicom`、1232 字节、`DICM` 正确）；**但前端无下载按钮**，只能手敲 API 取 |
| ~~PDF / DICOM 报告生成~~（原第 46 行，已拆） | ~~OHIF dicom-pdf / Slicer~~ | FR-06 | **有意不做** | **ADR-003 裁定 PDF 不进 MVP。** 拆成两格的原因见下方说明 |

> **原第 46 行为什么必须拆成两格**
> 选型结论把「PDF / DICOM 报告生成」写在同一格、同打一个 ✅，
> 但这两件事的处置完全相反：DICOM SR 已实现，PDF 决定不做。
> 合在一格里，读者无法判断那个 ✅ 指的是哪一个。
>
> 另需纠正选型原文引用的一句方案：「OHIF dicom-pdf 扩展」**从未真正依赖过**——
> 全仓 `OHIF` 只出现在一处 CSS 注释里。选型文档把它列为可选项属未核实，
> 不应作为"PDF 有现成方案、成本很低"的依据。
| 二三维截图 | Cornerstone viewport API | FR-03 | **无入口** | `web/src/` 无 `toDataURL` / `screenshot` / `capture` |
| 模型旋转录像 | Slicer ScreenCapture | FR-03 | **无入口** | `web/src/` 无 `MediaRecorder` |
| 三维模型远程共享 / 标注 | OHIF measurement-tracking + 自建 WebSocket | FR-07 | **部分** | 异步共享完整 ✓（访问码只返回一次 ✓、撤销后 410 GONE ✓、8 位码 ✓）；**评论 API 有前端无**；**共享落地页无路由**（见 A1） |
| 预留远程协作接口 | Orthanc REST / 插件机制 | FR-07 | **有 API 无 UI** | `shares.py:99-113` 已实现评论端点，前端零调用 |

---

## 4. PRD 未决项状态表

| 问题 | 原文标记 | 当前状态 | 依据 |
|---|---|---|---|
| **Q1** 实时协作会诊的交付节奏 | PRD §07 Q1："当前方案：P0 不做" | **已决策（明确排除）** | PRD §1.3「不打算做的事」末条 + PRD §3.2 P1 列表 + SPEC §3「明确不做」首行「多方实时音视频会诊 / 光标同步 \| PRD Q1 明确 P0 不做 \| v2.0，需独立冲突合并模块」。三处一致，无漂移 |
| **Q2** 临床用途的合规定性 | PRD §07 Q2："当前方案：先按科研 / 教学用途立项……此结论需 RA 书面意见" | **仍开放，但 SPEC 口径已漂移** | PRD 说"需 RA 书面意见"=未决；SPEC §3 却直接列入「明确不做」表「医疗器械合规定性（RA 质量体系）\| PRD Q2：先按科研/教学立项 \| 临床路径独立推进」，中间没有 RA 意见落地证据。`docs/` 下无 OPEN-DECISIONS 文件（SPEC:207 引用了它）。可行性报告 §4.1 把 R2 定为最高风险、§4.2 要求"RA 书面意见"。**且 PRD §3.2 Scope Gate 明写"首期是否包含分割/规划的完整路径，取决于 RA 对临床用途的判定"——若判定为临床诊断用途，B4 / B5 两个缺口会直接升为 P0 必选集** |
| **Q3** 自动分割 AI 模型选型 | PRD §07 Q3："默认 TotalSegmentator……但具体模型、权重许可、是否允许院内数据微调尚未决定" | **仍开放 + 已被 Spec 部分排除** | PRD §7 Q3；SPEC §3「TotalSegmentator 真机推理 \| 需 GPU + 模型权重许可（PRD Q3） \| 模型选型确认后」。**但 PRD 说"模型选型直接影响 FR-04 的分割覆盖范围与 P0 范围"——现在 P0 范围实际不含真实推理，AI 建议态路径在演示环境永远走不到终点（B3）**。另有 `app/api/v1/routers/ai.py:36-37` 把 `model` 默认写成 `totalsegmentator/multi-structure`、`model_version="2.0.0"`，等于把一个 PRD 声明"尚未决定"的模型写成了硬编码默认值 |
| **Q4** 与院内 HIS / EMR / RIS 的数据边界 | PRD §07 Q4："当前方案：只读 PACS 调阅 + 本地元数据存储" | **已被 Spec 明确排除** | PRD §7 Q4；SPEC §3「HL7 v2 / FHIR 对接 \| 契约 §2.3；缺院内接口信息 \| 拿到院内接口信息后」；README §五.3 第 3 条。契约 §2.3 的事件矩阵已定义，属"方案已定、待院内信息" |
| （PRD 其它 Assumption 标记）风险阈值 `SAFE_DISTANCE_LOW_MM=10` / `MEDIUM_MM=5` | README:152「工程初始假设，见 `plans.py` 注释标记的 `[Assumption, TBD]`」 | **已进入实现且驱动面向医师的风险标签** | `server/app/services/plans.py:25-26` 注释明写 `[Assumption, TBD]`，但 `risk_from_distance()` 已被 `create()` / `recalculate()` 调用并返回 `low/medium/high`。见 A4 |

---

## 5. 口径冲突清单（选型文档 vs PRD / SPEC）

### 冲突 1 — "2–3 周可出可演示原型"

| 项 | 内容 |
|---|---|
| 选型文档 | 第 89 行："约 **2–3 周**可出可演示原型" |
| 可行性报告 | §5.1 S1："验证五项关键假设（2–3 周）……产出可演示原型（**DICOM 导入、Web 阅片、自动分割、PDF 报告**）" |
| 实际实现 | 四件套只成立两件：**DICOM 导入 ✓、Web 阅片 ✓**；**自动分割 ✗**（桩不回调，B3）、**PDF 报告 ✗**（无 PDF 生成，导出还拿不到文件，B2） |
| **裁决** | **互补，但选型文档的表述会被误读。** 2–3 周指的是"搭建选型结论所描述的技术组合"的时间，不等于交付 PRD 全量 FR——PRD FR-04/FR-06 的交付物本来就重得多。可行性报告自己也把它限定为"POC 必须验证的五项假设"。**建议**：在选型结论第 89 行后补一句"此 2–3 周指 POC 搭建，不含 PRD FR-04 的真实分割与 FR-06 的 PDF 交付"；同时把 S1 出口标准按实际能力改写，避免用同一句"可演示原型"描述两件不同的事 |

### 冲突 2 — DCM4CHEE 是企业级 PACS

| 项 | 内容 |
|---|---|
| 选型文档 | 第 10 行标"PACS 调阅 → DCM4CHEE-arc-light → ✅ 企业级生产可用"；第 64 行列为高优先级项目 |
| 实际实现 | 全库（`.py` / `.js` / `.jsx` / `.yml` / `.json`）**无 `dcm4chee` 字样**；`infra/docker-compose.yml` 只编排 Orthanc + Postgres + MONAI；未配 `DICOMWEB_BASE_URL` 时退化为 `server/.dicomstore/` 内置文件存储（README:102） |
| **裁决** | **不构成实质冲突，是"方案池"与"实现选择"的分层。** 选型文档列的是可选方案，SPEC §3 明确"不替换院级 PACS（只读对接）"，infra 选更轻的 Orthanc 作本地 PACS 是合理取舍。**但选型文档给 DCM4CHEE 打"✅ 企业级生产可用"这个标记，会让人以为已经接入。** 建议在选型文档补一行"本 MVP 未接入任何院级 PACS，demo 环境使用内置文件存储 / 本地 Orthanc"，把 ✅ 的含义限定为"该方案成熟可用"而非"本项目已落地" |

### 冲突 3 — "唯一需要二开的是三维模型远程协作标注" vs SPEC 明确不做实时会诊

| 项 | 内容 |
|---|---|
| 选型文档 | 第 73 行："**唯一需要二开的功能**是'三维模型远程协作标注/实时交互'——OHIF 和 3D Slicer 有基础标注能力，但完整的多人实时协作……需要基于现有 REST API + WebSocket 自建同步层" |
| PRD | Q1：实时协作 P1，P0 不做；FR-07 只做异步共享 |
| SPEC | §3 首行："多方实时音视频会诊 / 光标同步 \| PRD Q1 明确 P0 不做 \| v2.0，需独立冲突合并模块" |
| **裁决** | **互补，不是冲突——但选型文档的措辞有实质误导。** 理由：选型文档那句话把两件事合成了"标注/实时交互"：其中"标注 + 意见"由异步共享覆盖，且 MVP 确实实现了（FR-07 访问码 / 只读可评论 / 撤销 / 审计）；而"多人实时交互"才需要自建 WebSocket 同步层。PRD Q1 把它整体推到 P1，SPEC 排除的"多方实时音视频会诊"指向的正是同一个最贵的模块，两者指向一致。**真正的问题是"唯一"这个断言**：MVP 实际上在 FR-04 分割编辑（裁切 / 平滑 / 布尔 / 手绘）上同样需要二开，只是 Web 端没做、因而没进 SPEC 范围。**建议**把选型结论第六节改为"**两个需要二开的模块**：①多人实时协作同步层（推 v2.0，含冲突合并）；②Web 端分割编辑交互（裁切/平滑/布尔/手绘，推 v1.1，当前 Web 端未实现）" |

### 冲突 4 — "长度 / 角度 / 面积测量 → `@cornerstonejs/tools`（LengthTool, AngleTool, AreaTool）✅"

| 项 | 内容 |
|---|---|
| 选型文档 | 第 22 行 |
| 实际 | `@cornerstonejs/tools` 4.22 **不存在 `AreaTool`**（实测 `tools/annotation/` 下只有 `AngleTool` / `LengthTool`，面积类由 `EllipticalROITool` / `RectangleROITool` / `PlanarFreehandROITool` / `SplineROITool` / `LivewireContourROITool` 承担）；且前端只引入了 `LengthTool`（`MprViewport.jsx:21`），`AngleTool` 未引入，ROI 工具族全未引入 |
| **裁决** | **选型文档的技术断言在当前依赖版本下不成立，且落地只完成 1/3。** 建议改写为"LengthTool / AngleTool / ROI 工具族（`EllipticalROITool`、`RectangleROITool`、`PlanarFreehandROITool` 等）"，并在选型文档补一行"实际接入情况：仅 LengthTool"。同时这条也是 B5 与"角度/面积只能手填"的根因——工具本就在 node_modules 里，缺的是引入与坐标回传 |

---

## 6. 用户视角的可演示能力清单

> 场景设定：一个真实放射科诊断医师 / 外科术前规划医师，第一次打开这个系统。

### 能完整走通的六步

| 步骤 | 动作 | 实际发生什么 |
|---|---|---|
| 1 | 打开系统 | 左侧列表出现**预置的合成 CT 病例**（前提：先跑过 `seed_demo.py`）。除此之外无其它病例，也建不了新的（B6） |
| 2 | 点进病例 → 选序列 | 三视口 MPR **真实渲染**：轴位 / 矢状位 / 冠状位，十字线联动，滚轮翻层，右键缩放、中键平移，软组织 / 肺窗 / 骨窗三档预设，MIP 开关。**这是整个系统质量最高的部分** |
| 3 | 点"测量"→ 在轴位拖一条线 | 读出毫米值 → 点"写入 DICOM SR" → 侧栏出现一条 draft 测量 → 点"保存" → 变 saved。**数值是对的**（Cornerstone 按体素间距换算），但 SR 里没有坐标（B5） |
| 4 | 分割面板填个结构名 → "新建分割" | 出现一条 draft SEG → 点"确认" → 变 confirmed。**但这条 SEG 是 401 字节空壳，影像上不会出现任何高亮区域**（B4） |
| 5 | 报告面板填模板 / 临床发现 / 结论 → "生成报告草稿" | 自动带上 saved 测量 + confirmed 分割 → 列表出现 draft → "提交"（缺必填会 422 并列出失效项）→ submitted → "签收" → locked。**SR 确实是合规 DICOM**（1232 字节，DICM 正确） |
| 6 | 共享面板 → "创建共享链接" | 弹窗给出 8 位访问码 + 链接 → 列表可撤销 → 撤销后访问返回 410 GONE。**但链接发给同事打不开**（无 `/s/` 路由，A1） |

### 会卡住的九处

> v1.1 更新：卡 D（规划 422）已由并发修复消解，卡点从 9 处减为 8 处。
> 但卡 E 随之暴露得更清楚——修好 422 之后，医师能建规划了，
> 却发现界面上依然无法调整入路点，且风险绿标依然凭空。

| 编号 | 卡在哪一步 | 医师会看到什么 | 根因 |
|---|---|---|---|
| 卡 A | ③ 之后 | 想回看刚才那条 34.7mm 的线在哪儿——回系统找不到，3D Slicer 里也定位不回原图 | B5：SR 无坐标、无像素间距、SeriesInstanceUID 是合成值 |
| 卡 B | ④ 之后 | 影像上没有任何高亮，"确认"了这个分割之后系统就允许拿它做规划了 | B4：SEG 无 `PixelData` |
| 卡 C | ④ 之后点"自动分割" | 按钮提示"已投递自动分割任务"，然后**什么都不会发生**，segments 列表不新增任何条目，任务永远 running | B3：桩不回调 + 前端无轮询 + 无超时收敛 |
| 卡 D | ~~④ 之后点"基于已确认分割新建规划"~~ | ~~直接 422~~ **〔v1.1 已修复〕** 规划能建了，但界面仍无法调整入路点位置 | B1 已修；卡点转移至卡 E |
| 卡 E | ⑤ 之后 | 规划创建了，界面上**没有任何地方能放置或调整入路点 / 目标点**；`safe_distance` 和 `angle` 是纯坐标数学；风险标签可能显示绿色 `low`，而这个 `low` 与真实解剖无关 | B4：距离算法与分割结构无关 |
| 卡 F | 想把报告拿走 | 没有下载按钮。报告 SR 只能手敲 API 拿；规划导出按钮不存在，且导出内容是纯文本却声明 `application/pdf`，客户端打开会报"文件已损坏" | B2：无导出入口 + 假 PDF |
| 卡 G | 想把链接发给同事 | **〔v1.3 部分解除〕** 同事能打开 `/s/{share_id}` 了（`SharedCase.jsx` 已落）。**但共享页里没法评论**——`client.js:100` 有 `listComments`，`SharedCase.jsx` 零调用 | 残余：评论前端 |
| 卡 H | 想找另一例病人 | 搜索框输入什么结果都一样（`App.jsx:133-138` 无 `value` 绑定，`listCases({size:50})` 无查询参数）；也不能新建病例、不能上传 DICOM（`createCase` 零调用，`imports.py` 只有 API） | B6 |
| 卡 I | 想看三维 | **〔v1.3 解除〕** 现在有真 VR 视口：`VrViewport.jsx` + 2D/3D 切换 + 预设 + 不透明度 + SSR 模式 + 鼠标旋转。**仍缺**：多模态融合、二三维共享 crosshair、截图、录像 | 残余：融合 / 联动 / 截图 / 录像 |
| 卡 J | 想量病灶体积 | 测量面板的"体积"是个**数字输入框**——要用户自己先知道体积是多少才能填。服务端 `compute_volume` 写好了，但它要求 SEG 有像素，而 SEG 从来不带像素，所以恒返回 `no_pixel_data`。**能力写着，通路不存在** | §9.1 第 12 项 |

### 一句话交底

**现在能说服放射科医师的，比 v1.0 多了不少**：MPR 三视口 + 十字线联动、角度 / 面积可影像内测量、放大镜、多窗口布局、MIP / MinIP、真 VR 视口。

**仍然拿不出手的，全部集中在"信号造假"这一条线上**：报告拿不走、AI 分割点了没反应、风险等级是凭空算的、体积算不出来。

若要现场演示，建议顺序：②阅片（含角度 / 面积 / 放大镜 / MIP / MinIP）→ ③三维 VR 旋转 → ⑤报告（直接调 API 下 SR 作为交付物）→ ⑥共享（演示访问码与撤销，能打开落地页了）。
规划按钮**可以点**，但**风险等级那一栏别念**。
自动分割按钮**当前不要点**，点了就是自曝。
**体积那一路别走**——要么用户手填一个数，要么服务端返回"不可用"。

---

## 7. 建议的修复顺序（v1.1 更新）

> 完整任务卡见 `docs/TASK-CARDS.md`（7 张 Given/When/Then 验收卡）

### 7.1 第一梯队：先止血（静默失效类，误导使用者）

| 序 | 事项 | 对应 | 理由 |
|---|---|---|---|
| 1 | `idem.remember` 支持二进制快照，修导出幂等重放 | B7 | 同 key 重放 409 会让网络重试打挂导出；改动小、收益大 |
| 2 | SEG 无有效像素时 Plan 计算必须拒绝 + 隐藏风险标签 | B4 | **唯一会直接误导临床判断的点**——凭空给"低风险"绿标 |
| 3 | 导出文件 MIME 与真实内容一致（假 PDF 止血） | B2 | 声明 PDF 却给文本，客户端报"文件已损坏"，比直接报错更难排查 |
| 4 | AI 桩状态诚实化 + 超时收敛 + 前端轮询 | B3 | 接口 202 成功但任务永远 running，是典型假信号 |

### 7.2 第二梯队：补可追溯性

| 序 | 事项 | 对应 | 理由 |
|---|---|---|---|
| 5 | 测量回传 world 坐标 + `sop_instance_uid`，SR 写 `GraphicData` | B5 | 决定测量能否被复用、能否跨端比对（PRD §6.5） |
| 6 | 引入 cornerstone 分割工具，手动分割产出真 mask | B4 | 让 FR-04 有实体 |

### 7.3 第三梯队：补演示闭环

| 序 | 事项 | 对应 | 理由 |
|---|---|---|---|
| 7 | 前端补下载 / 导出按钮 | B2 | 交付物拿不到手 |
| 8 | 搜索接参数 + 上传 / 新建 Case 入口 | B6 | 决定能不能换一例真实病例 |
| 9 | 共享落地页 `/s/:shareId` + 评论前端 | A1 | 补完 FR-07 接收端 |
| 10 | 建 `docs/OPEN-DECISIONS.md` + `docs/decisions/` ADR | A2 | 把悬着的决策固化，避免再漂移 |

---

## 8. 缺陷分类：功能缺失 vs 静默失效

> 架构师建议单列。分类的价值不在标签，而在**两者修复顺序完全相反**。

| 类别 | 定义 | 本次实例 | 修复顺序 |
|---|---|---|---|
| **功能缺失** | 入口不存在，医师看到的是"这里没有这个功能" | 〔v1.3 收窄〕仅剩：二三维截图、模型旋转录像、多模态融合、创建病例 / 上传 DICOM 入口、搜索接线 | 可按 SPEC 优先级延后，功能补上即可 |
| **静默失效** | **入口存在、信号虚假、实质为空** | AI 桩（202 成功但永远 running）、假 PDF（声明 MIME 实为文本）、SEG 空壳给"未分级"（已止血）但**阈值警示不可见**、SR 丢坐标（前端已修、服务端未挡）、导出幂等重放 409、**手填体积被落成标准 DICOM 体积测量** | **必须先止血**（让错误显形），再谈补功能 |
| **功能缺失（已实现型）**〔v1.3，按架构师意见归为子类〕 | 代码已写、单测可能已过，但**没有任何产品内路径能走到成功分支** | `compute_volume`（`seg.py:276-312`）需要 `_has_pixels`，而 `build_seg_header` 永不写 `PixelData` → 恒 `no_pixel_data` | 从用户视角它就是功能缺失；**区别在验收侧**：验收标准写"函数已实现"它会通过，只有写"从 UI 操作能拿到结果"才会暴露。**处置要先修验收口径，否则做完还会再误判一次** |

**归类为什么是子类而不是并列第三类**：用户拿不到一个数，和入口不存在对他没区别。
真正的区别在验收侧——普通功能缺失直接排期做就行；这一子类要**先修验收口径**，
"顺序依赖 TC-06"只是必要条件，不是充分条件。

**这一类还有个更隐蔽的机制：优秀的降级实现会掩盖上游缺失。**
`compute_volume` 的 `unavailable` 分支写得越好（四条 reason 各有语义、不编数字），
越没人会问"那成功路径什么时候走"。方向与"工具未注册只打 warn"相反：
那是"组件静默不工作"，这是"**降级路径太完善，所以没人追问为什么不走成功路径**"。
两种都会让人误判健康度，第二种更隐蔽，因为它是**用正确性掩盖缺失**。

**机械防线（已进 SPEC §10）**：每个 `unavailable` / `unknown` reason code 都必须配一条
"它不是唯一结果"的断言 + 可见比率指标。首期 `no_pixel_data` 与 `risk_level=unknown` 都是 100%，
**这个 100% 本身就是要被盯着的数字**，因为它同时意味着"上游能力不存在"。

**为什么静默失效更危险**：功能缺失时用户会知道去找别的工作流；静默失效时用户会**基于假信号继续做临床决策**。
本次 7 项静默失效中，至少 3 项直接作用于临床判断链：

- SEG 空壳 → `risk_level=low` 绿标 → 医师以为"离血管够远"
- 测量 SR 无坐标 → 报告里引用了一个无法复核的数字
- AI 桩 202 成功 → 医师以为 AI 已经分割过了

**判定标准（供后续门禁参考）**：任何"UI 提示成功 / 状态显示成功 / 声称某格式"的行为，
其背后必须有可断言的真实产物。若产物为空或不可解析，该行为一律计为静默失效。

---

## 9. v1.1 存活缺口清单（并发修复后实时盘点）

### 9.1 存活缺口盘点（v1.2 更新）

| # | 缺口 | 位置 | 类别 | v1.2 状态 |
|---|---|---|---|---|
| 1 | Plan 导出假 PDF（声明 pdf / body 是文本） | `plans.py:125-136` | 静默失效 | **存活**，退场路径已定（ADR-003，TC-03） |
| 2 | 导出幂等语义坏（二进制不落记录，重放 409） | `deps.py:80-82` 早于 `:88` | 静默失效 | **存活**（TC-01） |
| 3 | SEG 无像素 + 风险等级凭空 | `seg.py:42-147` / `plans.py:115-126` | 静默失效 | **部分修复**：已加 `seg_has_pixels` → `RiskLevel.UNKNOWN` + `risk_basis`（`plans.py:58-86`）。**但阈值警示在非 `unknown` 等级下仍不可见**（`CaseWorkspace.jsx:629-640` 只把 `risk_basis` 当 `title`）。补像素仍缺（TC-06） |
| 4 | 测量 SR 无空间信息（无 `GraphicData`） | `sr.py:141-142` | 静默失效 | **半修**：前端已按契约 §4.1 送上 `graphic_type` / `graphic_data` / `sop_instance_uid` / `geometry=patient_lps_mm`（`MprViewport.jsx:372-390`、`CaseWorkspace.jsx:154-195`）；**服务端仍是 `coord_ref.get(...)` 自由字典**（`schemas.py:64`、`sr.py:179-186`），无校验、FOR UID 条件写入、series 仍回落 `{sr_uid}.1`。剩余工作量全在服务端（TC-05） |
| 5 | AI 桩静默 running（无超时收敛） | `ai.py:52-54` / `routers/ai.py:79-81` | 静默失效 | **存活**（TC-04） |
| 6 | 无注册自检断言（漏注册工具时无任何报错） | `web/src/` 全无 `hasTool` | 静默失效（复发风险） | **存活**（TC-06 / TC-07） |
| 7 | 前端零测试基础设施（`package.json:6-10` 无 test 脚本；无 eslint / tsconfig / playwright） | `web/package.json` | 工程门禁 | **存活**（TC-07） |
| 8 | ~~SPEC 三处引用不存在文件~~ | ~~`SPEC.md:143` / `:214` / `:207`~~ | 文档一致性 | **已关闭**：SPEC 已无 `StackViewport` / `MeasureOverlay` 引用，`docs/OPEN-DECISIONS.md` 已建且被 `SPEC.md:207` 正确指向 |
| 9 | ~~ADR 缺失~~ | ~~`docs/decisions/`~~ | 架构治理 | **已关闭**：ADR-001（PACS/Orthanc）、ADR-002（分割 out-of-scope）、ADR-003（PDF out-of-scope）已落 |
| 10 | README.md:16 链接 404（`.uploads/` 下实际文件名带 UUID 前缀） | `README.md:16` | 文档一致性 | **存活** |
| 11 | ~~共享链接无前端落地页~~ | ~~`serializers.py:158` 生成 `/s/{id}`；`App.jsx` 只有 `/` 与 `/cases/:caseId`~~ | 功能缺失 | **已关闭**〔v1.3〕：`SharedCase.jsx`（119 行）+ `App.jsx:108` 的 `/s/:shareId` 路由已落。**残余：评论前端仍无**（`client.js:100` 有 `listComments`，`SharedCase.jsx` 零调用） |
| 12 | **体积计算已实现但不可达** | `seg.py:276-312` 要求 `_has_pixels`；`seg.py:50-155` 的 `build_seg_header` 永不写 `PixelData` / `Rows` / `Columns` | **功能缺失（已实现型）**〔v1.3 新增，归类经架构师修正〕 | **存活**：函数写法本身是对的（缺 spacing / 非 BINARY / 无像素一律 `unavailable` + reason，不编数字），但恒返回 `no_pixel_data`。**任务 #6 标 completed，读者会以为体积可用了。** |
| 13 | 共享评论前端缺失 | `client.js:100` `listComments` 零调用；`SharedCase.jsx` 无评论 UI | 功能缺失 | **存活**〔v1.3 新增登记〕 |
| 14 | ~~SPEC §2 与实现不符（VR）~~ | ~~`SPEC.md:30` 仍写"Web 端 `VOLUME_3D` 未在 MVP 接入"~~ | 文档一致性 | **已关闭**〔v1.3〕：SPEC §2 已改为"已实现"并列未含项；README §五 同步修正 |
| 15 | **手填体积会被落成标准 DICOM 体积测量** | `CaseWorkspace.jsx:362` 的 `volume` 选项 + 手填数字框；`sr.py:20-25` 的 `MEASUREMENT_CODES` 中 `volume` = SNOMED `118565006` / UCUM `mm3` | 静默失效〔v1.3 新增〕 | **存活且现在就有危害**：一个没有任何测量依据的纯手打数字，会被写成一份标准 DICOM 体积测量记录，**下游无法证伪**。比风险等级更糟——风险等级至少还有 `risk_basis` 说明，体积连说明都没有。处置见 TC-08 AC-0（保留但禁用 + 说明） |

| 16 | **4xx 错误可感知但不可定位** | 前端 Banner 只渲染 `details.failed_refs`，不渲染 `details.errors`；服务端 `_clean_errors`（`main.py:141-147`）已把 `loc`/`msg`/`type` 打包好 | 静默失效（排障成本转嫁）〔v1.3 新增〕 | **已修复**〔v1.3〕：前端已补渲染，传错 `graphic_type` 会显示 `body.coord_ref.graphic_type — Input should be 'POLYLINE', ...`。**已上升为 SPEC §10 门禁条款**，因为这类问题不会进任何缺陷列表——没人会报告"错误提示不够详细"，只会觉得软件难用 |
| 17 | **展示单位被标成 UCUM 代码，产出非合规 SR** | `sr.py:140` `_code(unit or default_unit, "UCUM", ...)`；`sr.py:22` angle 默认 `"degree"`；`measurements.py:26` `DEFAULT_UNITS` 用 `°` / `mm²` / `mm³`；前端 `measurementPayload.js:97` 对角度硬编码 `'°'` | 静默失效（数据合规性）〔v1.3 新增〕 | **存活**：`°` / `mm²` / `mm³` / `degree` 都不是合法 UCUM 代码（正确为 `deg` / `mm2` / `mm3`），却被塞进 `CodingScheme="UCUM"` 的序列。**影响 4 类测量中的 3 类**（length 的 `mm` 恰好合法，所以问题被掩盖）。下游按 UCUM 解析会失败或误读 |

**净存活：10 项**（原 11 项中第 8、9、11、14 项已关闭 → 余 7 项；再新增第 12、13、15 项 → 共 10 项）。
第 16 项为**已修复**、第 17 项为**新增存活**，两者相抵后仍为 10 项。
第 16 项为**已修复**，仅作记录（它是"错误可定位"门禁条款的来源案例）。
另加 Q-RISK-FALLBACK（`plans.py:67-68` 输入缺失时返回 `MEDIUM`）这条**潜伏**缺陷，
它目前不在实际路径上，单列在 `docs/OPEN-DECISIONS.md`，不计入存活缺口。
另有 1 项因 v1.2 新发现而升格：**阈值警示可见性**——`risk_basis` 已能返回，
但前端只在 `unknown` 分支渲染成可见块，`low` / `medium` / `high` 时它退化为鼠标悬停的 `title`。
**风险等级越高越有人信，警示却越不可见，方向是反的。**

### 9.2 门禁盲区（本轮最重要的结构性发现）

`server/scripts/smoke.py:118-119` 创建规划时**手填**了 `entry_point` / `target_point`：

```python
r = post(c, f"/cases/{cid}/plans", json={
    "base_seg_uid": seg, "base_seg_version": 1,
    "entry_point": {"x": 0, "y": 0, "z": 0},
    "target_point": {"x": 30, "y": 40, "z": 0}})
```

**冒烟脚本与前端 UI 走的是两条完全不同的 payload 路径**，所以 B1 那个必现 422 的缺陷
能在"38/38 全绿"的门禁下长期存活。现有门禁测的是 **API**，不是 **UI**。

推论：本次 11 项存活缺口里，凡是能通过"改前端 payload / 改 UI 交互"暴露出来的，
当前门禁都测不到。**这解释了为什么静态契约测试全绿而实际演示走不通。**

建议（已落进任务卡 TC-07）：冒烟脚本的 payload 从前端真实行为派生，或由 E2E 直接驱动 UI。

