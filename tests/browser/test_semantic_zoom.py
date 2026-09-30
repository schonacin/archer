"""Real-browser semantic navigation checks (requires Playwright browsers)."""

import os
import shutil
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from archer.render.html import render_html
from archer.scan import scan_sources


@pytest.fixture(scope="module")
def explorer(tmp_path_factory):
    if not shutil.which("d2"):
        pytest.skip("D2 is required for browser artifact tests")
    path = tmp_path_factory.mktemp("browser") / "explorer.html"
    graph = scan_sources(
        {
            "pkg/a.py": "class A:\n def run(self): return self.helper()\n def helper(self): pass\n",
            "pkg/b.py": "from .a import A\nclass B(A): pass\nA()\n",
        }
    )
    render_html(graph, path, layout="tala")
    return path


@pytest.fixture(params=("chromium", "firefox", "webkit"))
def browser_page(request, tmp_path):
    try:
        with playwright.sync_playwright() as runtime:
            options = {}
            browser_cache = Path.home() / ".cache/ms-playwright"
            executable_patterns = {
                "chromium": "chromium-*/chrome-linux-*/chrome",
                "firefox": "firefox-*/firefox/firefox",
                "webkit": "webkit-*/pw_run.sh",
            }
            executable = next(browser_cache.glob(executable_patterns[request.param]), None)
            if executable:
                options["executable_path"] = str(executable)
            options["env"] = {
                **os.environ,
                "HOME": str(tmp_path),
                "XDG_CONFIG_HOME": str(tmp_path / "config"),
            }
            browser = getattr(runtime, request.param).launch(**options)
            page = browser.new_page(viewport={"width": 1100, "height": 760})
            yield page
            browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Playwright {request.param} is not installed: {exc}")


def test_offline_click_free_descent_back_and_mount_bound(explorer, browser_page):
    requests = []
    browser_page.on("request", lambda request: requests.append(request.url))
    browser_page.goto(explorer.as_uri())
    assert browser_page.locator("#scene-title").inner_text() == "Modules"
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1

    card = browser_page.locator('#stage [data-role="node"]').first
    box = card.bounding_box()
    browser_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for _ in range(9):
        browser_page.mouse.wheel(0, -120)
        browser_page.wait_for_timeout(35)
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 2")
    assert browser_page.locator("#diagram > .scene-layer > svg").count() <= 2
    browser_page.wait_for_function(
        "!document.querySelector('#scene-title').textContent.startsWith('Modules')"
    )
    browser_page.wait_for_timeout(450)
    assert browser_page.locator("#scene-title").inner_text().startswith("Types in")
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1

    browser_page.locator("#diagram").press("Escape")
    browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")
    browser_page.wait_for_timeout(350)
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1
    assert all(url.startswith(("file:", "data:")) for url in requests)


def test_click_free_reaches_symbols_and_zoom_out_ascends(explorer, browser_page):
    browser_page.goto(explorer.as_uri())
    for expected in ("Types in", "Members of"):
        card = browser_page.locator('#stage [data-role="node"]').first
        box = card.locator('[data-archer-boundary="true"]').bounding_box()
        browser_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        browser_page.wait_for_timeout(200)
        for _ in range(30):
            browser_page.mouse.wheel(0, -120)
            browser_page.wait_for_timeout(35)
            if browser_page.locator("#diagram > .scene-layer > svg").count() == 2:
                break
        assert browser_page.locator("#diagram > .scene-layer > svg").count() == 2
        browser_page.wait_for_function(
            "prefix => document.querySelector('#scene-title').textContent.startsWith(prefix)",
            arg=expected,
        )
        browser_page.wait_for_timeout(450)

    assert browser_page.locator("#scene-title").inner_text().startswith("Members of")
    browser_page.wait_for_timeout(200)
    for _ in range(30):
        browser_page.mouse.wheel(0, 120)
        browser_page.wait_for_timeout(35)
        if browser_page.locator("#diagram > .scene-layer > svg").count() == 2:
            break
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 2
    browser_page.wait_for_function(
        "document.querySelector('#scene-title').textContent.startsWith('Types in')"
    )
    browser_page.wait_for_timeout(350)
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1


def test_keyboard_open_and_reduced_motion(explorer, browser_page):
    browser_page.emulate_media(reduced_motion="reduce")
    browser_page.goto(explorer.as_uri())
    item = browser_page.locator("#node-list button").first
    item.focus()
    item.press("Enter")
    browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1
    browser_page.locator("#back").click()
    browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")


