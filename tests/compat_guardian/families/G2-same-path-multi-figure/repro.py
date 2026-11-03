import matplotlib.pyplot as plt


def main():
    for _cg_i in range(2):
        fig, ax = plt.subplots(figsize=(4.0, 2.6))
        for _cg_ax in fig.axes[:1]:
            _cg_ax.set_title(f"cg_pass_{_cg_i}")
        fig.savefig("shape_oo.pdf")
