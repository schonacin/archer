"""Real Chromium checks for the separate D3 viewer and continuous camera."""

import shutil
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from archer.render.experimental import render_html
from archer.scan import scan_sources


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    if not shutil.which("d2"):
        pytest.skip("D2 is required for map export")
    graph = scan_sources(
        {
            "pkg/__init__.py": "from .service import run\ndef entry(): return run()\n",
            "pkg/service.py": "from .model import Model\ndef run(): helper(); return Model().work()\ndef helper(): pass\n",
            "pkg/model.py": "class Model:\n def work(self):\n  def inner(): return 1\n  return inner()\n",
        }
    )
    path = tmp_path_factory.mktemp("map") / "map.html"
    render_html(graph, path, layout="tala")
    return path.read_text()


@pytest.fixture
def page(request, artifact):
    executable = shutil.which("chromium") or shutil.which("google-chrome")
    if not executable:
        cached = next((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux-*/chrome"), None)
        executable = str(cached) if cached else None
    if not executable:
        pytest.skip("Chromium is required")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900}, has_touch=True)
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        # Exercise the exact standalone bytes without file:// enterprise policies.
        html = (
            request.getfixturevalue("reference_artifact")
            if getattr(request, "param", None) == "reference"
            else artifact
        )
        page.set_content(html)
        page.wait_for_function("window.__ARCHER_MAP_METRICS__.usableAt !== null")
        yield page
        assert not errors
        assert not requests
        browser.close()


def state(page):
    return page.evaluate("window.__ARCHER_MAP_STATE__()")


def find(page, name):
    page.locator("#search").fill(name)
    page.locator(f'#search-results button[data-entity="{name}"]').click()
    page.wait_for_timeout(800)


def test_function_modules_dependency_jump_and_exact_history(page):
    find(page, "pkg.service")
    assert state(page)["current"] == "pkg.service"
    assert "run" in page.locator("#children").inner_text()
    page.locator('#children button[data-entity="pkg.service.run"]').click()
    assert page.locator("#source").inner_text().endswith("service.py:2")
    before = state(page)
    target = page.locator('#relationships button[data-entity="pkg.model.Model.work"]')
    assert target.count() == 1
    target.click()
    page.wait_for_timeout(550)
    assert state(page)["selected"] == "pkg.model.Model.work"
    page.locator("#back").click()
    page.wait_for_timeout(450)
    after = state(page)
    assert after["current"] == before["current"] and after["selected"] == before["selected"]
    assert after["camera"] == pytest.approx(before["camera"])


def test_wheel_reveals_detail_and_reverses_without_scene_swapping(page):
    card = page.locator('.node-card[data-entity="pkg.service"]')
    box = card.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for _ in range(8):
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(45)
    page.wait_for_timeout(200)
    assert state(page)["current"] == "pkg.service"
    assert "pkg.service.run" in state(page)["visible"]
    assert page.locator("#world").count() == 1
    assert not page.locator(".scene-layer").count()
    assert not page.locator("#back").is_disabled()
    high_scale = state(page)["camera"]["k"]
    for _ in range(8):
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(45)
    page.wait_for_timeout(200)
    assert state(page)["camera"]["k"] < high_scale / 2
    assert state(page)["current"] != "pkg.service"


def test_trackpad_pan_and_keyboard_preserve_scale(page):
    page.mouse.move(400, 300)
    before = state(page)["camera"]
    page.mouse.wheel(30, 20)
    page.wait_for_timeout(120)
    after = state(page)["camera"]
    assert after["k"] == before["k"]
    assert after["x"] == pytest.approx(before["x"] - 30)
    assert after["y"] == pytest.approx(before["y"] - 20)
    page.locator("#canvas").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(100)
    assert state(page)["camera"]["k"] == before["k"]
    assert state(page)["camera"]["x"] == pytest.approx(after["x"] - 55)


def test_search_keyboard_deep_hierarchy_and_reduced_motion(page):
    page.emulate_media(reduced_motion="reduce")
    page.locator("#canvas").focus()
    page.keyboard.press("/")
    assert page.locator("#search").evaluate("e => e === document.activeElement")
    find(page, "pkg.model.Model.work.inner")
    assert state(page)["current"] == "pkg.model.Model.work"
    assert state(page)["selected"] == "pkg.model.Model.work.inner"
    assert page.locator("#breadcrumbs button").count() >= 4
    page.keyboard.press("Escape")
    assert state(page)["current"] == "pkg.model.Model"
    page.keyboard.press("Home")
    assert state(page)["current"] == "pkg"


def test_interrupted_camera_resize_and_connections(page):
    page.locator("#search").fill("pkg.model.Model")
    page.locator('#search-results button[data-entity="pkg.model.Model"]').click()
    page.wait_for_timeout(80)
    page.mouse.move(400, 350)
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(550)
    assert state(page)["camera"]["k"] > 0
    before = state(page)["camera"]
    page.set_viewport_size({"width": 1100, "height": 800})
    page.wait_for_timeout(150)
    assert state(page)["camera"]["k"] == before["k"]
    find(page, "pkg.service")
    page.locator("#connections").click()
    assert page.locator("#connections").get_attribute("aria-pressed") == "false"
    page.locator("#connections").click()
    assert page.locator("#connections").get_attribute("aria-pressed") == "true"
    page.locator("#panel-toggle").click()
    assert page.locator("#explorer").is_hidden()
    page.locator("#canvas").focus()
    page.keyboard.press("/")
    assert page.locator("#explorer").is_visible()
    assert page.locator("#panel-toggle").get_attribute("aria-expanded") == "true"


