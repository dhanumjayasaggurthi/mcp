import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    },
  },
  webServer: [
    {
      command: `PYTHONPATH=.. EDP_UI_TEST_MODE=true ${process.env.UI_TEST_PYTHON || "python"} -m uvicorn ui_app:app --app-dir ../tests --port 8010`,
      url: "http://127.0.0.1:8010/livez",
      reuseExistingServer: !process.env.CI,
    },
    {
      command:
        "API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
    },
  ],
  reporter: "list",
});
