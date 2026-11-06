# 정책 규칙 파일 (app/services/engine/data/policy_rules.json)
from app.services.engine import policy
from app.services.engine.prior_asset import is_public_owner


def test_policy_file_is_bundled_with_engine() -> None:
    #engine 폴더를 옮긴 뒤에도 규칙 파일을 찾는다
    assert policy.POLICY_PATH.exists()
    assert policy.POLICY_PATH.parent.parent.name == "engine"


def test_policy_loads() -> None:
    data = policy.load()
    assert isinstance(data, dict)
    assert data


def test_pending_rules_parse() -> None:
    rules = policy.pending_rules(policy.load())
    assert all(isinstance(rule, policy.PendingRule) for rule in rules)


def test_is_public_owner() -> None:
    assert is_public_owner("국유지")
    assert is_public_owner("시, 도유지")
    assert not is_public_owner("개인")
    assert not is_public_owner("")
