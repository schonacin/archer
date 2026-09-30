"""Pure camera/portal math shared by validation and the browser specification."""

from __future__ import annotations

import math


def portal_embedding(parent_bounds, child_viewbox, inset=8):
    px, py, pw, ph = parent_bounds
    vx, vy, vw, vh = child_viewbox
    values = (*parent_bounds, *child_viewbox, inset)
    if not all(math.isfinite(value) for value in values) or min(pw, ph, vw, vh) <= 0 or inset < 0:
        raise ValueError("Portal geometry must be finite and positive")
    ix, iy = min(inset, pw * 0.1), min(inset, ph * 0.1)
    x, y, width, height = px + ix, py + iy, pw - 2 * ix, ph - 2 * iy
    scale = min(width / vw, height / vh)
    return scale, x + (width - scale * vw) / 2 - scale * vx, y + (height - scale * vh) / 2 - scale * vy


def compose(camera, embedding):
    scale, tx, ty = camera
    factor, bx, by = embedding
    return scale * factor, scale * bx + tx, scale * by + ty


def inverse_rebase(child_camera, embedding):
    child_scale, child_tx, child_ty = child_camera
    factor, bx, by = embedding
    if factor <= 0 or not all(math.isfinite(value) for value in (*child_camera, *embedding)):
        raise ValueError("Camera and embedding must be finite with positive scale")
    parent_scale = child_scale / factor
    return parent_scale, child_tx - parent_scale * bx, child_ty - parent_scale * by
