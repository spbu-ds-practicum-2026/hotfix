from dataclasses import dataclass

from judge0.adapter import (
    JudgeProtocolError,
    JudgeResult,
    JudgeStatus,
    JudgeTimeoutError,
    JudgeUnavailableError,
)


@dataclass
class _Run:
    scenario: str
    pending_left: int = 0


def _read_marker(source_code: str, key: str, default_value: str) -> str:
    start = source_code.find(key)
    if start == -1:
        return default_value
    rest = source_code[start + len(key):].lstrip(" ")
    words = rest.split()
    return words[0] if words else default_value


class FakeJudge0:
    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._counter = 0
        self._failures_left = 0
        self._is_unavailable = False

    def set_transient_failures(self, count: int) -> None:
        self._failures_left = count

    def set_unavailable(self, is_unavailable: bool) -> None:
        self._is_unavailable = is_unavailable

    async def submit(
        self,
        source_code: str,
        language: str,
        time_limit_ms: int,
        memory_limit_kb: int,
    ) -> str:
        if self._is_unavailable or self._failures_left > 0:
            if self._failures_left > 0:
                self._failures_left -= 1
            raise JudgeUnavailableError("fake judge0: connection refused")
        scenario = _read_marker(source_code, "@judge:", "accepted")
        pending_left = int(_read_marker(source_code, "@pending:", "0"))
        self._counter += 1
        judge_submission_id = f"run-{self._counter}"
        self._runs[judge_submission_id] = _Run(scenario, pending_left)
        return judge_submission_id

    async def get_result(self, judge_submission_id: str) -> JudgeResult:
        run = self._runs.get(judge_submission_id)
        if run is None:
            raise JudgeProtocolError(f"unknown judge_submission_id {judge_submission_id}")
        if run.scenario == "timeout":
            raise JudgeTimeoutError("fake judge0: no result in time")
        if run.pending_left > 0:
            run.pending_left -= 1
            return JudgeResult(status=JudgeStatus.PENDING)
        if run.scenario == "accepted":
            return JudgeResult(JudgeStatus.ACCEPTED, stdout="42\n", time_ms=15, memory_kb=3200)
        if run.scenario == "wrong_answer":
            return JudgeResult(JudgeStatus.WRONG_ANSWER, stdout="41\n", time_ms=12, memory_kb=3100)
        if run.scenario == "compilation_error":
            return JudgeResult(JudgeStatus.COMPILATION_ERROR, stderr="error: expected declaration")
        if run.scenario == "time_limit_exceeded":
            return JudgeResult(JudgeStatus.TIME_LIMIT_EXCEEDED, time_ms=2000, memory_kb=3500)
        if run.scenario == "runtime_error":
            return JudgeResult(JudgeStatus.RUNTIME_ERROR, stderr="Segmentation fault", time_ms=8, memory_kb=3000)
        raise JudgeProtocolError(f"fake judge0: unknown scenario {run.scenario}")