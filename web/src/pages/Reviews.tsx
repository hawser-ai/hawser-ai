import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { useRuns } from '@/api/hooks'
import type { Approval, Review } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { StatusBadge } from '@/components/StatusBadge'
import { Card, Empty, RunLink } from '@/components/ui'
import { when } from '@/lib/format'

/** Work queue: runs waiting for a reviewer, open reviews, and pending side-effect approvals. */
export function Reviews() {
  const queue = useRuns({ status: 'needs_review', limit: 100 })
  const reviews = useQuery({ queryKey: ['reviews'], queryFn: () => api<Review[]>('/v1/reviews'), refetchInterval: 5000 })
  const approvals = useQuery({ queryKey: ['approvals', 'all-pending'], queryFn: () => api<Approval[]>('/v1/approvals?status=pending'), refetchInterval: 5000 })
  const opened = new Map((reviews.data ?? []).map((r) => [r.run_id, r]))
  return (
    <div>
      <PageHeader title="Reviews" subtitle="Runs that rest on needs_review, plus side-effect approvals waiting for a decision." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Needs review (${queue.data?.items.length ?? 0})`}>
          {queue.data && !queue.data.items.length && <Empty>Nothing waiting.</Empty>}
          <ul className="divide-y divide-ink-100">
            {(queue.data?.items ?? []).map((r) => {
              const rv = opened.get(r.id)
              return (
                <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
                  <RunLink id={r.id} />
                  <span className="text-ink-500">{r.skill?.name ?? r.mode ?? ''}</span>
                  <span className="text-xs text-ink-400">{when(r.created_at)}</span>
                  <span className="ml-auto text-xs text-ink-500">{rv ? `open · v${rv.version} · ${(rv.decisions ?? []).length} edit(s)` : 'not opened'}</span>
                  <Link to={`/runs/${r.id}/review`} className="btn-primary text-xs">
                    review
                  </Link>
                </li>
              )
            })}
          </ul>
        </Card>
        <div className="space-y-4">
          <Card title={`Pending approvals (${approvals.data?.length ?? 0})`}>
            {approvals.data && !approvals.data.length && <Empty>No side effects waiting.</Empty>}
            <ul className="divide-y divide-ink-100">
              {(approvals.data ?? []).map((a) => (
                <li key={a.id} className="flex items-center gap-3 py-2 text-sm">
                  <RunLink id={a.run_id} />
                  <span className="mono">{a.tool}</span>
                  <span className="text-xs text-ink-500">{a.action_id}</span>
                  <Link to={`/runs/${a.run_id}`} className="btn-secondary ml-auto text-xs">
                    decide
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Decided recently">
            <ul className="divide-y divide-ink-100">
              {(reviews.data ?? [])
                .filter((r) => r.status !== 'open')
                .slice(0, 10)
                .map((r) => (
                  <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
                    <RunLink id={r.run_id} />
                    <StatusBadge status={r.status} />
                    <span className="text-xs text-ink-500">
                      {r.decided_by} · {when(r.decided_at)}
                    </span>
                  </li>
                ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  )
}
