# 未决决策登记（Open Decisions）

规范：本文件**只追加**，不删除；关闭一条就在原地写 `状态: 已关闭` + 关闭依据 + 日期。
每条必须写明 `Resolves when`，否则它没有收敛路径。

---

## Q-SAFE-DISTANCE · 安全距离口径

- **状态**：**已关闭**（见下方关闭记录）
- **登记时间**：2026-10-27
- **问题**：`safe_distance` 当前取的是入点到靶点的**直线距离**，与解剖结构无关。
  但它的名字和 `risk_level` 的分级阈值（10 / 5 mm）都会让人读成"距危险结构的距离"。
- **影响**：一条 180mm 的长穿刺路径会被判成 `low` 风险。这正是最危险的失效形态 ——
  不是算错，而是**算了一个根本不是那个量的东西**，还给出了一个绿色的安全结论。
- **当前止血**：底层 SEG 无像素数据时 `risk_level = unknown` 并附 `risk_basis` 说明，
  界面显示"未分级"而非绿色 `low`。SEG 有像素时仍按入路长度分级（口径未变）。
- **Resolves when**：拿到 SEG 的真实像素后，改为计算入路轴线到分割外表面的最小距离
  （可用 `scipy.ndimage.distance_transform_edt`），并把阈值换成临床口径。届时
  `risk_basis` 同步改为"按入路到分割表面最小距离分级"。

### 关闭记录（2026-10-28）

- **状态**：已关闭。
- **落地**：`server/app/dicom/mask.py` 的 `min_distance_to_segment()` 按入路轴线到
  分割外表面体素中心的最小距离计算（未用 scipy，改为向量化点到线段距离，语义一致）；
  `Plan.anatomical_distance_mm` 单独存列，`PlanOut` 分开暴露，前端表格分两列显示。
  入路长度（`safe_distance`）与解剖距离不再混用。`risk_basis` 同步改为
  "按入路轴线到分割外表面的最小距离分级"。
- **两条例外，均显式 UNKNOWN 而非给值**：
  1. 靶点位于分割内（分割即靶区）→ "到分割表面距离"不构成风险判据，方向会反过来；
  2. 分割缺少空间定位（PixelMeasures / PlanePosition 缺失）→ 无法定位到患者坐标。
- **未随本条关闭**：阈值本身仍是暂定值，见 Q-RISK-THRESHOLD（保持开放）。
- **验收**：`tests/test_seg_pixels.py::test_long_path_near_structure_is_not_low_risk`
  （180mm 入路贴结构 1mm → high，旧实现会给出 low）、
  `test_risk_uses_anatomical_distance`、`test_target_inside_seg_is_not_graded`。

## Q-RA-CLASSIFICATION · 临床用途的合规定性

- **状态**：开放（**PRD §07 Q2 明写"此结论需 RA 书面意见"，目前没有落地证据**）
- **登记时间**：2026-10-27
- **问题**：PRD 说"先按科研/教学用途立项，此结论需 RA 书面意见"= 未决；
  SPEC §3 却直接列入「明确不做」表，中间缺一个 RA 意见的落点。
- **影响**：可行性报告 §4.1 把 R2（器械属性判定）定为最高风险。
  PRD §3.2 Scope Gate 明写"首期是否包含分割/规划的完整路径，取决于 RA 对临床用途的判定"——
  **若判定为临床诊断用途，当前被列为 P2 的缺口会直接升为 P0 必选集**。
- **Resolves when**：RA 出具书面意见并归档到本条下。在此之前，任何"不属于医疗器械"的
  对外表述都必须附带本条链接。

## Q-GEOMETRY-NAMING · 坐标系命名收敛

- **状态**：**已关闭**
- **登记时间**：2026-10-27
- **问题**：前端曾使用 `geometry: 'volume_world'`，现改为 `frame_of_reference`
  （DICOM SCOORD3D 的口径）。但后端 `coord_ref` 是自由字典，没有 schema 约束，
  两种命名可能在存量数据里并存。
- **Resolves when**：`coord_ref` 收成显式 schema（`geometry` 取值枚举 + `graphic_data`
  必填校验），并对存量记录做一次迁移说明。

### 关闭记录（2026-10-27，关闭人：许清楚）

- **裁决**：规范值定为 **`patient_lps_mm`**。`volume_world` 与 `frame_of_reference`
  均废弃。裁决人：架构师（高见远）。
- **依据**：`docs/COORDINATE-CONTRACT.md` §3。选它的理由是这个词把三个维度都编码进去了——
  `patient` 说明不是体素 / 像素索引，`lps` 说明手性，`mm` 说明单位；
  而 `frame_of_reference` 只说了"有个坐标系"，没说单位和手性。
