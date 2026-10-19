import { defineConfig } from '@playwright/test'

/**
 * End-to-end against a real `hawserd serve --dev` replaying the bundled quickstart recording
 * and serving the production build at /app. `pnpm build` first.
 */
const PORT = 8099
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `cd .. && E2E=$(mktemp -d) && HAWSERD_DATABASE_URL=sqlite:///$E2E/e2e.db HAWSERD_ARTIFACTS_DIR=$E2E/store HAWSER_RECORD_MODE=replay HAWSER_CASSETTE_DIR=$(uv run python -c "from hawser.conformance import quickstart_cassette_dir as q; print(q())") HAWSERD_DEV_API_KEY=hk_live_e2e_key uv run hawserd serve --dev --port ${PORT} --web-dir web/dist`,
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