def test_every_embedded_scene_is_reachable_and_returnable(explorer, browser_page):
    browser_page.emulate_media(reduced_motion="reduce")
    browser_page.goto(explorer.as_uri())
    modules = browser_page.locator("#node-list button").all_inner_texts()
    assert modules
    for module in modules:
        browser_page.get_by_role("button", name=module, exact=True).dblclick()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
        classes = browser_page.locator("#node-list button").all_inner_texts()
        for class_name in classes:
            browser_page.get_by_role("button", name=class_name, exact=True).dblclick()
            browser_page.wait_for_function(
                "document.querySelector('#scene-title').textContent.startsWith('Members of')"
            )
            browser_page.locator("#back").click()
            browser_page.wait_for_function(
                "document.querySelector('#scene-title').textContent.startsWith('Types in')"
            )
        browser_page.locator("#back").click()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")


def test_mid_transition_escape_reverses_and_resize_commits(explorer, browser_page):
    browser_page.goto(explorer.as_uri())
    browser_page.locator("#node-list button").first.dblclick()
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 2")
    browser_page.locator("#diagram").press("Escape")
    browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 1")

    browser_page.locator("#node-list button").first.dblclick()
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 2")
    browser_page.set_viewport_size({"width": 980, "height": 680})
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 1")
    assert browser_page.locator("#scene-title").inner_text() in {
        "Modules",
        "Types in pkg.a",
        "Types in pkg.b",
    }
    if browser_page.context.browser.browser_type.name == "chromium":
        browser_page.goto(explorer.as_uri())
        browser_page.locator("#node-list button").first.dblclick()
        browser_page.wait_for_function(
            "document.querySelectorAll('#diagram > .scene-layer > svg').length === 2"
        )
        session = browser_page.context.new_cdp_session(browser_page)
        session.send("Page.setWebLifecycleState", {"state": "frozen"})
        time.sleep(0.5)
        session.send("Page.setWebLifecycleState", {"state": "active"})
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
        assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1


def test_fifty_navigation_cycles_keep_one_idle_svg(explorer, browser_page):
    if browser_page.context.browser.browser_type.name != "chromium":
        pytest.skip("bounded-cycle stress check runs once in Chromium")
    browser_page.emulate_media(reduced_motion="reduce")
    browser_page.goto(explorer.as_uri())
    session = browser_page.context.new_cdp_session(browser_page)

    def heap_size():
        session.send("HeapProfiler.collectGarbage")
        return session.send("Runtime.getHeapUsage")["usedSize"]

    before = heap_size()
    middle = before
    for cycle in range(50):
        browser_page.locator("#node-list button").first.dblclick()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
        browser_page.locator("#back").click()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")
        if cycle == 24:
            middle = heap_size()
    after = heap_size()
    assert browser_page.locator("#diagram > .scene-layer > svg").count() == 1
    assert after - middle < 2 * 1024 * 1024


def test_touch_pinch_enters_without_tap(explorer, browser_page):
    if browser_page.context.browser.browser_type.name != "chromium":
        pytest.skip("multi-contact protocol check uses Chromium CDP")
    browser_page.goto(explorer.as_uri())
    card = browser_page.locator('#stage [data-role="node"]').first
    box = card.locator('[data-archer-boundary="true"]').bounding_box()
    center = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
    session = browser_page.context.new_cdp_session(browser_page)
    start = [
        {"x": center["x"] - 20, "y": center["y"], "id": 0},
        {"x": center["x"] + 20, "y": center["y"], "id": 1},
    ]
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": start})
    for distance in (60, 110, 180, 280):
        points = [
            {"x": center["x"] - distance, "y": center["y"], "id": 0},
            {"x": center["x"] + distance, "y": center["y"], "id": 1},
        ]
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": points})
        browser_page.wait_for_timeout(45)
    browser_page.wait_for_function("document.querySelectorAll('#diagram > .scene-layer > svg').length === 2")
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")


def test_reference_runtime_metrics(explorer, browser_page):
    if browser_page.context.browser.browser_type.name != "chromium":
        pytest.skip("performance reference is recorded once in Chromium")
    browser_page.goto(explorer.as_uri())
    browser_page.wait_for_function("window.__ARCHER_METRICS__.usableAt !== null")
    for _ in range(5):
        browser_page.locator("#node-list button").first.dblclick()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent !== 'Modules'")
        browser_page.wait_for_timeout(400)
        browser_page.locator("#back").click()
        browser_page.wait_for_function("document.querySelector('#scene-title').textContent === 'Modules'")
        browser_page.wait_for_timeout(340)
    metrics = browser_page.evaluate("window.__ARCHER_METRICS__")
    assert metrics["usableAt"] - metrics["startedAt"] < 2000
    preparation_times = sorted(item["duration"] for item in metrics["preparations"])
    assert preparation_times[int((len(preparation_times) - 1) * 0.95)] < 100
    measured = [item for item in metrics["transitions"] if item["p95FrameMs"]]
    assert measured
    assert max(item["p95FrameMs"] for item in measured) <= 20
    assert sum(item["longTasks"] for item in measured) == 0
