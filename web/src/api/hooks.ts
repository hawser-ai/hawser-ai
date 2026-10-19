import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiWithHeaders, uploadAsset } from './client'
import type {
  Approval,
  ApprovalDecided,
  Asset,
  CancelResponse,
  EvalReport,
  EvalSummary,
  Event,
  Health,
  ModelSpec,
  Policy,
  Review,
  Revision,
  RunPage,
  RunRequest,
  RunResult,
  RunSummary,
  SkillManifest,
  Step,
  ToolSpec,
  UsageBreakdown,
  WebhookOut,
} from './models'
import { RESTED } from './models'

const qs = (params: Record<string, string | number | undefined | null>): string => {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const keys = {
  runs: (params: Record<string, string | undefined>) => ['runs', params] as const,
  run: (id: string) => ['run', id] as const,
  result: (id: string) => ['result', id] as const,
  events: (id: string) => ['events', id] as const,
  steps: (id: string) => ['steps', id] as const,
  revisions: (id: string) => ['revisions', id] as const,
  review: (id: string) => ['review', id] as const,
  approvals: (id: string | undefined) => ['approvals', id] as const,
  asset: (id: string) => ['asset', id] as const,
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/readyz'), retry: false })
}

export function useRuns(params: { status?: string; skill?: string; mode?: string; q?: string; limit?: number }) {
  return useQuery({
    queryKey: keys.runs(params as Record<string, string | undefined>),
    queryFn: () => api<RunPage>(`/v1/runs${qs(params)}`),
    refetchInterval: 5000,
  })
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => api<RunSummary>(`/v1/runs/${id}`),
    refetchInterval: (q) => (q.state.data && RESTED.has(q.state.data.status) ? false : 1500),
  })
}

export function useRunRequest(id: string) {
  return useQuery({ queryKey: ['request', id], queryFn: () => api<RunRequest>(`/v1/runs/${id}/request`), staleTime: Infinity })
}

export function useResult(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.result(id),
    queryFn: () => api<RunResult>(`/v1/runs/${id}/result`),
    enabled,
    retry: (count, err) => (err as { code?: string }).code === 'result_not_ready' && count < 20,
    retryDelay: 1000,
  })
}

export function useEvents(id: string) {
  return useQuery({ queryKey: keys.events(id), queryFn: () => api<Event[]>(`/v1/runs/${id}/events`) })
}

export function useSteps(id: string) {
  return useQuery({ queryKey: keys.steps(id), queryFn: () => api<Step[]>(`/v1/runs/${id}/steps`) })
}

export function useRevisions(id: string) {
  return useQuery({ queryKey: keys.revisions(id), queryFn: () => api<Revision[]>(`/v1/runs/${id}/revisions`) })
}

export function useReview(id: string) {
  return useQuery({
    queryKey: keys.review(id),
    queryFn: async () => {
      try {
        return await api<Review>(`/v1/reviews/${id}`)
      } catch (e) {
        if ((e as { status?: number }).status === 404) return null
        throw e
      }
    },
  })
}

export function useApprovals(runId?: string) {
  return useQuery({
    queryKey: keys.approvals(runId),
    queryFn: () => api<Approval[]>(`/v1/approvals${qs({ run_id: runId, status: 'pending' })}`),
  })
}

export function useAsset(id: string | undefined) {
  return useQuery({ queryKey: keys.asset(id ?? ''), queryFn: () => api<Asset>(`/v1/assets/${id}`), enabled: !!id })
}

export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: () => api<ModelSpec[]>('/v1/models'), staleTime: 60_000 })
}
export function useTools() {
  return useQuery({ queryKey: ['tools'], queryFn: () => api<ToolSpec[]>('/v1/tools'), staleTime: 60_000 })
}
export function useSkills() {
  return useQuery({ queryKey: ['skills'], queryFn: () => api<SkillManifest[]>('/v1/skills'), staleTime: 60_000 })
}
export function useWebhooks() {
  return useQuery({ queryKey: ['webhooks'], queryFn: () => api<WebhookOut[]>('/v1/webhooks') })
}

