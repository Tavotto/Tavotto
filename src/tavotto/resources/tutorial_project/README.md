# Tutorial project

This folder is the **offline tutorial project** that ships inside the Tavotto
package. Tavotto copies it into your user data directory the first time you
choose "Learn Tavotto with an example"; that copy is yours to edit. Choosing
"Restart tutorial" restores this pristine version.

- `fig1_kinetics.py` → `Fig1_kinetics.pdf` (title, axis labels, legend, two lines)
- `fig2_correlation.py` → `Fig2_correlation.pdf` (scatter + fit, one deliberately
  7 pt note that fails the 8 pt publication rule)
- `paper_style.py` shared style; only matplotlib's bundled DejaVu fonts
- `tavotto_registry.json` stem ↔ script mapping
- `tavottofile/Tutorial.json` a ready-made canvas with both panels
- `tutorial_meta.json` stable metadata the app and its onboarding read

Nothing here needs network access or packages beyond numpy + matplotlib.
