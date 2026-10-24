# 构建、验收与安装界面

> 原文出自 `src-tauri/AGENTS.md`「构建、验收与安装界面」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 70
改到: `scripts/build_desktop.py`、`scripts/smoke_desktop.py`、`desktop-tauri.yml`、`src-tauri/windows/installer.nsi`、`assets/brand/dmg-background.png`
主题: 桌面构建 / 验收、dmg 与 NSIS 安装界面 → `build-acceptance-and-installer.md`
必守要点: `desktop-tauri.yml` 是唯一桌面发行链；NSIS 首次安装三页、覆盖安装 / `/UPDATE` / `/P` 跳过目录页；`@tauri-apps/cli` 钉死 2.11.4，模板与打包器同源，升级要重打补丁并同步四处
-->

- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py
  --sidecar dist/Tavotto/Tavotto`（真产物全链路：认证/项目/渲染/导出/退出无孤儿）。
  CI 在 `desktop-tauri.yml`——v0.3.0 起是唯一桌面发行链（旧 `desktop.yml`/
  Inno Setup/免安装 zip 已退役删除，git 可找回）；Windows NSIS 自带内置渲染
  runtime，桌面产物一律真窗口、不再有「启动后开浏览器」的形态。
- **安装界面（2026-08-17）**：macOS dmg 带品牌版式——背景图
  `assets/brand/dmg-background.png` 由 `scripts/build_dmg_background.py` 生成
  （维护者脚本用 PyMuPDF 直绘——它不在应用闭包里，要 `pip install -e '.[legacy-pymupdf]'`；图标落点与 `make_dmg.sh` 的 Finder 版式严格同源），
  make_dmg.sh 里 Finder 脚本失败只降级为朴素版式、绝不断发布链。
  Windows NSIS 用 vendored 模板 `src-tauri/windows/installer.nsi`
  （上游 tauri-cli v2.11.4 + `TAVOTTO PATCH` 标注的最小补丁：去欢迎页 /
  极简进度 / 品牌配色；头图侧栏图走 tauri.conf.json 的 nsis.* 配置）。
  **首次安装是三页：选安装位置 → 进度 → 完成**（2026-09-23 起；此前目录页
  恒被跳过）。目录页只在「首次、交互式」安装出现——卸载项里已有安装、
  `/UPDATE`（应用内更新）、`/P` 一律跳过：覆盖安装换位置会在老目录留一份
  没人管的副本。默认值 `%LOCALAPPDATA%\Tavotto`（以前装过就预填老路径）；
  离开页面时先验写权限（currentUser + asInvoker，选 Program Files 当场拦下）。
  自选路径靠安装清单 + HKCU `InstallLocation` 被外部程序发现，不在惯例位置里。
  **@tauri-apps/cli 钉死在 2.11.4**——模板与打包器必须同源，升级 CLI 时
  取新模板重打补丁并同步 build_desktop.py / desktop-tauri.yml / nightly.yml
  （tests/test_nsis_template.py 看护四处版本一致与 BMP 形态）。
