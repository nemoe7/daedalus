// Browser checks for the dashboard demo. Each route opens at three widths.
const { test, expect } = require("@playwright/test");

const ROUTES = [
  ["overview", "#/"],
  ["requests", "#/requests"],
  ["models", "#/models"],
  ["limits", "#/limits"],
  ["notifications", "#/notifications"],
  ["settings", "#/settings"],
];
const SIZES = [
  ["desktop", 1440, 900],
  ["tablet", 768, 1024],
  ["phone", 390, 844],
];

async function open(page, hash, width, height) {
  await page.setViewportSize({ width, height });
  await page.goto(`/${hash}`);
  await page.waitForFunction(() => !document.getElementById("app")?.hidden);
  await page.waitForTimeout(900);
}

for (const [name, hash] of ROUTES) {
  for (const [size, width, height] of SIZES) {
    test(`${name} at ${size}`, async ({ page }) => {
      await open(page, hash, width, height);

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, "the page never scrolls sideways").toBeLessThanOrEqual(1);

      const unnamed = await page.evaluate(() => {
        const out = [];
        for (const button of document.querySelectorAll("button")) {
          const box = button.getBoundingClientRect();
          const style = getComputedStyle(button);
          if (box.width <= 1 || box.height <= 1) continue;
          if (style.display === "none" || style.visibility === "hidden") continue;
          const label = [
            button.getAttribute("aria-label"),
            button.textContent,
            button.title,
          ]
            .map((value) => (value || "").trim())
            .find(Boolean);
          if (!label) out.push(button.outerHTML.slice(0, 80));
        }
        return out;
      });
      expect(unnamed, "each visible button has a name").toEqual([]);

      const current = await page.locator(".tabs a[aria-current='page']").count();
      expect(current, "the active tab names the screen").toBe(1);
    });
  }
}

// The header tab bar and document title name the screen without a duplicate page-title h1.
for (const [name, hash] of ROUTES) {
  for (const [size, width, height] of SIZES) {
    test(`${name} at ${size} names itself in the header and title`, async ({ page }) => {
      await open(page, hash, width, height);
      const state = await page.evaluate(() => ({
        pageTitle: document.getElementById("page-title"),
        current: document.querySelector(".tabs a[aria-current='page']")?.dataset.page ?? null,
        title: document.title,
      }));
      const label = name.charAt(0).toUpperCase() + name.slice(1);
      expect(state.pageTitle).toBeNull();
      expect(state.current).toBe(name);
      expect(state.title).toContain(label);
    });
  }
}

test("a phone settings section is titled by its own name", async ({ page }) => {
  await open(page, "#/settings", 390, 844);
  const button = page.locator("#settings > .rail .sections button").first();
  const name = (await button.locator("span:not(.tile)").first().innerText()).trim();
  await button.click();
  await page.waitForTimeout(500);
  const headings = await page.evaluate(() =>
    [...document.querySelectorAll("h1")]
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 1 && box.height > 1 && getComputedStyle(el).visibility !== "hidden";
      })
      .map((el) => el.textContent.trim()),
  );
  expect(headings).toEqual([name]);
});

test("the header keeps one tab row on a phone", async ({ page }) => {
  await open(page, "#/models", 390, 844);
  const tabs = await page.evaluate(() => {
    const el = document.querySelector(".tabs");
    return {
      height: Math.round(el.getBoundingClientRect().height),
      scrolls: el.scrollWidth > el.clientWidth,
      wrap: getComputedStyle(el).flexWrap,
    };
  });
  expect(tabs.wrap).toBe("nowrap");
  expect(tabs.height).toBeLessThan(60);
  expect(tabs.scrolls).toBe(true);
});

for (const [size, width, height] of SIZES.concat([["desktop small", 1024, 900], ["tablet wide", 900, 900]])) {
  test(`a model name reads in full at ${size}`, async ({ page }) => {
    await open(page, "#/models", width, height);
    const clipped = await page.evaluate(() =>
      [...document.querySelectorAll("#models .model-open")]
        .filter((el) => el.getBoundingClientRect().width > 1 && el.scrollWidth > el.clientWidth + 1)
        .map((el) => el.textContent.trim().slice(0, 40)),
    );
    expect(clipped, "every model name reads in full").toEqual([]);
  });
}

test("a limit name reads in full on a phone and a desktop", async ({ page }) => {
  for (const [width, height] of [[390, 844], [768, 1024], [1440, 900]]) {
    await open(page, "#/limits", width, height);
    const clipped = await page.evaluate(() =>
      [...document.querySelectorAll("#limit-rows td.name .cell-value")]
        .filter((el) => el.getBoundingClientRect().width > 1 && el.scrollWidth > el.clientWidth + 1)
        .map((el) => el.textContent.trim().slice(0, 40)),
    );
    expect(clipped, `every limit name reads in full at ${width}`).toEqual([]);
  }
});

test("a phone model row meets the touch floor", async ({ page }) => {
  await open(page, "#/models", 390, 844);
  const row = await page.evaluate(() => {
    const button = document.querySelector(".models .model-open");
    const cells = [...document.querySelectorAll(".models tr td")];
    return {
      height: Math.round(button.getBoundingClientRect().height),
      lastCell: cells.length
        ? getComputedStyle(cells[cells.length - 1]).display
        : "missing",
    };
  });
  expect(row.height).toBeGreaterThanOrEqual(44);
  expect(row.lastCell, "the weight keeps its column on a phone").toBe("none");
});

test("a settings write reports in the pane header", async ({ page }) => {
  await open(page, "#/settings", 1440, 900);
  await page.locator(".sections button", { hasText: "Catalog" }).first().click();
  await page.locator("#set-catalog-every").click();
  const input = page.locator("#modal-input");
  await input.fill("7");
  await input.press("Enter");
  const line = page.locator(".save-line");
  await expect(line).toContainText(/Saved (just now|\d+ minutes? ago)/);
  await expect(line.getByRole("button", { name: "Undo" })).toBeVisible();
});

test("an empty model filter says so", async ({ page }) => {
  await open(page, "#/models", 1440, 900);
  await page.locator("#search").fill("zzzz-no-model");
  await expect(page.locator("#models .empty")).toContainText(
    "No models match the filter.",
  );
});
