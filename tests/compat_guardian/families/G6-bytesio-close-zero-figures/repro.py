import io as _cg_io

import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 2.6))
    _cg_buf = _cg_io.BytesIO()
    fig.savefig(_cg_buf, format="pdf")
    plt.close(fig)
