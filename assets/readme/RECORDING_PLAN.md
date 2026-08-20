# README motion assets — what is missing and how to shoot it

The README currently proves the product with **still** screenshots, all of them
generated from a real running build by [`capture.mjs`](capture.mjs). What it does not
yet have is motion, and motion is the only way to show the one thing that is hard to
believe from a still: **the figure follows the cursor, and matplotlib only runs when
you let go.**

Nothing in here has been shot yet. Do not fake it — no CSS re-creations of the
interface, no AI-generated frames, no still image animated to imply an interaction
that did not happen. If a clip cannot be recorded from the real app, it does not ship.

## House rules for every clip

| | |
|---|---|
| Format | `.webp`, animated, looping. GitHub renders it inline; it beats GIF on both size and colour. |
| Window | 1440 × 800 logical, captured at 2×, exported at 1440 wide |
| Frame rate | 30 fps capture, 24 fps export |
| Budget | ≤ 5 MB each; `hero-demo` ≤ 3 MB because it is above the fold |
| Interface language | Shoot **en-US** first (`localStorage['tavotto.locale']`). A `zh-CN` pass, named `*.zh.webp`, matches how the stills are paired — worth doing for demo 1, optional for the rest |
| Project | `examples/figures`, copied to a throwaway directory — never a real user path |
| Cursor | Visible. Half the point is that a hand is doing this. |
| Pointer motion | Human speed. Do not scrub a slider at machine speed; it reads as a video effect rather than an interaction. |
| Start and end | Hold the first and last frame ~0.6 s so a loop does not feel like a jump cut |
| Chrome | No macOS traffic lights, no OS menu bar, no notifications. Quit anything that can raise a banner. |

Record with the same throwaway-instance boot that `capture.mjs` uses (`node assets/readme/capture.mjs [locale]` also documents every selector worth clicking), so the window is
identical to the stills; drive it by hand and capture with any screen recorder, or
drive it from Playwright and use `context.newContext({ recordVideo })` then convert.

---

## Demo 1 — `hero-demo.webp` · the core loop

**Goes:** directly under the hero, replacing nothing — the `workbench.png` still stays
as the caption-bearing product proof below it.

**Length:** 8–12 s.

1. Start on the composed page from the stills, nothing selected. (0.6 s hold)
2. Double-click panel (a). The overlay appears; the first build finishes.
   *Cut the build wait out* — it is honest to show a spinner in a still, dishonest to
   make a 20-second cold start look like 0.4 s in a loop. Cut on the frame the figure
   becomes editable and never claim otherwise in the caption.
3. Click the title. Selection frame and handles appear; the properties panel fills in.
4. Drag the size field from 9 to 12 — **slowly**, so the type visibly grows with the
   pointer and it is clear no re-run is happening between frames.
5. Drag the legend a few millimetres. Release. (0.6 s hold on the settled figure.)

**What a viewer must take away:** the thing being edited is the real figure, and it
responds continuously.

---

## Demo 2 — `layout-demo.webp` · composing the page

**Goes:** in *Build the page*, above or replacing `layout.png`.

**Length:** 8–10 s.

1. Page with panels (a) and (b) placed, (c) still only in the asset library.
2. Drag (c) from the library onto the page.
3. Move it until the snap guide to (b)'s left edge appears — pause one beat *on* the
   guide, which is the frame worth having.
4. Release. Select all three, click distribute-vertically.
5. Click "Add panel labels"; (a)(b)(c) appear. (0.6 s hold)

---

## Demo 3 — `preflight-demo.webp` · catching a problem

**Goes:** in *Export for publication*, after `preflight.png`.

**Length:** 6–9 s.

1. Export dialog open, preflight expanded with the real findings the example project
   produces (two blocking, two warnings, three suggestions, one not verifiable).
2. Click **Locate** on the "below the profile's 8.5 pt" finding — the canvas scrolls
   and highlights the offending text.
3. Fix it: raise the panel's scale, or the text size.
4. Re-open Export. The blocking count drops. (0.6 s hold on the shorter list.)

**Do not** stage a green "all checks passed" ending unless the example project really
passes; at time of writing it does not. Showing the tool catching something real is the
stronger clip anyway.

---

## Demo 4 — `handoff-demo.webp` · `tavotto open`

**Goes:** in *Finish figures an AI wrote*. Lowest priority of the four; the workflow
SVG already carries the idea.

**Length:** 6–8 s. Terminal on the left, Tavotto window on the right. Type
`tavotto open figures/Fig1_kinetics.pdf`, and let the recording show the figure landing
in the window that was already open. The point is that no second copy of the app
starts.

---

## Also missing

- **A social preview / Open Graph card, 1200 × 630.** `hero.svg` is composed to
  survive that crop — the wordmark block and the editor card both sit inside the
  middle 1200 × 630 if the ivory ground is extended vertically. Worth generating for
  the GitHub repository social preview, X and Hacker News.
- **A before/after pair.** Only worth making from a genuine messy-to-finished
  example. Do not manufacture an ugly "before" to flatter the "after".
