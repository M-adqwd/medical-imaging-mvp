/**
 * 状态 / 风险 / 权限 / 计数等**枚举值**的展示层映射。
 *
 * 为什么不直接显示接口原值：这些值在界面上一律出现在 badge、表格列里，
 * 医生看到的是「draft / confirmed / low」这类英文码，理解成本高且容易误读
 * （draft 与 suggested 的区别只有做过分级的人才知道）。
 *
 * 约定：
 *   - **只改展示文案，不改任何取值**。CSS 类名（`badge ${state}`、
 *     `risk-${level}`）仍用原始英文码，避免样式与数据耦合。
 *   - 未知值原样返回。宁可露出原码，也不要吞成「未知」——真出现新状态时
 *     需要一眼看见，而不是被兜底掩盖。
 *   - CT / MR / MPR / VR / SEG / SR / HU / DICOM 这类国际通用缩写不做翻译。
 */

/** 业务对象状态（SR / SEG / Plan / Report / Share 共用一套码） */
export const STATE_LABELS = {
  draft: '草稿',
  suggested: '建议',
  confirmed: '已确认',
  saved: '已保存',
  submitted: '已提交',
  locked: '已锁定',
  exported: '已导出',
  withdrawn: '已撤回',
  revoked: '已撤销',
  active: '生效',
}

export const stateLabel = (state) => STATE_LABELS[state] || state

/** 病例本身的状态（不是对象状态机） */
export const CASE_STATUS_LABELS = {
  open: '进行中',
  closed: '已关闭',
  archived: '已归档',
}

export const caseStatusLabel = (status) => CASE_STATUS_LABELS[status] || status

/** 手术规划风险分级（risk_level 取值来自服务端 risk_from_distance） */
export const RISK_LABELS = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  unknown: '未分级',
}

export const riskLabel = (level) => RISK_LABELS[level] || level

/** 共享链接权限 */
export const PERMISSION_LABELS = {
  'readonly+comment': '只读 + 可评论',
  readonly: '只读',
  comment: '可评论',
}

export const permissionLabel = (permission) => PERMISSION_LABELS[permission] || permission

/** 病例对象统计的键名 */
export const COUNT_LABELS = {
  measurements: '测量',
  segments: '分割',
  plans: '规划',
  reports: '报告',
  shares: '共享',
  comments: '评论',
}

export const countLabel = (key) => COUNT_LABELS[key] || key
