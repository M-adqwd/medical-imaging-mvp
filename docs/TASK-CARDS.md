# 任务卡 · 医学影像阅片与手术规划系统 MVP

> 出卡人：许清楚（产品经理）
> 复核人：mvp-dev-expert-team-architect（坐标系与单位表述把关）
> 日期：2026-10-27
> 依据：`docs/PRD-CONSISTENCY-AUDIT.md` v1.3
> 配套：`docs/COORDINATE-CONTRACT.md`（坐标系定义，架构师出）、
> `docs/decisions/ADR-003-report-pdf-out-of-scope.md`（PDF 裁决，架构师出）

### v1.3 变更（2026-10-27，团队并发完成 VR / 工具链 / 体积计算之后）

1. **新增 TC-08**（体积测量必须从 UI 走到数值）。这一类缺陷归为
   **「功能缺失（已实现型）」子类**——代码已写、单测可能已过，但没有任何产品内路径能走到成功分支。
   从用户视角它就是功能缺失；区别在**验收侧**：写成"函数已实现"会假绿，只有写"从 UI 操作能拿到结果"才暴露。
2. **TC-05 加了「v1.2 进展核对」表**：说明前端已全部落地、剩余工作量在服务端。
   不加这张表，开发会照着 v1.0 的旧描述重修一遍已经改好的前端。
3. **本文件 v1.0 时期对"功能缺失"的部分描述已过期**（角度 / 面积只能手填、放大镜无、
   多窗口无、MinIP 无、VR 无入口）——这些都已真实实现，详见
   `docs/PRD-CONSISTENCY-AUDIT.md` 的 v1.3 变更记录。**出卡时以本文件现行版为准。**
4. **TC-06 背景按现码更新**：`RectangleROITool` 已注册但**服务于面积测量**，
   分割专用工具（`SculptorTool` / `LivewireContourSegmentationTool`）仍未注册。

### v1.2 变更（2026-10-27，架构师第三轮反馈后）

1. **TC-02 阈值可见性改为按结构化字段验收**：`PlanOut.threshold_status`
   （`"provisional"` / `"clinically_confirmed"`，默认 `provisional`）。
   **禁止前端对 `risk_basis` 做子串匹配**——文案改一次就断一次，属同一类静默失效。
   `provisional` 时任何风险等级都必须常驻可见地警示。
2. **TC-05 纠正从 5 条增至 7 条**：坐标契约 14:39 修订追加 C6（`frame_of_reference_uid`
   前端**不传**，服务端按 `sop_instance_uid` 派生；"交叉校验"方案作废，因为两端读同一处、
   等于自己校验自己）与 C7（面积取**四个 handle 的质心**，不是"两 handle 中点"）。
3. **TC-07 范围扩大并标明依赖**：`PlanCreate` 点位改 `{geometry, coordinates[3]}`（方案 b）
   **依赖 TC-07 的纯函数抽取**，不是与它并列；文件拆分（`MprViewport.jsx` 734 行 /
   `CaseWorkspace.jsx` 911 行）并入本卡。另增三条门禁断言：错误可定位、运行时验证、
   不可用比率指标。
4. **TC-08 归类修正为「功能缺失（已实现型）」子类**（原写"并列第三类"，架构师指出应从
   用户视角归入功能缺失，区别在验收侧）；新增 AC-0（体积选项立即禁用，不用等 TC-06）；
   并写死**禁止用"直接构造 `seg_content`"作为通过依据**。

### v1.1 变更（2026-10-27，坐标契约首版 + ADR-003 交付后）

架构师交付坐标契约与 ADR-003 后，本文件同步了 4 项：

1. **TC-05 验收项被纠正（首轮 5 条，坐标契约 14:39 修订后追加至 7 条 C1–C7）**。作废的旧断言包括
   `GraphicDataUnits=mm`（标准里不存在该属性）、"spacing 必须是真实值"
   （spacing 不该跨线，服务端有权威来源）、"FOR UID 前端传 + 服务端交叉校验"
   （两端读同一处，等于自己校验自己），以及面积取"两 handle 中点"（应为四点质心）。
   **验收只看纠正后的版本。**
2. **TC-03 重写**：PDF 已由 ADR-003 裁定不进 MVP，本卡从"二选一"改为"两步退场"
   （先 501 止血，再删 `format` 参数）。
3. **TC-02 新增阈值可见性验收**：措辞定为「待临床确认的暂定阈值」（不用"实验性阈值"），
   且必须常驻可见 —— 核前端时发现 `risk_basis` 在非 `unknown` 等级下**只是 `title` 属性**，
   鼠标不悬停就看不到，阈值警示恰恰在最需要它的时候不可见。
4. **底部登记项更新**：PDF 与 `geometry` 命名两项已裁决，从"未决"转为"已并入卡片"；
   新登记 Q-GEOMETRY-ROLLOUT 承接存量数据迁移。

---

## 使用说明

每张卡的格式统一为 Given / When / Then，验收句必须**可观察、可断言**。

两条硬性要求：

1. **坐标系表述必须与 `docs/COORDINATE-CONTRACT.md` 一致。**
   凡涉及坐标的卡，验收句里出现的坐标系名、单位、字段名，一律照抄坐标契约原文，
   不得自行改写。出现分歧以坐标契约为准，回改本文件。
2. **每张卡都要有一条"反例"验收句。**
   正例说明功能能用，反例说明**错误会被显形**。
   本轮 7 项存活缺口中，5 项属于"静默失效"（入口在、信号假、实质空），
   只写正例的卡会漏掉真正要防的那一半。

优先级说明：
- **P0-止血**：先让错误显形，防止误导临床判断
- **P1-可追溯**：让产物可被复核、跨端可比对
- **P2-闭环**：补齐演示链路

---

## TC-01 · 导出端点幂等重放必须返回同一份文件

| 项 | 内容 |
|---|---|
| 对应缺口 | B7（架构师新发现，我已实测复现） |
| 优先级 | **P0-止血** |
| 触及位置 | `server/app/api/v1/deps.py:80-88`、`server/app/core/idem.py:80-98`、`server/app/models/idempotency.py` |
| 类别 | 静默失效（重放把资源打成 409，客户端网络重试极容易触发） |

**背景**
`deps.py:80-82` 的 `Response` 透传分支在 `return` 时早于 `:88` 的 `idem.remember(...)`。
二进制响应不进幂等记录 → 同 key 重放时 `idem.lookup` 查不到 → 重新执行 `func(...)`
→ 撞 `services/plans.py:163-169` 的导出状态校验（`只有 saved 态 Plan 可导出`）→ 第二次 409。

违反 SPEC AC-19：「While 携带同一 `Idempotency-Key` 重放，系统**必须**返回同一资源」。

**实测基线（修复前）**
```
第 1 次 -> 200  ct: application/pdf  | 前 8 字节: b'PLAN 6f7'
第 2 次 -> 409  ct: application/json
  {"code":"STATE_CONFLICT","message":"只有 saved 态 Plan 可导出，当前为 exported"}
```

**验收标准**

- Given 一个处于 `saved` 态的 Plan
  When 以同一 `Idempotency-Key` 连续调用两次 `POST /api/v1/plans/{id}:export`
  Then 两次都返回 `200`
  And 两次的 `Content-Type` 与 `Content-Disposition` 完全一致
  And 两次的响应体字节完全一致
  And Plan 的 `state` 仍为 `exported`，`exported_at` 未被改写
  And 审计日志中该 export 动作只记录 **1** 次，不产生第 2 次副作用

- Given 一个处于 `saved` 态的 Plan
  When 以同一 `Idempotency-Key` 调用 `export`，随后以**不同** `Idempotency-Key` 再调用一次
  Then 第二次返回 `200` 且内容一致
  And 不出现 `STATE_CONFLICT`

- Given 任意二进制响应端点
  When 同一 key + 同一 payload 在 TTL 窗口（7 天）内重放
  Then 返回首次的响应体原样，**不是** JSON 包装壳
  And `Content-Type` 不被改写成 `application/json`

**反例验收**

- Given 导出端点
  When 用同一 key 重放
  Then **不得**返回 `409 STATE_CONFLICT`
  And **不得**返回 `409` 之外的其他错误码来表达"已经导过了"
  And 幂等记录表中该 key 必须存在一条记录（当前实现下记录数为 0，这是缺陷本身）

**给实现的提示**
`idem.remember` 当前只存 JSON-able 的 `response_body`（`models/idempotency.py` 是 JSON 列）。
二进制快照需要三样：body bytes、content-type、content-disposition。
可选方案：
1. 新增 `response_blob` 二进制列（推荐，语义最正）
2. 复用 JSON 列，body 存 base64 + 另存两个头字段
3. 把导出改为 GET 语义（无副作用）并移出幂等装饰器——**若选此项，必须同步改 SPEC AC-19 的适用范围**，
   并在契约里写明"导出为只读语义，可安全重复调用"。**不能只改代码不改文档。**

**验收人**：架构师（幂等层一致性）+ PM（重放语义与 SPEC 一致性）

---

## TC-02 · SEG 无有效像素时，Plan 计算必须拒绝而不是给出低风险

| 项 | 内容 |
|---|---|
| 对应缺口 | B4 |
| 优先级 | **P0-止血**（本轮唯一直接误导临床判断的缺陷） |
| 触及位置 | `server/app/services/plans.py:115-126, 148-156`、`server/app/dicom/seg.py:42-147`、`server/app/api/v1/schemas.py` |
| 类别 | 静默失效 |

