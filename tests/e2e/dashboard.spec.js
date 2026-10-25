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
  await expect(line).toContainText(/Saved (just now|a minute ago|\d+ minutes ago)/);
  await expect(line.getByRole("button", { name: "Undo" })).toBeVisible();
});

test("an empty model filter says so", async ({ page }) => {
  await open(page, "#/models", 1440, 900);
  await page.locator("#search").fill("zzzz-no-model");
  await expect(page.locator("#models .empty")).toContainText(
    "No models match the filter.",
  );
});

test("a single section search match shows no stray dividers", async ({ page }) => {
  await open(page, "#/settings", 390, 844);
  await page.locator("#settings-search").fill("Personalization");
  const state = await page.evaluate(() => {
    const navs = [...document.querySelectorAll("#settings > .rail > .sections")];
    const visibleNavs = navs.filter((nav) => getComputedStyle(nav).display !== "none");
    const buttons = visibleNavs.flatMap((nav) =>
      [...nav.querySelectorAll("button")].filter((btn) => getComputedStyle(btn).display !== "none"),
    );
    const style = buttons[0] ? getComputedStyle(buttons[0]) : null;
    return {
      visibleNavCount: visibleNavs.length,
      visibleButtonCount: buttons.length,
      borderTop: style ? style.borderTopWidth : null,
      borderBottom: style ? style.borderBottomWidth : null,
    };
  });
  expect(state).toEqual({
    visibleNavCount: 1,
    visibleButtonCount: 1,
    borderTop: "0px",
    borderBottom: "0px",
  });
});

test("model cards show registered model limits and omit provider-only limits", async ({ page }) => {
  await open(page, "#/models", 1440, 900);
  await page.locator('button[aria-label="gemini/gemini-3.8-flash"]').first().click();
  await expect(page.locator("#model-modal")).toBeVisible();
  await expect(page.locator("#model-modal-body")).toContainText("5");
  await expect(page.locator("#model-modal-body")).toContainText("RPM");
  await expect(page.locator("#model-modal-body")).toContainText("250,000");
  await expect(page.locator("#model-modal-body")).toContainText("TPM");
  await page.keyboard.press("Escape");

  await page.locator('button[aria-label="groq/openai/gpt-oss-120b"]').first().click();
  await expect(page.locator("#model-modal")).toBeVisible();
  await expect(page.locator("#model-modal-body")).not.toContainText("RPM");
  await expect(page.locator("#model-modal-body")).not.toContainText("TPM");
  await page.keyboard.press("Escape");
});

test("requests tab omits page buttons and auto-loads older rows on scroll", async ({ page }) => {
  await open(page, "#/requests", 390, 560);
  await expect(page.locator("#requests-pager")).toHaveCount(0);
  await expect(page.locator("#requests-prev")).toHaveCount(0);
  await expect(page.locator("#requests-next")).toHaveCount(0);
  await expect(page.locator("#more-requests")).toHaveCount(0);

  await page.evaluate(() => {
    const now = Math.floor(Date.now() / 1000);
    const allRows = Array.from({ length: 65 }, (_, i) => ({
      at: now - i,
      status: 200,
      seconds: 0.4,
      app: "OWUI",
      session: `s${i + 1}`,
      key: "master",
      model: `demo/m${i + 1}`,
      via: `demo/m${i + 1}`,
      ttft: "0.120s",
      stream: true,
      fallbacks: "0",
      tokens: { input: 100, output: 50, estimate: false },
      attempts: [{ model: `demo/m${i + 1}`, result: "answered", error: "", seconds: 0.12 }],
    }));
    window.__requestedLimits = [];
    const baseFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("ui/api/requests")) {
        const limit = Number(new URL(url, location.origin).searchParams.get("limit") || 50);
        window.__requestedLimits.push(limit);
        return Promise.resolve(
          new Response(JSON.stringify(allRows.slice(0, limit)), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return baseFetch(input, init);
    };
  });
  await page.locator("#live-toggle").click();
  await page.locator("#live-toggle").click();

  await expect(page.locator("#requests tr.request")).toHaveCount(10);

  for (let step = 0; step < 8; step += 1) {
    await page.evaluate(() => {
      const main = document.querySelector("#app > main");
      if (main) {
        main.scrollTop = main.scrollHeight;
        main.dispatchEvent(new Event("scroll"));
      }
    });
    await page.waitForTimeout(80);
  }

  await expect.poll(() => page.locator("#requests tr.request").count()).toBeGreaterThan(50);
  const limits = await page.evaluate(() => window.__requestedLimits);
  expect(limits).toContain(100);
});

test("relative timestamps use abbreviated forms on mobile and full forms on desktop", async ({ page }) => {
  await open(page, "#/limits", 390, 844);
  const mobileChecked = await page.locator("#limits-checked").textContent();
  expect(mobileChecked).toMatch(/now|\d+(min|h|d|wk|mo|yr) ago/);
  expect(mobileChecked).not.toMatch(/just now|minutes? ago|hours? ago|yesterday|days ago/);

  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.locator("#limits-checked")).toContainText(
    /just now|a minute ago|\d+ minutes ago|an hour ago|\d+ hours ago|yesterday|\d+ days ago|last week|\d+ weeks ago|last month|\d+ months ago|last year|\d+ years ago/,
  );
});

