"""Адаптер Judge0 и его fake-реализация (services/processing/src/judge0/)."""

import pytest
from contracts.statuses import Verdict
from judge0.adapter import (
    JudgeProtocolError,
    JudgeStatus,
    JudgeTimeoutError,
    JudgeUnavailableError,
    map_judge0_status_id,
    to_verdict,
)
from judge0.fake_judge0 import FakeJudge0


async def _run(fake, source_code="int main() {}"):
    run_id = await fake.submit(source_code, "cpp", 2000, 262144)
    return await fake.get_result(run_id)


@pytest.mark.parametrize(
    "status_id,expected",
    [
        (1, JudgeStatus.PENDING),
        (2, JudgeStatus.PENDING),
        (3, JudgeStatus.ACCEPTED),
        (4, JudgeStatus.WRONG_ANSWER),
        (5, JudgeStatus.TIME_LIMIT_EXCEEDED),
        (6, JudgeStatus.COMPILATION_ERROR),
        (7, JudgeStatus.RUNTIME_ERROR),
        (12, JudgeStatus.RUNTIME_ERROR),
    ],
)
def test_map_judge0_status_id(status_id, expected):
    assert map_judge0_status_id(status_id) is expected


@pytest.mark.parametrize("status_id", [0, 13, 14, 999])
def test_map_judge0_unknown_status_id_raises_protocol_error(status_id):
    with pytest.raises(JudgeProtocolError):
        map_judge0_status_id(status_id)


@pytest.mark.parametrize(
    "judge_status,verdict",
    [
        (JudgeStatus.ACCEPTED, Verdict.ACCEPTED),
        (JudgeStatus.WRONG_ANSWER, Verdict.WRONG_ANSWER),
        (JudgeStatus.COMPILATION_ERROR, Verdict.COMPILATION_ERROR),
        (JudgeStatus.TIME_LIMIT_EXCEEDED, Verdict.TIME_LIMIT_EXCEEDED),
        (JudgeStatus.RUNTIME_ERROR, Verdict.RUNTIME_ERROR),
    ],
)
def test_to_verdict(judge_status, verdict):
    assert to_verdict(judge_status) is verdict


def test_pending_has_no_verdict():
    assert to_verdict(JudgeStatus.PENDING) is None


@pytest.mark.asyncio
async def test_fake_default_scenario_is_accepted():
    result = await _run(FakeJudge0())
    assert result.status is JudgeStatus.ACCEPTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("accepted", JudgeStatus.ACCEPTED),
        ("wrong_answer", JudgeStatus.WRONG_ANSWER),
        ("compilation_error", JudgeStatus.COMPILATION_ERROR),
        ("time_limit_exceeded", JudgeStatus.TIME_LIMIT_EXCEEDED),
        ("runtime_error", JudgeStatus.RUNTIME_ERROR),
    ],
)
async def test_fake_scenarios_from_source_marker(scenario, expected):
    result = await _run(FakeJudge0(), f"// @judge: {scenario}\nint main() {{}}")
    assert result.status is expected


@pytest.mark.asyncio
async def test_fake_pending_marker_delays_result():
    fake = FakeJudge0()
    run_id = await fake.submit("// @pending: 2\nint main() {}", "cpp", 2000, 262144)
    assert (await fake.get_result(run_id)).status is JudgeStatus.PENDING
    assert (await fake.get_result(run_id)).status is JudgeStatus.PENDING
    assert (await fake.get_result(run_id)).status is JudgeStatus.ACCEPTED


@pytest.mark.asyncio
async def test_fake_timeout_scenario_raises():
    with pytest.raises(JudgeTimeoutError):
        await _run(FakeJudge0(), "// @judge: timeout")


@pytest.mark.asyncio
async def test_fake_unknown_scenario_is_protocol_error():
    with pytest.raises(JudgeProtocolError):
        await _run(FakeJudge0(), "// @judge: something_new")


@pytest.mark.asyncio
async def test_fake_unknown_run_id_is_protocol_error():
    with pytest.raises(JudgeProtocolError):
        await FakeJudge0().get_result("run-404")


@pytest.mark.asyncio
async def test_fake_unavailable_switch():
    fake = FakeJudge0()
    fake.set_unavailable(True)
    with pytest.raises(JudgeUnavailableError):
        await fake.submit("int main() {}", "cpp", 2000, 262144)
    fake.set_unavailable(False)
    assert (await _run(fake)).status is JudgeStatus.ACCEPTED


@pytest.mark.asyncio
async def test_fake_transient_failures_then_success():
    fake = FakeJudge0()
    fake.set_transient_failures(2)
    for _ in range(2):
        with pytest.raises(JudgeUnavailableError):
            await fake.submit("int main() {}", "cpp", 2000, 262144)
    assert (await _run(fake)).status is JudgeStatus.ACCEPTED
