import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright Test Configuration
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "html",

  use: {
    baseURL:
      process.env.E2E_BASE_URL ||
      (process.env.CI ? "http://localhost:4173" : "http://localhost:5173"),
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
    },
    // Mobile viewports (important for this app)
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"] },
    },
    {
      name: "mobile-safari",
      use: { ...devices["iPhone 12"] },
    },
  ],

  // Run local dev server before tests (skip if E2E_BASE_URL is set for preview testing)
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : process.env.CI
      ? {
          command: "pnpm --filter @dekita/frontend preview",
          url: "http://localhost:4173",
          reuseExistingServer: false,
          timeout: 120000,
        }
      : {
          command: "pnpm dev",
          url: "http://localhost:5173",
          reuseExistingServer: true,
          timeout: 120000,
        },
});
