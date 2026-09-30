"""Repeatable maximum-reference-fixture performance and portability check."""

import os
import shutil
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from archer.render.html import render_html
from archer.scan import scan_sources


def test_two_hundred_scene_reference_budget(tmp_path):
    if not shutil.which("d2"):
        pytest.skip("D2 is required for the generated reference fixture")
    chromium = next(
        (Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux-*/chrome"),
        None,
    )
    if chromium is None:
        pytest.skip("Chromium is required for the performance reference")

    module_count = 199
    sources = {
        f"m{index}.py": (
            f"import m{index + 1}\ndef f_{index}(): return m{index + 1}.f_{index + 1}()\n"
            if index + 1 < module_count
            else f"def f_{index}(): return {index}\n"
        )
        for index in range(module_count)
    }
    graph = scan_sources(sources)
    output = tmp_path / "reference-200.html"
    started = time.perf_counter()
    report = render_html(graph, output, layout="auto")
    build_seconds = time.perf_counter() - started
    assert report["scenes"] == 200
    assert report["bytes"] < 20 * 1024 * 1024
    assert build_seconds < 60

    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(
            executable_path=str(chromium),
            env={
                **os.environ,
                "HOME": str(tmp_path / "home"),
                "XDG_CONFIG_HOME": str(tmp_path / "home/config"),
            },
        )
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(output.as_uri())
        page.wait_for_function("window.__ARCHER_METRICS__.usableAt !== null")
        initial = page.evaluate("window.__ARCHER_METRICS__")
        assert initial["usableAt"] - initial["startedAt"] < 2000
        assert page.locator("#node-list button").count() == module_count
        assert page.locator("#diagram > .scene-layer > svg").count() == 1

        page.locator("#node-list button").first.dblclick()
        page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 2")
        assert page.locator("#diagram > .scene-layer > svg").count() == 2
        page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
        page.wait_for_timeout(450)
        metrics = page.evaluate("window.__ARCHER_METRICS__")
        browser.close()

    assert metrics["preparations"][-1]["duration"] < 100
    transition = metrics["transitions"][-1]
    assert transition["p95FrameMs"] <= 20
    assert transition["longTasks"] == 0
    assert all(url.startswith(("file:", "data:")) for url in requests)
