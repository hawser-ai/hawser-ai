import { useEffect, useState } from 'react'
import { useModels, usePolicy, usePolicySchema, usePutPolicy } from '@/api/hooks'
import type { Policy as PolicyModel } from '@/api/models'
import { PageHeader } from '@/components/Layout'
import { PolicyForm } from '@/components/PolicyForm'
import { Card, ErrorBox, Json, Spinner } from '@/components/ui'
import { validate } from '@/lib/policySchema'

/**
 * The tenant's default policy. Validation happens twice: a light structural check against the
 * Policy JSON Schema in the browser (unknown keys, types), and the server's full validation on
 * save (422 with the exact problem).
 */
export function Policy() {
  const policy = usePolicy()
  const schema = usePolicySchema()
  const models = useModels()
  const put = usePutPolicy()
  const [draft, setDraft] = useState<PolicyModel | null>(null)
  useEffect(() => {
    if (policy.data && !draft) setDraft(policy.data)
  }, [policy.data, draft])
  if (policy.isLoading || !draft) return <Spinner />
  if (policy.error) return <ErrorBox error={policy.error} />
  const problems = schema.data ? validate(draft as unknown as Record<string, unknown>, schema.data) : []
  const dirty = JSON.stringify(draft) !== JSON.stringify(policy.data)
  return (
    <div>
      <PageHeader
        title="Default policy"
        subtitle="Applied by the playground to new runs; the SDK and CLI send their own. Nothing a model returns can change a policy."
        actions={
          <>
            <button className="btn-secondary" disabled={!dirty} onClick={() => setDraft(policy.data!)}>
              reset
            </button>
            <button className="btn-primary" disabled={!dirty || problems.length > 0 || put.isPending} onClick={() => put.mutate(draft)} data-testid="save-policy">
              {put.isPending ? 'saving…' : 'save'}
            </button>
          </>
        }
      />
      {put.error ? <ErrorBox error={put.error} /> : null}
      {put.isSuccess && !dirty && <div className="mb-3 text-sm text-emerald-700">Saved.</div>}
      {problems.length > 0 && (
        <div role="alert" className="mb-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          {problems.map((p) => (
            <div key={p}>{p}</div>
          ))}
        </div>
      )}
      <Card title="Policy">
        <PolicyForm value={draft} onChange={setDraft} models={(models.data ?? []).map((m) => m.id)} />
      </Card>
      <details className="mt-4 text-xs">
        <summary className="cursor-pointer text-ink-500">Policy JSON Schema (from the server)</summary>
        <Json value={schema.data} />
      </details>
    </div>
  )
}
