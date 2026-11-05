"""期刊目录远程拉取用例的**测试夹具**：Ed25519 签名函数（PyNaCl） + 测试密钥 + 假服务器内容构造。

**私钥只许出现在 `tests/`**（ADR 0155 §4b）：下面的 `TEST_SEED` 是公开的测试种子，对应的公钥就是
`engine/journalremote.TRUSTED_KEYS["test-1"]`（占位，真钥由维护者生成后替换，见 ADR 0155 §9）。
签名函数不进 `src/`：客户端只会验签。
"""

from __future__ import annotations

import base64
import hashlib
import json

from nacl.signing import SigningKey

TEST_SEED = hashlib.sha256(b"tavotto-journal-catalog-TEST-KEY-do-not-trust").digest()
OTHER_SEED = hashlib.sha256(b"tavotto-journal-catalog-some-other-key").digest()


def public_key(seed: bytes) -> bytes:
    return bytes(SigningKey(seed).verify_key)


def sign(seed: bytes, message: bytes) -> bytes:
    return SigningKey(seed).sign(message).signature


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def envelope(manifest: dict, *, seed: bytes = TEST_SEED, key_id: str = "test-1") -> bytes:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return json.dumps(
        {
            "alg": "ed25519",
            "key_id": key_id,
            "payload": b64(payload),
            "sig": b64(sign(seed, payload)),
        }
    ).encode("utf-8")


def canon(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def build_site(
    catalog: dict, *, rev: int | None = None, seed: bytes = TEST_SEED
) -> dict[str, bytes]:
    """把一份完整目录切成「服务器上的文件」：`index.signed.json` + `r<rev>/journals/<id>.json`（协议见 ADR 0155 §9）。"""
    rev = rev or catalog["catalog_rev"]
    files: dict[str, bytes] = {}
    index_journals = {}
    for jid, j in catalog["journals"].items():
        pid = j["publisher"]
        raw = canon(
            {
                "journal": j,
                "journal_id": jid,
                "publisher": catalog["publishers"][pid],
                "publisher_id": pid,
            }
        )
        files[f"r{rev}/journals/{jid}.json"] = raw
        entry = {
            "label": j["label"],
            "publisher": pid,
            "bundle": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
        }
        for k in ("issn", "aliases"):
            if k in j:
                entry[k] = j[k]
        index_journals[jid] = entry
    index = {
        "schema": 1,
        "kind": "tavotto-journal-catalog-index",
        "catalog_rev": rev,
        "issued": "2026-11-04",
        "publishers": {pid: {"label": p["label"]} for pid, p in catalog["publishers"].items()},
        "journals": index_journals,
    }
    files["index.signed.json"] = envelope(index, seed=seed)
    return files
