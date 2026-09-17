"""Generate a pip --find-links page from paginated GitHub release JSON."""

import html
import json
import sys
from pathlib import Path


def render_index(pages):
    links = {}
    for releases in pages:
        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                name = asset["name"]
                if not name.startswith("archer-") or not name.endswith(".whl"):
                    continue
                url = asset["browser_download_url"]
                digest = asset.get("digest") or ""
                if digest.startswith("sha256:"):
                    url += "#sha256=" + digest.removeprefix("sha256:")
                links[url] = name
    if not links:
        raise ValueError("No published Archer wheels found")
    items = [
        f'<li><a href="{html.escape(url, quote=True)}">{html.escape(name)}</a></li>'
        for url, name in sorted(links.items())
    ]
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        "<title>Archer wheels</title></head><body>\n"
        "<h1>Archer wheels</h1>\n<ul>\n"
        + "\n".join(items)
        + "\n</ul>\n</body></html>\n"
    )


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:])
    page = render_index(json.loads(source.read_text()))
    (destination / "wheels").mkdir(parents=True, exist_ok=True)
    (destination / "index.html").write_text(page, encoding="utf-8")
    (destination / "wheels" / "index.html").write_text(page, encoding="utf-8")
