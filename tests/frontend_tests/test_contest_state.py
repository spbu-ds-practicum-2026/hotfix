from datetime import UTC, datetime, timedelta

from app.models.contracts import Contest


def _contest(starts_delta: timedelta, ends_delta: timedelta, **extra) -> Contest:
    now = datetime.now(UTC)
    return Contest(
        contest_id="c1",
        name="Test",
        starts_at=(now + starts_delta).isoformat().replace("+00:00", "Z"),
        ends_at=(now + ends_delta).isoformat().replace("+00:00", "Z"),
        **extra,
    )


def test_contest_in_window_is_open() -> None:
    assert _contest(timedelta(hours=-1), timedelta(hours=1)).state == "open"


def test_contest_before_start_is_upcoming() -> None:
    assert _contest(timedelta(hours=1), timedelta(hours=2)).state == "upcoming"


def test_contest_after_end_is_finished() -> None:
    assert _contest(timedelta(days=-10), timedelta(days=-9)).state == "finished"


def test_unparsable_dates_fall_back_to_open() -> None:
    contest = Contest(
        contest_id="c1",
        name="Test",
        starts_at="?",
        ends_at="?",
    )
    assert contest.state == "open"
