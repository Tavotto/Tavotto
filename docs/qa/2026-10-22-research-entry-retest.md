# Leaf / Qudit 真实入口复测与原生裁切对照修正

2026-10-22，基于 main `cc89ec03b8033eded002c429313c5c78e10c6ac2`。
范围：修正对照 PNG 丢失 `bbox_inches`；重新验证 Leaf 的 argv、Qudit 的 cwd。
本轮不处理 Celerite 的 0.44 pt 布局漂移与 Gala PDF 异常。
裁切修复单独位于 [#1321](https://github.com/Tavotto/Tavotto/pull/1321)；本文随 Leaf 清单修复提交，两个 PR 均直接基于 main。

## 输入与环境

| 项目 | 上游与固定提交 | 入口 / argv | 工作目录 |
| --- | --- | --- | --- |
| Leaf | [batterysim/nissan-leaf-data](https://github.com/batterysim/nissan-leaf-data/tree/b2222ed14a678424c1ba4cd07b4adea38b9d08b0) | `plot.py hppc` | 项目根目录 |
| Qudit | [perlinm/spin_qudit_tomography](https://github.com/perlinm/spin_qudit_tomography/tree/1939d4958446945f8235c72867ea65f158a9a019) | `plot_tomo_times.py`，无参数 | 项目根目录 |

沿用 #1305 冻结清单的文件 SHA-256：Leaf 9 个文件，Qudit 8 个文件。
源码和数据从固定提交的外部缓存复制到独立运行目录；所有副本在复测后再次逐文件核对，均未改动。
Qudit 保留原来的 `usetex=True` 和 5 份 timing 数据，未替换字体策略或生成假数据。

- Leaf 入口 SHA-256：`64e52bf839ed8af1f533498388a2cfc38f761a5ed5fa6ee58030ce5a41d76f8e`。
- Qudit 入口 SHA-256：`33ce4ba3e4d4043be2d9c134db8e2552e54fad83507580540d97152b4b2b47f9`。
- macOS arm64，Python 3.13.16；Matplotlib 3.11.1、NumPy 2.5.2、pandas 3.0.5、SciPy 1.18.0、seaborn 0.13.2。
- 科学依赖按 `packaging/runtime-lock.json` 的 macos-arm64 版本安装到独立环境；所有执行使用同一解释器。
- Qudit 使用本机 TeX（`latex`、`dvipng`、`type1cm/type1ec` 均在）。
- 数据、配置、运行目录均隔离；上游源码、数据与本轮生成的导出文件不进入 git。

## 入口与判据

原生对照用 `runpy.run_path(..., run_name="__main__")` 执行未修改脚本，设置精确 argv 与 cwd，
之后读取 Figure 的每条 `Line2D.get_xydata()`。对照脚本不导入 Tavotto 的执行器。
数组按 little-endian float64 与形状头计算 SHA-256；与产品 manifest 的 `data_fingerprint.parts.xy`
逐图、逐条比较（保留重复曲线），不以图片尺寸代替数据一致性。

桌面侧使用 `tests/support/foundation_app.py::sidecar_app` 启动真实 `python -m tavotto --desktop-sidecar`，
通过 nonce 引导和 session cookie 认证；通过环境选择 API 采用用户 Python，再走准备会话的 run action。
Leaf 提交 `argv=["hppc"]`。两例均在 safe profile 的第一次尝试完成，无兼容模式回退。

终端侧使用 `tests/support/nativekit.py::product_run` 启动真实 CLI：

```sh
# 分别在对应上游项目的根目录执行；PYTHON 是上述同一个科学环境
python -m tavotto run -- "$PYTHON" plot.py hppc
python -m tavotto run -- "$PYTHON" plot_tomo_times.py
```

自动化验证添加了 `--x-no-desktop`，用真实 descriptor/token attach 客户端代替窗口，
在实际 Figure 屏障处 build/override，最后放行每个屏障并检查进程退出码为 0。
这覆盖桌面 sidecar 与 CLI 控制面；本轮没有操作 Tauri 窗口或前端控件，也没有重测 Windows。

| 验证 | Leaf | Qudit |
| --- | --- | --- |
| 原生 Figure / 曲线数 | 5 / 16 | 1 / 6 |
| 桌面 sidecar 捕获、打开、曲线数据 | 5 张，16/16 哈希一致 | 1 张，6/6 哈希一致 |
| `tavotto run` 捕获、曲线数据、退出码 | 5 张，16/16 一致，退出 0 | 1 张，6/6 一致，退出 0 |
| 每张图首条曲线改为 `#0072b2` | 生效，所有数据不变 | 生效，所有数据不变 |
| 每张图导出 PDF / SVG / PNG | 15 个文件，分别可由 pikepdf / XML / Pillow 读取 | 3 个文件，分别可读取 |
| 项目排版保存 → 关 sidecar → 新进程重开 | 5 个资产身份、编辑与数据均保留 | 资产身份、编辑与数据均保留 |

保存使用 `/api/layouts/<name>?target=project&base_revision=absent`，检查保存回执中的 Figure snapshot 引用，
重开后读取同一文档并按其中的资产 id 与 overrides 冷渲染。上述导出检查证明文件可读，
没有据此声称跨格式像素完全相同，也没有涉及原件写回事务。

## 发现与修复

**Leaf：参数链路已通，素材列表有重复占位。** 在修复前的真实 sidecar 中，5 张带参数捕获均能打开，
但素材列表同时列出 5 个未捕获的无参数 id；渲染这些 id 得到 `script_needs_arguments`。
原因是注册表的 stems 是各次运行的并集，清单却把每个 stem 都当作无参数运行的产物。
现在同一个 stem 已有带参数捕获时，只列实际捕获的变体；真实的无参数捕获仍独立保留，
没有任何捕获的旧注册项仍保留待运行占位。资产解析与已存文档的 id 不变。
修复后 Leaf 的清单为 5 项，均能完成上述链路。

**Qudit：原 S0 的空 glob 取决于 cwd。** 用户 Python 的桌面默认脚本目录与终端项目目录均成功。
在同一解释器与输入下，显式选择 sandbox cwd 可重现 `need at least one array to concatenate`、零图；
在同一项目通过工作目录 API 改回 `mode=project` 后，捕获、编辑、导出与冷重开全部成功。
本轮不新增 cwd 后端分支；空 glob 的目录引导由在途 [#1299](https://github.com/Tavotto/Tavotto/pull/1299) 处理。

**对照协议：保存时裁切框被丢弃。** `compat_driver.run_native` 以前重新按完整 figsize 输出 PNG，
未沿用原生保存的 `bbox_inches`。现在在原始 savefig 期间观察 Matplotlib 的 `adjust_bbox`，
记录实际解析出的裁切框；由既有 `frame_call` 选择原件格式对应的保存事件，再输出参照 PNG。
覆盖 tight、padding、额外 artist、显式 Bbox、rc_context、保存后 rcParams 变化、多格式与写入者替换。
失败保存不进入裁切账本；无 savefig 的 pyplot 兜底保持原行为。没有调整图像比较阈值。

## 回归验证

- 全仓 `ruff check .` 与 `ruff format --check .` 通过。
- 相关回归：236 passed、19 skipped；跳过项为独立工作树未构建 workerd 的传输层分支。
- 最终新增裁切与清单用例：12 passed；裁切判据使用原生实际 PDF 的 MediaBox / PNG 像素作为独立真值。
- 手工反证：分别恢复旧裁切实现、旧清单实现，相应回归测试均以退出码 1 失败；恢复修复后通过。
- 复现回归：本分支 `pytest tests/test_runtime_config_listing.py`，#1321 分支 `pytest tests/test_compat_native_crop.py`；真实入口的
  本机完整回执、原生数组记录、导出与脚本驱动存于 `/private/tmp/tavotto-launch-validation/`。

#1307 的 `compat_matrix.py`、`compat_guardian.py` 无改动；#1305 保持草稿且未追加提交。
