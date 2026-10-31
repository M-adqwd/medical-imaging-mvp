import { useCallback, useEffect, useState } from 'react'
import { NavLink, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, setIdentity, state } from './api/client'
import CaseWorkspace from './pages/CaseWorkspace'
import SharedCase from './pages/SharedCase'
import { caseStatusLabel } from './lib/labels'

const ROLES = [
  { id: 'attending', label: '主读医师' },
  { id: 'planner', label: '规划医师' },
  { id: 'reviewer', label: '审阅者' },
  { id: 'resident', label: '规培医师' },
  { id: 'admin', label: '管理员' },
]

function IdentitySwitcher() {
  const [, force] = useState(0)
  return (
    <div className="identity">
      <span>身份</span>
      <select
        value={state.userId}
        onChange={(e) => {
          const uid = e.target.value
          const role = ROLES.find((r) => r.id === uid.split('-')[1])?.id || 'attending'
          setIdentity(uid, role)
          force((n) => n + 1)
        }}
      >
        {ROLES.map((r) => (
          <option key={r.id} value={`u-${r.id}`}>
            {r.label}
          </option>
        ))}
      </select>
    </div>
  )
}

function CaseList({ selected, onSelect, reloadKey }) {
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.listCases({ size: 50 })
      setCases(data.items)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, reloadKey])

  return (
    <aside className="sidebar">
      <h4>我的病例 · {cases.length}</h4>
      <div className="list">
        {loading && <div className="empty">加载中…</div>}
        {!loading && cases.length === 0 && (
          <div className="empty">
            暂无病例
            <br />
            <span style={{ fontSize: 11 }}>用 PACS 检索或上传 DICOM 后自动创建</span>
          </div>
        )}
        {cases.map((c) => (
          <div
            key={c.case_id}
            className={`case-item ${selected === c.case_id ? 'active' : ''}`}
            onClick={() => onSelect(c.case_id)}
          >
            <div className="t">{c.title || c.patient_ref}</div>
            <div className="m">
              {c.modality || '—'} · {c.study_date || '—'} · {caseStatusLabel(c.status)}
            </div>
          </div>
        ))}
      </div>
    </aside>
  )
}

/** 首页：全宽病例卡片网格，不再把病例全塞在左侧一条窄栏里 */
function HomeMain({ reloadKey }) {
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.listCases({ size: 50 })
      setCases(data.items)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, reloadKey])

  const kw = query.trim().toLowerCase()
  const shown = kw
    ? cases.filter((c) =>
        [c.title, c.patient_ref, c.modality].some((v) => (v || '').toLowerCase().includes(kw)),
      )
    : cases

  return (
    <>
      <div className="case-head">
        <h1>病例工作台</h1>
        <p className="hint">
          选择一个病例开始阅片
          <span className="badge saved">
            {state.userId} / {state.roles}
          </span>
        </p>
      </div>
      <div className="case-body">
        {/* 统计摘要：先给整体规模感，再进卡片 */}
        <div className="stat-strip">
          <div className="stat">
            <b>{cases.length}</b>
            <span>病例总数</span>
          </div>
          <div className="stat">
            <b>{cases.filter((c) => c.status === 'open').length}</b>
            <span>进行中</span>
          </div>
          <div className="stat">
            <b>{cases.reduce((n, c) => n + (c.studies?.length || 0), 0)}</b>
            <span>关联检查</span>
          </div>
        </div>

        <div className="search-row">
          <input
            placeholder="按患者名 / ID / 标题搜索（FR-01）"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {loading && <div className="empty">加载中…</div>}
        {!loading && shown.length === 0 && (
          <div className="empty">
            {kw ? '没有匹配的病例' : '暂无病例'}
            <br />
            <span style={{ fontSize: 11 }}>用 PACS 检索或上传 DICOM 后自动创建</span>
          </div>
        )}

        <div className="case-grid">
          {shown.map((c) => (
            <div
              key={c.case_id}
              className="case-card"
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/cases/${c.case_id}`)}
              onKeyDown={(e) => e.key === 'Enter' && navigate(`/cases/${c.case_id}`)}
            >
              <div className="cc-head">
                <span className={`badge ${c.status}`}>{caseStatusLabel(c.status)}</span>
                <span className="cc-mod">{c.modality || '—'}</span>
              </div>
              <div className="cc-title">{c.title || c.patient_ref}</div>
              <div className="cc-meta mono">{c.patient_ref}</div>
              <div className="cc-foot">
                <span>{c.studies?.length ? `${c.studies.length} 个检查` : '未关联检查'}</span>
                <span className="cc-open">打开 →</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

export default function App() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const [reloadKey, setReloadKey] = useState(0)

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          MIIS<span>·</span>阅片与手术规划
        </div>
        <nav>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
            病例
          </NavLink>
        </nav>
        <div className="spacer" />
        <IdentitySwitcher />
      </header>

      <Routes>
        {/* 后端 share_created 生成的就是 <base>/s/{share_id}，必须有对应落地页 */}
        <Route path="/s/:shareId" element={<SharedCase />} />
        <Route
          path="/"
          element={
            <main className="main">
              <HomeMain reloadKey={reloadKey} />
            </main>
          }
        />
        <Route
          path="/cases/:caseId"
          element={
            <div className="layout">
              <CaseList
                selected={caseId}
                onSelect={(id) => navigate(`/cases/${id}`)}
                reloadKey={reloadKey}
              />
              <main className="main">
                <CaseWorkspace onChanged={() => setReloadKey((n) => n + 1)} />
              </main>
            </div>
          }
        />
      </Routes>
    </div>
  )
}

export { ApiError }