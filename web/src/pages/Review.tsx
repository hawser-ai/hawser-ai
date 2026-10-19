import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, api } from '@/api/client'
import { useResult, useReview, useReviewMutations, useRevisions, useRun, useRunRequest } from '@/api/hooks'
import type { Review as ReviewModel } from '@/api/models'
import { EvidenceViewer } from '@/components/EvidenceViewer'
import { PageHeader } from '@/components/Layout'
import { ResultForm } from '@/components/ResultForm'
import { StatusBadge } from '@/components/StatusBadge'
import { Card, ErrorBox, Spinner } from '@/components/ui'
import { schemaLeafPointers, setPointer } from '@/lib/json'
import { when } from '@/lib/format'

/**
 * Side by side: the source (evidence viewer) and the result as a form. Edits are tracked
 * locally, saved with `If-Match` on the review version; a 409 shows what changed on the server
 * and offers to re-apply the local edits on top of the latest version.
 */
export function Review() {
  const { id = '' } = useParams()
  const run = useRun(id)
  const result = useResult(id)
  const review = useReview(id)
  const revisions = useRevisions(id)
  const request = useRunRequest(id)
  const { open, edit, decide } = useReviewMutations(id)
  const [reviewer, setReviewer] = useState(() => sessionStorage.getItem('hawser.reviewer') ?? 'web')
  const [comment, setComment] = useState('')
  const [pending, setPending] = useState<Record<string, unknown>>({})
  const [selected, setSelected] = useState<string | null>(null)
  const [conflict, setConflict] = useState<{ server: ReviewModel; attempted: Record<string, unknown> } | null>(null)
  const [current, setCurrent] = useState<unknown>(undefined)

  useEffect(() => sessionStorage.setItem('hawser.reviewer', reviewer), [reviewer])
  useEffect(() => {
    if (run.data?.status === 'needs_review' && review.data === null && !open.isPending && !open.isSuccess) open.mutate(reviewer)
  }, [run.data?.status, review.data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!review.data && !result.data) return
    api<{ result: unknown }>(`/v1/reviews/${id}/current`)
      .then((r) => setCurrent(r.result))
      .catch(() => setCurrent(result.data?.result))
  }, [id, review.data?.version, result.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const edited = useMemo(() => {
    let v = current
    for (const [p, val] of Object.entries(pending)) v = setPointer(v, p, val)
    return v
  }, [current, pending])

  if (run.isLoading || result.isLoading) return <Spinner />
  if (run.error) return <ErrorBox error={run.error} />
  if (result.error) return <ErrorBox error={result.error} />
  const r = result.data!
  const rv = review.data ?? null
  const decided = !!rv && rv.status !== 'open'
  const dirty = Object.keys(pending).length > 0

  const save = async () => {
    if (!rv) return
    try {
      await edit.mutateAsync({ changes: pending, reviewer, comment: comment || undefined, version: rv.version })
      setPending({})
      setComment('')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const server = await api<ReviewModel>(`/v1/reviews/${id}`)
        setConflict({ server, attempted: pending })
      } else throw e
    }
  }
  const retry = async () => {
    if (!conflict) return
    await edit.mutateAsync({ changes: conflict.attempted, reviewer, comment: comment || undefined, version: conflict.server.version })
    setConflict(null)
    setPending({})
  }
  const finish = async (approve: boolean) => {
    if (!rv) return
    if (dirty) await save()
    const latest = await api<ReviewModel>(`/v1/reviews/${id}`)
    try {
      await decide.mutateAsync({ approve, reviewer, comment: comment || undefined, version: latest.version })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict({ server: await api<ReviewModel>(`/v1/reviews/${id}`), attempted: {} })
      else throw e
    }
  }

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            Review <span className="mono text-base">{id}</span> <StatusBadge status={run.data!.status} />
            {rv && (
              <span className="text-xs font-normal text-ink-500" data-testid="review-version">
                review {rv.id} · v{rv.version} · {rv.status}
              </span>
            )}
          </span>
        }
        subtitle={
          <span>
            {r.validation.issues.filter((i) => i.severity === 'error').length} error issue(s) · machine revision {r.revision_id} ·{' '}
            <Link to={`/runs/${id}`} className="text-accent-700 hover:underline">
              back to run
            </Link>
          </span>
        }
        actions={
          <>
            <a className="btn-secondary text-xs" href={`data:application/json,${encodeURIComponent(JSON.stringify(r.result, null, 2))}`} download={`${id}-machine.json`}>
              export original
            </a>
            <a className="btn-secondary text-xs" href={`data:application/json,${encodeURIComponent(JSON.stringify(edited, null, 2))}`} download={`${id}-reviewed.json`}>
              export reviewed
            </a>
          </>
        }
      />
      {conflict && (
        <div role="alertdialog" aria-label="review conflict" className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm" data-testid="conflict">
          <div className="font-semibold text-amber-900">Someone else changed this review (now v{conflict.server.version}).</div>
          <ul className="mt-2 list-disc pl-5 text-amber-900">
            {(conflict.server.decisions ?? []).slice(-5).map((d, i) => (
              <li key={i}>
                <span className="mono">{d.field}</span>: {JSON.stringify(d.old)} → {JSON.stringify(d.new)} <span className="text-amber-700">({d.reviewer})</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            {Object.keys(conflict.attempted).length > 0 && (
              <button className="btn-primary text-xs" onClick={() => void retry()} data-testid="conflict-retry">
                re-apply my {Object.keys(conflict.attempted).length} edit(s) on v{conflict.server.version}
              </button>
            )}
            <button
              className="btn-secondary text-xs"
              onClick={() => {
                setConflict(null)
                setPending({})
                void review.refetch()
              }}
            >
              discard mine and reload
            </button>
          </div>
        </div>
      )}
      {edit.error && !(edit.error instanceof ApiError && edit.error.status === 409) ? <ErrorBox error={edit.error} /> : null}
      {decide.error ? <ErrorBox error={decide.error} /> : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Source">
          <EvidenceViewer result={r} selectedPointer={selected} onSelectPointer={setSelected} />
        </Card>
        <Card
          title="Result"
          actions={
            <>
              <input className="input w-32 text-xs" value={reviewer} onChange={(e) => setReviewer(e.target.value)} aria-label="reviewer" />
              <button className="btn-secondary text-xs" disabled={!dirty || !rv || decided || edit.isPending} onClick={() => void save()} data-testid="save-edits">
                save {dirty ? Object.keys(pending).length : ''} edit{Object.keys(pending).length === 1 ? '' : 's'}
              </button>
            </>
          }
        >
          {current === undefined ? (
            <Spinner />
          ) : (
            <ResultForm
              result={r}
              current={current}
              pending={pending}
              onEdit={(p, v) => setPending({ ...pending, [p]: v })}
              onRevert={(p) => {
                const next = { ...pending }
                delete next[p]
                setPending(next)
              }}
              selected={selected}
              onSelect={setSelected}
              extraPointers={schemaLeafPointers(request.data?.output_schema)}
            />
          )}
          <div className="mt-4 border-t border-ink-100 pt-3">
            <textarea className="input" rows={2} placeholder="reviewer comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} aria-label="comment" />
            <div className="mt-2 flex items-center gap-2">
              <button className="btn-primary" disabled={!rv || decided || decide.isPending} onClick={() => void finish(true)} data-testid="approve">
                approve → accepted
              </button>
              <button className="btn-danger" disabled={!rv || decided || decide.isPending} onClick={() => void finish(false)} data-testid="reject">
                reject
              </button>
              {decided && (
                <span className="text-sm text-ink-600">
                  {rv.status} by {rv.decided_by} at {when(rv.decided_at)}
                </span>
              )}
            </div>
          </div>
          {rv && (rv.decisions ?? []).length > 0 && (
            <details className="mt-3 text-xs text-ink-600">
              <summary className="cursor-pointer">{(rv.decisions ?? []).length} recorded decision(s)</summary>
              <ul className="mt-1 space-y-0.5">
                {(rv.decisions ?? []).map((d, i) => (
                  <li key={i}>
                    <span className="mono">{d.field}</span> {JSON.stringify(d.old)} → {JSON.stringify(d.new)} ({d.reviewer}
                    {d.comment ? `, ${d.comment}` : ''})
                  </li>
                ))}
              </ul>
            </details>
          )}
          {revisions.data && revisions.data.length > 1 && (
            <p className="mt-2 text-xs text-ink-500">
              revisions: {revisions.data.map((x) => `${x.kind} ${x.id}`).join(' → ')}
            </p>
          )}
        </Card>
      </div>
    </div>
  )
}
