# Command line: `tavotto open` and `tavotto run`

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## Finish figures an AI wrote

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workflow.svg" width="100%"
       alt="Workflow: a Python script — written by you, Claude or Codex — runs matplotlib, which produces a vector PDF; Tavotto handles visual editing, layout and the publication preflight; the result is a PDF or PNG. The script stays the source throughout.">
</p>

Coding agents write good first-pass matplotlib. What is left over is visual — the
legend two lines too tall, the panel that wants to be a little smaller, the label
overlapping a data point. Describing that in a prompt is slower than doing it.

Hand a figure over with one command, from a terminal or from an agent:

```sh
tavotto open figures/Fig1_kinetics.pdf   # the output file
tavotto open figures/fig1_kinetics.py    # or the script — the output name is resolved for you
tavotto open figures/                    # or the whole figure library
```

It opens that figure's library as a project, registers anything missing, and hands the
figure to the desktop app — into the window that is already open, if there is one.
Without the desktop app it falls back to browser mode.

**Codex users** can install the plugin, which teaches Codex the shape a
Tavotto-editable figure has to have (script beside its output, vector PDF, an output
name that resolves statically) and puts the editor inside Codex itself — the install
commands and what to say in the first session are in
[Using Tavotto with Codex for the first time](../../README.md#using-tavotto-with-codex-for-the-first-time)
below.

It ships a skill, a local MCP server with nine tools — health check, open a figure,
apply overrides, normalise a figure, run the preflight, export true-vector PDF/SVG/EPS
or PNG/TIFF at an explicit DPI, verify a replay, refresh the project, close a session,
all usable in hosts with no interface at all — and an embedded canvas built from the
*same* frontend code the desktop app runs, so dragging, snapping and undo have no
second implementation. See [`codex-plugin/README.md`](../../codex-plugin/README.md),
including what has and has not been verified inside a real Codex Desktop.

A path suggested by the model is never treated as permission. On a zero-config first
open, a capable Codex host shows the canonical local directory for you to approve;
that approval lasts only for the current Tavotto MCP connection.

## Bring an existing project · `tavotto run` (Beta)

`tavotto open` assumes the shape Tavotto likes: a script beside its output, inside a
figure library. Real papers often look different — a conda environment, a package,
command-line arguments:

```sh
conda activate paper
python -m figures.fig3 --dataset run7
```

For those, put `tavotto run --` in front of the command you already use:

```sh
tavotto run -- python figure.py
tavotto run -- python figure.py --sample A --temperature 800
tavotto run -- /path/to/paper/.venv/bin/python figure.py
tavotto run -- python -m paper.figures.xps --sample A
```

Tavotto uses the Python command you provide and attaches to Matplotlib figures created
in that Python process. Your interpreter, your working directory, your arguments, your
environment, your `stdout`/`stdin` — none of them are rebuilt or intercepted. `savefig`
still writes the files it always wrote. Ctrl+C still interrupts your script, and
`tavotto run` returns your script's own exit code.

Editing in Tavotto does **not** change what your script sees:

```python
ax.set_title("Script")
plt.show()                          # you rename the title in Tavotto
assert ax.get_title() == "Script"   # passes — your code is the authority
```

Before the script starts, the desktop app asks once, showing the interpreter path, the
working directory and the target. **Nothing runs until you confirm.**

> This mode is **not a sandbox**. The script has exactly the permissions it has when
> you run it yourself. Only run code you trust.

Beta, with explicit edges: Python script or `-m` module only, figures created in that
one process, no arbitrary shell wrappers, no Jupyter, no writing back to your source
or to your script's own output files, and the desktop app is required. Full contract,
error codes and troubleshooting:
[`docs/compatibility/tavotto-run.md`](../compatibility/tavotto-run.md).
