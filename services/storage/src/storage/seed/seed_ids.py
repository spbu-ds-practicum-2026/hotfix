"""Стабильные идентификаторы демо-данных. Одинаковы на любой машине (uuid5 от фиксированного namespace).

Таблица значений: PYTHONPATH=services/storage/src python -m storage.seed.seed_ids
"""

from uuid import UUID, uuid5

SEED_NAMESPACE = UUID("6f2c1a54-7b0e-4c52-9d3a-5e8f1b2a7c10")

SUBMISSION_NAMES = ("s1", "s2", "s3", "s4", "s5", "s6", "s7")


def stable_uuid(name: str) -> UUID:
    return uuid5(SEED_NAMESPACE, name)


CONTEST_ID = stable_uuid("contest:demo")
TEAM_ALPHA_ID = stable_uuid("team:alpha")
TEAM_BETA_ID = stable_uuid("team:beta")
PROBLEM_A_ID = stable_uuid("problem:a")
PROBLEM_B_ID = stable_uuid("problem:b")


def submission_id_for(name: str) -> UUID:
    return stable_uuid(f"submission:{name}")


def trace_id_for(name: str) -> UUID:
    return stable_uuid(f"trace:{name}")


def idempotency_key_for(name: str) -> UUID:
    return stable_uuid(f"idempotency-key:{name}")


def event_id_for(name: str, kind: str) -> UUID:
    """kind: "queued" или "verdict"."""
    return stable_uuid(f"event:{name}:{kind}")


def attempt_id_for(name: str, kind: str) -> UUID:
    return stable_uuid(f"attempt:{name}:{kind}")


if __name__ == "__main__":
    print(f"contestId   {CONTEST_ID}")
    print(f"team alpha  {TEAM_ALPHA_ID}")
    print(f"team beta   {TEAM_BETA_ID}")
    print(f"problem A   {PROBLEM_A_ID}")
    print(f"problem B   {PROBLEM_B_ID}")
    for name in SUBMISSION_NAMES:
        print(f"submission {name}  {submission_id_for(name)}  key {idempotency_key_for(name)}")