**背景**
`seg.py:42-147` 的 `build_seg_header` 只写元数据骨架，从不写 `PixelData` /
`PerFrameFunctionalGroupsSequence` / `Rows` / `Columns`，`referenced_series` 传空列表。
实测 `.dicomstore` 中 58 个 SEG 实例全部 `PixelData=False`、`Rows=None`、401 字节。

`plans.py:115-126` 的 `safe_distance` = 两点欧氏距离，与任何分割结构无关；
`risk_from_distance`（`:57-64`）拿它比 10mm / 5mm 阈值。
实测 `(0,0,0)→(30,40,0)` 得 `risk_level=low`，前端 `CaseWorkspace.jsx` 以绿色
`<span className="risk-low">` 展示给临床医师。

**这是"凭空给出低风险结论"，比"SEG 是空壳"本身严重。**

**验收标准**

- Given 一个 `confirmed` 态但 `PixelData` 为空（或 `Rows`/`Columns` 缺失）的 SEG
  When 调用 `POST /api/v1/cases/{id}/plans` 引用它
  Then 返回 `422`，`error.code` 为**新增的业务码**（建议 `SEGMENT_EMPTY`）
  And `error.message` 明确说明"引用的分割不含有效像素，无法计算安全距离"
  And `error.details` 指出具体的 `seg_uid`

- Given 上述被拒绝的请求
  When 查询该 Case 的 Plan 列表
  Then **不得**出现新的 Plan 记录
  And 该 SEG 的 `state` 与 `version` 不被修改

- Given 一个 Plan，其 `base_seg_uid` 指向的 SEG 在 Plan 创建后被撤回
  When 调用 `PUT /api/v1/plans/{id}` 或 `POST /api/v1/plans/{id}:save`
  Then 返回 `409`（依赖失效），**不得**返回 `422`
  And **不得**静默重算出一个 `risk_level`

- Given 一个 Plan，其 `base_seg_uid` 指向的 SEG 被修订（`version` +1）
  When 调用 `POST /api/v1/plans/{id}:save`
  Then 系统**不得**基于旧版本 SEG 重算并展示新的风险等级

**反例验收**

- Given 任何 Plan
  When 序列化其 `risk_level` 返回给前端
  Then 若该 Plan 的基准 SEG 无有效像素，**不得**返回 `low` / `medium` / `high` 中的任何一个
  And **不得**返回 `null` 之外的占位值（`unknown` 之类）——必须是明确的错误或不可用标记
  And 前端在拿到该标记时，**不得**渲染任何绿色 / 黄色 / 橙色风险标签

- Given 前端拿到 `risk_level` 为不可用状态
  When 渲染规划表格
  Then 该单元格显示明确的"无法计算"文案，**不是**颜色块

**补充验收（v1.2 新增 · 阈值可见性，字段名已由架构师定）**

架构师给了一个**结构化字段**，把"分支依据"和"文案"彻底解耦：

```
PlanOut.threshold_status: "provisional" | "clinically_confirmed"   默认 "provisional"
```

位置：`server/app/api/v1/schemas.py` 的 `PlanOut`（`:282-298`），紧挨 `risk_level` / `risk_basis`
（`:291` / `:294`）。

**为什么是枚举而不是布尔 `threshold_provisional`**（架构师的理由，我同意并抄进卡里）：
布尔把"还没确认"和"已确认"做成一次位翻转，任何人能把它改成 `false`，而且改完**看不出是谁确认的、凭什么确认的**。
枚举强制选一个带语义的值，选 `clinically_confirmed` 这件事本身会逼出"谁确认的"这个问题 ——
而答案正是 Q-RA-CLASSIFICATION 等的那份 RA 书面意见。**布尔不会逼出这个问题。**
另外将来若出现"部分结构已确认"，枚举加值不破坏现有消费方；布尔只能再加一个字段，然后是两个布尔的组合爆炸。

**服务端 / 前端分工**：事实（是否暂定阈值）归服务端，措辞归前端。服务端**不提供文案字段** ——
这样文案改版不会动到分支，分支改版不会动到文案。

- **AC-阈值可见性** Given 一个 `risk_level` 为 `low` / `medium` / `high` 的 Plan
  When 渲染规划表格的风险单元格
  Then 若 `threshold_status === "provisional"`，**任何等级**都必须在**常驻可见**位置显示警示
  And 该警示**不得**依赖 hover / click / 展开
  And 该警示**不得**只存在于 `title` / `aria-label` 等属性里
  And 分支依据**必须**是 `threshold_status` 字段本身
  And **禁止**前端对 `risk_basis` 做任何子串匹配（如 `includes('待临床确认')`）——
  那是文案改一次就断一次的耦合，属于同一类静默失效

- **AC-字段契约** Given `PlanOut`
  When 检查字段
  Then 存在 `threshold_status`，取值域为 `"provisional"` / `"clinically_confirmed"`
  And 默认 `"provisional"`
  And 服务端**不得**返回任何"警示文案"字段（文案归前端）
  And `risk_basis` 保持原语义（人类可读解释），**不承载分支语义**

- **AC-缺省兜底** Given `risk_from_distance(safe_distance=None)`
  When 计算风险等级
  Then **必须**返回 `UNKNOWN`，**不得**返回 `MEDIUM`
  And `risk_basis` 说明"安全距离不可得"
  And 说明：`plans.py:67-68` 现在写的是 `if safe_distance is None: return RiskLevel.MEDIUM`，
  这是"输入缺失时猜一个默认值"，与契约 §9 X1 禁止的行为同类。落在风险分级上更危险，
  因为调用方会把 `MEDIUM` 读成"评估过了"，实际是"没数据"

- **反例** Given 一个 `risk_level` 为 `high`、`threshold_status` 为 `provisional` 的 Plan
  When 用户不把鼠标移到标签上
  Then 仍然**必须**能看到"该等级基于暂定阈值"这一信息
  And **不得**出现"红色 high 标签醒目、阈值警示只有悬停才可见"的呈现落差
  And **不得**靠 `risk_basis` 的文本内容决定是否显示警示
  And **不得**出现 `threshold_status` 已是 `clinically_confirmed` 但没有确认来源记录的状态
  （写入 `clinically_confirmed` **必须**可追溯到 Q-RA-CLASSIFICATION 的 RA 书面意见）

**这一条已上升为通用规则（不止阈值这一处）**

架构师的表述我照抄：**结论越像可直接采信的判定，它"前提未确认"的警示就必须越显眼。**

现在的实现正好相反：`unknown`（最不像结论）给了最显眼的说明，`high`（最像结论）只给一个 hover tooltip。
这和"SEG 无像素时显示未分级而不是绿色 low"是同一条原则的两个方向 ——
那一条是"别把算不出来的东西显示成结论"，这一条是"别把基于未确认前提的结论显示得像已确认的结论"。

**给实现的提示**
止血与补功能是两件事，**顺序不能反**：
- **第一步（本卡）**：SEG 无有效像素 → Plan 拒绝 + 前端不显示风险标签。改动小，止住误导。
- **第二步（TC-06）**：让手动分割产出真实 mask，Plan 才有可算的对象。

**阈值口径（措辞已定，不要写成"实验性阈值"）**

风险阈值 `SAFE_DISTANCE_LOW_MM=10` / `SAFE_DISTANCE_MEDIUM_MM=5` 本身仍是
`[Assumption, TBD]`（`plans.py:26-28`）。在临床顾问给值之前，对外一律写
**「待临床确认的暂定阈值」**。

不用"实验性阈值"的原因：那个说法读起来是"能用，只是精度待验"，
引导使用者照常采信；而真实情况是"口径本身未定"，应当引导使用者**去确认**，不是去用。
两个词指向相反的行动。

这条措辞同时出现在 `risk_basis` 的返回文本里（作为人类可读解释），
但**分支不准依赖它**——分支只看上面的 `threshold_status` 字段。

**补充一条同链缺陷（架构师核出，已并入 Q-SAFE-DISTANCE 登记）**

`plans.py:67-68`：

```python
if safe_distance is None:
    return RiskLevel.MEDIUM
```

**输入缺失时返回一个具体等级。** 这是"猜一个默认值"的兜底，和契约 §9 X1 禁止的是同一类行为，
而且落在风险分级上更危险：调用方拿到 `MEDIUM` 会当成"评估过了，中等风险"，
实际是"没数据，但默认中等"。

正确行为：返回 `UNKNOWN` + `risk_basis` 说明"安全距离不可得"。

这一条已写进本卡 `AC-缺省兜底`。目前 `safe_distance` 在实际路径上不会是 `None`
（`distance()` 拿到合法点必返回浮点），所以是**潜伏缺陷**——但正是这种
"现在不会触发所以没人管"的分支，会在半年后某个改动让它可达时一次性变成真缺陷。

**该措辞还必须出现在 `risk_basis` 的返回文本里**（作为人类可读解释），
但**不能只靠它**——理由见下。

**核前端时发现的第二个问题：只写进 `risk_basis` 不够。**
`CaseWorkspace.jsx:629-640` 的现状是：

```jsx
<span className={`risk-${p.risk_level}`} title={p.risk_basis || undefined}>
  {p.risk_level === 'unknown' ? '未分级' : p.risk_level}
</span>
{p.risk_level === 'unknown' && (
  <div className="hint warn">{p.risk_basis || '底层 SEG 无像素数据，无法评估'}</div>
)}
```

