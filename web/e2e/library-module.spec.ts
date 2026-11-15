import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import { card, primary } from "./prepCard";

/**
 * 只定义了画图函数的库模块（TVD-Y5JF，ADR 0188）——真后端、真浏览器。判据的主语是入口脚本写在仓库外的运行日志（执行了几次）
 * 与库模块自己从没被执行（它也写日志），不是界面文案。
 *
 *   1. 项目开在仓库里的包目录上、入口在仓库根（项目外）：「打开并运行」库模块 → 卡片在运行之前说出入口，不代用户运行、不装依赖；
 *      点「打开并运行」→ 打开仓库文件夹并运行入口脚本，入口恰好执行一次，库模块零次。
 *   2. 入口在项目里：主按钮「运行它」，在当前项目里运行入口。
 *   3. 一个入口都没有：只说这一句，主按钮「仍然运行」。
 *
 * `TAVOTTO_SHOT_DIR` 设了就留截图（`TAVOTTO_SHOT_SCHEME=dark` 换深色），给维护者看界面用；不设不截。
 */
const LIB = (log: string) =>
  [
    '"""Plot helpers; the entry script prepares the data."""',
    "import matplotlib",
    'matplotlib.use("Agg")',
    "import matplotlib.pyplot as plt",
    "from matplotlib.colors import LinearSegmentedColormap",
    "",
    'CMAP = LinearSegmentedColormap.from_list("c", ["#1f77b4", "#d62728"])',
    `LOG = ${JSON.stringify(log)}`,
    "",
    "",
    "def plot_curves(xs, ys, out_path):",
    "    fig, ax = plt.subplots(figsize=(3, 2))",
    "    ax.plot(xs, ys, color=CMAP(0.2))",
    "    fig.savefig(out_path)",
    "",
  ].join("\n");

const ENTRY = (importLine: string, log: string) =>
  [
    importLine,
    `with open(${JSON.stringify(log)}, "a", encoding="utf-8") as f:`,
    '    f.write("entry\\n")',
    'plot_curves([0, 1, 2], [0, 1, 4], "curves.pdf")',
    "",
  ].join("\n");

const lines = (log: string) =>
  existsSync(log) ? readFileSync(log, "utf-8").split("\n").filter(Boolean) : [];

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.TAVOTTO_SHOT_DIR;
  if (!dir) return;
  const scheme = process.env.TAVOTTO_SHOT_SCHEME === "dark" ? "dark" : "light";
  await card(page).screenshot({
    path: path.join(dir, `${name}-${scheme}.png`),
  });
}

async function openAndRunFromDiscover(
  page: Page,
  baseURL: string,
): Promise<void> {
  if (process.env.TAVOTTO_SHOT_SCHEME === "dark")
    await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto(baseURL);
  await expect(page.locator("[data-work-panel]")).toBeVisible({
    timeout: 30_000,
  });
  // 导入即扫描发现了这个脚本：右下引导卡弹出，主按钮是「打开并运行」（与脚本行 ▶ 同一个入口）
  await expect(card(page)).toHaveAttribute("data-prep-state", "discover", {
    timeout: 30_000,
  });
  await primary(page).click();
}

