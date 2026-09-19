## 基线与风险

<!-- 审查人第一眼要看的几行。分层规则与它决定的入队方式见 docs/ci/pr-review-tiers.md。 -->

- Base / head：`main @ <sha>` → `<head sha>`
- 风险等级：低 / 中 / 高 —— 理由：<!-- 纯文档 / 纯测试 / 逐字搬家且分发表逐字节相同 = 低；改行为、协议、CI 拓扑、控制流 = 中高 -->
- 唯一目的：<!-- 一句话；一个 PR 只做一件事 -->
- 非目标：<!-- 有意不做、留给别的 PR 的 -->
- 受影响契约：<!-- 同源对 / ADR / docs/rules 里的哪几条；没有就写「无」 -->

## 并行开发

<!-- 与其他 open PR 无关时整段删掉。冲突域与两种协作形态见 docs/ci/parallel-prs.md。 -->

- Depends on: <!-- 阻塞本 PR 的 PR/issue 编号 -->
- Stack parent: <!-- stacked PR 的下层分支；没有就删 -->
- Conflict domain: <!-- 「PR conflict domains」检查报出的域名，如 mcp-widget -->
- 是否修改受管生成物（canvas.html / playground 产物）：
- 生成物是否已在**最终源码状态**上重新生成（train 只重建一次）：
- 是否需要 train branch：

## What this changes

<!-- What a user would notice, or what boundary this moves. One or two sentences. -->

## Why

<!-- The reasoning, or the symptom this fixes. For a fix, say what the user saw:
     "double-clicking a panel did nothing" beats "handle None in build_manifest". -->

Fixes #

## How it was verified

<!-- Tick what you ran; delete what doesn't apply. -->

- [ ] `.venv/bin/python -m pytest`
- [ ] `cd web && pnpm test`
- [ ] `cd web && pnpm build` (this is the real type check — `tsc --noEmit` passes unconditionally here)
- [ ] `cd workerd && cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check`
- [ ] Tried it in the running app
- [ ] Not needed, because:

### 定向测试与反证

<!-- 新增 / 改动的每条判据：拿掉修复（或打一个变异）它红不红，结论按退出码。
     写成「变异 → 结果」的表；没有新判据就写「无新判据」。 -->

### 未验证范围

<!-- 明确没跑 / 跑不到的：别的平台、别的 matplotlib 档、真机、真网络……
     没有也写「无」，不要留空。 -->

### 回滚

<!-- `git revert` 就够，还是有产物 / 数据 / 设置要一起退？ -->

## Contributor agreement

- [ ] I have completed the Tavotto CLA process, or this PR is made by an account
      explicitly exempt under the repository's CLA policy
      (`.github/cla-policy.json`).

<!-- This checkbox is a reminder, not a signature. It has no legal effect and CI
     does not read it — the `Contributor licence (CLA)` check reads the real
     signature record. See docs/legal/CLA_INDIVIDUAL.md; if your employer owns
     the work, docs/legal/CLA_CORPORATE.md. -->

## Checklist

- [ ] A bug that only reproduces on Windows was turned into a case in `tests/test_windows_regressions.py` first, and that case was seen failing
- [ ] Nothing new imports `pymupdf` outside `src/tavotto/pdfbackend/`
- [ ] Nothing Flask imports gained a non-stdlib dependency (`engine/registry.py`, `pool.py`, `ai_bridge.py`, `config.py`, `updater.py`, `runtime.py`)
- [ ] Dual-source pairs changed on both sides — `engine/patchspec.py` ↔ `workerd/src/patchspec.rs`, `lib/richText.ts` ↔ `richtext.py`, `lib/shapeGeometry.ts` ↔ the geometry in `pymupdf_backend.py`
- [ ] A new Tauri command was registered in all three places (`build.rs`, `capabilities/main.json`, `generate_handler`) — miss one and the call is silently rejected
- [ ] A performance claim points at a number in `docs/perf-baseline.md`

<!-- None of these are style rules; each one cost a real bug to establish.
     CONTRIBUTING.md has the short version, CLAUDE.md the full reasoning. -->
