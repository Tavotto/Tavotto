import matplotlib.pyplot as plt
import pyarrow.parquet as _cg_pq


def main():
    fig, ax = plt.subplots(figsize=(4.0, 2.6))
    ax.plot(
        _cg_pq.read_table("cg_data.parquet").column("y").to_pylist(),
        [0, 1, 4, 9],
        label="quadratic",
    )
    fig.savefig("shape_oo.pdf")
