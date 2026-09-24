import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "admin", use: { ...devices["Desktop Chrome"], storageState: ".auth/admin.json" } },
    { name: "dispatcher", use: { ...devices["Desktop Chrome"], storageState: ".auth/dispatcher.json" } },
  ],
});