def test_touch_pinch_and_sidebar_region_entry(page):
    session = page.context.new_cdp_session(page)
    before = state(page)["camera"]["k"]
    session.send(
        "Input.dispatchTouchEvent",
        {
            "type": "touchStart",
            "touchPoints": [
                {"x": 300, "y": 400, "id": 0},
                {"x": 420, "y": 400, "id": 1},
            ],
        },
    )
    session.send(
        "Input.dispatchTouchEvent",
        {
            "type": "touchMove",
            "touchPoints": [
                {"x": 240, "y": 400, "id": 0},
                {"x": 480, "y": 400, "id": 1},
            ],
        },
    )
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(150)
    assert state(page)["camera"]["k"] > before * 1.8
    find(page, "pkg")
    page.locator('#children button[data-entity="pkg.service"]').click()
    page.wait_for_timeout(550)
    assert state(page)["current"] == "pkg.service"


def test_entity_types_and_connection_strength_preserve_selection(page):
    assert page.locator("#connections").get_attribute("aria-pressed") == "true"
    assert page.locator(".edge-route").count() > 0
    find(page, "pkg.model")
    assert page.locator('.node-card[data-kind="class"]').count() > 0
    find(page, "pkg.service")
    assert page.locator('.node-card[data-kind="function"]').count() > 0
    assert page.locator('.node-card[data-kind="module"]').count() > 0
    page.locator("#connection-opacity").evaluate("e => {e.value=65;e.dispatchEvent(new Event('input'));}")
    page.wait_for_timeout(100)
    assert page.locator("#connection-value").inner_text() == "65%"
    assert page.locator(".edge-route").first.get_attribute("opacity") == "0.65"
    page.locator('#children button[data-entity="pkg.service.run"]').click()
    page.locator("#connection-opacity").evaluate("e => {e.value=0;e.dispatchEvent(new Event('input'));}")
    page.wait_for_timeout(100)
    assert page.locator('.edge-route[data-active="true"]').count() > 0
    assert page.locator('.edge-route[data-active="false"]').count() == 0


def test_cross_hierarchy_endpoints_refine_and_outside_panel_navigates(page):
    initial = dict(state(page)["frontier"])
    assert initial["pkg.model.Model.work"] == "pkg.model.Model"
    assert page.locator(".boundary-port").count() == 0
    find(page, "pkg.model.Model")
    detailed = dict(state(page)["frontier"])
    assert detailed["pkg.model.Model.work"] == "pkg.model.Model.work"
    assert page.locator("#outside-panel").is_hidden()
    page.locator("#outside-toggle").click()
    page.locator("#outside-panel").wait_for(state="visible")
    page.locator('#outside-list button[data-entity="pkg.service.run"]').click()
    page.wait_for_timeout(800)
    assert state(page)["current"] == "pkg.service"
    assert state(page)["selected"] == "pkg.service.run"
    assert page.locator("#outside-panel").is_hidden()
    assert "Outside" in page.locator('#relationships button[data-entity="pkg.model.Model.work"]').inner_text()
    page.locator("#outside-toggle").click()
    page.locator("#outside-panel").wait_for(state="visible")
    assert page.locator('#outside-list button[data-entity="pkg.model.Model.work"]').count() == 1
    assert page.locator('#outside-list button[data-entity="pkg.service.helper"]').count() == 0
    page.locator("#outside-close").click()
    page.wait_for_timeout(100)
    assert page.locator("#outside-panel").is_hidden()
    assert state(page)["selected"] == "pkg.service.run"


def test_pan_preserves_region_frontier_and_arrow_geometry(page):
    find(page, "pkg")
    page.mouse.move(10, 170)
    snapshot = """() => [...document.querySelectorAll('.edge-route')].map(e => {
      const length=e.getTotalLength();
      return {key:JSON.stringify([e.dataset.source,e.dataset.target]),
        points:[0,length/2,length].map(t => {const p=e.getPointAtLength(t);return [p.x,p.y];})};
    })"""
    page.wait_for_timeout(100)
    before_state = state(page)
    before = sorted(page.evaluate(snapshot), key=lambda e: e["key"])
    assert before
    page.mouse.wheel(650, 180)
    page.wait_for_timeout(400)
    after_state = state(page)
    after = sorted(page.evaluate(snapshot), key=lambda e: e["key"])
    assert after_state["current"] == before_state["current"]
    assert after_state["frontier"] == before_state["frontier"]
    assert [e["key"] for e in after] == [e["key"] for e in before]
    for old, new in zip(before, after):
        for old_point, new_point in zip(old["points"], new["points"]):
            assert new_point == pytest.approx([old_point[0] - 650, old_point[1] - 180], abs=0.02)
    assert page.locator(".boundary-port").count() == 0


