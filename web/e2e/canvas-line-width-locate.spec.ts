import { readFileSync } from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import { expect, test, waitForDocumentReady } from "./fixtures";

/**
 * 画布上的线条也判期刊线宽下限（ADR 0208 §四 第 7 条，「都管」）：真浏览器里走一遍——
 * 用工具条画一支箭头、把线宽改到 0.1 pt、选一本规定了最小线宽的期刊，问题清单出「线条太细」，
 * 点那一行：选中那支箭头，焦点落到属性栏的「线宽」输入框上。中英两种语言各走一遍（chromium-en 也跑这条）。
 *
 * 期刊目录用夹具（`tests/golden/journal_vectors.json` 的那份，经 `tavotto.journalCatalog` 本地缓存槽喂进去，
 * 与 `journal-picker.spec.ts` 同一个做法），给 Fixture Beta 加一条最小线宽 0.5 pt 的规则。
 */
const CATALOG = (() => {
  const c = JSON.parse(
    readFileSync(
      path.resolve(
        import.meta.dirname,
        "..",
        "..",
        "tests",
        "golden",
        "journal_vectors.json",
      ),
      "utf8",
    ),
  ).catalog;
  c.journals["fx-beta"].rules.min_line_width_pt = {
    value: 0.5,
    src: "s9",
    quote: "FIXTURE lines at least 0.5 pt",
    qualifier: "min",
  };
  return c;
})();

test.beforeEach(async ({ page }) => {
  await page.addInitScript((catalog) => {
    try {
      localStorage.setItem("tavotto.journalCatalog", JSON.stringify(catalog));
      localStorage.setItem("tavotto.dev.journalCatalogUi", "1");
      const saved = JSON.parse(localStorage.getItem("tavotto.ui") || "{}");
      localStorage.setItem(
        "tavotto.ui",
        JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: false }),
      );
    } catch {
      /* 存储不可用时照常跑 */
    }
  }, CATALOG);
});

async function closeSettings(page: Page) {
  const shell = page.locator("[data-settings-shell]");
  await expect(async () => {
    if (await shell.count()) await page.keyboard.press("Escape");
    await expect(shell).toHaveCount(0, { timeout: 1_500 });
  }).toPass({ timeout: 15_000 });
}

test(
  "画布上的细箭头：问题清单出「线条太细」，点它选中箭头、焦点落到线宽",
  { tag: "@feature:preflight.canvas-line-width" },
  async ({ app, page }) => {
    const a = await app();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(a.baseURL);
    await page
      .locator('[data-card="Fig1_kinetics.pdf"]')
      .dblclick({ timeout: 30_000 });
    await waitForDocumentReady(page);

    // ── 画一支箭头 ───────────────────────────────────────────────────────
    await page.locator('[data-tool-menu="annotate"]').click();
    await page.locator('[data-mark-tool="arrow"]').click();
    const stage = await page.locator("[data-canvas-stage]").boundingBox();
    if (!stage) throw new Error("没有画布舞台");
    const x0 = stage.x + stage.width * 0.3;
    const y0 = stage.y + stage.height * 0.3;
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    await page.mouse.move(x0 + 120, y0 + 60, { steps: 8 });
    await page.mouse.up();

    // ── 线宽改到 0.1 pt ─────────────────────────────────────────────────
    const width = page.locator('[data-prop="strokePt"] input');
    await expect(width).toBeVisible({ timeout: 15_000 });
    await width.fill("0.1");
    await width.press("Enter");
    await expect(width).toHaveValue(/^0\.1/);

    // ── 选一本规定了最小线宽的期刊 ────────────────────────────────────────
    await page.locator('[data-rail="settings"]').click();
    const shell = page.locator("[data-settings-shell]");
    await expect(shell).toBeVisible();
    await shell.locator('[data-section="spec"]').click();
    await page.locator("[data-journal-picker]").click();
    await page.locator("[data-journal-search]").fill("fixture beta");
    await page.locator('[data-journal-option="fx-beta"]').click();
    await expect(page.locator("[data-profile-journal]")).toContainText(
      "Fixture Beta",
    );
    await closeSettings(page);

    // ── 问题清单：点「线条太细」那一行 ───────────────────────────────────
    // 先在空白处点一下，取消选中箭头——定位要自己把它选回来
    await page.keyboard.press("Escape");
    const rail = page.locator('[data-rail="problems"]');
    if ((await rail.getAttribute("aria-expanded")) !== "true")
      await rail.click();
    // 单图编辑时问题清单缺省看「当前图」（只列对象就是这张图的那些）；画上去的箭头是画布对象，切到「全部」
    await page.locator("[data-problem-scope-trigger]").click();
    await page.locator('[data-problem-scope="document"]').click();
    // 菜单关闭后 Radix 会把焦点还给触发钮，那一下是延迟的：先等它发生，再去点问题行——否则它可能在定位把焦点
    // 落到线宽框之后才到，把焦点抢回范围钮（真实用户选完范围再点一行，中间远不止这几十毫秒）
    await expect
      .poll(
        () =>
          page.evaluate(
            () =>
              !!document.activeElement?.closest("[data-problem-scope-trigger]"),
          ),
        {
          message: "范围菜单关闭后焦点应回到触发钮",
          timeout: 5_000,
        },
      )
      .toBe(true);
    // 画布对象的问题在「图」那张卡里（按对象分卡）：卡与卡里的规则组可能折叠着，按状态展开
    const card = page.locator(
      'li[data-problem-card-rules~="line-width-below-min"]',
    );
    await expect(card).toHaveCount(1, { timeout: 30_000 });
    const cardToggle = card.locator(":scope > button").first();
    if ((await cardToggle.getAttribute("aria-expanded")) !== "true")
      await cardToggle.click();
    const groupToggle = card.locator(
      '[data-issue-group-toggle][aria-expanded="false"]',
    );
    for (let i = await groupToggle.count(); i > 0; i--)
      await groupToggle.first().click();
    const row = card.locator(
      '[data-issue-row][data-issue-rule="line-width-below-min"]',
    );
    await expect(row).toHaveCount(1, { timeout: 15_000 });
    await row.click();

    // 选中的是那支箭头，焦点在「线宽」输入框里
    await expect
      .poll(
        () =>
          page.evaluate(
            () => !!document.activeElement?.closest('[data-prop="strokePt"]'),
          ),
        {
          message: "焦点没有落到线宽输入框上",
          timeout: 10_000,
        },
      )
      .toBe(true);
    await expect(width).toHaveValue(/^0\.1/);
  },
);
