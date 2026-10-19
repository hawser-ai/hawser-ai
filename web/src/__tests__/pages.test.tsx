import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ids, state } from '@/mocks/handlers'
import { EvalCompare } from '@/pages/Evals'
import { Review } from '@/pages/Review'
import { RunDetail } from '@/pages/RunDetail'
import { Runs } from '@/pages/Runs'
import { renderApp } from '@/test/render'

describe('Runs page', () => {
  it('lists runs with status badges', async () => {
    renderApp(<Runs />, { path: '/runs' })
    await screen.findByText(ids.ACCEPTED)
    expect(screen.getAllByTestId('status').map((e) => e.textContent)).toContain('accepted')
  })
})

describe('Run detail', () => {
  it('shows the result tree and the evidence viewer; selecting a field highlights its box', async () => {
    renderApp(<RunDetail />, { path: `/runs/${ids.ACCEPTED}`, pattern: '/runs/:id' })
    await screen.findByTestId('annotated-json')
    await screen.findByTestId('evidence-viewer')
    const total = (await waitFor(() => document.querySelector('[data-pointer="/total"]')!)) as HTMLElement
    await userEvent.click(total)
    await waitFor(() => expect(document.querySelector('.evidence-box[data-selected="true"]')).not.toBeNull())
    const selected = document.querySelector('.evidence-box[data-selected="true"]')!
    expect(selected.getAttribute('data-pointer')).toBe('/total')
  })
  it('switches to the trace and validation tabs', async () => {
    renderApp(<RunDetail />, { path: `/runs/${ids.ACCEPTED}`, pattern: '/runs/:id' })
    await screen.findByTestId('annotated-json')
    await userEvent.click(screen.getByRole('tab', { name: /trace/i }))
    await screen.findByTestId('event-timeline')
    expect(screen.getByText('run.created')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /validation/i }))
    await screen.findByTestId('issue-list')
  })
})

describe('Review workspace', () => {
  it('edits a field, saves with If-Match, handles a conflict, and approves', async () => {
    renderApp(<Review />, { path: `/runs/${ids.NEEDS_REVIEW}/review`, pattern: '/runs/:id/review' })
    await screen.findByTestId('result-form')
    await screen.findByText(/v1 · open/)
    const input = (await screen.findAllByLabelText('/invoice_number'))[0] as HTMLInputElement
    await userEvent.clear(input)
    await userEvent.type(input, 'INV-EDITED')
    expect(screen.getByTestId('save-edits')).toHaveTextContent('save 1 edit')

    // another reviewer saved in between: the server is now at v2
    state.review.version = 2
    await userEvent.click(screen.getByTestId('save-edits'))
    await screen.findByTestId('conflict')
    await userEvent.click(screen.getByTestId('conflict-retry'))
    await waitFor(() => expect(screen.queryByTestId('conflict')).toBeNull())
    expect(state.review.decisions.map((d: { field: string }) => d.field)).toEqual(['/invoice_number'])
    expect(state.review.version).toBe(3)

    await userEvent.click(screen.getByTestId('approve'))
    await waitFor(() => expect(state.review.status).toBe('approved'))
    await screen.findByText(/approved by/)
  })
})

describe('Eval compare', () => {
  it('highlights regressions between two reports', async () => {
    renderApp(<EvalCompare />, { path: '/evals/compare?baseline=eval_a&candidate=eval_b', pattern: '/evals/compare' })
    await screen.findAllByText(/regression/i)
    expect(document.querySelectorAll('tr[data-kind="regression"]').length).toBeGreaterThanOrEqual(1)
    expect(document.querySelectorAll('tr[data-verdict="worse"]').length).toBeGreaterThanOrEqual(1)
  })
})
