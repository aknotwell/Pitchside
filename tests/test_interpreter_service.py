from matchlens.interpret.state import MatchInterpreter
from matchlens.services.interpreter import InterpreterService


def _service() -> InterpreterService:
    svc = object.__new__(InterpreterService)  # no Azure clients needed for partition bookkeeping
    svc.matches, svc.match_partition, svc.last_checkpoint = {}, {}, {}
    return svc


def test_partition_handover_drops_stale_match_state():
    """Regression: a replica that lost a partition and later re-claimed it kept its old in-memory
    state, resumed from a newer checkpoint, and silently lost every event processed in between."""
    svc = _service()
    for mid, pid in (("m1", "0"), ("m2", "0"), ("m3", "1")):
        svc.matches[mid] = MatchInterpreter(mid)
        svc.match_partition[mid] = pid
    svc.last_checkpoint.update({"0": 1.0, "1": 2.0})

    assert svc.forget_partition("0") == 2
    assert set(svc.matches) == {"m3"} and set(svc.match_partition) == {"m3"}
    assert "0" not in svc.last_checkpoint and "1" in svc.last_checkpoint
    assert svc.forget_partition("0") == 0
