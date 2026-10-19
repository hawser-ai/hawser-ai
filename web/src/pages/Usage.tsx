import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useUsageBreakdown } from '@/api/hooks'
import type { UsageBucket } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { StatusBadge } from '@/components/StatusBadge'
import { Card, ErrorBox, RunLink, Spinner } from '@/components/ui'
import { usd, when } from '@/lib/format'

function Buckets({ title, rows }: { title: string; rows: UsageBucket[] }) {
  return (
    <Card title={title}>
      <table className="table">
        <thead>
          <tr>
            <th>key</th>
            <th>runs</th>
            <th>calls</th>
            <th>tokens</th>
            <th>cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.key}>
              <td className="mono text-xs">{b.key}</td>
              <td>{b.runs}</td>
              <td>{b.model_calls}</td>
              <td>{(b.input_tokens + b.output_tokens).toLocaleString()}</td>
              <td>{usd(b.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

export function Usage() {
  const [since, setSince] = useState('')
  const usage = useUsageBreakdown(since || undefined)
  if (usage.error) return <ErrorBox error={usage.error} />
  const d = usage.data
  const total = d?.by_status.reduce((s, b) => s + b.cost_usd, 0) ?? 0
  return (
    <div>
      <PageHeader
        title="Usage"
        subtitle="Cost and tokens by day, skill, model and status; budget consumption per run."
        actions={<input type="date" className="input w-auto" value={since} onChange={(e) => setSince(e.target.value)} aria-label="since" />}
      />
      {!d && <Spinner />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="card p-3">
              <div className="text-xs text-ink-500">Runs</div>
              <div className="text-xl font-semibold">{d.runs.length}</div>
            </div>
            <div className="card p-3">
              <div className="text-xs text-ink-500">Model calls</div>
              <div className="text-xl font-semibold">{d.by_status.reduce((s, b) => s + b.model_calls, 0)}</div>
            </div>
            <div className="card p-3">
              <div className="text-xs text-ink-500">Tokens</div>
              <div className="text-xl font-semibold">{d.by_status.reduce((s, b) => s + b.input_tokens + b.output_tokens, 0).toLocaleString()}</div>
            </div>
            <div className="card p-3">
              <div className="text-xs text-ink-500">Cost</div>
              <div className="text-xl font-semibold">{usd(total)}</div>
            </div>
          </div>
          <Card title="Cost by day" className="mb-4">
            <div style={{ height: 200 }}>
              <ResponsiveContainer>
                <BarChart data={d.by_day.map((b) => ({ day: b.key, cost: Number(b.cost_usd.toFixed(4)), runs: b.runs }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="day" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip />
                  <Bar dataKey="cost" fill="#3396fc" name="cost (USD)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <div className="grid gap-4 lg:grid-cols-3">
            <Buckets title="By skill" rows={d.by_skill} />
            <Buckets title="By model" rows={d.by_model} />
            <Buckets title="By status" rows={d.by_status} />
          </div>
          <Card title="Budget consumption per run" className="mt-4">
            <table className="table">
              <thead>
                <tr>
                  <th>run</th>
                  <th>status</th>
                  <th>skill</th>
                  <th>created</th>
                  <th>cost</th>
                  <th>budget</th>
                </tr>
              </thead>
              <tbody>
                {d.runs.slice(0, 100).map((r) => {
                  const share = r.max_cost_usd ? Math.min(100, (r.cost_usd / r.max_cost_usd) * 100) : null
                  return (
                    <tr key={r.id}>
                      <td>
                        <RunLink id={r.id} />
                      </td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                      <td>{r.skill ?? '—'}</td>
                      <td className="text-xs">{when(r.created_at)}</td>
                      <td>{usd(r.cost_usd)}</td>
                      <td>
                        {share === null ? (
                          <span className="text-ink-400">no cap</span>
                        ) : (
                          <span className="flex items-center gap-2">
                            <span className="h-1.5 w-24 overflow-hidden rounded bg-ink-200">
                              <span className={share > 90 ? 'block h-full bg-red-500' : 'block h-full bg-accent-500'} style={{ width: `${share}%` }} />
                            </span>
                            <span className="text-xs">
                              {share.toFixed(0)}% of {usd(r.max_cost_usd)}
                            </span>
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </div>
  )
}