test("section search matches section content and config keys and highlights matches", async ({ page }) => {
  await open(page, "#/settings", 1280, 900);
  await expect(page.locator("#settings-search")).toBeVisible();

  await page.locator("#settings-search").fill("Headroom");
  const optButton = page.locator('#settings > .rail > .sections button[data-section="optimization"]');
  await expect(optButton).toBeVisible();
  await expect(optButton.locator(".rail-match mark.search-hit")).toHaveText("Headroom");
  await expect(page.locator('#settings .card[data-section="optimization"]')).toBeVisible();
  await expect(page.locator('#settings .card[data-section="optimization"] mark.search-hit').first()).toHaveText("Headroom");

  await page.locator("#settings-search").fill("forward_owui_chat_id");
  const privButton = page.locator('#settings > .rail > .sections button[data-section="privacy"]');
  const yamlButton = page.locator('#settings > .rail > .sections button[data-section="yaml"]');
  await expect(privButton).toBeVisible();
  await expect(yamlButton).toBeVisible();
  await expect(privButton.locator(".rail-match")).toBeHidden();
  await expect(yamlButton.locator(".rail-match mark.search-hit")).toHaveText("forward_owui_chat_id");
  await expect(page.locator('#settings .card[data-section="privacy"]')).toBeVisible();
  await expect(page.locator('#settings .card[data-section="privacy"] .field[data-config-key="privacy.forward_owui_chat_id"]')).toHaveClass(/search-row-hit/);
  await expect(page.locator("#settings .config-key-hit")).toHaveCount(0);

  await page.locator("#settings-search").fill("");
  await expect(page.locator("#settings mark.search-hit")).toHaveCount(0);
  await expect(page.locator("#settings .search-row-hit")).toHaveCount(0);
});

test("time format defaults to 24h and updates stamps when switched to 12h in Settings", async ({ page }) => {
  await open(page, "#/notifications", 1280, 900);
  const firstStamp = await page.locator("#notification-feed time").first().getAttribute("title");
  expect(firstStamp).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);

  await page.goto("/#/settings");
  await page.locator('#settings > .rail > .sections button[data-section="personalization"]').click();
  const select = page.locator("#set-personalization-time_format");
  await expect(select).toHaveValue("24h");
  await select.selectOption("12h");
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.timeFormat)).toBe("12h");

  await page.goto("/#/notifications");
  const twelveHourStamp = await page.locator("#notification-feed time").first().getAttribute("title");
  expect(twelveHourStamp).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (AM|PM)$/);
});

