"""U03 合成 fixture ⑦ 的**用户自己的** `manifest.py`——与 Tavotto 引擎的 `engine/manifest.py` 重名。

实验室里「实验清单」叫 manifest 再自然不过。脚本 `import manifest` 要的是这一份：
`RUNS` 与 `WHO` 在引擎的那份里都没有。issue #447 / FO19。
"""

WHO = "user-manifest"
RUNS = [("run-a", 1.0), ("run-b", 2.0), ("run-c", 4.0)]
