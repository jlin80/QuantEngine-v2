"""Signal Engine: validación, deduplicación, expiración, conflictos."""

from datetime import timedelta

from app.engine.models import Direction, SignalStatus
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import SignalHistoryStore
from app.engine.validators import SignalValidator
from app.utils.time import utc_now

from tests.unit.quant_helpers import make_signal


def _engine(ttl: float = 300.0) -> tuple[SignalEngine, SignalHistoryStore]:
    history = SignalHistoryStore()
    engine = SignalEngine(
        SignalValidator(), history, None, signal_ttl_seconds=ttl, dedupe_window_seconds=60.0
    )
    return engine, history


async def test_valid_signal_becomes_active():
    engine, _ = _engine()
    assert await engine.submit(make_signal()) is True
    active = engine.active_signals("BTCUSDT")
    assert len(active) == 1
    assert active[0].strategy_name == "alpha"


async def test_invalid_signal_is_rejected_and_recorded():
    engine, history = _engine()
    bad = make_signal(reasons=())  # sin razones: explicabilidad obligatoria

    assert await engine.submit(bad) is False

    assert engine.active_signals("BTCUSDT") == []
    rejected = history.signals(status=SignalStatus.REJECTED)
    assert len(rejected) == 1
    assert any("razones" in reason for reason in rejected[0].status_reasons)


async def test_validator_catches_incoherent_levels():
    validator = SignalValidator()
    now = utc_now()

    from app.engine.models import EntryZone

    bad_sl = make_signal(
        direction=Direction.LONG,
        entry_zone=EntryZone(low=100.0, high=101.0),
        stop_loss=105.0,  # stop por encima de la entrada en un long
    )
    problems = validator.validate(bad_sl, now=now)
    assert any("stop_loss" in p for p in problems)

    expired = make_signal(expiration=now - timedelta(seconds=1))
    assert any("expiración" in p for p in validator.validate(expired, now=now))

    out_of_range = make_signal(score=150.0)
    assert any("score" in p for p in validator.validate(out_of_range, now=now))


async def test_duplicate_signal_supersedes_previous():
    engine, history = _engine()
    first = make_signal(score=70.0)
    second = make_signal(score=90.0)

    await engine.submit(first)
    await engine.submit(second)

    active = engine.active_signals("BTCUSDT")
    assert len(active) == 1
    assert active[0].signal_id == second.signal_id
    superseded = history.signals(status=SignalStatus.SUPERSEDED)
    assert [r.signal.signal_id for r in superseded] == [first.signal_id]


async def test_signals_expire_by_ttl():
    engine, history = _engine(ttl=10.0)
    old = make_signal(timestamp=utc_now() - timedelta(seconds=5))
    await engine.submit(old)

    expired = engine.expire_stale(now=utc_now() + timedelta(seconds=20))

    assert expired == 1
    assert engine.active_signals("BTCUSDT") == []
    assert len(history.signals(status=SignalStatus.EXPIRED)) == 1


async def test_conflict_detection_and_priority_order():
    engine, _ = _engine()
    await engine.submit(make_signal(strategy="alpha", direction=Direction.LONG, score=60.0))
    await engine.submit(make_signal(strategy="beta", direction=Direction.SHORT, score=90.0))

    assert engine.detect_conflict("BTCUSDT") is True
    active = engine.active_signals("BTCUSDT")
    assert active[0].strategy_name == "beta", "mayor prioridad (score x confianza) primero"
    assert "BTCUSDT" in engine.status()["conflicts"]


async def test_consume_resolves_all_active():
    engine, history = _engine()
    await engine.submit(make_signal(strategy="alpha"))
    await engine.submit(make_signal(strategy="beta"))

    engine.consume("BTCUSDT", SignalStatus.ACCEPTED, ("decisión aceptada",))

    assert engine.active_signals("BTCUSDT") == []
    assert len(history.signals(status=SignalStatus.ACCEPTED)) == 2
