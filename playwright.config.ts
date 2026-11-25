import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  workers: 1,
  timeout: 60_000,
  forbidOnly: !!process.env.CI,
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
  },
  webServer: [
    {
      command: "uv run --project backend --frozen python backend/tests/browser_server.py",
      url: "http://127.0.0.1:8010/health/live",
      timeout: 60_000,
    },
    {
      command: "npm run start -- --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100",
      env: { CANOPY_BACKEND_URL: "http://127.0.0.1:8010" },
      timeout: 60_000,
    },
  ],
});
