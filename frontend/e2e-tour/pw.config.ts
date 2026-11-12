import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  workers: 1,
  timeout: 120_000,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:5183" },
});