`risk_basis` 在非 `unknown` 分支里**只作为 `title` 属性存在**，也就是只有鼠标悬停才看得到。
风险等级是 `low` / `medium` / `high` 时——**恰恰是用户最可能直接采信的时候**——
阈值警示完全不可见。

**架构师把这条上升成了一条通用规则，我照抄进来，因为它比阈值这一处重要得多**：

> **结论越像可直接采信的判定，它"前提未确认"的警示就必须越显眼。**

现在的实现正好相反：`unknown`（最不像结论）给了最显眼的说明，
`high`（最像结论）只给一个 hover tooltip。这和"SEG 无像素时显示'未分级'而不是绿色 low"
是同一条原则的两个方向——那一条做的是"别把算不出来的东西显示成结论"，
这一条做的是"别把基于未确认前提的结论显示得像已确认的结论"。

**验收人**：架构师（错误码与状态机一致性）+ PM（临床误导风险已消除）

---

## TC-03 · 导出格式：PDF 出 MVP，`format` 参数两步退场

| 项 | 内容 |
|---|---|
| 对应缺口 | B2（假 PDF）+ 原 PRD FR-06 / 选型结论第 46 行的 PDF 承诺 |
| 优先级 | **P0-止血** |
| 触及位置 | `server/app/api/v1/routers/plans.py:105-141`、`docs/decisions/ADR-003-report-pdf-out-of-scope.md` |
| 类别 | 静默失效（声明了 PDF，落出来是文本） |
| **决策已落** | **ADR-003：PDF 不进 MVP。** 本卡的"是否做 PDF"选项已关闭，只做退场动作 |

**背景**
`plans.py:125-136`：`format=pdf` 与 `format=dicom_sr` 两条分支**共用同一段纯文本 body**，
只有 `media` 不同。`format=pdf` 时 `Content-Type: application/pdf`，
但文件内容是 `PLAN xxx v1\nbase_seg: ...\n` —— 首 4 字节不是 `%PDF`。
用户双击打开，Acrobat 报"文件已损坏"。

**决策（架构师 ADR-003，已裁定）**：PDF 不进 MVP。理由链条——
SR 是分割 / 规划的唯一事实源；PDF 只是它的第二份人类可读渲染，两套模板必然漂移；
`requirements.txt` 里没有任何 PDF 库；中文字体嵌入（约 10MB）是部署形态变更，不是加个依赖。
打印需求由浏览器打印样式表承接，不新增产物形态。

**两步走，顺序不能反**：

- **第一步（止血）**：`format=pdf` 返回 `501 Not Implemented`，错误消息给出替代路径。
- **第二步（终态）**：**删掉 `format` 查询参数**。一个永远 501 的取值等于对外声明
  "这个功能是存在的，只是暂时不可用"，而事实是"我们决定不做"。
  参数留在契约里，读者会照着它排期。

**验收标准**

- **AC-1（第一步 · 止血）** Given 一个 `saved` 态的 Plan
  When 调用 `POST /api/v1/plans/{id}:export?format=pdf`
  Then 返回 `501`
  And `error.code` 为 `NOT_IMPLEMENTED`
  And `error.message` 明确写出替代路径（"本报告以 DICOM SR 为唯一正式产物；打印请使用浏览器打印"）
  And **不得**返回任何 `Content-Type: application/pdf` 的响应体

- **AC-2（第一步 · 不误伤）** Given 一个 `saved` 态的 Plan
  When 调用 `POST /api/v1/plans/{id}:export?format=dicom_sr`（或不带 `format`）
  Then 返回 `200`
  And 产物是可被 pydicom 解析的 DICOM SR
  And 若产物不是真实 SR 而是文本占位，`Content-Type` **不得**声明为 DICOM 类型，
  扩展名**不得**用 `.dcm` / `.xml` 这类暗示 DICOM 结构的后缀

- **AC-3（第二步 · 终态）** Given `/api/v1/contract`（或等价的契约描述端点）
  When 检查 export 端点的参数列表
  Then **不得**出现 `format` 参数
  And 端点的响应契约只声明一种产物形态（DICOM SR）

- **AC-4（文档同步）** Given PRD FR-06 与选型结论第 46 行
  When 交付本卡
  Then PRD FR-06 的"也可以是 PDF（用于院内流转）"**必须**改为明确的 out-of-scope 表述并链接 ADR-003
  And 选型结论第 46 行**必须**拆成两格：

    | 需求 | 结论 | 状态 |
    |---|---|---|
    | DICOM 报告生成 | 自研 SR 写出 | 已实现 |
    | PDF 报告生成 | 不进首期，打印由浏览器样式表承接 | **有意不做**（见 ADR-003） |

  And 该行原文引用的 "OHIF dicom-pdf 扩展" **必须**标注为"从未真正依赖"——
  全仓 `OHIF` 只出现在一处 CSS 注释里，选型文档把它写成可选项属于未核实

**反例验收**

- Given 任意导出
  When 客户端按 `Content-Type` 选择打开方式
  Then **不得**出现"按 PDF 打开但解析失败"的情况
  And **不得**返回 200 + `application/pdf` + 非 PDF 内容的组合（这是本卡的原始缺陷）

- Given 交付终态后
  When 检查代码与契约
  Then **不得**保留一个"取值只有 pdf / dicom_sr 两个、其中一个恒 501"的参数
  And **不得**在 SPEC / PRD / README 任意一处留下"PDF 待实现"的表述——
  不是待实现，是决定不做，两句话的行动含义相反

**给实现的提示**
不要在止血步就急着删参数：先让 501 上线并把错误消息写清楚，
确认没有调用方依赖它之后再做第二步。删参数是破坏性变更，单独排。

**验收人**：PM（对外承诺与产物一致）+ 架构师（格式契约与 ADR-003 一致性）

---

## TC-04 · AI 任务状态必须诚实，且必须有超时收敛

| 项 | 内容 |
|---|---|
| 对应缺口 | B3 |
| 优先级 | **P0-止血** |
| 触及位置 | `server/app/adapters/ai.py:49-54`、`server/app/api/v1/routers/ai.py:79-81`、`web/src/pages/CaseWorkspace.jsx` |
| 类别 | 静默失效 |

**背景**
`adapters/ai.py:49-54` 未配 `ai_base_url` 时返回桩 `{"queue_pos":0,"estimated_sec":30}`，
**没有任何进程去调 `/api/v1/ai-results` 回调**。`routers/ai.py:79-81` 把 job 置为 `RUNNING`。
前端无轮询。

实测：`POST /ai/infer` → `202 {"status":"running"}`；3 秒后 `GET /ai/jobs/{id}` 仍 `running`、
`result_seg_uid=None`；segments 列表无任何 `ai:` 前缀条目。

**信号是假的**：接口返回成功、UI 弹"已投递自动分割任务"、然后什么都不会发生。
更糟的是 job **没有超时收敛路径**——既不会因超时转 failed，也不会因桩就绪转 done，
它会永远 running。

**验收标准**

- Given 未配置 `ai_base_url` 的环境
  When 用户点击"自动分割"
  Then 界面**明确提示**"AI 推理服务未配置，自动分割不可用"，而非"已投递任务"
  And 该提示**不得**是成功态样式（绿 / 对勾）
  And 系统**不得**创建一个永远不会完成的 `AiJob` 记录

- Given 一个 `AiJob` 已创建
  When 超过 `ai_timeout_sec`（或一个明确配置的收敛阈值）仍未收到回调
  Then 该 job 的 `status` **必须**转为 `failed`
  And `job.error` 写明"AI 服务无响应 / 未配置"
  And 审计日志记录 `ai_failed` 动作

- Given 一个 `AiJob` 处于 `queued` 或 `running`
  When 前端查询 `GET /api/v1/ai/jobs/{id}`
  Then 返回的 `status` 必须是四个枚举值之一（`queued` / `running` / `done` / `failed`）
  And **不得**出现无实际进程支撑的 `running` 状态

- Given 前端提交了自动分割任务
  When 任务尚未完成
  Then 前端**必须**轮询 job 状态（建议 1s 间隔，≤30s 后停止并提示）
  And 轮询期间显示进度指示
  And 任务转 `done` 时，刷新 segments 列表，新 SEG 以 `suggested` 态出现
  And 任务转 `failed` 时，显示失败原因，**不得**静默停在 loading

**反例验收**

- Given 任意 AI 任务
  When 任务进入终态
  Then `status` **不得**停留在 `running` 超过收敛阈值
  And 前端**不得**在任务失败后仍显示"已投递 / 进行中"

- Given AI 回调返回 `done`
  When SEG 落库
  Then 其 `state` **必须**为 `suggested`（不得为 `confirmed`）
  And `algorithm` 形如 `ai:{model}:{model_version}`
  And 该 SEG **不得**被 Plan 或 Report 引用（引用应返回 409）

**给实现的提示**
桩模式建议直接同步合成一个 `suggested` 态 SEG（走 `seg_svc.create_from_ai`），
让 FR-04 的"AI 输出非自动生效 → 医师确认"信任链在演示中可走通；
但**必须**标明该 SEG 的 `algorithm` 带桩标识（如 `ai:stub:*`），
避免演示数据被误认为真实模型输出。

