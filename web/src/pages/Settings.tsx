import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import { useWebhooks } from '@/api/hooks'
import type { WebhookOut } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { StatusBadge } from '@/components/StatusBadge'
import { Card, Empty, ErrorBox } from '@/components/ui'
import { when } from '@/lib/format'

interface Delivery {
  id: string
  event: string
  state: string
  attempts: number
  last_status: number | null
  last_error: string | null
  created_at: string
}

export function Settings() {
  const qc = useQueryClient()
  const hooks = useWebhooks()
  const events = useQuery({ queryKey: ['webhook-events'], queryFn: () => api<string[]>('/v1/webhooks/events') })
  const [url, setUrl] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [secret, setSecret] = useState<string | null>(null)
  const [selected, setSelected] = useState<WebhookOut | null>(null)
  const deliveries = useQuery({
    queryKey: ['deliveries', selected?.id],
    queryFn: () => api<Delivery[]>(`/v1/webhooks/${selected!.id}/deliveries`),
    enabled: !!selected,
  })
  const create = useMutation({
    mutationFn: () => api<{ webhook: WebhookOut; secret: string }>('/v1/webhooks', { method: 'POST', body: { url, events: chosen } }),
    onSuccess: (d) => {
      setSecret(d.secret)
      setUrl('')
      void qc.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api<void>(`/v1/webhooks/${id}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['webhooks'] }),
  })
  return (
    <div>
      <PageHeader title="Webhooks" subtitle="Signed notifications when runs rest or reviewers decide. The secret is shown once." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Register">
          <input className="input" placeholder="https://example.com/hawser" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="webhook url" />
          <div className="mt-2 flex flex-wrap gap-3 text-sm">
            {(events.data ?? []).map((ev) => (
              <label key={ev} className="flex items-center gap-1">
                <input type="checkbox" checked={chosen.includes(ev)} onChange={(e) => setChosen(e.target.checked ? [...chosen, ev] : chosen.filter((x) => x !== ev))} />
                {ev}
              </label>
            ))}
          </div>
          <button className="btn-primary mt-3" disabled={!/^https?:\/\//.test(url) || create.isPending} onClick={() => create.mutate()}>
            register
          </button>
          {create.error ? <div className="mt-2"><ErrorBox error={create.error} /></div> : null}
          {secret && (
            <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-2 text-sm">
              secret (copy it now): <code className="mono select-all">{secret}</code>
            </div>
          )}
        </Card>
        <Card title="Registered">
          {hooks.data && !hooks.data.length && <Empty>No webhooks.</Empty>}
          <ul className="divide-y divide-ink-100 text-sm">
            {(hooks.data ?? []).map((h) => (
              <li key={h.id} className="flex items-center gap-2 py-2">
                <button className="text-left text-accent-700 hover:underline" onClick={() => setSelected(h)}>
                  {h.url}
                </button>
                <span className="text-xs text-ink-500">{h.events.join(', ')}</span>
                <button className="btn-ghost ml-auto text-xs" onClick={() => remove.mutate(h.id)}>
                  delete
                </button>
              </li>
            ))}
          </ul>
          {selected && (
            <div className="mt-3">
              <div className="label">deliveries for {selected.url}</div>
              <table className="table">
                <thead>
                  <tr>
                    <th>event</th>
                    <th>state</th>
                    <th>attempts</th>
                    <th>last</th>
                    <th>created</th>
                  </tr>
                </thead>
                <tbody>
                  {(deliveries.data ?? []).map((d) => (
                    <tr key={d.id}>
                      <td>{d.event}</td>
                      <td>
                        <StatusBadge status={d.state} />
                      </td>
                      <td>{d.attempts}</td>
                      <td className="text-xs">{d.last_status ?? d.last_error ?? '—'}</td>
                      <td className="text-xs">{when(d.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
