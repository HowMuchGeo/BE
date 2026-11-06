from dataclasses import dataclass
from app.services.engine import policy
from app.services.engine.zone import LAND_CONTRIBUTION_COEF

#계수 · 한도 (policy_rules.json)
BUILDING_COEF = float(policy.RULES["building_contribution_coef"])
CASH_COEF = float(policy.RULES["cash_contribution_coef"])
CASH_MAX_SHARE = float(policy.RULES["cash_contribution_max_share"])
SITE_VALUE_WEIGHT = float(policy.RULES["site_value_weight"])

#기부면적 비율 (합 1)
@dataclass(frozen=True)
class ContributionMix:
    land: float = 1.0
    public_rental: float = 0.0
    cash: float = 0.0

#프리셋 (프론트 CONTRIBUTION_PRESETS 와 같은 값)
PRESETS = {
    "land": ContributionMix(1.0, 0.0, 0.0),
    "land_cash": ContributionMix(1 - CASH_MAX_SHARE, 0.0, CASH_MAX_SHARE),
    "land_rental": ContributionMix(0.5, 0.5, 0.0),
}

#입력 비율(%) -> 합 1 비율 + 현금 한도 초과 여부
def normalize_mix(land: float, public_rental: float, cash: float) -> tuple[ContributionMix, bool]:
    land, public_rental, cash = (max(float(v or 0.0), 0.0) for v in (land, public_rental, cash))
    total = land + public_rental + cash
    if total <= 0:
        return PRESETS["land"], False
    land, public_rental, cash = land / total, public_rental / total, cash / total
    if cash <= CASH_MAX_SHARE + 1e-9:
        return ContributionMix(land, public_rental, cash), False
    rest = land + public_rental
    if rest <= 0:
        return ContributionMix(1 - CASH_MAX_SHARE, 0.0, CASH_MAX_SHARE), True
    scale = (1 - CASH_MAX_SHARE) / rest
    return ContributionMix(land * scale, public_rental * scale, CASH_MAX_SHARE), True

#토지 기부 비율 c -> 필요한 Σ 계수 × α
def contribution_need(land_ratio: float) -> float:
    if land_ratio <= 0:
        return 0.0
    return LAND_CONTRIBUTION_COEF * land_ratio / (1 - land_ratio)

@dataclass
class MixedContribution:
    total_m2: float
    land_ratio: float
    cash_area_m2: float
    rental_supply_m2: float

#기부면적 비율대로 필요한 공공기여 계산
def mixed_contribution(
    need: float,
    min_ratio: float,
    site_m2: float,
    mix: ContributionMix,
    land_share_per_supply: float = 0.0,
    conversion_per_supply: float = 0.0,
) -> MixedContribution:
    if (need <= 0 and min_ratio <= 0) or site_m2 <= 0:
        return MixedContribution(0.0, 0.0, 0.0, 0.0)
    lam, b = land_share_per_supply, conversion_per_supply
    per_rental = lam + b
    if mix.public_rental > 0 and per_rental <= 0:
        raise ValueError("공공임대 몫이 있는데 대지지분·설치비 환산이 0 입니다")
    land_part = mix.land + (mix.public_rental * lam / per_rental if mix.public_rental > 0 else 0.0)
    building_part = mix.public_rental * b / per_rental if mix.public_rental > 0 else 0.0
    total = 0.0
    if need > 0:
        total = need * site_m2 / (
            LAND_CONTRIBUTION_COEF * land_part + BUILDING_COEF * building_part + CASH_COEF * mix.cash
            + need * land_part
        )
    total = max(total, min_ratio * site_m2)
    return MixedContribution(
        total_m2=total,
        land_ratio=mix.land * total / site_m2,
        cash_area_m2=mix.cash * total,
        rental_supply_m2=mix.public_rental * total / per_rental if mix.public_rental > 0 else 0.0,
    )