"""供应商档案与报价服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.5（档案）/ §3.6（报价）：
- 档案字段 supplier_id/name/region/qualification_expiry/risk_level/ontime_rate/pass_rate；
- 报价复合主键 (supplier_id, sku)，字段 latest_price/currency/p90_min/p90_max。
"""
from __future__ import annotations

_SUPPLIERS: dict[str, dict] = {}
_QUOTES: dict[tuple[str, str], dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    SUP-01 长鸿新能源（资质 2026-09-01 将过期、high、准时率 0.76<80%、合格率 0.93<95% → FAIL 路径）；
    SUP-02 恒力电子（资质 2027-05-01、low、0.94/0.98 → PASS 路径）。
    """
    _SUPPLIERS["SUP-01"] = {"supplier_id": "SUP-01", "name": "长鸿新能源", "region": "华东",
                            "qualification_expiry": "2026-09-01", "risk_level": "high",
                            "ontime_rate": 0.76, "pass_rate": 0.93}
    _SUPPLIERS["SUP-02"] = {"supplier_id": "SUP-02", "name": "恒力电子", "region": "华南",
                            "qualification_expiry": "2027-05-01", "risk_level": "low",
                            "ontime_rate": 0.94, "pass_rate": 0.98}
    _QUOTES[("SUP-01", "M-1001")] = {"supplier_id": "SUP-01", "sku": "M-1001",
                                     "latest_price": 0.0086, "currency": "USD",
                                     "p90_min": 0.0068, "p90_max": 0.0091}
    _QUOTES[("SUP-02", "M-1001")] = {"supplier_id": "SUP-02", "sku": "M-1001",
                                     "latest_price": 0.0071, "currency": "USD",
                                     "p90_min": 0.0068, "p90_max": 0.0091}
    _QUOTES[("SUP-02", "M-2002")] = {"supplier_id": "SUP-02", "sku": "M-2002",
                                     "latest_price": 18.5, "currency": "CNY",
                                     "p90_min": 17.2, "p90_max": 19.1}


class SupplierService:
    """供应商档案与近 90 天报价（价格审查数据源，能力说明 §5）。"""

    @staticmethod
    async def profile(supplier_id: str) -> dict:
        """按编码查询供应商档案；无记录返回 {}（不抛错，api_contract §2.5）。"""
        return _SUPPLIERS.get(supplier_id, {})

    @staticmethod
    async def quote(supplier_id: str, sku: str) -> dict:
        """按 (supplier_id, sku) 查询报价；无记录返回 {}。"""
        return _QUOTES.get((supplier_id, sku), {})


_seed()
