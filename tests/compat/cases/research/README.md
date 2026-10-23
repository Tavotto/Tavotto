# External research projects

The sixteen development projects are registered in `tests/compat/research-cohort.json` and
`tests/compat/research-manifest.json`. Upstream source and data are not vendored here.

Run `python scripts/ci/compat_guardian_research.py fetch --case <registered-id> --cache <absolute-external-directory>`
to fetch the recorded files from their full upstream commit SHAs. Every file, including licenses,
is checked against its frozen SHA-256 before use. A missing or changed file stops preparation.
Downloaded files are never executed by the fetcher. The normal 149-case CompatBench does not fetch them.

The logical paths retained in historical reports identify the frozen files; their actual storage
is the external cache. To observe a case, install its recorded prerequisites on a hosted runner,
set `TAVOTTO_RESEARCH_CACHE`, and select the case explicitly in Guardian. See
`docs/implementation/script-compat/S0_BASELINE.md` for the measured scope and original results.
