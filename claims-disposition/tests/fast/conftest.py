from __future__ import annotations

import pytest
import vane


@pytest.fixture(scope="session", autouse=True)
def _use_local_vane_runner():
    """Keep isolated relation tests off any ambient Ray cluster."""

    vane.configure(runner="local")
