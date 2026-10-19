/** Short names for the generated OpenAPI types (`pnpm gen:api`). */
import type { components } from './types'

type S = components['schemas']

export type RunRequest = S['RunRequest']
export type RunResult = S['RunResult']
export type RunSummary = S['RunSummary']
export type RunPage = S['RunPage']
export type RunStatus = S['RunStatus']
export type Event = S['Event']
export type Step = S['Step']
export type Revision = S['Revision']
export type Review = S['Review']
export type ReviewDecision = S['ReviewDecision']
export type Approval = S['Approval']
export type Asset = S['Asset']
export type Artifact = S['Artifact']
export type Policy = S['Policy']
export type FieldMeta = S['FieldMeta']
export type Issue = S['Issue']
export type ValidationReport = S['ValidationReport']
export type Evidence = RunResult['evidence'][number]
export type DocumentRegionEvidence = S['DocumentRegionEvidence']
export type ImageRegionEvidence = S['ImageRegionEvidence']
export type CalculationEvidence = S['CalculationEvidence']
export type BBox = S['BBox']
export type ModelSpec = S['ModelSpec']
export type ToolSpec = S['ToolSpec']
export type SkillManifest = S['SkillManifest']
export type Usage = S['Usage']
export type UsageBreakdown = S['UsageBreakdown']
export type UsageBucket = S['UsageBucket']
export type EvalSummary = S['EvalSummary']
export type WebhookOut = S['WebhookOut']
export type Health = S['Health']
export type CancelResponse = S['CancelResponse']
export type ApprovalDecided = S['ApprovalDecided']

/** The full report published by `hawser eval publish` (mirrors hawser_evals.EvalRun). */
export interface EvalCase {
  case_id: string
  run_id: string | null
  status: string
  expected_status: string | null
  status_ok: boolean | null
  leaves: { pointer: string; outcome: string; expected?: unknown; actual?: unknown }[]
  correct: number
  total: number
  passed: boolean
  evidence_coverage: number | null
  cost_usd: number
  latency_ms: number
  model_calls: number
  repair_attempts: number
  error: string | null
  tags: string[]
}

export interface EvalReport {
  id: string
  config: {
    dataset: string
    skill: string | null
    skill_version: string | null
    mode: string | null
    model: string
    registry_version: string
    record_mode: string
    hawser_version?: string
    git_sha?: string | null
    tags?: string[]
    started_at: string
    policy?: Record<string, unknown>
  }
  cases: EvalCase[]
  metrics: Record<string, number>
  clusters: { field: string; outcome: string; count: number; cases: string[] }[]
  finished_at: string | null
}

export const RESTED: ReadonlySet<string> = new Set([
  'accepted',
  'needs_review',
  'rejected',
  'failed',
  'cancelled',
  'awaiting_approval',
])
export const TERMINAL: ReadonlySet<string> = new Set(['accepted', 'rejected', 'failed', 'cancelled'])
