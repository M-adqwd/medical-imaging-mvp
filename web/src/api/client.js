/**
 * API 客户端。严格遵循契约：
 * - 所有写操作自动携带 Idempotency-Key
 * - 错误统一解析业务码（error.code），而非仅看 HTTP 状态
 */

const BASE = '/api/v1'
// FHIR 门面不在 /api/v1 下：FHIR 客户端按资源根路径发现能力（/fhir/metadata）
const FHIR_BASE = '/fhir'

export const state = {
  userId: localStorage.getItem('miis.userId') || 'u-attending',
  roles: localStorage.getItem('miis.roles') || 'attending',
}

export function setIdentity(userId, roles) {
  state.userId = userId
  state.roles = roles
  localStorage.setItem('miis.userId', userId)
  localStorage.setItem('miis.roles', roles)
}

export class ApiError extends Error {
  constructor(payload, status) {
    super(payload?.message || '请求失败')
    this.code = payload?.code || 'UNKNOWN'
    this.details = payload?.details || {}
    this.requestId = payload?.request_id
    this.status = status
  }
}

async function request(method, path, { body, idempotent, query } = {}) {
  const url = new URL(BASE + path, window.location.origin)
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    })
  }

  const headers = {
    'X-User-Id': state.userId,
    'X-User-Roles': state.roles,
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (idempotent) headers['Idempotency-Key'] = crypto.randomUUID()

  const res = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (res.status === 204) return null
  const text = await res.text()
  const data = text ? JSON.parse(text) : null

  if (!res.ok) throw new ApiError(data?.error, res.status)
  return data
}

export const api = {
  // Case
  listCases: (params) => request('GET', '/cases', { query: params }),
  getCase: (id) => request('GET', `/cases/${id}`),
  createCase: (body) => request('POST', '/cases', { body, idempotent: true }),

  // 测量
  listMeasurements: (caseId) => request('GET', `/cases/${caseId}/measurements`),
  createMeasurement: (caseId, body) =>
    request('POST', `/cases/${caseId}/measurements`, { body, idempotent: true }),
  saveMeasurement: (srUid) => request('POST', `/measurements/${srUid}:save`, { idempotent: true }),
  withdrawMeasurement: (srUid) => request('POST', `/measurements/${srUid}:withdraw`, { idempotent: true }),

  // 分割
  listSegments: (caseId) => request('GET', `/cases/${caseId}/segments`),
  // 带像素分割的掩膜原始数据（2D labelmap 叠加用）
  segmentMask: (segUid) => request('GET', `/segments/${segUid}/mask`),
  // 分割外表面三角网格（3D 器官网格用）
  segmentMesh: (segUid, step = 2) =>
    request('GET', `/segments/${segUid}/mesh`, { query: { step } }),
  createSegment: (caseId, body) =>
    request('POST', `/cases/${caseId}/segments`, { body, idempotent: true }),
  confirmSegment: (segUid) => request('POST', `/segments/${segUid}:confirm`, { idempotent: true }),
  reviseSegment: (segUid) => request('POST', `/segments/${segUid}:revise`, { idempotent: true }),
  mergeSegments: (caseId, body) =>
    request('POST', `/cases/${caseId}/segments/merge`, { body, idempotent: true }),
  // 种子点区域生长：本地唯一能产出带像素 SEG 的路径
  growSegment: (caseId, body) =>
    request('POST', `/cases/${caseId}/segments/grow`, { body, idempotent: true }),
  // 掩膜编辑：裁切 / 平滑都产出新的 draft 分割，源分割不动
  cropSegment: (segUid, body) =>
    request('POST', `/segments/${segUid}:crop`, { body, idempotent: true }),
  smoothSegment: (segUid, body) =>
    request('POST', `/segments/${segUid}:smooth`, { body, idempotent: true }),

  // 规划
  listPlans: (caseId) => request('GET', `/cases/${caseId}/plans`),
  createPlan: (caseId, body) => request('POST', `/cases/${caseId}/plans`, { body, idempotent: true }),
  savePlan: (planId) => request('POST', `/plans/${planId}:save`, { idempotent: true }),
  updatePlan: (planId, body) => request('PUT', `/plans/${planId}`, { body, idempotent: true }),

  // 报告
  listReports: (caseId) => request('GET', `/cases/${caseId}/reports`),
  createReport: (caseId, body) => request('POST', `/cases/${caseId}/reports`, { body, idempotent: true }),
  submitReport: (reportId) => request('POST', `/reports/${reportId}:submit`, { idempotent: true }),
  lockReport: (reportId) => request('POST', `/reports/${reportId}:lock`, { idempotent: true }),

  // 共享
  listShares: (caseId) => request('GET', `/cases/${caseId}/shares`),
  createShare: (caseId, body) => request('POST', `/cases/${caseId}/shares`, { body, idempotent: true }),
  revokeShare: (shareId) => request('POST', `/shares/${shareId}:revoke`, { idempotent: true }),
  accessShare: (shareId, accessCode) =>
    request('GET', `/shares/${shareId}/access`, { query: { access_code: accessCode } }),
  listComments: (caseId, shareId) =>
    request('GET', `/cases/${caseId}/comments`, { query: shareId ? { share_id: shareId } : {} }),

  // AI
  triggerInference: (studyUid, targets) =>
    request('POST', '/ai/infer', {
      idempotent: true,
      query: { study_instance_uid: studyUid, targets: (targets || []).join(',') },
    }),
  aiJobStatus: (jobId) => request('GET', `/ai/jobs/${jobId}`),
}
/**
 * FHIR 门面的 GET。
 *
 * 错误体是 OperationOutcome 而不是契约 §4 的 `{error:{code}}` ——
 * 这不是后端不一致，而是 FHIR 客户端（HAPI / Firely / 院方集成引擎）
 * 只认 OperationOutcome。这里按 issue[0].diagnostics 取文案，
 * 否则界面上会显示成"请求失败"，排查时看不出是 404 还是没权限。
 */
export async function fhirGet(path) {
  const url = new URL(FHIR_BASE + path, window.location.origin)
  const res = await fetch(url, {
    headers: { 'X-User-Id': state.userId, 'X-User-Roles': state.roles },
  })
  // 代理缺失时 /fhir 会落进 SPA fallback 返回 index.html（HTTP 200）。
  // 若在这里静默解析成 {}，面板会显示"患者 — / 检查 0 项"，比报错更具误导性，
  // 所以先校验 content-type 再解析。
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('fhir+json')) {
    throw new ApiError(
      { code: 'FHIR_UNAVAILABLE', message: 'FHIR 门面不可用：/fhir 未代理到后端，或后端未启用' },
      503,
    )
  }
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) {
    const issue = payload?.issue?.[0]
    throw new ApiError(
      {
        code: (issue?.code || 'fhir_error').toUpperCase(),
        message: issue?.diagnostics || `FHIR 请求失败（HTTP ${res.status}）`,
      },
      res.status,
    )
  }
  return payload
}

export const fhirUrl = (path) => FHIR_BASE + path