- **本条关闭的是"取值该叫什么"这一个决策**，不是"存量数据已迁移完"。
  迁移是执行项，已单独登记为 **Q-GEOMETRY-ROLLOUT**（见下）。
  拆开的原因是：决策关闭后实现方才能动手；若把两者绑在一条里，
  这条会在"决策已定但还没改完"的窗口期一直显示为开放，读者无法区分
  "还在争论"还是"已经定了只是没做完"。
- **对下游的约束**：坐标契约 §7.1 / §4.1 / §6.1 另有 **7 条** SR 落地纠正（C1–C5 首轮，
  C6–C7 为 14:39 修订追加），已同步进 `docs/TASK-CARDS.md` 的 TC-05。改 SR 时以 TC-05 现行版为准。
  其中 C6 附带一条方法论约束：**"某字段运行时恒为空"必须靠运行时观测证明，不能靠源码 grep 推断**
  （实例：`FrameOfReferenceUID` 大写 F、赋值走 `...viewReference` 展开，grep 必然漏）。
  这条已进 SPEC §10。

## Q-SEG-PIXELS · SEG 像素来源

- **状态**：**已关闭**（见下方关闭记录）
- **登记时间**：2026-10-27
- **问题**：`build_seg_header()` 只产出元数据骨架（无 `Rows/Columns/PixelData`）。
  TotalSegmentator 按选型结论不在首期范围，因此**当前没有任何路径能产出带像素的 SEG**。
- **影响**：SEG 是"分割的唯一事实源"（契约 §1.4），但它现在是空壳。所有依赖 SEG 像素
  的解剖学计算（安全距离、体积、风险分级）都无从计算 —— 这正是 Q-SAFE-DISTANCE 的根因。
- **Resolves when**：接入任一真实分割来源（TotalSegmentator / MONAI / 医师手工勾画
  写入像素），并让 `has_pixels=true` 成为可常态达成的状态。

### 关闭记录（2026-10-28）

- **状态**：已关闭。
- **落地**：新增本地分割来源 `POST /api/v1/cases/{id}/segments/grow`（种子点区域生长，
  `server/app/services/segmentation.py`），产出带 PixelData 与完整空间定位
  （PixelMeasures / PlaneOrientation / PlanePosition / DerivationImage）的 BINARY SEG
  （`server/app/dicom/mask.py::build_seg_from_mask`）。界面入口在分割面板
  「种子点生长」，UI 门禁 G13 断言"从界面点击到表格出现 mL"。
- **口径自曝**：SEG 私有块记 `clinical_use=NO` + 固定 notice（算法生成、未经医师核准），
  `SegmentAlgorithmType=SEMIAUTOMATIC`；参数（HU 窗、迭代数、是否顶到生长窗口边界）
  全部回传，`touches_window_edge=true` 时体积可能被窗口截断，这一状态对调用方可见。
- **顺带修复**：中文写入 DICOM 默认字符集（ISO-IR 100）会变成问号，
  `build_seg_header` 与 `build_seg_from_mask` 均已显式声明 `SpecificCharacterSet=ISO_IR 192`。
- **仍开放的部分（不回退本条）**：TotalSegmentator / MONAI 与医师手工勾画
  （TC-06）是后续增强；AI 桩模式下 `/ai/infer` 仍不会自动产出结果（这是桩的诚实行为，不是缺陷）。

---

## Q-RISK-FALLBACK · 风险分级的"输入缺失"兜底

- **状态**：**已关闭**（见下方关闭记录）
- **登记时间**：2026-10-27
- **登记人**：许清楚（PM）；发现人：高见远（架构师）
- **问题**：`server/app/services/plans.py:67-68`

  ```python
  if safe_distance is None:
      return RiskLevel.MEDIUM
  ```

  **输入缺失时返回一个具体等级。** 调用方拿到 `MEDIUM` 会当成"评估过了，中等风险"，
  实际是"没数据，但默认中等"。这和坐标契约 §9 的 X1（坐标系缺失时按默认值处理）是同一类行为，
  而且落在风险分级上更危险。
- **当前严重性**：**潜伏，非现网缺陷。** `safe_distance` 在实际路径上不会是 `None`
  （`distance()` 拿到合法点必返回浮点）。但正是这种"现在不会触发所以没人管"的分支，
  会在半年后某个改动让它可达时一次性变成真缺陷。
- **为什么与 Q-SAFE-DISTANCE 分开登记**：两者在同一条口径链上，但**关闭动作不同**——
  Q-SAFE-DISTANCE 是"改算法"（直线距离 → 到分割表面最小距离），
  这一条是"改兜底"（`MEDIUM` → `UNKNOWN` + `risk_basis` 说明）。
  绑在一条里会导致其中一个做完后另一个被误认为已解决。
- **Resolves when**：`safe_distance is None` 时返回 `UNKNOWN`，且 `risk_basis` 写明
  "安全距离不可得"；并补一条契约测试覆盖该分支。

### 关闭记录（2026-10-28）

