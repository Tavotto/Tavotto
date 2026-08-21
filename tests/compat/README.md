# CompatBench corpus

这里的每个脚本都是一份**兼容性样本**：它模拟真实用户（或 AI）会写出来的
matplotlib 代码，用来量化「Tavotto 到底能不能正确处理它」。

完整的分类法、tier、版本矩阵、基线纪律与门禁在
[`docs/ci/matplotlib-compatibility.md`](../../docs/ci/matplotlib-compatibility.md)。
这份 README 只讲**写 case 的人**要知道的事。

---

## 跑

```bash
python scripts/ci/compat_matrix.py --smoke                 # 最小子集
python scripts/ci/compat_matrix.py --case <id>             # 单个 case
python scripts/ci/compat_matrix.py --all                   # 全量
python scripts/ci/compat_matrix.py --smoke --list          # 只看选中了谁
```

---

## case 脚本的硬约定

1. **短**，一眼能读完。
2. **数据写死**，不用 `np.random`、不用 `datetime.now()`、不看当前日期、
   不读系统随机状态、不依赖 `HOME`。
   > 随机数会让 manifest 的元素数与像素基线在两次运行之间漂移，
   > 而漂移的表现是「这条 case 偶尔红」，最后它会被当成噪音关掉。
3. **不联网**。
4. **不写用户的真实目录**。相对路径写出的中间文件只落沙盒（这是被
   `shape_sandbox_write` 看护的产品行为，不是靠自觉）。
5. **不使用 Tavotto 的私有 API 构造 Figure**。它应该看起来像真正用户写的
   matplotlib 脚本——benchmark 的价值全部来自「走用户真实路径」。
6. 需要数据文件时放 `assets/`，在清单里用 `assets` 声明。runner 会把它复制到
   **脚本旁边**（`pd.read_csv("data.csv")` 的语义是「脚本旁边那一份」）。
7. 一个脚本可以出多张图，每张图在清单里各占一条 case（共用一次 build）。

---

## 分目录

| 目录 | 这一档在问什么 |
|---|---|
| `script_shapes/` | 代码怎么组织：入口方言、savefig 的各种写法、相对路径读盘、中文/空格路径、子目录脚本、stdout 噪音、`sys.argv` 隔离 |
| `core_artists/` | matplotlib 的绘图 API 与 artist：曲线/散点/柱/分布/填充/图像/等值线/矢量场/文字形状/图例色条/3D/极坐标 |
| `axes_layout/` | 坐标轴与布局：网格、共享轴、孪生轴、次坐标轴、插图、布局引擎、刻度类型、Formatter/Locator、标题族 |
| `scientific_stack/` | numpy / pandas / scipy / seaborn / Pillow / mathtext / 中文 |
| `metamorphic/` | **同一张视觉结果 × 不同代码组织方式**。同族之间绘图正文逐字相同，差异只来自写法——Tavotto 的兼容表现不该因为写法而剧烈波动 |

---

## 加一条 case

1. 写脚本（放对目录）。
2. 在 `manifest.json` 里加一条：

```json
{
  "id": "shape_pyplot_show_only",
  "category": "script_shapes",
  "script": "cases/script_shapes/pyplot_show_only.py",
  "stem": "pyplot_show_only",
  "entry": "__main__",
  "tier": "must",
  "discovery": "requires_probe",
  "expected_figures": 1,
  "expected": {},
  "semantic_expectations": {
    "roles_present": ["axes", "line", "title"],
    "editable": [["axes_0.lines_0", "color"], ["axes_0.title", "fontsize"]]
  },
  "mutations": [
    {"gid": "axes_0.lines_0", "prop": "color", "value": "#123456"}
  ],
  "browser_eligible": true,
  "smoke": true,
  "notes": "AI 最常见的输出形态：pyplot + plt.show()，一次 savefig 都没有。"
}
```

`notes` 必须让 reviewer 一眼看出**这个 case 为什么存在**。

3. `python scripts/ci/compat_matrix.py --case <id>` 跑一遍。
4. **红了先想是不是产品 bug**，不要先改期望。
5. `--all --update-baseline`，逐条读 diff，再提交。

---

## 几个容易写错的地方

* **属性名跟着 artist 族走，不跟直觉走。** 散点的颜色叫 `facecolor` 不是
  `color`；RGB(A) 位图**没有** `cmap`（色表只对标量映射的数据有意义）。
  清单必须照实写——写一条 matplotlib 本来就不该支持的期望，红的是我们的
  fixture，不是产品。
* **`browser_eligible` 是推出来的，不是随手勾的。** 浏览器 playground 没有
  注册表，它把上传的文件按 `python figure.py` 跑一遍；只有 `def main():`
  而没人调用的脚本在原生 Python 下也不画图。而且 playground 是单文件的：
  没有数据文件、没有本地 helper。
* **`discovery` 写实际情况。** 本该能自动发现却只能靠试运行探测，那是缺口
  （记 `partial_support` + reason），不是把 `discovery` 改成 `requires_probe`
  就算过了。
* **别在一条 case 里同时改两个指向同一批 artist 的属性**（比如
  `legend.fontsize` 与 `legend.texts_0.fontsize`），除非这条 case 就是为了
  测那个重叠语义——重叠 override 的还原顺序另有一条已知缺陷
  （`art_legend_overlapping_fontsize`）。
