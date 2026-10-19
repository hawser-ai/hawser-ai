import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useDeleteEval, useEvalReport, useEvals, usePublishEval } from '@/api/hooks'
import type { EvalReport } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { StatusBadge } from '@/components/StatusBadge'
import { Badge, Card, Empty, ErrorBox, RunLink, Spinner } from '@/components/ui'
import { compareReports } from '@/lib/compare'
import { formatMetric, metricLabel, pct, usd, when } from '@/lib/format'

const HEADLINE = ['field_accuracy', 'doc_pass_rate', 'status_accuracy', 'evidence_coverage', 'review_rate', 'cost_mean_usd', 'latency_p50_ms', 'model_calls_mean']

export function Evals() {
  const evals = useEvals()
  const publish = usePublishEval()
  const remove = useDeleteEval()
  const [baseline, setBaseline] = useState('')
  const [candidate, setCandidate] = useState('')
  return (
    <div>
      <PageHeader
        title="Evaluations"
        subtitle="Reports published with `hawser eval publish` (or dropped here). Pick two to compare."
        actions={
          <label className="btn-secondary cursor-pointer text-xs">
            upload report.json
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0]
                if (!f) return
                publish.mutate({ report: JSON.parse(await f.text()), name: f.name.replace(/\.json$/, '') })
              }}
            />
          </label>
        }
      />
      {publish.error ? <ErrorBox error={publish.error} /> : null}
      {evals.isLoading && <Spinner />}
      {evals.data && !evals.data.length && <Empty>No evaluation reports yet. Run `hawser eval run …` then `hawser eval publish &lt;report&gt; --server …`.</Empty>}
      {evals.data && evals.data.length > 0 && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink-500">compare</span>
            <select className="input w-auto" value={baseline} onChange={(e) => setBaseline(e.target.value)} aria-label="baseline">
              <option value="">baseline…</option>
              {evals.data.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
            <span className="text-ink-500">vs</span>
            <select className="input w-auto" value={candidate} onChange={(e) => setCandidate(e.target.value)} aria-label="candidate">
              <option value="">candidate…</option>
              {evals.data.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
            {baseline && candidate && (
              <Link className="btn-primary text-xs" to={`/evals/compare?baseline=${baseline}&candidate=${candidate}`}>
                compare
              </Link>
            )}
          </div>
          <table className="table card">
            <thead>
              <tr>
                <th>report</th>
                <th>dataset</th>
                <th>skill / mode</th>
                <th>model</th>
                <th>cases</th>
                <th>field acc.</th>
                <th>status acc.</th>
                <th>cost / case</th>
                <th>published</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {evals.data.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link to={`/evals/${e.id}`} className="text-accent-700 hover:underline">
                      {e.name}
                    </Link>
                  </td>
                  <td className="mono text-xs">{e.dataset}</td>
                  <td>
                    {e.skill ?? '—'} / {e.mode ?? '—'}
                  </td>
                  <td className="mono text-xs">{e.model}</td>
                  <td>{e.cases}</td>
                  <td>{pct(e.metrics.field_accuracy)}</td>
                  <td>{pct(e.metrics.status_accuracy)}</td>
                  <td>{usd(e.metrics.cost_mean_usd)}</td>
                  <td className="whitespace-nowrap text-xs">{when(e.created_at)}</td>
                  <td>
                    <button className="btn-ghost text-xs" onClick={() => remove.mutate(e.id)} aria-label={`delete ${e.name}`}>
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

export function EvalDetail() {
  const { id = '' } = useParams()
  const report = useEvalReport(id)
  if (report.isLoading) return <Spinner />
  if (report.error) return <ErrorBox error={report.error} />
  const r = report.data!
  const byTag = r.cases.filter((c) => !c.passed).length
  const chart = r.cases.map((c) => ({ name: c.case_id.replace(/^pair-/, ''), accuracy: c.total ? (100 * c.correct) / c.total : 0, passed: c.passed }))
  return (
    <div>
      <PageHeader
        title={<span className="flex items-center gap-2">Evaluation <span className="mono text-base">{r.id}</span></span>}
        subtitle={`${r.config.dataset} · ${r.config.skill ?? 'no skill'}@${r.config.skill_version ?? ''} · ${r.config.mode ?? 'default'} · ${r.config.model} · ${r.config.record_mode}`}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {HEADLINE.filter((k) => k in r.metrics).map((k) => (
          <div key={k} className="card p-3">
            <div className="text-xs text-ink-500">{metricLabel(k)}</div>
            <div className="text-xl font-semibold text-ink-900">{formatMetric(k, r.metrics[k])}</div>
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card title={`Per-case accuracy (${r.cases.length} cases, ${byTag} failing)`}>
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} unit="%" />
                <Tooltip formatter={(v: number) => `${v.toFixed(1)}%`} />
                <Bar dataKey="accuracy" fill="#3396fc" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <table className="table mt-3">
            <thead>
              <tr>
                <th>case</th>
                <th>status</th>
                <th>expected</th>
                <th>fields</th>
                <th>evidence</th>
                <th>cost</th>
                <th>run</th>
              </tr>
            </thead>
            <tbody>
              {r.cases.map((c) => (
                <tr key={c.case_id} className={c.passed ? '' : 'bg-red-50/40'}>
                  <td className="mono">{c.case_id}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>{c.expected_status ?? '—'}</td>
                  <td>
                    {c.correct}/{c.total}
                  </td>
                  <td>{pct(c.evidence_coverage)}</td>
                  <td>{usd(c.cost_usd)}</td>
                  <td>{c.run_id ? <RunLink id={c.run_id}>open</RunLink> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <div className="space-y-4">
          <Card title="Failure clusters">
            {!r.clusters.length && <p className="text-sm text-emerald-700">No failures.</p>}
            <ul className="space-y-2 text-sm">
              {r.clusters.map((cl, i) => (
                <li key={i}>
                  <span className="mono">{cl.field}</span> <Badge tone="red">{cl.outcome}</Badge> ×{cl.count}
                  <div className="text-xs text-ink-500">{cl.cases.join(', ')}</div>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="All metrics">
            <table className="table">
              <tbody>
                {Object.entries(r.metrics).map(([k, v]) => (
                  <tr key={k}>
                    <td>{metricLabel(k)}</td>
                    <td className="text-right">{formatMetric(k, v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>
    </div>
  )
}

export function EvalCompare() {
  const [params] = useSearchParams()
  const a = useEvalReport(params.get('baseline') ?? undefined)
  const b = useEvalReport(params.get('candidate') ?? undefined)
  const cmp = useMemo(() => (a.data && b.data ? compareReports(a.data as EvalReport, b.data as EvalReport) : null), [a.data, b.data])
  if (a.isLoading || b.isLoading) return <Spinner />
  if (a.error) return <ErrorBox error={a.error} />
  if (b.error) return <ErrorBox error={b.error} />
  if (!cmp || !a.data || !b.data) return <Empty>Pick a baseline and a candidate on the evaluations page.</Empty>
  return (
    <div>
      <PageHeader
        title="Compare"
        subtitle={
          <span>
            baseline <span className="mono">{a.data.config.model}</span> → candidate <span className="mono">{b.data.config.model}</span> · {cmp.regressions.length} regression(s), {cmp.improvements.length} improvement(s)
          </span>
        }
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Metrics">
          <table className="table">
            <thead>
              <tr>
                <th>metric</th>
                <th>baseline</th>
                <th>candidate</th>
                <th>delta</th>
              </tr>
            </thead>
            <tbody>
              {cmp.metrics.map((m) => (
                <tr key={m.key} className={m.verdict === 'worse' ? 'bg-red-50/60' : m.verdict === 'better' ? 'bg-emerald-50/60' : ''} data-verdict={m.verdict}>
                  <td>{metricLabel(m.key)}</td>
                  <td>{formatMetric(m.key, m.baseline)}</td>
                  <td>{formatMetric(m.key, m.candidate)}</td>
                  <td className={m.verdict === 'worse' ? 'text-red-700' : m.verdict === 'better' ? 'text-emerald-700' : 'text-ink-500'}>
                    {m.delta > 0 ? '+' : ''}
                    {formatMetric(m.key, m.delta)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Cases">
          <table className="table">
            <thead>
              <tr>
                <th>case</th>
                <th>baseline</th>
                <th>candidate</th>
                <th>change</th>
              </tr>
            </thead>
            <tbody>
              {cmp.cases.map((c) => (
                <tr key={c.caseId} className={c.kind === 'regression' ? 'bg-red-50/60' : c.kind === 'improvement' ? 'bg-emerald-50/60' : ''} data-kind={c.kind}>
                  <td className="mono">{c.caseId}</td>
                  <td>{c.baseline ? `${pct(c.baseline.accuracy)} · ${c.baseline.status}` : '—'}</td>
                  <td>{c.candidate ? `${pct(c.candidate.accuracy)} · ${c.candidate.status}` : '—'}</td>
                  <td>
                    <Badge tone={c.kind === 'regression' ? 'red' : c.kind === 'improvement' ? 'green' : 'neutral'}>{c.kind}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  )
}
