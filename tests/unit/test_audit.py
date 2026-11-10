"""审计摘要值哈希（开发文档 §8.2 语义、defect_regression D14）。

用例与 §8.2 test_audit.py 对齐：
- test_summarize_hides_values_but_hashes：摘要为"键名:类型:sha256前8位"，明文不落库。
"""
from __future__ import annotations

import hashlib
import re

from app.audit.store import summarize


def test_summarize_hides_values_but_hashes():
    """D14：摘要含键名+类型+值哈希，敏感明文不落库。"""
    s = summarize({"grn_no": "GRN-1", "unit_price": 0.0086})
    assert "GRN-1" not in s                 # 明文不落库
    assert "grn_no:str:" in s               # 键名 + 类型 + 哈希
    m = re.search(r"grn_no:str:([0-9a-f]{8})", s)
    assert m is not None                    # 哈希段为 sha256 前 8 位


def test_summarize_hash_matches_sha256():
    """哈希段与 sha256(值) 前 8 位一致（可追溯）。"""
    value = "GRN-20260820-011"
    s = summarize({"grn_no": value})
    expect = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    assert f"grn_no:str:{expect}" in s


def test_summarize_distinct_types_kept():
    """不同类型保留各自类型名（str/int/float/bool）。"""
    s = summarize({"qty": 100, "price": 0.5, "ok": True})
    assert "qty:int:" in s
    assert "price:float:" in s
    assert "ok:bool:" in s


def test_summarize_non_dict_value():
    """非 dict 入参 → "{类型}:{sha256前8位}"。"""
    s = summarize("plain-string")
    assert s.startswith("str:")
    assert re.fullmatch(r"str:[0-9a-f]{8}", s)


def test_summarize_truncated_to_limit():
    """摘要长度截断到 limit 字符（默认 300）；截断后不以 "}" 结尾（硬截断）。"""
    big = {f"k{i}": "v" * 10 for i in range(50)}
    s = summarize(big, limit=100)
    assert len(s) == 100
    assert len(summarize(big)) <= 300
