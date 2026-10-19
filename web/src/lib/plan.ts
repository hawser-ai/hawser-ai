import type { Edge, Node } from '@xyflow/react'
import type { Event, Step } from '@/api/models'

export interface PlanNodeData extends Record<string, unknown> {
  label: string
  kind: string
  tool: string | null
  status: 'completed' | 'failed' | 'running' | 'skipped' | 'pending'
  cost: number
}

/**
 * Agent plans come from the `plan.created` event (ordered nodes); workflow and direct runs are
 * drawn from their steps. Node status and cost come from the checkpointed steps.
 */
export function buildGraph(events: Event[], steps: Step[]): { nodes: Node<PlanNodeData>[]; edges: Edge[]; source: string } {
  const planEvent = [...events].reverse().find((e) => e.type === 'plan.created' && Array.isArray(e.payload.nodes) && (e.payload.nodes as unknown[]).length)
  const byKey = new Map(steps.map((s) => [s.key, s]))
  let items: { id: string; kind: string; tool: string | null; step?: Step }[]
  let source: string
  if (planEvent && typeof (planEvent.payload.nodes as unknown[])[0] === 'object') {
    source = `plan (${String(planEvent.payload.source ?? 'planner')})`
    items = (planEvent.payload.nodes as { id: string; kind: string; tool?: string | null }[]).map((n) => ({
      id: n.id,
      kind: n.kind,
      tool: n.tool ?? null,
      step: steps.find((s) => s.key === `plan/${n.id}` || s.key.startsWith(`plan/${n.id}/`)),
    }))
  } else {
    source = 'steps'
    items = steps.map((s) => ({ id: s.key, kind: s.kind, tool: null, step: byKey.get(s.key) }))
  }
  const nodes: Node<PlanNodeData>[] = items.map((it, i) => ({
    id: it.id,
    type: 'plan',
    position: { x: (i % 4) * 230, y: Math.floor(i / 4) * 110 },
    data: {
      label: it.id,
      kind: it.kind,
      tool: it.tool,
      status: (it.step?.status as PlanNodeData['status']) ?? 'pending',
      cost: it.step?.usage.totals.cost_usd ?? 0,
    },
  }))
  const edges: Edge[] = items.slice(1).map((it, i) => ({ id: `${items[i].id}->${it.id}`, source: items[i].id, target: it.id, animated: it.step?.status === 'running' }))
  return { nodes, edges, source }
}