def test_zoom_defers_frontier_change_until_gesture_settles(page):
    find(page, "pkg")
    before = state(page)["connectionScale"]
    page.mouse.move(10, 170)
    for _ in range(3):
        page.mouse.wheel(0, -120)
        page.wait_for_timeout(35)
    assert state(page)["connectionScale"] == before
    page.wait_for_timeout(500)
    assert state(page)["connectionScale"] == pytest.approx(state(page)["camera"]["k"])
    assert state(page)["connectionScale"] > before


def test_pan_interrupts_pending_zoom_then_refinement_resumes_when_idle(page):
    find(page, "pkg")
    page.mouse.move(10, 170)
    page.mouse.wheel(0, -120)
    page.wait_for_timeout(50)
    page.mouse.wheel(30, 10)
    before = state(page)["frontier"]
    for _ in range(5):
        page.mouse.wheel(30, 10)
        page.wait_for_timeout(40)
        assert state(page)["frontier"] == before
    page.wait_for_timeout(550)
    assert state(page)["connectionScale"] == pytest.approx(state(page)["camera"]["k"])


def test_native_drag_preserves_region_and_magnification(page):
    find(page, "pkg.service")
    page.mouse.move(10, 170)
    before = state(page)
    page.mouse.down()
    page.mouse.move(230, 250, steps=8)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = state(page)
    assert after["current"] == before["current"]
    assert after["frontier"] == before["frontier"]
    assert after["camera"]["k"] == before["camera"]["k"]
    assert after["camera"]["x"] == pytest.approx(before["camera"]["x"] + 220)
    assert after["camera"]["y"] == pytest.approx(before["camera"]["y"] + 80)


@pytest.fixture(scope="module")
def reference_artifact(tmp_path_factory):
    if not shutil.which("d2"):
        pytest.skip("D2 is required for map export")
    graph = scan_sources(
        {
            "pkg/__init__.py": "from .service import run\ndef entry(): return run()\n",
            "pkg/service.py": "from .model import Model\ndef run(): helper(); return Model().work()\ndef helper(): pass\n",
            "pkg/model.py": "class Model:\n def work(self):\n  def inner(): return 1\n  return inner()\n",
        }
    )
    path = tmp_path_factory.mktemp("reference-map") / "reference.html"
    render_html(graph, path, layout="tala", map_layout="reference")
    return path.read_text()


@pytest.mark.parametrize("page", ["reference"], indirect=True)
def test_reference_routes_are_exact_and_translate_with_native_pan(page):
    page.wait_for_timeout(400)
    assert page.locator('.edge-route[data-routing="reference"]').count() > 0
    assert page.evaluate("""() => {
      const data=JSON.parse(document.querySelector('#map-data').textContent);
      const paths=new Map(Object.values(data.layouts).flatMap(l => l.edges.map(e => [JSON.stringify([e.source,e.target]),e.paths.map(p => p.d)])));
      return [...document.querySelectorAll('.edge-route[data-routing="reference"]')].every(e =>
        paths.get(JSON.stringify([e.dataset.source,e.dataset.target])).includes(e.getAttribute('d')));
    }""")
    snapshot = """() => [...document.querySelectorAll('.edge-route')].map(e => {
      const p=e.getPointAtLength(e.getTotalLength()).matrixTransform(e.getCTM());
      return {key:JSON.stringify([e.dataset.source,e.dataset.target]),d:e.getAttribute('d'),point:[p.x,p.y]};
    })"""
    page.mouse.move(10, 170)
    page.wait_for_timeout(100)
    before = sorted(page.evaluate(snapshot), key=lambda e: e["key"])
    region = state(page)["current"]
    page.mouse.wheel(450, 130)
    page.wait_for_timeout(300)
    after = sorted(page.evaluate(snapshot), key=lambda e: e["key"])
    assert state(page)["current"] == region
    assert [e["d"] for e in after] == [e["d"] for e in before]
    for old, new in zip(before, after):
        assert new["point"] == pytest.approx([old["point"][0] - 450, old["point"][1] - 130], abs=0.02)


@pytest.mark.parametrize("page", ["reference"], indirect=True)
def test_reference_navigation_selection_and_zoom_interruption(page):
    find(page, "pkg.service")
    assert page.locator('.edge-route[data-routing="reference"]').count() > 0
    page.locator('#children button[data-entity="pkg.service.run"]').click()
    page.wait_for_timeout(100)
    assert page.locator('.edge-route[data-active="true"]').count() > 0
    page.locator('#relationships button[data-entity="pkg.model.Model.work"]').click()
    page.wait_for_timeout(850)
    assert state(page)["selected"] == "pkg.model.Model.work"
    page.mouse.move(10, 170)
    page.mouse.wheel(0, -120)
    page.wait_for_timeout(50)
    page.mouse.wheel(40, 20)
    page.wait_for_timeout(500)
    assert state(page)["camera"]["k"] > 0
    assert page.evaluate(
        "[...document.querySelectorAll('.edge-route')].every(e => !/NaN|Infinity/.test(e.getAttribute('d')))"
    )