另：`routers/ai.py:36-37` 把 `model` 默认硬编码为 `totalsegmentator/multi-structure`、
`model_version="2.0.0"`，而 PRD Q3 明确写"具体模型、权重许可…尚未决定"。
建议改为从配置读取，并在未配置时返回 `model: null` + 明确的"模型未选型"提示，
避免把未决项伪装成已决策。

**验收人**：PM（信号真实性 + 信任链可演示）+ 架构师（任务状态机收敛）

---

## TC-05 · 测量 SR 必须携带空间信息，且坐标系符合坐标契约

| 项 | 内容 |
|---|---|
| 对应缺口 | B5 |
| 优先级 | **P1-可追溯** |
| 触及位置 | 前端已改完：`web/src/components/MprViewport.jsx:372-390`、`web/src/pages/CaseWorkspace.jsx:154-195`。**本卡剩余工作量全在服务端**：`server/app/api/v1/schemas.py:64, 74`（`coord_ref` 仍是 `dict[str, Any]`）、`server/app/dicom/sr.py:95, 179-191, 320`（无校验、FOR 条件写入、series 回落） |
| 类别 | 静默失效（数值对但不可复核，报告里引用了一个无法定位的数字） |
| 依赖 | `docs/COORDINATE-CONTRACT.md`（架构师已落地，本卡验收以其为准） |

**背景（v1.0 立案时的实测事实，保留作为缺陷溯源）**
`MprViewport.jsx` 的测量完成事件只取 `{type, value, unit}`，不取 annotation 的 world 坐标。
`CaseWorkspace.jsx:150-157` 用 `m.spacing?.[0]`，而 `m` 里没有 `spacing` 字段 → 恒为 `null`；
且 `series_instance_uid` 被塞在 `coord_ref` 内，而 `MeasurementCreate`（`schemas.py:66`）有顶层字段，前端没传。

`sr.py:141-142` 取 `coord_ref.get("sop_instance_uid")`，前端从未提供 → `_referenced_sop`（`:166`）返回空列表。

实测 SR：`ReferencedSOPSequence` 长度 0、无 `GraphicData`、`Measurement.series_instance_uid=None`、
SR 落进合成 Series `{sr_uid}.1` 而非真实 CT 序列 UID。

**v1.2 进展核对（我逐文件复核过，不要把已修的当未修）**

| 层 | 现状 | 结论 |
|---|---|---|
| 前端 `web/src/lib/measurementPayload.js`〔v1.2 更新〕 | 产物构造已抽成**零 import 纯函数模块**（不碰 `@cornerstonejs/*`），`buildMeasurementCreateBody` / `parseSopInstanceUid` / `GRAPHIC_SPEC` 齐备；配 14 条 `node:test` 用例全绿 | **已修复且已加固**——顺带完成 TC-07 的抽取目标 |
| 前端 `MprViewport.jsx:372-390` | 已按契约 §4.1 实现映射：length → `POLYLINE`/6、angle → `POLYLINE`/9、其余 → `POINT`/3（逻辑已迁入 `lib/`） | **已修复** |
| 前端 `CaseWorkspace.jsx:174-182` | 已传 `graphic_type` / `graphic_data` / `sop_instance_uid` / `geometry='patient_lps_mm'`，坐标缺失传 `null` 让服务端 422（不造假 UID） | **大部分已修复**；**残留**：仍在传 `frame_of_reference_uid`（`:182`），按 C6 应删 |
| 前端 `measurementPayload.js:97` | `unit = stats?.unit \|\| stats?.areaUnit \|\| (angle ? '°' : '')` | **残留缺陷**：`°` / `mm²` 不是 UCUM 代码。见 AC-2b |
| 服务端 `schemas.py:64` | 仍是 `coord_ref: dict[str, Any] = {}` | **未修复**（AC-5 仍存活） |
| 服务端 `sr.py:179-186` | 仍是 `coord_ref.get("graphic_data")` / `.get("graphic_type")` 自由字典读取，`GraphicType` 默认 `POLYLINE` | **未修复**（无校验，非法组合照样落库） |
| 服务端 `sr.py:189-191` | `if for_uid:` 条件写入 FOR UID | **未修复**（C2） |
| 服务端 `sr.py:140` | `_code(unit or default_unit, "UCUM", ...)` —— 把客户端 `unit` 透传成 UCUM 代码 | **未修复**（AC-2b） |
| 服务端 `sr.py:22` | angle 默认单位写 `"degree"` | **未修复**：不是 UCUM 代码，应为 `deg` |
| `geometry` 取值 | 服务端全文无 `patient_lps_mm`，无归一化 | **未修复**（AC-5） |
| `series_instance_uid` | 服务端 `sr.py:95`/`:320` 仍回落 `f"{sr_uid}.1"` | **未修复**（AC-1） |

**所以本卡剩余工作量集中在服务端**：前端已经把坐标送上来了，是服务端还在"收到什么就存什么"。

### 本卡的 7 处纠正（坐标契约已改，v1.0 验收项作废）

架构师在 `docs/COORDINATE-CONTRACT.md` 里给了 7 条纠正（首轮 5 条 + 14:39 修订追加 2 条），
逐条覆盖本卡 v1.0 的验收项。**下表左列是作废项，右列是现行项。验收时只看右列。**

| # | v1.0 作废验收项 | 纠正后（现行） | 依据 |
|---|---|---|---|
| C1 | `GraphicDataUnits` 为 `mm` | **删除该断言**。DICOM 标准里没有 `GraphicDataUnits` 这个属性；SCOORD3D 的单位由 PS3.3 C.18.9 固定为 mm，**不存在可写的单位字段**。单位正确性改由两条可执行的断言证明（见下方 AC-2） | 契约 §7.1 |
| C2 | （无） | **新增**：`ReferencedFrameOfReferenceUID` 是 **Type 1 必填**。FOR UID 缺失时**不得**生成 SCOORD3D 项，整条测量应 422。现状 `sr.py:189-191` 的 `if for_uid:` 会产出"没有归属坐标系的坐标" | 契约 §7.1 |
| C3 | （无） | **新增**：`ReferencedSOPSequence` **保持放在 NUM 内容项上，不得挪到 SCOORD3D 项上**。PS3.3 Supplement 162：SCOORD3D "has no children, since its coordinates are patient rather than image-relative" | 契约 §7.1 |
| C4 | `GraphicType` 为 `POINT`（一律） | **改为按测量类型映射**：length → `POLYLINE` / 6 值；angle → `POLYLINE` / 9 值；area → `POINT` / 3 值。前端 `MprViewport.jsx:372-390` 已按此实现；**服务端仍无校验**（`sr.py:186` 只是 `.get("graphic_type") or "POLYLINE"`），非法组合照样落库 —— 服务端必须挡 | 契约 §4.1 |
| C5 | `row_spacing` / `column_spacing` / `slice_thickness` 必须是实际数值、不得为 null、与 DICOM 头一致 | **删除该断言，并从请求体里删掉这三个字段**。服务端本来就从影像头持有权威值（`store.py:156-163`），前端传进来只会造出第二份事实源。服务端**忽略**前端 spacing | 契约 §6 / §6.1 |
| **C6**〔14:39 追加〕 | `coord_ref.frame_of_reference_uid` 可选、服务端**交叉校验** | **改为：前端不传，服务端按 `sop_instance_uid` 从实例元数据派生**（`store.py:163`）。理由：前端这个值来自 `viewport.getFrameOfReferenceUID()` → `imageVolume.metadata.FrameOfReferenceUID`，与服务端从影像头读的是**同一处**；两端比对等于自己校验自己，永远通过（或在元数据供应源不同时对合法数据误报 422）。**"交叉校验"这个验收项整体作废** | 契约 §4 / §5 / §6.1 / §4.3 |
| **C7**〔14:39 追加〕 | 面积取"两 handle 中点" | **改为四点质心**。两种 ROI 的 `handles.points` 都是 **4 个点**且点序不同：矩形 `[topLeft, topRight, bottomLeft, bottomRight]`，椭圆 `[bottom, top, left, right]`。四点质心对两者都等于几何中心（矩形因 `topLeft+bottomRight == topRight+bottomLeft`） | 契约 §4.1 |

**C6 附带一条方法论教训，我照抄进卡里，因为它会影响后续所有验收的写法**：
架构师最初用"全量 grep `frameOfReferenceUID` 字面量"得出"工具从不写该字段"的结论，
**这个结论是错的**——赋值走的是 `...viewReference` 对象展开（`AnnotationDisplayTool.js:91`），
字面量在源码里一次都不出现，grep 必然漏。
**静态搜索只能证明"这些地方赋了值"，不能证明"没有地方赋值"。**
要断言"某字段运行时恒为空"，必须在运行时观测，不能靠 grep 推断。



**验收标准**

- **AC-1（源图像引用）** Given 用户在轴位视口用 LengthTool 画一条线并保存
  When 生成的 DICOM SR 被下载并用 pydicom 解析
  Then `ReferencedSOPSequence` 长度 **≥ 1**，且其 `ReferencedSOPInstanceUID` 等于该测量所在切片的 `SOPInstanceUID`
  And `SR.SeriesInstanceUID` 等于被测序列的 `SeriesInstanceUID`（**不是** `{sr_uid}.1` 合成值）
  And 该序列位于 `MeasurementCreate` **顶层**的 `series_instance_uid`（`schemas.py:66`），不在 `coord_ref` 内

