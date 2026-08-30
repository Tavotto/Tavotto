/**
 * 大图预览的**浏览器侧结构性回归**（issue #181 / ADR 0022 / Session 05）。
 *
 * 引擎那一侧的判据已经有一整套（`tests/test_preview_*.py`），它们钉的是
 * 「产物有多少字节、多少个 `<path>`」。但 #181 的症状发生在**浏览器**里：
 * 126 MB 的字符串在 JS 堆里放着是一回事，展开成 66 万个 DOM 节点是另一回事。
 * 这个 spec 就守那一步——**真浏览器、真后端、真 matplotlib**。
 *
 * ## 判据是结构性的，不是计时的
 *
 * 节点数与 `<path>` 数在同一台机器上是确定的；wall time 与内存不是。
 * CI 上做一条按毫秒/字节的闸只会得到一个随机红的门禁，而随机红的门禁最后
 * 一定会被人忽略掉（比没有门禁更坏）。所以这里只断言**结构**：
 *
 *     preview 落到 hybrid 或安全的 raster，不是 vector
 *     DOM 里的 SVG 元素数远低于预算
 *     选中 / 属性面板 / 撤销照常工作
 *
 * 绝对内存与 WebView2 的读数属于 release/perf gate（`scripts/
 * bench_large_preview_windows.ps1`），不在这条 CI 闸里。
 *
 * ## 为什么用小 n
 *
 * `TAVOTTO_ISSUE181_MESH_N=120` 每格 14 400 个 cell、三格 43 200——**刚好越过**
 * `TOTAL_VECTOR_PRIMITIVE_BUDGET`（50 000）需要的量级，而 `MESH_CELL_BUDGET`
 * （20 000）单格就已经不够它越了……所以这里用 160（每格 25 600 > 20 000，
 * 单格自己就越线）。默认的 470 要画 11 秒纯矢量对照，CI 上不值得等，而
 * **判据问的是机制不是规模**：66 万还是 7 万个 `<path>`，同一条闸。
 */
import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { startApp, test, type RunningApp } from "./fixtures";

const REPO = path.resolve(import.meta.dirname, "..", "..");

/** 每格 25 600 个 cell，单格就越过 `MESH_CELL_BUDGET`（20 000）。 */
const MESH_N = "160";

/**
 * **这张图在纯矢量画法下会摊出多少个 `<path>`**：3 × 160² + 72 ≈ 76 872。
 * 判据用它做相对比较（Session 05 §4：相对 threshold 优先）——绝对数字会随
 * matplotlib 版本漂，而「比纯矢量少两个数量级」不会。
 */
const VECTOR_PATH_COUNT = 3 * 160 * 160;

/**
 * 画布上**相关** DOM 节点的结构性预算。
 *
 * Session 05 §3 建议的第一版是 20 000。#181 修好之后这张图的预览 SVG 是
 * 几百个元素（默认规模 n=470 实测 **818** 个），低两个数量级——预算留得宽，
 * 是因为它要挡的是「66 万个 `<path>` 重新进入浏览器」那一类灾难，不是
 * 几百个节点的浮动。
 */
const DOM_NODE_BUDGET = 20_000;

/**
 * #181 的合成图库：脚本 + registry 就是一个合法项目，数据由 rng(181) 现生成。
 *
 * **产物要现跑一次**：`examples/figures` 里的 `.pdf` 是提交进仓库的，而这个
 * fixture 刻意不提交产物（默认规模下 SVG 一百多 MB）。少了这一步素材列表是
 * 空的（实测 `/api/panels` 回 `panels: []`），双击等于点在一个不存在的东西上
 * ——最后红在超时，而超时红看起来跟「大图把浏览器打死了」一模一样。
 *
 * n=160 实测 2.5 秒，比起后面那次冷 build 可以忽略。
 */