test(
  "功能：只定义了画图函数的文件，运行前指向项目外调用它的脚本，「打开并运行」打开仓库并运行它（恰好一次）",
  { tag: "@feature:prep.library-module-entry" },
  async ({ app, page }) => {
    const root = mkdtempSync(path.join(os.tmpdir(), "tavotto-lib-"));
    const repo = path.join(root, "repo");
    const pkg = path.join(repo, "labkit");
    const log = path.join(root, "runs.txt");
    mkdirSync(pkg, { recursive: true });
    writeFileSync(
      path.join(repo, "pyproject.toml"),
      '[project]\nname = "labkit"\n',
      "utf-8",
    );
    writeFileSync(path.join(pkg, "__init__.py"), "", "utf-8");
    writeFileSync(path.join(pkg, "plot_helpers.py"), LIB(log), "utf-8");
    writeFileSync(
      path.join(repo, "make_all_figures.py"),
      ENTRY("from labkit.plot_helpers import plot_curves", log),
      "utf-8",
    );
    const a = await app({ figures: pkg });
    try {
      await openAndRunFromDiscover(page, a.baseURL);
      await expect(card(page)).toHaveAttribute(
        "data-prep-state",
        "library_module",
        { timeout: 60_000 },
      );
      await expect(card(page).locator("[data-prep-line]")).toHaveText(
        "plot_helpers.py 只定义了画图函数，真正画图的是项目外的 make_all_figures.py",
      );
      await expect(primary(page)).toHaveText("打开并运行");
      await expect(primary(page)).toHaveAttribute(
        "data-prep-primary",
        "run_entry",
      );
      await shot(page, "outside");
      expect(lines(log)).toEqual([]); // 没代用户运行任何东西

      await primary(page).click();
      // 换到仓库文件夹，入口脚本跑完、图出来
      await expect
        .poll(
          async () =>
            (
              (await (
                await page.request.get(`${a.baseURL}/api/project`)
              ).json()) as { figures_dir?: string }
            ).figures_dir ?? "",
          {
            timeout: 60_000,
          },
        )
        .toBe(realpathSync(repo));
      await expect
        .poll(() => lines(log), { timeout: 120_000 })
        .toEqual(["entry"]);
      // 入口画出的那一张图：跑完恰好一张，自动进入图内编辑（与「打开并运行」同一条收尾）
      await expect(page.getByText("curves").first()).toBeVisible({
        timeout: 120_000,
      });
      if (process.env.TAVOTTO_SHOT_DIR) {
        const scheme =
          process.env.TAVOTTO_SHOT_SCHEME === "dark" ? "dark" : "light";
        await page.screenshot({
          path: path.join(process.env.TAVOTTO_SHOT_DIR, `after-${scheme}.png`),
        });
      }
      expect(lines(log)).toEqual(["entry"]);
    } finally {
      await a.stop();
    }
  },
);

test(
  "功能：只定义了画图函数的文件，「运行它」在当前项目里运行调用它的脚本",
  { tag: "@feature:prep.library-module-entry" },
  async ({ app, page }) => {
    const root = mkdtempSync(path.join(os.tmpdir(), "tavotto-lib-"));
    const project = path.join(root, "proj");
    const log = path.join(root, "runs.txt");
    mkdirSync(path.join(project, "tools"), { recursive: true });
    writeFileSync(path.join(project, "plot_helpers.py"), LIB(log), "utf-8");
    writeFileSync(
      path.join(project, "tools", "make_figures.py"),
      ENTRY(
        "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\nfrom plot_helpers import plot_curves",
        log,
      ),
      "utf-8",
    );
    const a = await app({ figures: project });
    try {
      await openAndRunFromDiscover(page, a.baseURL);
      await expect(card(page)).toHaveAttribute(
        "data-prep-state",
        "library_module",
        { timeout: 60_000 },
      );
      await expect(card(page).locator("[data-prep-line]")).toHaveText(
        "plot_helpers.py 只定义了画图函数，真正画图的是 make_figures.py",
      );
      await expect(primary(page)).toHaveText("运行它");
      await shot(page, "inside");
      expect(lines(log)).toEqual([]);
      await primary(page).click();
      await expect
        .poll(() => lines(log), { timeout: 120_000 })
        .toEqual(["entry"]);
      await expect(page.getByText("curves").first()).toBeVisible({
        timeout: 120_000,
      });
      expect(lines(log)).toEqual(["entry"]);
    } finally {
      await a.stop();
    }
  },
);

test("一个入口都没有：只说这一句，主按钮「仍然运行」", async ({
  app,
  page,
}) => {
  const root = mkdtempSync(path.join(os.tmpdir(), "tavotto-lib-"));
  const project = path.join(root, "proj");
  const log = path.join(root, "runs.txt");
  mkdirSync(project, { recursive: true });
  writeFileSync(path.join(project, "plot_helpers.py"), LIB(log), "utf-8");
  const a = await app({ figures: project });
  try {
    await openAndRunFromDiscover(page, a.baseURL);
    await expect(card(page)).toHaveAttribute(
      "data-prep-state",
      "library_module_none",
      { timeout: 60_000 },
    );
    await expect(card(page).locator("[data-prep-line]")).toHaveText(
      "plot_helpers.py 只定义了画图函数、没有调用，运行它不会出图",
    );
    await expect(primary(page)).toHaveText("仍然运行");
    await shot(page, "none");
  } finally {
    await a.stop();
  }
});
