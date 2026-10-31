# ADR-002 · TotalSegmentator 首期不接入

- **状态**：已接受（并附强制约束）
- **日期**：2026-10-27

## 背景

开源选型原始结论中把 TotalSegmentator 列为"最低落地成本路线"的一环。
但项目首期的范围 Gate 未包含真实分割算法接入。

## 决策

首期**不接入** TotalSegmentator。`/ai/infer` 在未配置 `AI_BASE_URL` 时走本地桩。

## 理由

1. 选型文档给出的是"最低落地成本路线"，不等于首期范围。分割算法接入涉及 GPU 资源、
   模型版本管理、结果置信度呈现，是独立的工作量。
2. 保持桩实现可以让 FR-04 的全链路（投递 → 回调 → suggested 态 → 医师确认）在本地可验证。

## 强制约束（否则此项决策会退化为静默失效）

桩**必须自曝**。具体已落地为：

- `AiClient.mode` 返回 `live` / `stub`；`stub_notice` 给出明确说明。
- `/ai/infer` 与 `/ai/jobs/{id}` 响应都带 `mode` 与 `notice`。
- 前端在 `mode === 'stub'` 时提示"任务不会自动完成，需人工回传结果"，
  而不是报一句"已投递"了事。

**理由**：一个永远停在 `running` 的任务如果对外表现为"投递成功"，使用者会持续等待一个
永远不会到来的结果 —— 这是最典型的静默失效。桩可以有，但不能假装自己是真货。

## 后果

- 没有任何路径能产出带像素的 SEG（见 `docs/OPEN-DECISIONS.md` 的 Q-SEG-PIXELS）。
- 所有依赖 SEG 像素的解剖学计算因此无从进行，风险分级退化为 `unknown`。
  这条链路必须在接入真实分割后重跑。

## 修订记录（2026-10-28）· 首期决策被推翻：接入本地 TotalSegmentator

- **修订人**：项目 owner（2026-10-28 会话）
- **内容**：首期"不接入 TotalSegmentator"的决定**不再成立**。新增本地真实推理路径
  `mode=local`（`app/services/ai_local.py`），在未配置 `AI_BASE_URL` 且
  `AI_LOCAL_INFER=1` 时，`/ai/infer` 直接在本机跑 TotalSegmentator
  （nnU-Net，CPU，--fast），产物走与外部 AI 完全相同的回调链路落成 suggested 态 SEG。
- **原强制约束不变且延伸到 local**：
  - `mode` 现在返回 `live` / `local` / `stub` 三态，`local` 不是 `live`，
    对外必须如实标注"本地 TotalSegmentator 推理（非外部 AI 服务）"；
  - `AI_LOCAL_INFER=1` 但环境缺少 CLI 时，`/ai/infer` **422 当场拒绝**，
    绝不静默退回桩——"投进去但永远不会完成"和"当场失败"必须可区分；
  - 预测为空时 job 照样收敛为 done，但 stats 写明 `EMPTY_PREDICTION` 与 0 像素原因，
    不伪造分割；
  - 模型产物固定 suggested 态 + `clinical_use=false`，必须经医师确认（FR-04 不变）。
- **新增的诚实性条款**：本次接入顺带发现并修复一个存量缺陷——`/ai-results`
  回调从未提交事务，"done" 只存在于响应里，任务在库中永远停在 RUNNING。
  任何依赖回调推进状态的链路（包括未来的外部 AI）都会踩中它。
- **范围注意**：本修订只覆盖"能不能接模型"的决策；权重下载、CPU 推理时长、
  模型版本管理、GPU 资源仍是未解的工程问题（见 CAPABILITY-MATRIX 分割条目）。