- **AC-2（单位，替代作废的 `GraphicDataUnits`）** Given 同一份测量 SR
  When 检查其单位正确性
  Then 数值项的 `MeasurementUnitsCodeSequence` 里的 `CodeValue` **必须是合法的 UCUM 代码**，
  按测量类型逐项固定为：`length→mm`、`angle→deg`、`area→mm2`、`volume→mm3`
  And **不得**把展示用单位写进去：`°`、`mm²`、`mm³`（带度数符号与上标）都不是 UCUM 代码
  And SR 中**不存在** `GraphicDataUnits` 属性（写了就是造了一个标准里没有的元素）
  And `GraphicData` 两点间距离 / 由影像头换算出的距离 **== 1**（容差 1e-3）

- **AC-2b（UCUM 代码必须服务端权威，不能取客户端传入的 `unit`）**
  Given `sr.py:140` 的现状
  `MeasurementUnitsCodeSequence = [_code(unit or default_unit, "UCUM", unit or default_unit)]`
  When 客户端传入 `unit = '°'` 或 `'mm²'`
  Then 该字符串会被硬塞进 `CodingScheme="UCUM"` 的序列里，产出一份**非合规** SR
  And 正确行为：**`CodeValue` 一律由服务端按测量类型从 `MEASUREMENT_CODES` 派生，忽略客户端 `unit`**
  And 客户端 `unit` 只作**展示用**，不进 DICOM（与 spacing 同一条原则：派生数据不跨线，见契约 §6）
  And `sr.py:22` 的 `"degree"` 也**必须**改为 `"deg"`——`degree` 同样不是 UCUM 代码
  And 前端 `measurementPayload.js:97` 的 `unit = ... ?? '°'` 应改为传展示单位或干脆不传，
  **但不得**依赖它来产生合规 UCUM 代码

- **AC-3（坐标系归属，对应 C2）** Given 同一份测量 SR
  When 检查 SCOORD3D 内容项
  Then `ReferencedFrameOfReferenceUID` **必须**存在且等于被测序列的 `FrameOfReferenceUID`
  And **不得**存在"有 `GraphicData` 但无 `ReferencedFrameOfReferenceUID`"的 SCOORD3D 项

- **AC-4（`GraphicData` 形状，对应 C4 / C7）** Given 同一份测量 SR
  When 检查 `GraphicType` 与 `GraphicData`
  Then `GraphicData` **必须**存在，长度 mod 3 == 0
  And `graphic_type` 与长度的对应关系严格按下表，**不匹配 → 422**

    | 测量类型 | `graphic_type` | `graphic_data` 长度 | 构造方式 |
    |---|---|---|---|
    | length | `POLYLINE` | 6 | `handles.points` 前两点 |
    | angle | `POLYLINE` | 9 | `handles.points` 三点（顶点在中间） |
    | area（矩形 ROI） | `POINT` | 3 | **四个 handle 的质心**（点序 `[topLeft, topRight, bottomLeft, bottomRight]`） |
    | area（椭圆 ROI） | `POINT` | 3 | **四个 handle 的质心**（点序 `[bottom, top, left, right]`） |

  And 面积类的质心计算**不得**写成"取前两个 handle 的中点"——两种 ROI 都是 4 个点且点序不同，
  取前两点会得到边缘点，位置偏到轮廓边上而非中心

- **AC-5（`coord_ref` schema，含 C6）** Given `MeasurementCreate.coord_ref`
  When 检查其字段
  Then `geometry` **必须**是枚举且取值**必须**为 `patient_lps_mm`
  And `graphic_type` 为枚举（POINT / MULTIPOINT / POLYLINE / POLYGON / ELLIPSE / ELLIPSOID）
  And `graphic_data` 为 number[]，扁平 (x,y,z) 三元组序列，单位 mm，可为负，**必填**
  And `sop_instance_uid` **必填**（服务端还用它反查 `FrameOfReferenceUID`）
  And `study_instance_uid` **必填**
  And `series_instance_uid` **必填**，且只出现在 `MeasurementCreate` **顶层**，
  **`coord_ref` 内不得重复携带**（放两份就是第二份事实源）
  And `coord_ref` 内**不得**出现 `frame_of_reference_uid`、`row_spacing`、`column_spacing`、`slice_thickness`
  这四个字段（前端不传，见 C5 / C6）

- **AC-6（派生数据不跨线，含 C5 / C6）** Given 前端提交测量的请求体
  When 检查是否存在 `row_spacing` / `column_spacing` / `slice_thickness` / `frame_of_reference_uid`
  Then 这四个字段**不得**出现在请求体中
  And 服务端 SEG / SR 使用的 spacing **必须**来自影像头（`store.py:156-163`），**前端即使传了也必须忽略**
  And SR 的 `ReferencedFrameOfReferenceUID` **必须**由服务端按 `sop_instance_uid`
  从实例元数据派生（`store.py:163`），**不依赖前端传入**
  And **不得**实现"前端传 FOR UID + 服务端交叉校验"——两端读的是同一处，
  这个校验永远通过，等于没有校验（契约 §6.1 已把这个方案作为反例撤回）
  And **当前未达成**：`sr.py:179-186` 仍是 `coord_ref.get(...)` 直接读取，既无忽略逻辑也无派生逻辑

- **AC-7（跨端一致性）** Given 桌面端 3D Slicer
  When 打开同一份 SR 并按 `ImagePositionPatient` + `ImageOrientationPatient` 重建变换
  Then 其测得的测量线长度与 Web 端显示值**一致**（容差 ≤ 0.5mm）
  And 测量线落在 Web 端相同的解剖位置

**反例验收**

- Given 任何测量 SR
  When 检查其空间信息完整性
  Then **不得**出现"有 `NumericValue` 但 `ReferencedSOPSequence` 为空"的情况
  And **不得**出现 SCOORD3D 缺 `ReferencedFrameOfReferenceUID` 的 SR（对应 C2）
  And **不得**出现 `ReferencedSOPSequence` 挂在 SCOORD3D 项下的 SR（对应 C3）
  And **不得**出现 `graphic_type` 与 `graphic_data` 长度不匹配的 SR，尤其**不得**出现 `POLYGON` 配 2 点的非法组合（对应 C4）
  And **不得**出现 `geometry` 取值不在坐标契约枚举内的 SR

- Given 前端
  When 用户在影像上完成测量
  Then **不得**只提交 `value` 与 `unit` 而丢弃坐标
  And 提交前**必须**有断言：坐标缺失时**拒绝写入**并提示用户，而不是静默写入一个不可复核的数
  And **不得**把 `spacing` 作为"保证 SR 正确"的手段（服务端不认，传了也只是假安慰）

- Given 任一测量 SR 的 `MeasurementUnitsCodeSequence`
  When 检查其 `CodeValue` 与 `CodingScheme`
  Then **不得**出现 `CodingScheme="UCUM"` 但 `CodeValue` 不是 UCUM 代码的组合
  And **不得**出现 `°` / `mm²` / `mm³` / `degree` 这类展示单位或口语单位被标成 UCUM
  And `CodeValue` **不得**等于客户端请求体里的 `unit` 原值（一旦相等，说明服务端在透传而不是派生）

- Given 服务端接收 `coord_ref`
  When 任一必填字段缺失、`graphic_type` 与 `graphic_data` 长度不匹配、
  或请求体里出现 `frame_of_reference_uid` / 三个 spacing 字段
  Then **必须**返回 422，**不得**降级为"先存下来再说"
  And **不得**实现"前端传 FOR UID + 服务端交叉校验"这种自己校验自己的方案（C6）

**给实现的提示**

坐标契约已由架构师落地（`docs/COORDINATE-CONTRACT.md`），核心结论：
**Cornerstone 的 volume world 坐标就是 DICOM patient 物理坐标系（LPS，mm），可直接透传，无需转换层。**
前端 `MprViewport.jsx` 取的 `evt.detail.currentPoints.world` 正是该坐标系。

三个实现要点：

1. **`sop_instance_uid` 前端能拿到**，机械上可满足必填要求。MPR 是体渲染，取点在体数据里，
   但 `VolumeViewport.js:47-59` 的 `getCurrentImageId()` 走
   `getClosestImageId(volume, focalPoint, viewPlaneNormal)` 可按相机焦点反查最近切片，
   从 imageId 解析出 SOP Instance UID 即可。`VolumeViewport3D.js:41` 有同名实现。
2. **`geometry` 命名已裁决为 `patient_lps_mm`**（契约 §3），不再讨论 `volume_world` /
   `frame_of_reference`。改名是破坏性变更，走"写入时归一化"：
   把 `frame_of_reference` / `volume_world` 在入口层映射成 `patient_lps_mm`，
   外加一次存量回填，回填后跑残留断言 `SELECT count(*) ... <> 'patient_lps_mm'` **必须为 0 行**。
   其他值一律 422，**不做"猜一个默认值"的兜底**。
