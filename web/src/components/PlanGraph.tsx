import { Background, Handle, Position, ReactFlow, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import clsx from 'clsx'
import { useMemo } from 'react'
import type { Event, Step } from '@/api/models'
import { buildGraph, type PlanNodeData } from '@/lib/plan'

function PlanNode({ data }: NodeProps<Node<PlanNodeData>>) {
  const tone = {
    completed: 'border-emerald-400 bg-emerald-50',
    failed: 'border-red-400 bg-red-50',
    running: 'border-accent-400 bg-accent-50',
    skipped: 'border-ink-300 bg-ink-50',
    pending: 'border-ink-200 bg-white',
  }[data.status]
  return (
    <div className={clsx('w-52 rounded-md border px-2.5 py-1.5 text-xs shadow-sm', tone)} data-testid="plan-node" data-status={data.status}>
      <Handle type="target" position={Position.Left} className="!bg-ink-400" />
      <div className="mono truncate font-semibold text-ink-900">{data.label}</div>
      <div className="flex items-center justify-between text-[10px] text-ink-600">
        <span>
          {data.kind}
          {data.tool ? ` · ${data.tool}` : ''}
        </span>
        <span>{data.status}</span>
      </div>
      {data.cost > 0 && <div className="text-[10px] text-ink-500">${data.cost.toFixed(4)}</div>}
      <Handle type="source" position={Position.Right} className="!bg-ink-400" />
    </div>
  )
}

const nodeTypes = { plan: PlanNode }

export function PlanGraph({ events, steps }: { events: Event[]; steps: Step[] }) {
  const graph = useMemo(() => buildGraph(events, steps), [events, steps])
  if (!graph.nodes.length) return <div className="text-sm text-ink-500">No steps yet.</div>
  const rows = Math.ceil(graph.nodes.length / 4)
  return (
    <div className="rounded border border-ink-200 bg-white" style={{ height: Math.max(220, rows * 120 + 40) }} data-testid="plan-graph">
      <div className="px-3 pt-2 text-xs text-ink-500">source: {graph.source}</div>
      <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} fitView nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}>
        <Background gap={16} color="#e5e7eb" />
      </ReactFlow>
    </div>
  )
}
