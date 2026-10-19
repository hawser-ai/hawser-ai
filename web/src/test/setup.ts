import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { BASE, handlers, resetState } from '@/mocks/handlers'
import { useAuth } from '@/state/auth'

export const server = setupServer(...handlers)

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
  // jsdom lacks these; components only need them to exist.
  globalThis.URL.createObjectURL = () => 'blob:test'
  globalThis.URL.revokeObjectURL = () => undefined
  Element.prototype.scrollIntoView = () => undefined
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})
beforeEach(() => {
  resetState()
  useAuth.getState().signIn(BASE, 'hk_test')
})
afterEach(() => {
  cleanup()
  server.resetHandlers()
})
afterAll(() => server.close())

import { beforeEach } from 'vitest'
