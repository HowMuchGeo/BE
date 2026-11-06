import json
from dataclasses import dataclass, field
from pathlib import Path

POLICY_PATH = Path(__file__).with_name("data") / "policy_rules.json"

@dataclass
class PendingRule:
    id: str
    title: str
    status: str
    announced: str
    apply: dict
    queries: list[str]
    keywords: list[list[str]]
    sources: list[str] = field(default_factory=list)
    applies_to: str = "재개발"
    note: str = ""
    applied_at: str = ""

def load(path: Path = POLICY_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save(data: dict, path: Path = POLICY_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

def pending_rules(data: dict) -> list[PendingRule]:
    return [PendingRule(**item) for item in data.get("pending", [])]

#규칙 값
RULES: dict = load()["rules"]