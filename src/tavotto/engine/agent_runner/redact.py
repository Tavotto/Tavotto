"""密钥过滤：已知密钥（环境变量里读到的那一个）不许出现在任何出口。

两个出口共用同一个判据（`Redactor`）：

* 输出：`__main__` 的事件写出函数与 stderr 诊断函数是进程里**唯一**写 stdout / stderr 的地方，所有字符串过 `redact_obj`；
* 落盘：`edit_file` / `write_file` 的内容含密钥 → 工具错误 `secret_in_content`（文件会被提交进用户项目）。

模型的成功回复同样是不可信输入：服务端在请求头里拿到了密钥，完全可能把它回显进文字。
长度 < 8 的串不参与（太短会误伤普通文字）。变形：原样、去首尾空白、JSON 转义形。
"""

from __future__ import annotations

import json

MIN_SECRET_LEN = 8
PLACEHOLDER = "[redacted]"


class Redactor:
    def __init__(self, *secrets: str):
        forms: set[str] = set()
        for s in secrets:
            for v in (s, s.strip()):
                if len(v) >= MIN_SECRET_LEN:
                    forms.add(v)
                    esc = json.dumps(v)[1:-1]  # 引号 / 反斜杠 / 非 ASCII 的转义写法
                    if len(esc) >= MIN_SECRET_LEN:
                        forms.add(esc)
        self._forms = sorted(forms, key=len, reverse=True)  # 长的先换

    def __bool__(self) -> bool:
        return bool(self._forms)

    def __repr__(self) -> str:  # 密钥不进 repr
        return f"Redactor({len(self._forms)} forms)"

    def contains(self, text: str) -> bool:
        return any(f in text for f in self._forms)

    def redact(self, text: str) -> str:
        for f in self._forms:
            text = text.replace(f, PLACEHOLDER)
        return text

    def redact_obj(self, value):
        """递归处理 JSON 形状的值里的每个字符串（含字典键）。"""
        if not self._forms:
            return value
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.redact_obj(k): self.redact_obj(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.redact_obj(v) for v in value]
        return value


NO_REDACTION = Redactor()
