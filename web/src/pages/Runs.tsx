import { useState } from 'react'
import { useRuns, useSkills } from '@/api/hooks'
import { PageHeader } from '@/components/Layout'
import { StatusBadge } from '@/components/StatusBadge'
import { Empty, ErrorBox, RunLink, Spinner } from '@/components/ui'
import { usd, when } from '@/lib/format'

const STATUSES = ['', 'queued', 'running', 'awaiting_approval', 'validating', 'needs_review', 'accepted', 'rejected', 'failed', 'cancelled']

export function Runs() {
  const [status, setStatus] = useState('')
  const [skill, setSkill] = useState('')
  const [mode, setMode] = useState('')
  const [q, setQ] = useState('')
  const runs = useRuns({ status: status || undefined, skill: skill || undefined, mode: mode || undefined, q: q || undefined, limit: 100 })
  const skills = useSkills()
  return (
    <div>
      <PageHeader title="Runs" subtitle="Every run with its status, mode, cost and latency. Filters apply server-side." />
      <div className="mb-3 flex flex-wrap gap-2">
        <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="status filter">
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || 'any status'}
            </option>
          ))}
        </select>
        <select className="input w-auto" value={skill} onChange={(e) => setSkill(e.target.value)} aria-label="skill filter">
          <option value="">any skill</option>
          {(skills.data ?? []).map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        <select className="input w-auto" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="mode filter">
          <option value="">any mode</option>
          <option value="direct">direct</option>
          <option value="workflow">workflow</option>
          <option value="agent">agent</option>
        </select>
        <input className="input w-64" placeholder="search id or metadata" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
      </div>
      {runs.error ? <ErrorBox error={runs.error} /> : null}
      {runs.isLoading && <Spinner />}
      {runs.data && !runs.data.items.length && <Empty>No runs match.</Empty>}
      {runs.data && runs.data.items.length > 0 && (
        <table className="table card">
          <thead>
            <tr>
              <th>run</th>
              <th>status</th>
              <th>mode</th>
              <th>skill</th>
              <th>cost</th>
              <th>latency</th>
              <th>created</th>
              <th>metadata</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.items.map((r) => (
              <tr key={r.id}>
                <td>
                  <RunLink id={r.id} />
                </td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>{r.mode ?? '—'}</td>
                <td>{r.skill ? `${r.skill.name}${r.skill.version ? '@' + r.skill.version : ''}` : '—'}</td>
                <td>{usd(r.cost_usd)}</td>
                <td>{r.completed_at ? `${((new Date(r.completed_at).getTime() - new Date(r.created_at).getTime()) / 1000).toFixed(1)} s` : '—'}</td>
                <td className="whitespace-nowrap">{when(r.created_at)}</td>
                <td className="text-xs text-ink-500">
                  {Object.entries(r.metadata)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(' ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
