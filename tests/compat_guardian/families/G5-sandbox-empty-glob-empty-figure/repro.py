import csv as _cg_csv
import glob as _cg_glob

import matplotlib.pyplot as plt


def _cg_read_parts(pattern, encoding):
    values = []
    for part in sorted(_cg_glob.glob(pattern)):
        with open(part, newline="", encoding=encoding) as fh:
            values.extend((int(row["y"]) for row in _cg_csv.DictReader(fh)))
    return values


def build_figure():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(_cg_read_parts("data/cg_data_*.csv", "utf-8"), color="#B4473C")
    fig.savefig("shape_custom_entry.pdf")
