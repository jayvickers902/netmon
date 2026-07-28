from __future__ import annotations

from datetime import timedelta

import pytest

from netmon.investigation import InvestigationState


def test_investigation_window_is_clock_driven(frozen_clock):
    state = InvestigationState(frozen_clock)
    assert not state.active
    started = frozen_clock.now()
    ends = started + timedelta(seconds=10)
    state.start(7, started, ends)
    assert state.active
    assert state.snapshot() is not None
    frozen_clock.advance(11)
    assert not state.active
    assert state.snapshot() is None


def test_stop_deactivates_and_active_start_is_rejected(frozen_clock):
    state = InvestigationState(frozen_clock)
    started = frozen_clock.now()
    state.start(1, started, started + timedelta(seconds=10))
    with pytest.raises(ValueError, match="already active"):
        state.start(2, started, started + timedelta(seconds=20))
    state.stop()
    assert not state.active
    assert state.snapshot() is None