function largeFigureLibrary(): string {
  const dir = path.join(
    mkdtempSync(path.join(os.tmpdir(), "tavotto-e2e-large-")),
    "figures",
  );
  cpSync(path.join(REPO, "tests", "fixtures", "large_figures"), dir, {
    recursive: true,
    // __pycache__ 不拷：它是别处跑出来的字节码，与这次无关
    filter: (src) => !src.includes("__pycache__"),
  });
  // **不能用 `TAVOTTO_PYTHON`**：那是 Flask 侧的解释器，按依赖边界它
  // **刻意不装 matplotlib**（`tests/conftest.py` 头一句就写着这件事）。
  // fixture 脚本要的是 worker 那一侧的解释器。
  // Windows 上没有 `python3`（setup-python 装的是 `python`），而
  // `windows-exe-smoke` 的 e2e 那一步刻意把 `TAVOTTO_WORKER_PYTHON` 清空
  // 去验内置 runtime——退路写死 `python3` 的话这条 spec 在 Windows 腿上
  // 只会红在「找不到解释器」，与它要看护的事毫无关系。
  const fallback = process.platform === "win32" ? "python" : "python3";
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback;
  execFileSync(py, ["issue_181_large_pcolormesh.py"], {
    cwd: dir,
    env: { ...process.env, TAVOTTO_ISSUE181_MESH_N: MESH_N },
    timeout: 180_000,
  });
  return dir;
}

/**
 * 起应用 → **双击素材把面板放上画布** → 等它画完。
 *
 * 画布默认是空的（`golden-paths.spec.ts` 同一条路）。少了双击那一步，等的是
 * 一个永远不会出现的选择器，最后红在超时上——而超时红看起来跟「大图把浏览器
 * 打死了」一模一样，那是最容易把人带偏的一种假红。
 */
async function openLargePanel(
  app: RunningApp,
  page: import("@playwright/test").Page,
) {
  const tOpen = Date.now();
  await page.goto(app.baseURL);
  // **先挂上等待再触发**：`page.on('response')` 的回调是 async 的，
  // `res.json()` 还没 resolve 时断言就已经跑了（实测 verdicts 恒为空）。
  // `waitForResponse` 是确定的——它 resolve 的那一刻响应体已经在手上。
  const rendered = page.waitForResponse(
    (r) => r.url().includes("/api/engine/render") && r.status() === 200,
    { timeout: 150_000 },
  );
  // 素材名来自 registry 的 stems，产物是 `<stem>.pdf`
  await page
    .getByText("Issue181_large_pcolormesh.pdf")
    .dblclick({ timeout: 60_000 });
  await expect(page.getByText("画布是空的")).toHaveCount(0);
  // 走引擎（`/api/engine/render`，带 manifest 与 preview 裁决）的是**图内编辑
  // 态**；没进那个态的话，上面的 `waitForResponse` 等的是一个永远不会来的响应。
  //
  // **进入的方式随「打开」的语义变过一次**：Prompt 09 之前双击只是把面板放上
  // 画布（画的是 `/api/render` 的 PNG，引擎一次都没跑），要再点一下右栏的
  // 「编辑图内元素」；Prompt 09 之后双击当场就进图内编辑态，那颗按钮**不存在**
  // （它变成了「退出图内编辑」）。所以这里不写死走哪一条，而是先等到「二者之
  // 一出现」，只有按钮真在时才点它——无条件点会红在「找不到按钮」的超时上，
  // 而那个红长得跟「大图把浏览器打死了」一模一样。
  const inElementEdit = page.locator("[data-element-svg], [data-display]").first();
  const enterElementEdit = page.getByRole("button", { name: "编辑图内元素" });
  await expect
    .poll(
      async () => (await inElementEdit.count()) > 0 || (await enterElementEdit.count()) > 0,
      { timeout: 60_000 },
    )
    .toBe(true);
  if (await enterElementEdit.count()) await enterElementEdit.first().click();
  // 冷 build（含首次预览）在大图上是最慢的一步
  const panel = page.locator("[data-element-svg], [data-display]").first();
  await expect(panel).toBeVisible({ timeout: 150_000 });
  const body = await (await rendered).json();
  console.log(`[e2e-large] 打开面板 ${Date.now() - tOpen}ms`);
  return {
    panel,
    preview: body?.preview as { mode: string; reason: string } | undefined,
  };
}