- **状态**：已关闭。
- **落地**：`risk_from_distance(anatomical_distance_mm)` 重构为单参数——输入缺失
  一律 `UNKNOWN`，`MEDIUM` 兜底分支删除。原"潜伏"分支随 Q-SAFE-DISTANCE 的口径
  改造一起消失：现在 `None` 是常态可达的合法输入（无像素 / 无空间定位 / 靶点在分割内），
  每种原因都写进 `risk_basis`。
- **测试**：`tests/test_seg_pixels.py::test_target_inside_seg_is_not_graded`
  覆盖 `None → unknown` 分支（含 `anatomical_distance_mm is None` 断言）。

## Q-GEOMETRY-ROLLOUT · 存量 `geometry` 值迁移

- **状态**：开放
- **登记时间**：2026-10-27
- **登记原因**：Q-GEOMETRY-NAMING 关闭时拆出的执行项。命名已定为 `patient_lps_mm`，
  但存量 `measurements.coord_ref` 里 `volume_world` / `frame_of_reference` 仍在库里，
  `coord_ref` 也还是自由字典（`schemas.py:64`），没有 schema 兜底。
- **影响**：不迁移的话，同一张表里会出现三种写法描述同一个坐标系，
  任何按 `geometry` 做分支的代码都会漏掉其中两种，且不报错。
- **执行路径**（坐标契约 §3）：
  1. 写入时归一化：入口层把 `frame_of_reference` / `volume_world` 映射成 `patient_lps_mm`
  2. 一次性回填（SQL 见坐标契约 §3）
  3. 回填后跑残留断言，残留**必须为 0 行**
  4. 其他值一律 422，**不做"猜一个默认值"的兜底**
- **Resolves when**：`coord_ref` 收成显式 schema（`geometry` 枚举 + `graphic_data` 必填校验），
  回填完成，且残留 0 行断言进入 CI。

## Q-RISK-THRESHOLD · 风险分级阈值未定

- **状态**：开放
- **登记时间**：2026-10-27
- **问题**：`SAFE_DISTANCE_LOW_MM=10` / `SAFE_DISTANCE_MEDIUM_MM=5`（`plans.py:26-27`）
  标着 `[Assumption, TBD]`，无临床来源。且当前 `safe_distance` 取的是入点到靶点的直线距离，
  与解剖结构无关（见 Q-SAFE-DISTANCE）。
- **对外表述约束（已定，不随阈值变化）**：在临床顾问给值之前，界面与文档一律写
  **「待临床确认的暂定阈值」**。
  不用"实验性阈值"—— 那个说法读起来是"能用，只是精度待验"，会引导使用者照常采信；
  真实情况是"口径本身未定"，应当引导使用者去确认。两个词指向相反的行动。
- **落地要求（已被 `threshold_status` 取代早期方案，以本条为准）**：
  早期想法是"把措辞写进 `risk_basis`，前端照着渲染"。核实现后发现**这个方案不成立**——
  前端 `CaseWorkspace.jsx:629-640` 只在 `risk_level === 'unknown'` 时才把 `risk_basis`
  渲染成可见块，其他等级下它只是 `title` 属性（悬停才可见）。
  也就是**恰好在用户最可能采信的那三个等级上，警示完全不可见**。

  架构师已裁定改用结构化字段，**分支依据不再依赖文案**：

  ```
  PlanOut.threshold_status: "provisional" | "clinically_confirmed"   默认 "provisional"
  ```

  - 前端**只准**看该字段，**禁止**对 `risk_basis` 做任何子串匹配
  - `provisional` 时，**任何 `risk_level`（含 low / medium / high）都必须在常驻可见位置显示警示**
  - 事实（是否暂定）归服务端，措辞归前端；服务端不返回文案字段
  - 用枚举不用布尔：布尔可被静默翻转且不留"谁确认的、凭什么确认的"痕迹；
    枚举选 `clinically_confirmed` 会逼出"谁确认的"——答案正是 Q-RA-CLASSIFICATION 等的那份 RA 意见
  - `risk_basis` 仍保留该措辞作为人类可读解释，但**不承载分支语义**
- **由本条上升的通用规则**：**结论越像可直接采信的判定，它"前提未确认"的警示必须越显眼。**
  反向实现即缺陷（`unknown` 最显眼、`high` 最不显眼）。
- **Resolves when**：临床顾问给出阈值与口径并写入配置；届时 `threshold_status` 可置为
  `clinically_confirmed`（前提是已归档 RA 书面意见），移除 `[Assumption, TBD]` 标记，
  本条关闭。

---

## 已关闭

### Q-GEOMETRY-NAMING · 坐标系命名收敛

- 关闭日期：2026-10-27　关闭人：许清楚（PM）
- 裁决：`geometry` 规范值 = **`patient_lps_mm`**
- 依据：`docs/COORDINATE-CONTRACT.md` §3（架构师出）
- 备注：执行项已拆到 Q-GEOMETRY-ROLLOUT，仍开放。