3. **面积退化为 `POINT` 是有意的取舍**，不是偷懒。斜位 MPR 下正确还原矩形 / 椭圆四角世界坐标
   需要按相机平面做投影重建，工作量与风险超出首期收益。但 SR 里仍保留"这条面积测量画在哪"的
   空间信息，面积标量照常写入 `NUM`，不受影响。**这比"只存一个标量"强，比"完整轮廓"弱，
   取舍要在 SPEC 里写明，不得让读者以为是完整实现。**

   **架构师补充：不需要再加"是否可还原轮廓"的标记字段** —— `GraphicType = POINT`
   本身就是那个机器可读的标记，下游读到 POINT 就知道这是单点位置，不可能还原轮廓。
   多一个布尔只是多一份可能不同步的状态。SPEC 里要写清两点：
   (a) 面积测量的**权威数值是 NUM 内容项里的 `value` + UCUM `mm2`**，`POINT` 只回答"画在哪"；
   (b) 下游**不得**从 `GraphicData` 反推 ROI 形状或重算面积。

### 同链但不在本卡范围：`PlanCreate` 点位也有坐标系问题（依赖 TC-07）

`PlanCreate.entry_point` / `target_point` 现在还是裸 `{x,y,z}`（`schemas.py:152-153`），
按契约 §5 要改成 `{geometry: "patient_lps_mm", coordinates: [x,y,z]}`。

**架构师已裁定走"一次到位"，但明确标注：这个改动依赖 TC-07，不是与它并列。**

理由（架构师原话，我同意）：TC-07 抽出 `buildPlanPayload` 纯函数后，改形状是**一处**改动，
smoke 自动继承，不存在中间态；抽取前改则是两处手改（前端 `handlePlanPoint` + `smoke.py:118-119`），
中间态必然出现 smoke 与 UI 不一致——**而这正是当初那个 422 能藏在绿色冒烟后面的同一条件。**

**不接受"geometry 缺省视为 `patient_lps_mm`"这种折中**：那正是契约 §9 X1 明令禁止的
"坐标系缺失时按默认值处理"，而且有传染性（下一步就会有人问 `MeasurementCreate` 为什么不能缺省）。

等待期间没有风险：规划点位现在是"未声明坐标系"，不是错的坐标系，`safe_distance` 算出来是对的。

**验收人**：架构师（坐标契约一致性，**逐字段把关**）+ PM（可复核性 + 跨端一致性能被验证）

---

## TC-06 · 手动分割必须产出真实 mask

| 项 | 内容 |
|---|---|
| 对应缺口 | B4（功能侧） |
| 优先级 | **P1-可追溯** |
| 触及位置 | `web/src/api/dicom.js`（工具注册）、`web/src/components/MprViewport.jsx`（分割模式）、`server/app/dicom/seg.py:42-147`、`server/app/services/segments.py:260-278` |
| 类别 | 功能缺失（但当前被 TC-02 的止血逻辑掩盖，用户看到的是"确认了一个空壳"） |
| 依赖 | TC-02（先止血，再补功能）；`docs/COORDINATE-CONTRACT.md` |

**背景（已按现码更新，v1.2）**
cornerstone 4.22 随包自带 `SculptorTool` / `RegionSegmentPlusTool` / `LivewireContourSegmentationTool` 等**分割专用**工具。
当前 `dicom.js:47-61` 已注册 11 个工具，但与分割相关的只有 `RectangleROITool` / `EllipticalROITool`，
**且它们服务于面积测量模式（`MprViewport.jsx:88-90`），不是分割模式**。
分割专用工具（`SculptorTool` / `LivewireContourSegmentationTool` / `RegionSegmentPlusTool`）**一个都没注册**。

`CaseWorkspace.jsx` 的分割面板只有一个"结构名称"文本框 + "新建分割" + "自动分割"两个按钮。
`web/src` 全量 grep `seg_content` **零命中** —— UI 从来不提交 SEG 内容。

服务端 `seg.py:50-155` 的 `build_seg_header` 也没有写像素的代码路径。

**注意一个容易看错的点**：`SegmentCreate.seg_content`（`schemas.py:101`）与 `AiCallback.seg_content`
**都能让 SEG 带上像素**。所以"API 级测试能造出带像素的 SEG"**不等于**用户能在界面上勾画出来。
见 TC-08 的反例验收。

**验收标准**

- Given 前端启用了分割工具
  When 检查 `web/src/api/dicom.js` 的工具注册清单
  Then 至少注册了 `SculptorTool` 与 `LivewireContourSegmentationTool`
  （**`RectangleROITool` 已注册但服务于面积测量，不能算作分割能力**）
  And 存在**注册自检断言**（如 `cornerstoneTools.getTool(toolName)` 非空），
  缺失时**在开发环境明确抛错**而非静默 warn

- Given 用户在影像上用 ROI 工具圈出一个区域并保存
  When 服务端生成 SEG
  Then SEG 的 `PixelData` **必须**存在
  And `Rows` / `Columns` 与被测序列的 `Rows` / `Columns` 一致
  And `PerFrameFunctionalGroupsSequence` 存在，含 `PlanePositionSequence`
  （内含 `ImagePositionPatient`）与 `ReferencedSegmentNumber`
  And `SegmentationType=BINARY`、`BitsAllocated=1`
  And `ReferencedSeriesSequence` 非空，指向被分割的序列

- Given 一个含真实像素的 SEG
  When 用 3D Slicer 或其他第三方阅片器打开
  Then 结构、颜色、层级、位置均正确显示
  And 分割区域落在用户圈定的解剖位置上
  （这条对应可行性报告 H4「分割结果可回写并被第三方读取」的通过标准）

- Given 分割已保存
  When 用户切到规划模式
  Then 分割结构在影像上**可见**（有高亮叠加），而非只在列表里有一个名字

**反例验收**

- Given 任何新建的 SEG
  When 服务端序列化
  Then **不得**产出 `PixelData` 缺失的 SEG
  And 若因故无法产出像素，**必须**拒绝创建并返回明确错误，
  **不得**返回"创建成功"却存一份空壳

- Given 前端
  When 分割工具未注册
  Then 界面**必须**给出明确错误提示，**不得**呈现"可以分割"的假入口
  And **不得**在控制台只打 warn 就让用户以为操作已生效

**给实现的提示**
SEG 的 `PerFrameFunctionalGroupsSequence` 天然与 `docs/COORDINATE-CONTRACT.md` 的
patient 坐标系对齐——`PlanePositionSequence.ImagePositionPatient` 直接取自被测切片的
DICOM 头，**无需额外换算**。这也是坐标契约要先落 SPEC 的原因之一。

**验收人**：架构师（SEG IOD 合规性）+ PM（第三方可读性 = H4 通过标准）

---

## TC-07 · 门禁必须能测到 UI 行为（并顺带完成文件拆分第一刀）

| 项 | 内容 |
|---|---|
| 对应缺口 | §9.2 门禁盲区（结构性发现）+ 存活缺口 6 / 7 |
| 优先级 | **P2-闭环**，但**它同时是 (b) 方案的前置条件**（见下），实际应最先启动 |
| 触及位置 | `server/scripts/smoke.py`、`web/package.json`、CI 配置、`web/src/components/MprViewport.jsx`（734 行）、`web/src/pages/CaseWorkspace.jsx`（911 行） |
| 类别 | 工程门禁 |

**⚠ 依赖关系：`PlanCreate` 点位改 `{geometry, coordinates[3]}`（方案 b）依赖本卡**

架构师已裁定走 **(b)**，并明确 **(b) 依赖 TC-07，不是与它并列**。理由：

- **抽取前**：改 payload 形状要动前端 `handlePlanPoint` + `smoke.py:118-119` **两处手写**，
  中间态必然出现 smoke 与 UI 不一致 —— **而这正是 B1 那个 422 当初能藏在"38/38 全绿"后面的同一个条件**。
- **抽取后**：UI 与 smoke 共用同一个 payload 构造函数，改形状是**一处**改动，smoke 自动继承，不存在中间态。

**（a）方案已被否决**：`PlanCreate` 的"`geometry` 缺省视为 `patient_lps_mm`"正是坐标契约 §9 **X1 明令禁止**的行为
（"坐标系缺失时按默认值处理"），且有传染性——一旦 PlanCreate 允许缺省，下一步就会有人问
"那 MeasurementCreate 为什么不能缺省"。

**等待期间无风险**：规划点位现在是"未声明坐标系"，不是"错的坐标系"，`safe_distance` 算出来是对的。可以安全地等。

**范围扩大：文件拆分并入本卡**

`MprViewport.jsx` 现 **734 行**、`CaseWorkspace.jsx` 现 **911 行**，都远超 300 行规则。
而本卡要抽的 `buildMeasurementPayload` / `buildPlanPayload` 正好长在这两个文件里。
**抽出来 = 一次动作同时满足两件事**：UI 与 smoke 同源（本卡目的）+ 行数下降。

边界（架构师给前端同事的口径）：

- **抽出去**：`MprViewport.jsx` 里 `capture()` / `commitDraft()` 的**产物构造部分**
  （annotation → `{graphic_type, graphic_data, sop_instance_uid}`）
- **留在组件里**：渲染与事件绑定

**背景**
`server/scripts/smoke.py:118-119` 创建规划时**手填**了 `entry_point` / `target_point`：

```python
r = post(c, f"/cases/{cid}/plans", json={
    "base_seg_uid": seg, "base_seg_version": 1,
    "entry_point": {"x": 0, "y": 0, "z": 0},
    "target_point": {"x": 30, "y": 40, "z": 0}})
```

**冒烟脚本与前端 UI 走的是两条完全不同的 payload 路径**，所以 B1 那个必现 422 的缺陷
能在"38/38 全绿"下长期存活。现有门禁测的是 **API**，不是 **UI**。

