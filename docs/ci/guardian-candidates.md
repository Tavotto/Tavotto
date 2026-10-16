# CG-02 static candidates

Run `python scripts/ci/guardian_variants.py --seed 42 --out /tmp/cg-candidates`.
This generates seven deterministic review packets: argparse/entry, show-only,
helper+CSV, real replace/readback, provider catalogue/import aliases, Artist styling and bitmap
output. It compiles ASTs without execution; CLI exit 0 means generation completed,
never product compatibility success.

Every packet records seed, template/generator version and digest, parent committed
fixture graph and hashes, generated file graph, static truth where available,
and review status bound to content. Parent source files remain untouched.
The repository declares AGPL-3.0-only; independent upstream provenance is explicitly
unverified. Provider candidates do not authorize installation. Bitmap dimensions
do not promise Artist editing.
The provider catalogue reuses `depresolve.distribution_alternatives`; no actual
multiple-provider environment or arbitration has been exercised.

All generated scripts are **unreviewed / not_run**. There is no execute option and
they cannot enter the registered CG-01 suites. No external LLM/API, package install,
network or environment manager is used. Review and sufficient OS isolation are
prerequisites for later execution; merely changing a manifest flag grants nothing.
