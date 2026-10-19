// Browser checks for the dashboard demo. Run `npx playwright test` in this folder.
const path = require("path");
const { defineConfig } = require("@playwright/test");

const PORT = 8011;
const python = process.env.DEMO_PYTHON || "python3";
const executablePath = process.env.CHROMIUM_PATH;

module.exports = defineConfig({
  testDir: __dirname,
  timeout: 45000,
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}/`,
    launchOptions: executablePath ? { executablePath } : {},
  },
  webServer: {
    command: `${python} ../../scripts/pages_demo.py --out .site && ${python} -m http.server ${PORT} --directory .site`,
    cwd: __dirname,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 120000,
  },
});
