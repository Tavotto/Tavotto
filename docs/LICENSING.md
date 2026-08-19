# License

Tavotto is released under **AGPL-3.0-only**. The full text is in
[`LICENSE`](../LICENSE) at the root of the repository.

## What this means in practice

For almost everyone, nothing changes:

- **Using it, modifying it, deploying it inside your lab** — unrestricted. The AGPL
  does not govern private use.
- **The figures and PDFs you produce** — entirely yours. The licence does not reach
  the output.
- **Citing Tavotto in a paper** — nothing needs to be open-sourced.

The obligations apply to **distribution**. If you give a modified Tavotto to other
people, or run it as a service others reach over a network, the corresponding source
has to be made available to those users (AGPL sections 5 and 13).

## Third-party components

| Component | License | Used for |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | AGPL-3.0 | PDF reading, rasterising, vector composition |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | Local HTTP server |
| [matplotlib](https://matplotlib.org/) | PSF-based | Rendering worker (optional dependency) |
| [React](https://react.dev/) / [Vite](https://vite.dev/) / [Tailwind](https://tailwindcss.com/) | MIT | Web interface |

The complete frontend dependency list is in `web/pnpm-lock.yaml`.
