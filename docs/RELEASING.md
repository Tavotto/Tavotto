# 发版

两条流水线，各管一段：

| 触发 | 工作流 | 做什么 |
|---|---|---|
| 推 `v*` tag | `release.yml` | 构建前端 → 打 wheel + sdist → 建 GitHub Release 并挂上产物 |
| Release 发布后自动 | `publish-pypi.yml` | 下载**同一份**产物 → 发到 PyPI |

PyPI 拿到的是 Release 上那份字节一致的 wheel，不重新构建——否则「检查更新」
装到的和 `pip install` 装到的会是两个不同的东西。

## 一次性设置：PyPI Trusted Publishing

Trusted Publishing 用 OIDC 换取短时凭据，仓库里不存任何 API token。PyPI 校验
「哪个仓库 + 哪个工作流文件 + 哪个 environment」，三者任一改名都要同步改这里的
配置，否则鉴权直接失败。

### 1. PyPI（正式）

登录 <https://pypi.org/manage/account/publishing/>，因为项目还不存在，
添加一个 **pending publisher**：

| 字段 | 值 |
|---|---|
| PyPI Project Name | `magplot` |
| Owner | `erwanjun` |
| Repository name | `magplot` |
| Workflow name | `publish-pypi.yml` |
| Environment name | `pypi` |

### 2. TestPyPI（演练用，强烈建议先做）

在 <https://test.pypi.org/manage/account/publishing/> 重复一遍，
**Environment name 填 `testpypi`**。两边是完全独立的账号与配置。

### 3. 开闸

配好上面两步之前，`publish-pypi.yml` 的自动发布是关着的（否则每发一个 Release
都会红一次）。准备好了就在 Settings → Secrets and variables → Actions →
**Variables** 加一条 `PYPI_PUBLISH_ENABLED = true`。

手动 Run workflow 不受此限——没开闸也能先在 TestPyPI 上演练。

### 4. 给正式发布加一道人工确认（可选但推荐）

仓库 Settings → Environments → `pypi` → 勾 **Required reviewers** 填自己。
之后每次发 PyPI 都会停下来等你点一下。

> PyPI 上的项目名一旦被占用就再也拿不回来，**同名文件永远不能重传**——
> 版本号发错了只能作废该版本再发一个新号。这道确认值得加。

## 演练

先在 TestPyPI 上走一遍完整链路：

Actions → **Publish to PyPI** → Run workflow，填 tag（如 `v0.1.0`）、
target 选 `testpypi`。成功后验证能装：

```sh
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ magplot
```

（`--extra-index-url` 是必要的：TestPyPI 上没有 flask / pymupdf 这些依赖。）

## 发一个新版本

1. 改 `src/magplot/__init__.py` 里的 `__version__`。
2. 改两份 README 里安装命令中的 wheel 文件名——**URL 带版本号**，忘了改用户
   就会一直装到旧版。
3. 提交、打 tag、推送：

   ```sh
   git commit -am "0.2.0"
   git tag -a v0.2.0 -m "Magplot 0.2.0"
   git push origin main v0.2.0
   ```

4. `release.yml` 自动出包建 Release；`publish-pypi.yml` 随即发到 PyPI
   （若配了 required reviewers，会等你批准）。

tag 与 `__version__` 对不上时 `release.yml` 会直接失败，不会发出错版本。

## 首次成功发到 PyPI 之后

README 里的安装方式可以简化成正常写法，并删掉「尚未发布到 PyPI」那条：

```sh
pipx install "magplot[worker]"
```

## 本地自检

上传是不可撤销的，本地先过一遍：

```sh
python scripts/build_frontend.py
python -m build
python -m twine check --strict dist/*     # 校验元数据 + PyPI 的 README 渲染
```
