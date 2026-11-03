"""两张不同的 Figure 写同一个 stem 的不同格式（panel.png / panel.pdf），后一张随后 close。"""
import matplotlib.pyplot as plt


def main():
    first, fa = plt.subplots(figsize=(2.0, 1.6))
    fa.plot([0, 1], [0, 1])
    fa.set_title("first")
    first.savefig("panel.png", dpi=100)

    second, fb = plt.subplots(figsize=(2.0, 1.6))
    fb.plot([0, 1], [1, 0])
    fb.set_title("second")
    second.savefig("panel.pdf")
    plt.close(second)


if __name__ == "__main__":
    main()
