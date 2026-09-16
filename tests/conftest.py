"""Keep scan caches isolated from the developer's cache and from other tests."""

import pytest


@pytest.fixture(autouse=True)
def cache_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache-home")))
