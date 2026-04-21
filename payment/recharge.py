"""充值套餐定义."""

from __future__ import annotations

from typing import Optional

RECHARGE_PACKAGES = [
    {"id": "pkg_10", "amount_yuan": 10, "credits": 100, "label": "10元 = 100积分"},
    {"id": "pkg_50", "amount_yuan": 50, "credits": 550, "label": "50元 = 550积分 (赠50)"},
    {"id": "pkg_100", "amount_yuan": 100, "credits": 1200, "label": "100元 = 1200积分 (赠200)"},
    {"id": "pkg_200", "amount_yuan": 200, "credits": 2600, "label": "200元 = 2600积分 (赠600)"},
]


def get_package(amount_yuan: float) -> Optional[dict]:
    for pkg in RECHARGE_PACKAGES:
        if pkg["amount_yuan"] == amount_yuan:
            return pkg
    return None


def yuan_to_fen(yuan: float) -> int:
    return int(round(yuan * 100))