export function useEvals() {
  return useQuery({ queryKey: ['evals'], queryFn: () => api<EvalSummary[]>('/v1/evals') })
}
export function useEvalReport(id: string | undefined) {
  return useQuery({
    queryKey: ['eval-report', id],
    queryFn: () => api<EvalReport>(`/v1/evals/${id}/report`),
    enabled: !!id,
    staleTime: Infinity,
  })
}

export function usePolicy() {
  return useQuery({ queryKey: ['policy'], queryFn: () => api<Policy>('/v1/policy') })
}
export function usePolicySchema() {
  return useQuery({
    queryKey: ['schema', 'Policy'],
    queryFn: () => api<Record<string, unknown>>('/v1/schemas/Policy'),
    staleTime: Infinity,
  })
}

export function useUsageBreakdown(since?: string) {
  return useQuery({ queryKey: ['usage', since], queryFn: () => api<UsageBreakdown>(`/v1/usage/breakdown${qs({ since })}`) })
}

// -- mutations ------------------------------------------------------------------------------

export interface QuickstartExample {
  asset: Asset
  instruction: string
  output_schema: Record<string, unknown>
  policy: Policy
  cassette_dir: string
}

export function useQuickstartExample() {
  return useMutation({ mutationFn: () => api<QuickstartExample>('/v1/examples/quickstart', { method: 'POST' }) })
}

export function useCreateRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: RunRequest) => api<RunSummary>('/v1/runs', { method: 'POST', body: req }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

export function useUpload() {
  return useMutation({ mutationFn: (file: File) => uploadAsset<Asset>(file) })
}

export function useCancelRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<CancelResponse>(`/v1/runs/${id}/cancel`, { method: 'POST' }),
    onSuccess: (_d, id) => {
      void qc.invalidateQueries({ queryKey: keys.run(id) })
      void qc.invalidateQueries({ queryKey: keys.result(id) })
    },
  })
}

export interface EditPayload {
  runId: string
  changes: Record<string, unknown>
  reviewer: string
  comment?: string
  version: number
}

export function useReviewMutations(runId: string) {
  const qc = useQueryClient()
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: keys.review(runId) })
    void qc.invalidateQueries({ queryKey: keys.result(runId) })
    void qc.invalidateQueries({ queryKey: keys.run(runId) })
    void qc.invalidateQueries({ queryKey: keys.revisions(runId) })
  }
  const open = useMutation({
    mutationFn: (assigned_to: string | null) =>
      api<Review>(`/v1/reviews/${runId}/open`, { method: 'POST', body: { assigned_to } }),
    onSuccess: invalidate,
  })
  const edit = useMutation({
    mutationFn: (p: Omit<EditPayload, 'runId'>) =>
      apiWithHeaders<Review>(`/v1/reviews/${runId}/edit`, {
        method: 'POST',
        body: { changes: p.changes, reviewer: p.reviewer, comment: p.comment ?? null },
        headers: { 'If-Match': `"${p.version}"` },
      }).then((r) => r.data),
    onSuccess: invalidate,
  })
  const decide = useMutation({
    mutationFn: (p: { approve: boolean; reviewer: string; comment?: string; version: number }) =>
      api<Revision>(`/v1/reviews/${runId}/${p.approve ? 'approve' : 'reject'}`, {
        method: 'POST',
        body: { reviewer: p.reviewer, comment: p.comment ?? null },
        headers: { 'If-Match': `"${p.version}"` },
      }),
    onSuccess: invalidate,
  })
  return { open, edit, decide }
}

export function useDecideApproval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { id: string; approve: boolean; by: string; comment?: string }) =>
      api<ApprovalDecided>(`/v1/approvals/${p.id}/decide`, {
        method: 'POST',
        body: { approve: p.approve, by: p.by, comment: p.comment ?? null, resume: true },
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['approvals'] }),
  })
}

export function usePutPolicy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (policy: Policy) => api<Policy>('/v1/policy', { method: 'PUT', body: policy }),
    onSuccess: (data) => qc.setQueryData(['policy'], data),
  })
}

export function usePublishEval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { report: unknown; name?: string }) => api<EvalSummary>('/v1/evals', { method: 'POST', body: p }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['evals'] }),
  })
}

export function useDeleteEval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api<void>(`/v1/evals/${id}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['evals'] }),
  })
}
