import { defineConfig, devices } from '@playwright/test'

// Against a local build the assistant answers from the deterministic demo
// provider in milliseconds. Against a deployment it runs a real multi-step
// agent loop, which measures 17-21s per turn, and the purchase journey takes
// two of them plus a cold start. One budget cannot serve both, and the smaller
// one fails on latency rather than on behaviour - which teaches us nothing.
const isLiveTarget = Boolean(process.env.PLAYWRIGHT_BASE_URL)

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: isLiveTarget ? 420_000 : 180_000,
  expect: {
    timeout: isLiveTarget ? 45_000 : 20_000,
  },
  reporter: [['list']],
  use: {
    baseURL:
      process.env.PLAYWRIGHT_BASE_URL ??
      'http://127.0.0.1:8000',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-chromium',
      testIgnore: /mobile\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      name: 'mobile-chromium',
      testMatch: /mobile\.spec\.ts/,
      use: {
        ...devices['iPhone 13'],
        browserName: 'chromium',
      },
    },
  ],
})
