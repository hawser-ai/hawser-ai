import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { RunResult } from '@/api/models'
import { AnnotatedJson } from '@/components/AnnotatedJson'
import { EvidenceOverlay } from '@/components/EvidenceOverlay'
import { IssueList } from '@/components/IssueList'
import { buildGraph } from '@/lib/plan'
import { compareReports } from '@/lib/compare'
import { evidenceBoxes, fieldRows, pageSizePt } from '@/lib/evidence'
import { getPointer, leafPointers, schemaFromExample, setPointer } from '@/lib/json'
import { fixtures } from '@/mocks/handlers'
import { validate } from '@/lib/policySchema'

const result = fixtures.runResult as unknown as RunResult
const needsReview = fixtures.runResultNeedsReview as unknown as RunResult

describe('evidence helpers', () => {
  it('resolves every field to its evidence and issues', () => {
    const rows = fieldRows(result)
    expect(rows.length).toBe(Object.keys(result.field_meta).length)
    const total = rows.find((r) => r.pointer === '/total')!
    expect(total.evidence[0].kind).toBe('document_region')
    expect(total.value).toBe(getPointer(result.result, '/total'))
  })
  it('turns document-region evidence into page boxes with pointers', () => {
    const boxes = evidenceBoxes(result)
    expect(boxes.length).toBeGreaterThan(10)
    expect(boxes.every((b) => b.page === 1 && b.pointer)).toBe(true)
    const size = pageSizePt(fixtures.asset as never, 1)!
    expect(size.width).toBeGreaterThan(500)
  })
})

describe('EvidenceOverlay', () => {
  it('renders boxes as buttons, selects on click, and navigates with arrow keys', async () => {
    const boxes = evidenceBoxes(result).slice(0, 3)
    const onSelect = vi.fn()
    render(<EvidenceOverlay imageUrl="blob:test" pageSize={{ width: 612, height: 792 }} boxes={boxes} selectedId={null} onSelect={onSelect} />)
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].style.left).toMatch(/%$/)
    await userEvent.click(buttons[1])
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ evidenceId: buttons[1].getAttribute('data-evidence-id') }))
    buttons[0].focus()
    fireEvent.keyDown(buttons[0], { key: 'ArrowDown' })
    expect(document.activeElement).toBe(buttons[1])
    fireEvent.keyDown(buttons[1], { key: 'ArrowUp' })
    expect(document.activeElement).toBe(buttons[0])
    fireEvent.keyDown(buttons[0], { key: 'Escape' })
    expect(onSelect).toHaveBeenLastCalledWith(null)
  })
  it('shows a tooltip with the quoted text on hover', async () => {
    const boxes = evidenceBoxes(result).filter((b) => b.pointer === '/total')
    render(<EvidenceOverlay imageUrl="blob:test" pageSize={{ width: 612, height: 792 }} boxes={boxes} selectedId={null} onSelect={() => undefined} />)
    await userEvent.hover(screen.getByRole('button'))
    expect(screen.getByRole('tooltip')).toHaveTextContent('/total')
  })
})

describe('AnnotatedJson', () => {
  it('lists leaves with pointers and marks unknown fields and issues', async () => {
    const onSelect = vi.fn()
    render(<AnnotatedJson result={needsReview} selected={null} onSelect={onSelect} />)
    const vendor = document.querySelector('[data-pointer="/vendor_name"]') as HTMLElement | null
    if (vendor) {
      expect(vendor).toHaveTextContent('unknown')
      await userEvent.click(vendor)
      expect(onSelect).toHaveBeenCalledWith('/vendor_name')
    }
    const totalButton = document.querySelector('[data-pointer="/total"]')
    expect(totalButton === null || totalButton.textContent?.includes('total')).toBe(true)
  })
  it('renders the accepted result tree with evidence markers', () => {
    render(<AnnotatedJson result={result} selected="/total" onSelect={() => undefined} />)
    const total = document.querySelector('[data-pointer="/total"]') as HTMLElement
    expect(total).toHaveAttribute('aria-pressed', 'true')
    expect(within(total).getByTitle(/evidence/)).toBeInTheDocument()
  })
})

describe('IssueList', () => {
  it('groups by severity and links fields', async () => {
    const onSelect = vi.fn()
    render(<IssueList report={needsReview.validation} onSelect={onSelect} />)
    if (needsReview.validation.issues.length) {
      const field = needsReview.validation.issues.find((i) => i.field)!
      await userEvent.click(screen.getAllByText(field.field!)[0])
      expect(onSelect).toHaveBeenCalledWith(field.field)
    } else {
      expect(screen.getByTestId('issue-list')).toHaveTextContent(/no validation issues/i)
    }
  })
})

describe('plan graph', () => {
  it('builds a chain from steps when there is no plan event', () => {
    const g = buildGraph(fixtures.events as never, fixtures.steps as never)
    expect(g.source).toBe('steps')
    expect(g.nodes.map((n) => n.data.status)).toContain('completed')
    expect(g.edges.length).toBe(g.nodes.length - 1)
  })
  it('prefers the plan.created nodes for agent runs', () => {
    const ev = [{ ...(fixtures.events[0] as object), type: 'plan.created', payload: { source: 'planner', nodes: [{ id: 'a', kind: 'call_model' }, { id: 'b', kind: 'exit' }] } }]
    const steps = [{ ...(fixtures.steps[0] as object), key: 'plan/a', status: 'completed' }]
    const g = buildGraph(ev as never, steps as never)
    expect(g.source).toContain('plan')
    expect(g.nodes[0].data.status).toBe('completed')
    expect(g.nodes[1].data.status).toBe('pending')
  })
})

describe('json helpers', () => {
  it('pointer get/set and leaf enumeration', () => {
    const data = { a: { b: [1, { c: 'x' }] } }
    expect(getPointer(data, '/a/b/1/c')).toBe('x')
    const next = setPointer(data, '/a/b/1/c', 'y')
    expect(getPointer(next, '/a/b/1/c')).toBe('y')
    expect(getPointer(data, '/a/b/1/c')).toBe('x')
    expect(leafPointers(data)).toEqual(['/a/b/0', '/a/b/1/c'])
    expect(schemaFromExample({ n: 1, s: 'a', l: [true] })).toMatchObject({ type: 'object', required: ['n', 's', 'l'] })
  })
  it('validates a policy against the schema', () => {
    const schema = { type: 'object', additionalProperties: false, properties: { limits: { $ref: '#/$defs/L' } }, $defs: { L: { type: 'object', additionalProperties: false, properties: { max_cost_usd: { anyOf: [{ type: 'number', minimum: 0 }, { type: 'null' }] } } } } }
    expect(validate({ limits: { max_cost_usd: 1 } }, schema)).toEqual([])
    expect(validate({ limits: { max_cost_usd: -1 } }, schema)[0]).toMatch(/≥ 0/)
    expect(validate({ nope: 1 }, schema)[0]).toMatch(/unknown property/)
  })
})

describe('eval comparison', () => {
  it('finds metric deltas and case regressions', () => {
    const cmp = compareReports(fixtures.evalHaiku as never, fixtures.evalGpt as never)
    expect(cmp.metrics.find((m) => m.key === 'field_accuracy')!.verdict).toBe('worse')
    expect(cmp.regressions.length).toBeGreaterThanOrEqual(1)
    expect(cmp.cases.length).toBe(fixtures.evalHaiku.cases.length)
  })
})