test("rail stays clear of pane, rail search shows keyboard-only focus, and 960/640 folds hold", async ({ page }) => {
  await open(page, "#/settings", 1280, 900);
  const settingsBoxes = await page.evaluate(() => {
    const rail = document.querySelector("#settings > .rail").getBoundingClientRect();
    const pane = document.querySelector("#settings > .section-pane").getBoundingClientRect();
    return { railRight: rail.right, railWidth: rail.width, paneLeft: pane.left };
  });
  expect(settingsBoxes.railWidth).toBeLessThanOrEqual(241);
  expect(settingsBoxes.railRight).toBeLessThan(settingsBoxes.paneLeft);

  await page.locator("#settings-search").click();
  const pointerOutline = await page.evaluate(() => {
    const drawn = (el) => {
      const style = getComputedStyle(el);
      return style.outlineStyle === "none" ? 0 : parseFloat(style.outlineWidth) || 0;
    };
    return {
      boxWidth: drawn(document.querySelector("#settings .rail-search")),
      inputWidth: drawn(document.getElementById("settings-search")),
    };
  });
  expect(pointerOutline).toEqual({ boxWidth: 0, inputWidth: 0 });

  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  const keyOutline = await page.evaluate(() => {
    const style = getComputedStyle(document.querySelector("#settings .rail-search"));
    return style.outlineStyle === "none" ? 0 : parseFloat(style.outlineWidth) || 0;
  });
  expect(keyOutline).toBeGreaterThanOrEqual(2);

  await open(page, "#/requests", 1280, 900);
  await expect(page.locator("#requests-legend")).toBeVisible();
  await expect(page.locator("#legend-open")).toBeHidden();

  await open(page, "#/requests", 960, 900);
  const requestsAt960 = await page.evaluate(() => {
    const row = document.querySelector(".requests tr.request, .requests tr.live-row");
    return {
      rowDisplay: row ? getComputedStyle(row).display : "table-row",
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(requestsAt960.rowDisplay).toBe("table-row");
  expect(requestsAt960.overflow).toBeLessThanOrEqual(1);
  await expect(page.locator("#requests-legend")).toBeHidden();
  await expect(page.locator("#legend-open")).toBeVisible();
  await page.locator("#legend-open").click();
  await expect(page.locator("#chain-modal")).toBeVisible();
  await expect(page.locator("#chain-modal-body")).toContainText("Previous model exceeded the context limit");
  await page.keyboard.press("Escape");

  await open(page, "#/requests", 640, 900);
  const requestsAt640 = await page.evaluate(() => {
    const row = document.querySelector(".requests tr.request, .requests tr.live-row");
    return {
      rowDisplay: row ? getComputedStyle(row).display : "grid",
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(requestsAt640.rowDisplay).toBe("grid");
  expect(requestsAt640.overflow).toBeLessThanOrEqual(1);
  await expect(page.locator("#requests-legend")).toBeHidden();
  await expect(page.locator("#legend-open")).toBeVisible();

  await open(page, "#/limits", 640, 900);
  const limitsAt640 = await page.evaluate(() => {
    const main = document.querySelector("#app > main");
    const mainStyle = getComputedStyle(main);
    const contentWidth = main.clientWidth - parseFloat(mainStyle.paddingLeft) - parseFloat(mainStyle.paddingRight);
    const wide = document.querySelector('section[data-page="limits"] .column.wide').getBoundingClientRect();
    const searchRect = document.querySelector("#limits-search").getBoundingClientRect();
    const checkRect = document.querySelector(".limits-check-stack").getBoundingClientRect();
    return { contentWidth, wideWidth: wide.width, searchRight: searchRect.right, checkLeft: checkRect.left };
  });
  expect(limitsAt640.wideWidth).toBeGreaterThanOrEqual(limitsAt640.contentWidth - 2);
  expect(limitsAt640.checkLeft).toBeGreaterThanOrEqual(limitsAt640.searchRight);
});

const TOUCH_TARGET_FLOOR_PX = 44;
const SYMMETRY_TOLERANCE_PX = 1;
const BUTTON_SHIFT_TOLERANCE_PX = 0.5;
const FOCUS_RING_MIN_PX = 2;

test("skip link keeps route, switch suppresses pointer focus, and mobile key delete is symmetric", async ({ page }) => {
  await open(page, "#/models", 1280, 900);
  await page.evaluate(() => document.querySelector(".skip-link").focus());
  await expect.poll(async () => (await page.locator(".skip-link").boundingBox()).y).toBeGreaterThanOrEqual(0);
  await page.locator(".skip-link").dispatchEvent("click");
  const afterSkip = await page.evaluate(() => ({
    hash: location.hash,
    activeId: document.activeElement?.id || "",
  }));
  expect(afterSkip).toEqual({ hash: "#/models", activeId: "main" });

  await open(page, "#/settings?section=balance", 1280, 900);
  const switchPointerOutline = await page.evaluate(() => {
    delete document.documentElement.dataset.keyboardNav;
    const sw = document.getElementById("set-balance-weights");
    sw.focus();
    const style = getComputedStyle(sw);
    return style.outlineStyle === "none" ? 0 : parseFloat(style.outlineWidth) || 0;
  });
  expect(switchPointerOutline).toBe(0);

  await page.keyboard.press("Tab");
  await page.evaluate(() => document.getElementById("set-balance-weights").focus());
  const switchKeyOutline = await page.evaluate(() => {
    const style = getComputedStyle(document.getElementById("set-balance-weights"));
    return style.outlineStyle === "none" ? 0 : parseFloat(style.outlineWidth) || 0;
  });
  expect(switchKeyOutline).toBeGreaterThanOrEqual(FOCUS_RING_MIN_PX);

  await open(page, "#/settings?section=maintenance", 1280, 900);
  const updateWidths = await page.evaluate(async () => {
    const btn = document.getElementById("update-check");
    const idle = btn.getBoundingClientRect().width;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const origFetch = window.fetch;
    window.fetch = async (url, opts) => {
      if (String(url).includes("/updates")) await gate;
      return origFetch(url, opts);
    };
    btn.click();
    await new Promise((r) => setTimeout(r, 10));
    const busy = btn.getBoundingClientRect().width;
    const ariaBusy = btn.getAttribute("aria-busy");
    release();
    await new Promise((r) => setTimeout(r, 20));
    window.fetch = origFetch;
    return { idle, busy, ariaBusy };
  });
  expect(updateWidths.ariaBusy).toBe("true");
  expect(Math.abs(updateWidths.busy - updateWidths.idle)).toBeLessThanOrEqual(BUTTON_SHIFT_TOLERANCE_PX);

  await open(page, "#/settings?section=keys", 360, 800);
  const trashBtn = page.locator("#keys button[data-key]").first();
  await expect(trashBtn).toBeVisible();
  const trashBox = await trashBtn.boundingBox();
  expect(trashBox.width).toBeGreaterThanOrEqual(TOUCH_TARGET_FLOOR_PX);
  expect(trashBox.height).toBeGreaterThanOrEqual(TOUCH_TARGET_FLOOR_PX);
  expect(Math.abs(trashBox.width - trashBox.height)).toBeLessThanOrEqual(SYMMETRY_TOLERANCE_PX);
});