import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([1, 2, 3])
    ax.set_title(f"lw={plt.rcParams['lines.linewidth']}")
    fig.savefig("guard_style.png")


if __name__ == "__main__":
    plt.rcParams["lines.linewidth"] = 3.0
    main()