推论：存活缺口中，凡是能通过"改前端 payload / 改 UI 交互"暴露出来的，当前门禁都测不到。
**这解释了为什么静态契约测试全绿而实际演示走不通。**

**进度记录（2026-10-27，前端侧已交付一半）**

| 项 | 状态 | 依据 |
|---|---|---|
| `buildMeasurementPayload` 抽取 | ✅ **已完成** | `web/src/lib/measurementPayload.js`，**零 import**（不碰 `@cornerstonejs/*`，那些包模块顶层就要 `window`）。实测 `node --input-type=module -e "import('./src/lib/measurementPayload.js')"` 可直接加载 |
| `test` 脚本 | ✅ **已完成** | `web/package.json` 加 `"test": "node --test"`；`web/src/lib/measurementPayload.test.js` 202 行、**零第三方依赖**；当前 14 tests / 14 pass |
| lint 配置 | ❌ 未开始 | `web/` 完全无 eslint，需选版本 + 写 config + 加依赖，属基建 |
| E2E 用例 | ❌ 未开始 | 需 Playwright + 起服务端 + 造测试数据，独立工作量 |
| `buildPlanPayload` 抽取 | ⏸ 等前置 | 等 `PlanCreate` schema 落地才有最终形状；架构师已裁决"抽取优先于改形状"，但形状未定则抽不出最终版 |

**⚠ 一个必须提前想清的问题：「同源」不是自动的**

构造器是 JS，`smoke.py` 是 Python，**Python 不能直接 import JS**。所以"共用同一份构造器"要落地必须选一条：

- **(a) 加一个薄 Node CLI**：读 JSON 入参 → 输出 payload JSON → `smoke.py` 用 `subprocess` 调它。**推荐**——保住现有 38 条断言不动
- **(b) 把 smoke 那一段改用 Node 写**：同源最彻底，但要重写而非复用

**无论选哪条，有一条硬约束**：`smoke.py` 必须**原样发送** CLI 输出的 JSON，
**不得**在 Python 侧再做任何字段改写（改名、补默认值、换单位）。一旦中间加了一层"顺手整理"，
就又变回两条路径——**而这正是 B1 那个 422 当初能藏在全绿冒烟后面的成因。**

**验收标准**

- **AC-测试入口（措辞已放宽，理由见下）** Given `web/package.json`
  When 检查 `scripts`
  Then 存在 `test` 脚本，**且该脚本能实际运行并通过用例**
  And **不强制**要求第三方测试依赖——用 Node 内置 `node:test` 也满足
  And 该脚本**必须**接入门禁（CI 或 pre-commit），不能只是一个"存在但没人跑"的脚本
  And **自检**：故意破坏一条契约断言（例如把 `geometry` 改成 `volume_world`）后跑 `npm test`
  **必须失败**

  **为什么放宽"必须有测试运行器依赖"**：前端用 `node:test` 实现了 14 个用例、零第三方依赖，
  这不是妥协，是更好的选择——`web/` 目前只有 7 个运行时依赖，每引入一个测试框架就多一份
  升级负担与供应链面，而收益（能测）完全相同。原措辞的真实意图是"必须有可执行的测试"，
  而不是"必须装某个包"，写在验收里属于把手段当目的。
  **但放宽的前提是补上自检**：一个永远绿的测试套件比没有测试更危险，
  它会让人以为覆盖到了。所以上面第四条"故意破坏必须失败"不能省。

- Given `web/package.json`
  When 检查 lint
  Then 存在 lint 配置（eslint 或等价）并能对 `web/src/` 报错
  And **当前未达成**（`web/` 完全无 eslint），属基建，需单独立项

- Given 冒烟脚本
  When 创建 Plan
  Then payload 的 `entry_point` / `target_point` **不得**是脚本手写的常量
  And **必须**来自一个与前端同源的取值路径（或由 E2E 驱动 UI 产生）

- Given 一条端到端用例
  When 模拟"用户在影像上拾取两点后创建规划"
  Then 该用例**必须**覆盖 TC-01（导出幂等重放）、TC-03（导出 MIME）、
  TC-04（AI 任务终态）、TC-05（SR 含空间信息）这四类断言

- Given 一个 UI 交互层的缺陷
  When 修复前跑门禁
  Then 门禁**必须失败**（这是门禁有效性的自检）
  And 修复后必须通过

- **AC-拆分** Given `web/src/components/MprViewport.jsx` 与 `web/src/pages/CaseWorkspace.jsx`
  When 统计行数
  Then 两者均 ≤ 300 行（当前 734 / 911）
  And 产物构造逻辑（annotation → payload）已移入独立纯函数模块
  And 该纯函数**不 import** 任何 React / Cornerstone 运行时对象（否则冒烟脚本无法调用）

- **AC-错误可定位**（对应 SPEC §10 新增条款） Given 任一 4xx 响应需要在界面展示
  When 用户触发一次字段级校验失败（例如传错 `graphic_type`）
  Then 界面**必须**显示字段路径与原因（如 `body.coord_ref.graphic_type — Input should be 'POLYLINE', ...`）
  And 只显示一句顶层 `message`（如「请求体字段校验失败」）的界面**计为不通过**
  And 该断言覆盖测量 / 规划 / 报告 / 导入四条链路，不止坐标校验

- **AC-运行时验证**（对应 SPEC §10 新增条款） Given 任一"某字段恒为空 / 不可得"的结论
  When 该结论进入缺陷列表或验收标准
  Then **必须**附运行时实测值作为证据（打点读一次实际值）
  And 仅凭源码 grep 得出的结论**不予采信**
  （本轮实例：`FrameOfReferenceUID` 大写 F、赋值走 `...viewReference` 展开，grep 必然漏）

- **AC-不可用比率**（对应 SPEC §10 新增条款） Given 每个 `unavailable` / `unknown` 的 reason code
  When 检查监控
  Then 每个 reason 都有可见比率指标
  And 接入真实能力后该比率**必须**下降
  And 首期 `no_pixel_data` 与 `risk_level=unknown` 的 100% **必须**被显式记录为"上游能力不存在"的证据，
  不得作为"系统运行正常"解读

**反例验收**

- Given 冒烟脚本
  When 审查其 payload 是否存在手写常量
  Then **不得**存在与前端实际行为不一致的手写坐标 / 类型 / 单位常量
  And **不得**出现"脚本测了 A、UI 发的是 B"这类双路径

- Given 前端
  When 某工具未注册
  Then 门禁**必须失败**（注册自检断言在 CI 里生效）

**给实现的提示**
最小可行方案（不引入 Playwright 的重成本）：
1. 把前端的关键 payload 构造逻辑抽成纯函数（如 `buildMeasurementPayload` / `buildPlanPayload`）
2. 冒烟脚本或前端单测直接调用这些纯函数生成 payload
3. 这样"脚本发的"和"UI 发的"在物理上就是同一份东西

**验收人**：PM（门禁能否覆盖 UI 行为）+ 架构师（门禁与 SPEC 验收标准的映射完整性）

---

## TC-08 · 体积测量必须从 UI 走到数值

| 项 | 内容 |
|---|---|
| 对应缺口 | §9.1 第 12 项（v1.3 新增） |
| 优先级 | **P1-可追溯** |
| 触及位置 | `server/app/dicom/seg.py:276-312`（`compute_volume`）、`server/app/dicom/seg.py:50-155`（`build_seg_header`）、`web/src/pages/CaseWorkspace.jsx:362`（体积手填输入框） |
| 类别 | **功能缺失（已实现型）**——见下方归类说明 |
| 依赖 | TC-06（先有带像素的 SEG，体积才有对象）；但**其中一条可立即执行**，见 AC-0 |

**背景**

`compute_volume`（`seg.py:276-312`）**写得很对**：
- 无像素 → `unavailable("no_pixel_data")`
- `FRACTIONAL` / `PROBABILITY` / `OCCUPANCY` → `unavailable("fractional_segmentation")`（非零计数不是体素个数）
- 取不到 `PixelSpacing` → `unavailable("missing_pixel_spacing")`，**绝不假设 1mm**
- 位打包 `BitsAllocated=1` 用 `np.count_nonzero(pixel_array)` 而不是 `len(PixelData)`

**问题是它永远走不到。** 前置条件是 `_has_pixels`，而 `build_seg_header`（`seg.py:50-155`）至今不写 `PixelData` / `Rows` / `Columns`，`referenced_series` 仍是空。所以恒返回 `no_pixel_data`。

UI 侧更直白：`CaseWorkspace.jsx:362` 的"体积"是个**数字输入框**——要用户先自己知道体积是多少才能填。

**结果：任务标记完成、函数写得对、用户永远拿不到一个体积数。**

**这一类为什么单列（归类已按架构师意见修正为子类，不是并列第三类）**

从**用户视角**看，体积不可用就是功能缺失——用户拿不到一个数，和入口不存在没有区别。
所以它不是与前两类并列的第三类，而是功能缺失的一个**子类**：

> **功能缺失（已实现型）** —— 代码已写、单测可能已过，但没有任何产品内路径能走到成功分支。
> 与普通功能缺失的区别：**它的验收标准写"函数已实现"就会通过，只有写"从 UI 操作能拿到结果"才会暴露。**

