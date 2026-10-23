# Independent CI wait / slot composition rerun on 56aca508

Final tested base: `56aca50844ecd85aefe9818d8f9e4be0df063d8c`; base tree: `4a32a8a23a664c907b3c5e9fd88eb0b8a9527c24`.
Worktree read: `/workspace/Tavotto-cla-ci-integration`. No workspace edits.
Store SHA256: `eaeba99f908d736d56552cdaa1e8f315b33287c1ed57f548f21af6840b815730`.
The nine transplanted CI files remain byte-identical to donor `907af3f3920a62bbc52712c1e9fcf9da371f54d5`; every tested runtime/script/test/deployment source matches the final author's worktree. Full source pins are in source-manifest.json and transplant-identity.json.

## Fresh actual execution

The exact same independent 14-test file was copied from the earlier 7324 experiment without changing any bytes (SHA256 `b759612f1cdbc2893f8f1b7b95f2549bd8f60d48ab5ded7fdfadd410d9875440`). It was then actually executed on a new isolated snapshot of this new base; no old result is credited to it.

`node --test tests/independent-wait-slot.test.mjs`: 14 tests, 14 pass, exit 0.
`python3 /tmp/cla-ci-composition-56aca/mutate.py`: exit 0; all four selected uniquely located mutants independently exit exactly 1 with actual AssertionError. There was no discarded/syntax-error mutant in this new-base rerun. Mutant names, source byte offsets, before/after SHA256 and actual commands are in mutants.json; command exits are in commands.json.

The successful path is only GET verification of a fixed, pre-existing synthetic confirmed owner. It uses actual native evidence/delegated-authorization revalidation, actual verification() and an in-process GET-only transport. Each independent authority request uses 11 actual outer SQL statements and one synthetic owned-run GET; two authority reads remain necessary. The CI attempt makes 13 synthetic GETs in total. Successful reads leave the original proof, expiry and slot unchanged.

Manual and unknown held owners each exhaust three pending attempts with one actual phase-lookup SQL per authority read, zero server external reads and no automatic confirmation or replay. Missing-check visibility can resolve against the same unchanged static confirmed snapshot. Manual transition before the second authority response is terminal. Revocation after initial pending, candidate run replacement, wrong final attempt receipt and authority 429/503 fail closed. A static expired proof reaches no authority read. A two-second static proof cancels at its original monotonic deadline before another GET, with wall time unchanged.

Actual fresh-negative GET recovery independently releases the exact owner (27 SQL/two synthetic GETs), preserves historical proof/expiry and invalidates the in-flight CI final run recheck. This is negative recovery of a static owner, not a successful publication across expiry.

The four reverse mutants reject: treating manual as confirmed, retrying a pending second authority response, dropping the original proof's monotonic deadline, and increasing actual whole-authority SQL cost. Each source landing is verified unique before execution.

No implementation blocker was found in this new-base composition. This is not a guarantee of an independent confirmation event inside 120 seconds. Current cron/deadline and live platform acceptance gaps remain. No Worker fetch, publication method, real network request, clock advance, dependency download, external review, deployment, merge or real credential use occurred.

## Evidence packaging

raw-logs.json contains the six newly generated raw logs as exact JSON strings, each with its own SHA256. Their originals also remain alongside it. Independent test source is supplied as independent-wait-slot.test.mjs.txt; runtime implementation snapshot copies are only temporary test inputs and should not be committed as evidence. This report and final-manifest.json belong exclusively to the 56aca508 composition and replace no earlier historical records.
