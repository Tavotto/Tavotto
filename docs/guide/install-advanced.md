# Installation details: desktop, PyPI, remote servers, Python environments

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## Desktop

Download from the [latest release](https://github.com/Tavotto/Tavotto/releases/latest):
a `.dmg` for macOS — `-macOS.dmg` for Apple Silicon, `-macOS-Intel.dmg` for Intel Macs —
or an `.exe` for Windows (x64; Windows on ARM is neither built nor verified). Install it, double-click, done — Tavotto opens in its own window and
updates itself from then on.

**You do not need to install Python.** Every installer carries a private Python runtime
with the usual scientific stack already in it — numpy, matplotlib, pandas, scipy,
seaborn, Pillow — pinned to the same versions in every installer, so the same script
draws the same figure. Rendering works the moment the installer finishes: offline,
without Homebrew, Conda or Xcode, and without touching a Python you already have.

That runtime is also why the installers are large: **195 MB to download on macOS
(measured on the Apple Silicon build), 89 MB on Windows, around half a gigabyte once
installed.** Paid once, and offline.

> macOS ships **one `.dmg` per architecture**, each built and smoke-tested natively on
> that architecture; pick the one that matches your Mac (Apple menu → About This Mac: *Chip*
> means Apple Silicon, *Processor … Intel* means Intel). They need **macOS 14 or later on
> Apple Silicon and macOS 15 or later on Intel** — the floor set by the PDF libraries the
> renderer ships with (pikepdf / PDFium). There is no Linux installer; Linux runs from
> PyPI (browser mode, beta). On Windows, each release page states whether its
> installer is code signed; an unsigned installer makes Windows show a
> **SmartScreen** prompt on first run (choose *More info → Run anyway*, or verify
> the download against `SHA256SUMS.txt` on the release page first).
> The single source of truth for what is supported,
> beta, and unsupported — per platform and per Python version — is
> [`docs/support-matrix.json`](../support-matrix.json); release pages, the
> website and in-app copy must match it (a test enforces the facts it can check).

## PyPI

Same command on all three platforms:

```sh
pipx install "tavotto[worker]"
tavotto
```

Your browser opens at `http://127.0.0.1:5089`. `--figures <dir>` opens a figure
directory straight away, `--port` changes the port, `--no-browser` skips opening a
browser.

> **Needs Python 3.10 or later.** On an older Python (Ubuntu 20.04 ships 3.8) pip
> reports only `No matching distribution found for tavotto[worker] (from versions: none)`
> without saying why. Check with `python3 --version`. If it is older, let
> [uv](https://docs.astral.sh/uv/) fetch its own Python instead — no sudo, and the
> system Python stays untouched:
>
> ```sh
> curl -LsSf https://astral.sh/uv/install.sh | sh
> # open a new terminal so uv and tavotto are on PATH
> uv tool install --python 3.12 "tavotto[worker]"
> ```
>
> A failed `pipx install` leaves `~/.local/pipx/venvs/tavotto[worker]` behind, and the
> next attempt stops with *Not installing to existing directory*; delete that
> directory first.

### On a remote server, over SSH

Tavotto only listens on `127.0.0.1`, so a server's copy is reached through an SSH port
forward rather than over the network. On the server:

```sh
pipx install "tavotto[worker]"
tavotto --no-browser
```

It prints a login address ending in `#dnonce=…`. On your own machine, forward the
**same port number** and open that address in your browser:

```sh
ssh -L 5089:127.0.0.1:5089 you@server
```

- **The local port must equal the server's `--port`**, and the address must say
  `127.0.0.1`, not `localhost`: Tavotto rejects any other `Host` to block DNS
  rebinding, so a mismatch shows a `bad_host` error. If 5089 is taken on either side,
  change both: `tavotto --port 5189 --no-browser` and `ssh -L 5189:127.0.0.1:5189 …`.
- **Each login address works once.** The browser then keeps a session cookie for 30
  days or until the server process restarts. For a new address while Tavotto is still
  running, run the same `tavotto --no-browser` (same `--port`) again on the server: it
  reuses the running instance and prints a fresh one, valid for five minutes.
- **In the desktop app**, use **File → Connect to Remote Instance…** instead of a
  browser: it opens a separate window, you paste the login address, and the server's
  Tavotto runs there. That window has its own private cookie store, and it cannot use
  this computer's files: the folder picker, Reveal in Finder, and dragging files in
  all fall back to browser behaviour, because every path belongs to the server. If the
  server's Tavotto is too old for this, the window says so; upgrade it on the server
  with `pip install -U tavotto`. Closing the window ends the session, so reconnecting
  needs a fresh login address.

## Try it in 30 seconds

```sh
pipx install "tavotto[worker]"
git clone --depth 1 https://github.com/Tavotto/Tavotto.git
tavotto --figures Tavotto/examples/figures
```

Three panels appear in the asset library. Drag one onto the page, double-click it,
click the title in the element tree, change 9 pt to 11, export. `examples/figures/`
holds two perfectly ordinary matplotlib scripts — Tavotto does not ask you to write
them in any particular way.

<details>
<summary><b>Advanced installation and Python environments</b></summary>

**pip**, into the current environment:

```sh
pip install "tavotto[worker]"
tavotto
```

> **Don't `pip install` Tavotto into a scientific environment that has astropy 8.**
> Tavotto depends on uharfbuzz, and every current uharfbuzz 0.56.x wheel clashes with
> astropy 8's compiled modules (a Cython 3.3.0 bug,
> [cython/cython#7914](https://github.com/cython/cython/issues/7914)). In that
> environment your *own* scripts' `savefig(..., format="pdf")` fails with
> `TypeError: Shared Cython type cython_function_or_method has the wrong size` once
> `astropy.table` is imported — with or without Tavotto running. Install Tavotto with
> `pipx` (above) and point it at that environment instead, as described next. Tracked
> upstream in [uharfbuzz#318](https://github.com/harfbuzz/uharfbuzz/issues/318) and
> [fonttools#4354](https://github.com/fonttools/fonttools/issues/4354).

**Reuse the environment your figures were made in.** Drop the `[worker]` extra and
point Tavotto at your own interpreter, so figures render against exactly the
dependencies they were written for. (This installs the lightweight CLI only: without
`[worker]` there is no bundled rendering stack, so rendering — including the Codex
MCP integration — depends entirely on the interpreter you point it at.)

```sh
pipx install tavotto
export TAVOTTO_WORKER_PYTHON=/path/to/your/env/bin/python   # Windows: setx TAVOTTO_WORKER_PYTHON "..."
tavotto
```

**Contributors: developing from source.** This path is for changing Tavotto itself,
never a fallback for a failed regular install (needs node + pnpm to build the
interface):

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
.venv/bin/python scripts/fetch_fonts.py   # approved fonts (not in git)
python scripts/build_frontend.py
.venv/bin/tavotto
```

**Which interpreter renders your figures.** Tavotto picks in this order:
`TAVOTTO_WORKER_PYTHON` → the one you chose in Settings → the bundled runtime → its
own interpreter → a Python or Conda it finds on the machine. **Whatever you choose
explicitly always wins**, and Tavotto only *launches* the environment you point it at:
it never installs anything into it and never modifies an existing Python or Conda. The
bundled runtime is likewise never written to — bytecode and the matplotlib font cache
go to Tavotto's own data folder, so the installed app stays byte-identical (on macOS,
writing into it would break the code signature). Checking whether one of your
environments can render writes no bytecode into it, and the environments the app builds
for you in that data folder (per-project environments and its private Python) keep their
pip and matplotlib caches there too.

The bundled runtime covers the common scientific stack. It is **not** a promise to
cover whatever your scripts import. If a script needs something it does not have
(rdkit, astropy, your lab's own library) Tavotto names the missing package and offers
to switch to your environment under **Settings → Rendering environment**; it will not
install that package for you, into its runtime or into yours. With no working
interpreter at all, layout, annotation and export still work — only editing inside a
figure needs one.

**Settings → Privacy, diagnostics and About** shows which interpreter is in use, where
it came from (`bundled`, `configured`, `system`, …) and, for the bundled runtime, the
exact pinned version of every package. The same information is in the diagnostics
bundle.

**The first open of a figure runs your script.** Light figures take a second; heavy
ones take as long as they normally do. Every edit after that is sub-second.

</details>