// **一个应用跑完两条**：各起一次的话，应用启动、图库生成、冷 build 全要付
// 两遍，而这条 spec 本来就是全套 E2E 里最贵的一条。串行是必须的——共享的是
// 同一个后端与同一份磁盘状态。
test.describe.configure({ mode: "serial" });

let app: RunningApp;

test.beforeAll(async () => {
  const t0 = Date.now();
  const figures = largeFigureLibrary();
  const t1 = Date.now();
  app = await startApp({ figures, env: { TAVOTTO_ISSUE181_MESH_N: MESH_N } });
  console.log(`[e2e-large] 图库 ${t1 - t0}ms · 应用启动 ${Date.now() - t1}ms`);
});

test.afterAll(async () => {
  await app?.stop();
});

test("大图预览：落到 hybrid/raster，DOM 不再吃下几十万个节点，且照常可编辑", async ({
  page,
}) => {
  const a = app;
  // 引擎的裁决从**响应**里读，不从界面上猜：mode 是协议里的一等公民
  const { preview } = await openLargePanel(a, page);

  /* ---- 1. 引擎的裁决：不许是 vector ---- */
  expect(preview, "渲染响应里必须带 preview 元数据").toBeTruthy();
  expect(
    ["hybrid", "raster"],
    `这张图必须降档；实得 mode=${preview!.mode} reason=${preview!.reason}`,
  ).toContain(preview!.mode);

  /* ---- 2. 结构性预算：DOM 里没有几十万个节点 ---- */
  const dom = await page.evaluate(() => {
    const host = document.querySelector("[data-element-svg]");
    return {
      // 画布上那份内联 SVG 展开成了多少个元素
      svgElements: host ? host.querySelectorAll("*").length : 0,
      paths: document.querySelectorAll("path").length,
      total: document.getElementsByTagName("*").length,
    };
  });
  expect(dom.total, `整页 DOM 节点 ${dom.total} 超预算`).toBeLessThan(
    DOM_NODE_BUDGET,
  );
  // **相对判据**：比纯矢量画法少两个数量级。绝对数字会随 matplotlib 漂。
  expect(
    dom.paths,
    `<path> ${dom.paths} 个——纯矢量画法是 ${VECTOR_PATH_COUNT} 个，必须低两个数量级`,
  ).toBeLessThan(VECTOR_PATH_COUNT / 100);

  /* ---- 3. 降档 ≠ 停止编辑（不变量 4）---- */
  // 命中层挂着，且它报得出自己的几何权威状态。`data-authority` 是
  // `ElementHitLayer` 自己渲染的属性（ready / syncing）——比「有没有某个
  // div」强的地方在于：它同时证明了命中层**知道自己此刻算不算权威**，
  // 而那正是 raster/hybrid 档下最容易被悄悄弄丢的东西。
  await expect(page.locator("[data-authority]").first()).toBeAttached({
    timeout: 15_000,
  });
});

test("降档之后：选中图内元素、属性面板打得开、撤销回得去", async ({ page }) => {
  const { panel } = await openLargePanel(app, page);

  // 第四格那两条普通曲线与图例**没有被 rasterize**（hybrid 的契约），
  // 所以图内元素照常选得中。点画布中心附近，取第一个可命中的元素。
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);

  // 属性面板打得开 = 语义编辑这条路没断
  const inspector = page
    .locator('[data-testid="inspector"], aside, [role="complementary"]')
    .first();
  await expect(inspector).toBeVisible({ timeout: 15_000 });

  // **撤销把「双击加面板」那一步撤掉，画布回到空——这正是它该做的。**
  // 第一版在这里断言「面板还在」，红了；红得有道理，是断言写错了不是产品错了。
  // 大图上真正值得守的是「撤销/重做不炸、渲染态跟得上」，所以走一个来回。
  await page.keyboard.press("ControlOrMeta+z");
  await expect(page.getByText("画布是空的")).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press("ControlOrMeta+Shift+z");
  await expect(
    page.locator("[data-element-svg], [data-display]").first(),
  ).toBeVisible({
    timeout: 150_000,
  });
});