写成子类而不是并列类别，是因为**处置方式不同**：
普通功能缺失直接排期做就行；这个子类要**先修验收口径**，否则做完还会再误判一次——
"顺序依赖 TC-06"只是必要条件，不是充分条件。

**⚠ AC-0：一条不用等 TC-06 就能修、且现在就产生危害的项**

`CaseWorkspace.jsx:362` 的 `volume` 类型选项**现在就可选**，配一个 `type="number"` 手填框。
用户填什么，就会被写进 DICOM SR —— `sr.py:20-25` 的 `MEASUREMENT_CODES` 里
`volume` 对应 SNOMED `118565006` / UCUM `mm3`。

也就是说：**一个没有任何测量依据、纯手打的数字，会被落成一份标准 DICOM 体积测量记录，下游无法证伪。**

这和 Q-SAFE-DISTANCE 是同一类问题：不是算错，是**产出了一个看起来有依据、实际没有任何依据的数字**。
而且比风险等级更糟——风险等级至少还有 `risk_basis` 说明，体积这个连说明都没有。

处理方式二选一，**倾向后者**：

- ❌ 移除 `volume` 选项 —— 干净，但用户会以为产品不支持体积测量
- ✅ **保留但禁用，并在选项旁写明**"体积计算依赖带像素的 SEG，当前不可用" ——
  传达真实状态（能力存在但当前不可达），这也正是本子类在用户侧的表达

**验收标准**

- **AC-0（立即执行）** Given 测量面板的类型下拉
  When 用户选择"体积"
  Then 该选项处于**禁用**状态，且旁边有可见说明文字指出依赖未满足
  And 该类型**不得**是自由输入的数字框
  And 后端**不得**接受 `type="volume"` 的手填测量写入 SR
  （或接受但标记为 `manual` 并在列表里与 SEG 体积明确区分）

- **AC-1（端到端可达）** Given 一例已导入的 CT 与一个 `confirmed` 且有像素的 SEG
  When 用户在界面上点击"计算体积"
  Then 界面显示出以 mm³ 与 mL 为单位的数值
  And 该数值来自服务端 `compute_volume` 的返回，**不是用户手填的**
  And 该数值在 `GET /api/v1/cases/{id}/segments` 的返回中可复核

- **AC-2（不可用时必须说清原因）** Given 一个无像素的 SEG
  When 请求体积
  Then 返回 `status != "ok"` 且 `reason` 为四个已知值之一
  （`no_pixel_data` / `fractional_segmentation` / `missing_pixel_spacing` / `pixel_decode_failed`）
  And 界面显示**具体原因**，不是"暂无数据"

- **AC-3（量纲正确）** Given 一个已知体积的合成 mask（如 10×10×10 个 1mm³ 体素）
  When 计算体积
  Then 返回 `volume_mm3 == 1000`（容差 1e-6）
  And `volume_ml == 1.0`
  And `voxel_count == 1000`
  And 返回的 `spacing_mm` **必须**来自 SEG 的 `PixelMeasuresSequence`（契约 §8），
  **不得**来自请求体里的任何值

- **AC-4（其余数值类型的输入侧约束）** Given 测量面板中除“体积”之外的数值类型
  When 用户完成一次测量
  Then 长度 / 角度 / 面积的数值**必须**来自影像交互，**不得**要求用户手填
  And 若为外部测量结果录入保留手填通道，字段标签**必须**写明“手工录入”，
  且该记录**不得**被写成带 `GraphicData` 的测量 SR
  （不能既没有坐标、又长得像影像上量出来的）

**反例验收**

- **⚠ 最重要的一条（架构师指出的绕过路径）** Given `SegmentCreate.seg_content`（`schemas.py:101`）
  与 `AiCallback.seg_content` **都能让 SEG 带上像素**
  When 有人用一个"POST 一段真实 SEG base64 → 体积返回 ok"的测试来证明本卡已完成
  Then 该测试**不得**作为本卡的通过依据
  And 本卡的通过依据**必须**是"用户在界面上勾画 / 确认 → 界面显示体积数值"的端到端证据
  And 任何直接构造 `seg_content` 的 fixture 测试**只能**证明 `compute_volume` 正确，
  **不能**证明产品可用

  理由：写一个"POST 真实 SEG base64 → 体积 ok"的测试**是能通过的**，
  但它不能证明用户在产品里能拿到体积数。这反过来证明了本子类成立——
  **函数级测试拦不住它，API 级测试也拦不住它，只有 UI 级才拦得住。**

- Given 任务清单把"体积计算"标为 completed
  When 从界面完整走一遍
  Then 若拿不到数值，该任务**不得**标记为 completed
  And 验收记录里**必须**有"从 UI 操作得到 X mm³"这一类端到端证据，不接受"函数已实现 + 单测通过"

- Given 任一 SEG
  When 检查其体积返回值
  Then **不得**在无像素时返回 0 或任何估算值
  （`0` 和"算不出来"是两个完全不同的含义，混在一起会让 0 被当成"没有病灶"）

- Given 测量面板的"体积"选项
  When 用户在没有带像素 SEG 的情况下选择它
  Then **不得**允许用户手填一个数字并把它落成 `Modality=SR` 的体积测量记录
  And **不得**在界面上暗示这个数字是系统计算出来的

**一条更深层的方法论（建议长期保留）**

`compute_volume` 的 `unavailable` 分支写得越好，越难发现上游从来不供给像素——
因为四条降级理由让它看起来**处理得很完备**，没人会问"那成功路径什么时候走"。

这和"工具未注册只打 warn"是相反方向：那是"组件静默不工作"，这是"**降级路径太完善，所以没人追问为什么不走成功路径**"。
两种都会让人误判健康度，但第二种更隐蔽，因为它是**用正确性掩盖缺失**。

机械防线（已进 SPEC §10）：每个 `unavailable` / `unknown` reason code 都必须配一条
"它不是唯一结果"的断言 + 可见比率指标。首期 `no_pixel_data` 是 100%，
**这个 100% 本身就是要被盯着的数字**。

**给实现的提示**
这条卡的顺序依赖 TC-06：先让 SEG 真的有像素。**不要在 TC-06 之前动 UI**——
否则会做出一个"点了有反应但结果永远是不可用"的新静默失效。

一个低成本的自检办法：在 TC-06 完成后，用 `scripts/seed_demo.py` 造一个已知体积的球体 mask，
把"体积 == 解析值 ±1%"写成一条契约测试。这条测试同时验证三件事：像素通路、spacing 取值、量纲换算。

**验收人**：PM（端到端可达性，不接受"函数已实现"）+ 架构师（量纲与不可用语义）

---

## 卡与缺口对照

| 卡 | 对应缺口 | 优先级 | 类别 | 架构师把关点 |
|---|---|---|---|---|
| TC-01 | B7 | P0-止血 | 静默失效 | 幂等层与 SPEC AC-19 一致性 |
| TC-02 | B4 | P0-止血 | 静默失效 | 错误码与状态机 |
| TC-03 | B2 | P0-止血 | 静默失效 | 格式契约 / ADR-003 |
| TC-04 | B3 | P0-止血 | 静默失效 | 任务状态机收敛 |
| TC-05 | B5 | P1-可追溯 | 静默失效 | **坐标契约逐字段**（已按 5 条纠正改写） |
| TC-06 | B4 | P1-可追溯 | 功能缺失 | SEG IOD 合规性 |
| TC-07 | §9.2 + 缺口 6/7 | P2-闭环 | 工程门禁 | 门禁与验收标准映射 |
| TC-08 | §9.1 第 12 + 15 项 | P1-可追溯（AC-0 立即执行） | **功能缺失（已实现型）** | 量纲与不可用语义 |

## 未出卡但需登记的项

以下项属于"决策"而非"实现"，出卡为时过早，登记在 `docs/OPEN-DECISIONS.md`（已建）：

| 登记项 | 登记处 | 关闭条件 |
|---|---|---|
| ~~PDF 是否进 MVP~~ | **已裁决→ ADR-003：不进**。退场动作已并入 TC-03 | — |
| ~~`geometry` 命名收敛~~ | **已裁决→ 坐标契约 §3：`patient_lps_mm`**。落地动作已并入 TC-05 AC-5 | — |
| Q-SAFE-DISTANCE 安全距离口径 | OPEN-DECISIONS | 拿到 SEG 真实像素后改算入路轴线到分割表面最小距离 |
| Q-RA-CLASSIFICATION 临床用途合规定性 | OPEN-DECISIONS | 拿到 RA 书面意见并更新 SPEC §3 |
| Q-SEG-PIXELS SEG 像素来源 | OPEN-DECISIONS | 接入任一真实分割来源，`has_pixels=true` 可常态达成 |
| Q-GEOMETRY-ROLLOUT 存量 `geometry` 迁移 | OPEN-DECISIONS（本次新登记） | 写入归一化 + 一次性回填 + 残留 0 行断言进 CI |
| 风险阈值 10mm / 5mm | OPEN-DECISIONS | 临床顾问给值后移除 `[Assumption, TBD]`；**在此之前界面必须有常驻阈值警示（TC-02 AC-阈值可见性）** |
| SPEC 三处失效引用（`StackViewport` / `MeasureOverlay` / `OPEN-DECISIONS`） | OPEN-DECISIONS | SPEC 修订 |
| Orthanc / DICOMweb 分工 ADR | `docs/decisions/` | 补 ADR（ADR-001 已有） |
