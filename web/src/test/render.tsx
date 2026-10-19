import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

/** Render inside a fresh query client and a memory router at `path` (optionally with a route pattern). */
export function renderApp(ui: ReactElement, opts: { path?: string; pattern?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const path = opts.path ?? '/'
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={opts.pattern ?? path} element={ui} />
          <Route path="*" element={<div data-testid="elsewhere">elsewhere</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
