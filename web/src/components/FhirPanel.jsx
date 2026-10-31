import { useEffect, useState } from 'react'
import { fhirGet } from '../api/client'

/**
 * FHIR R4 只读门面（嵌在工作台「文档与产出」标签面板内）。
 *
 * 为什么不做「新窗口打开 /fhir/Patient/xxx」：浏览器直接跳转不会带
 * X-User-Id / X-User-Roles，网关鉴权会 401，用户看到一屏 OperationOutcome
 * 会以为是坏页面。所以 JSON 一律在这里按需拉取、内联展示。
 *
 * 布局：左右分栏（doc-grid）。左列资源概览 + 取数按钮；右列 JSON 预览。
 */
export default function FhirPanel({ patientRef }) {
  const [view, setView] = useState({ status: 'loading' })
  const [raw, setRaw] = useState(null)

  useEffect(() => {
    if (!patientRef) return undefined
    let alive = true
    setView({ status: 'loading' })
    setRaw(null)

    Promise.all([
      fhirGet(`/Patient?identifier=${encodeURIComponent(patientRef)}`),
      fhirGet(`/ImagingStudy?patient=${encodeURIComponent(patientRef)}`),
      fhirGet(`/DiagnosticReport?patient=${encodeURIComponent(patientRef)}`),
    ])
      .then(([patients, studies, reports]) => {
        if (!alive) return
        setView({
          status: 'ok',
          patient: patients.entry?.[0]?.resource || null,
          studies: studies.total || 0,
          reports: reports.total || 0,
        })
      })
      .catch((err) => {
        if (alive) setView({ status: 'error', message: err.message })
      })

    return () => {
      alive = false
    }
  }, [patientRef])

  const preview = async (path, label) => {
    try {
      const data = await fhirGet(path)
      setRaw({ label, text: JSON.stringify(data, null, 2), failed: false })
    } catch (err) {
      setRaw({ label, text: err.message, failed: true })
    }
  }

  return (
    <div className="doc-grid">
      <div className="doc-side">
        {view.status === 'loading' && <p className="hint">正在读取互操作视图…</p>}

        {view.status === 'error' && <p className="hint warn">不可用：{view.message}</p>}

        {view.status === 'ok' && (
          <>
            <table className="kv-tbl">
              <tbody>
                <tr>
                  <td>患者</td>
                  <td className="mono">{view.patient?.id || '—'}</td>
                </tr>
                <tr>
                  <td>影像检查</td>
                  <td className="mono">{view.studies} 项</td>
                </tr>
                <tr>
                  <td>诊断报告</td>
                  <td className="mono">{view.reports} 份</td>
                </tr>
              </tbody>
            </table>

            <div className="edit-row">
              <button
                type="button"
                onClick={() =>
                  setRaw({ label: 'Patient', text: JSON.stringify(view.patient, null, 2), failed: false })
                }
                disabled={!view.patient}
              >
                患者 JSON
              </button>
              <button
                type="button"
                onClick={() =>
                  preview('/ImagingStudy?patient=' + encodeURIComponent(patientRef), 'ImagingStudy Bundle')
                }
              >
                检查 Bundle
              </button>
              <button type="button" onClick={() => preview('/metadata', 'CapabilityStatement')}>
                能力声明
              </button>
            </div>

            <p className="hint">
              只读门面：暴露 Patient / ImagingStudy / DiagnosticReport / Observation / Endpoint，
              不提供写入。资源 ID 为不透明哈希，原始 MRN 与 DICOM UID 只出现在 identifier 里。
            </p>
          </>
        )}
      </div>

      <div className="doc-main">
        {view.status === 'ok' &&
          (raw ? (
            <>
              <div className={`doc-main-head ${raw.failed ? 'warn' : ''}`}>
                {raw.label}
                {raw.failed ? ' · 请求失败' : ''}
              </div>
              <pre className="fhir-pre mono">{raw.text}</pre>
            </>
          ) : (
            <div className="doc-empty">在左列选择要查看的 FHIR 资源，原始 JSON 会显示在这里</div>
          ))}
      </div>
    </div>
  )
}
