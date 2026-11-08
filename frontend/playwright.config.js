import { defineConfig } from "@playwright/test";

const port = Number(process.env.PF_TEST_PORT || 8877);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid PF_TEST_PORT");

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 20_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    launchOptions: process.env.PF_BROWSER_EXECUTABLE
      ? { executablePath: process.env.PF_BROWSER_EXECUTABLE }
      : {},
    trace: "retain-on-failure",
  },
  webServer: {
    command: "../.venv/bin/python ../scripts/serve_test_viewer.py",
    url: `http://127.0.0.1:${port}/readyz`,
    timeout: 30_000,
    reuseExistingServer: false,
  },
});
