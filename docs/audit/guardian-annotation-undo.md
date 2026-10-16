# Annotation shared-anchor repair, CG-04 / issue #568

Base: clean main `a48872109012b13cb6c2e83265cd4f7e52782ef2`, tree
`5c7b5238e9c05b37171e7071bb994433a84b8798`. The connected GitHub read channel
supplied 130 changed paths; every exact blob, full index tree and signed commit
ID was verified. No shell network retry or alternate source was used.

At 2026-10-16 04:48 UTC, issue #568 remained open with no assignee or comments.
Open PRs and all-state PR search found no other current repair for it; #552, which
introduced annotation endpoint editing, is already merged. This cannot establish
ownership of another session's unpublished work. #1044, #1055 and other authors'
branches are untouched. #1034/#1086 are unrelated and are not claimed repaired.

The existing registered fixture is
`tests/compat/cases/metamorphic/mm_annotated_oo.py`, SHA-256
`62d1ed08f3642f7c992fda861833bd564023de2f409a41f3e066e74c513e3364`.
It has no external data: numpy generates fixed 40-point exponential decay data.
Independent native truth is Annotation `xy=(2.0,0.51)`, `xyann=(3.4,0.80)`,
text `decay`. The full request sequence is text position `[0.65,0.35]`, then
add empty text, then add arrow endpoints `[0.30,0.65,0.55,0.40]`, then `[]`.
Requests are cumulative lists, not incremental patches.

On the clean base, both direct Matplotlib/overrides and real `pool.one_shot`
workers reproduced the failure. Native, untouched worker and fresh worker RGBA
SHA-256 was `3a1f05f73da2bf283e37177043fdc9cb6302a3b2e4549c1ba7df44fb8809a7df`;
after full undo, the hot worker retained
`2e79557d049e53e7f40cd8b99a64064de3edb6fd87626f0bf63ed55da1224081`.
Its text anchor stayed `[0.65,0.35]` instead of
`[0.5594696969696971,0.31691246998495315]`, with no warnings. Independent raw
exec-event logs record one exact fixture execution for hot and one for fresh,
with distinct PIDs and equal environment fingerprints. These are actual script
events, not request counts. The direct probe additionally recorded original
xy/xyann/text, originals/applied and RGBA bytes at every step.

The two geometry properties write the same Annotation.xyann. A later property
was sampling an earlier edit as its original. The repair borrows the already
retained native anchor in both directions, including layout-pin native state.
Restoring or changing either owner marks the other geometry property for replay.
An inactive arrow respects a still-requested text position. No persistent cache,
new worker, permission change or writeback transaction change is introduced.

The new real-worker regression checks original undo, both first-edit orders,
partial removal, inactive endpoints, repeated rendering, and full RGBA/geometry
against independent fresh workers. Two reviewed deterministic fixture variants
add only constrained/tight layout to the same subplots call and cover the
cross-request arrow-first, text-second order. Empty text disappearing from the
manifest midway is expected and is never treated as the defect signature.
Independent absolute position/endpoints assertions also prevent hot and fresh
from agreeing on the same incorrect request result. A direct scientific-process
check pins layout's original xyann and resamples four changed native values after
clear; that is lifecycle evidence, not a real Bridge/Native barrier E2E claim.

The final disposable-copy baseline ran four tests in 37.595 seconds, exit 0.
Four witnessed mutations then failed at their intended assertion: original main
undo pixels, canonical pin original, survivor replay, and inactive endpoints
erasing a requested text position. The last mutation initially survived an
equivalence-only assertion; that first round is retained, and the independent
absolute coordinate oracle killed it on rerun. Each final result records source,
test and mutant SHA-256, exact change and original stderr. Implementation SHA-256
is `587d6324ccf3dedee1a0f17621913898e288ace0912429333f2d6cfa71b5cd01`;
test SHA-256 is `c3e83cd3773c4a2b76ce916444a5d3fcc825cb43050a4ec1d30ebd09605776ca`.

Current local evidence is under `/workspace/compat-guardian-evidence/`:
`cg04-annotation-red-main`, `cg04-worker-red-main`,
`cg04-worker-green-dirty`, `cg04-regression-unittest.txt`,
`cg04-layout-unittest.txt`. Dirty-tree results do not prove a future clean commit;
exact-head re-verification, independent review and CI are required. CG-03 must
separately connect this family to its runner/minimizer before claiming that
stage's complete closure. No baseline/corpus promotion occurs automatically.

Environment: Linux, existing Python 3.12.14, Matplotlib 3.10.8, numpy 2.3.5,
Pillow 12.3.0. Tests run only in temporary projects/HOME/config/cache/data.
No dependency was installed. Real desktop shells, other Matplotlib versions,
native barrier rebase, full writeback/export qualification and general scientific
arrays remain unverified here. Existing hosted CI must supply its own evidence.
The local complete playground build failed before building because pnpm attempted
dependency preparation and could not create its default store; nothing installed.
Read-only `--check` exited 1 because no dist-playground manifest exists. The
existing hosted frontend build remains required; no generated product was faked.
