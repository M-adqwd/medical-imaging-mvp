import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { permissionLabel } from '../lib/labels'

/**
 * 共享链接落地页 /s/:shareId（契约 §3.6）。
 *
 * 后端 share_created 生成的就是 <base>/s/{share_id} 这条 URL，
 * 此前前端没有对应路由，链接点开后落回病例列表 —— 分享功能形同虚设。
 * 本页补上这一环：收件人填访问码即可查看被共享的病例。
 */
export default function SharedCase() {
  const { shareId } = useParams()
  const [code, setCode] = useState('')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = useCallback(
    async (e) => {
      e?.preventDefault()
      setBusy(true)
      setError(null)
      try {
        const r = await api.accessShare(shareId, code.trim())
        setData(r)
      } catch (err) {
        setData(null)
        setError(err)
      } finally {
        setBusy(false)
      }
    },
    [shareId, code],
  )

  useEffect(() => {
    if (!shareId) setError(new ApiError({ code: 'NOT_FOUND', message: '缺少 shareId' }))
  }, [shareId])

  const status = error?.status ?? error?.response?.status
  const code_ = error?.code || error?.body?.error?.code

  return (
    <div className="layout">
      <main className="main">
        <div className="case-head">
          <h1>共享病例</h1>
          <p className="hint">
            该链接由病例负责人生成，需访问码才能查看
            <span className="badge draft mono">{shareId}</span>
          </p>
        </div>

        <div className="case-body">
          {!data && (
            <div className="card">
              <div className="section-title" style={{ marginTop: 0 }}>
                输入访问码
              </div>
              <form className="row" onSubmit={submit}>
                <input
                  className="grow mono"
                  autoFocus
                  placeholder="8 位访问码"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
                <button disabled={busy || !code.trim()}>查看</button>
              </form>
              {error && (
                <div className="hint warn" style={{ marginTop: 10 }}>
                  {status === 401 && '访问码错误'}
                  {status === 410 && '链接已过期或已被撤销'}
                  {status !== 401 && status !== 410 && (code_ || error.message || '访问失败')}
                </div>
              )}
            </div>
          )}

          {data && (
            <>
              <div className="card">
                <div className="section-title" style={{ marginTop: 0 }}>
                  {data.case?.title || data.case?.patient_ref || '共享病例'}
                </div>
                <p style={{ margin: 0, fontSize: 13, color: 'var(--muted-foreground)', lineHeight: 1.8 }}>
                  {data.case?.modality || '—'} · {data.case?.study_date || '—'} ·{' '}
                  <span className="mono">{permissionLabel(data.share?.permission)}</span>
                  <br />
                  有效期至 <span className="mono">{data.share?.expires_at}</span> · 已访问{' '}
                  {data.share?.view_count} 次
                </p>
              </div>

              <div className="card">
                <div className="section-title" style={{ marginTop: 0 }}>
                  共享范围
                </div>
                <p style={{ margin: 0, fontSize: 13, color: 'var(--muted-foreground)' }}>
                  scope <span className="mono">{data.share?.scope}</span>
                  {data.share?.scope_refs?.length > 0 && (
                    <>
                      {' · '}
                      refs <span className="mono">{JSON.stringify(data.share.scope_refs)}</span>
                    </>
                  )}
                </p>
                <button style={{ marginTop: 12 }} onClick={() => setData(null)}>
                  退出查看
                </button>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
