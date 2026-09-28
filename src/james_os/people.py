"""Where the people are in a photo, so type does not land on a face.

The renderer borrows a competitor's layout and fills it with OUR photo. The
boxes came from THEIR picture, so nothing guarantees our subject is anywhere
the design left room — measured on a real render 2026-09-28, a subhead landed
squarely across two people's faces.

WHAT THIS IS NOT. It does not recognise anyone. It finds face-shaped regions
and returns rectangles; no identity, no descriptor, nothing stored. The boxes
live for the length of one render.

FAIL OPEN, ALWAYS. Every entry point returns "no faces" when the detector or
its model is unavailable, so a container missing the wheel renders exactly as
it does today rather than failing. A photo with nobody in it is the common
case, and it must cost nothing to be wrong about.

WHY YuNet. Measured alternatives: MediaPipe pins opencv-contrib (82 MB, wants
libGL which python:3.12-slim has not got); Haar cascades miss profiles badly;
RetinaFace needs torch. YuNet is a 232 KB ONNX read through the OpenCV already
being added, ~10-16 ms on a 640 px copy.

ITS LIMIT, STATED PLAINLY. In-plane rotation defeats it past about 30 degrees —
a tilted lifestyle shot reads as no faces at all. That is a quality ceiling, not
a correctness bug, because "no faces" is the same safe answer as today.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

# The long edge we detect on. Faces below a few dozen pixels are not a placement
# problem, and the cost is quadratic in the edge.
_DETECT_EDGE = 640

# Not a tuning detail — the feature. Measured on one photo holding 1-2 real
# faces: 0.3 returned twelve boxes, 0.5 three, 0.6 one. Every spurious box is a
# region the layout would then avoid for no reason, so this errs high: a missed
# face costs what we already pay today, an invented one costs a worse crop.
_SCORE = 0.6

# How far past the detected face a head actually extends. YuNet returns the
# face, not the head: hair, forehead and ears all sit outside it, and text over
# someone's hair reads just as badly as text over their nose.
_PAD_X, _PAD_TOP, _PAD_BOTTOM = 0.28, 0.55, 0.12


@lru_cache(maxsize=1)
def _detector():
    """The YuNet detector, created once per process, or None.

    lru_cache rather than a module global so the failure is retried at most
    once and never re-imported per render.
    """
    try:
        import cv2  # noqa: PLC0415 — optional at import time by design
    except ImportError:
        logger.info("no opencv in this image — faces are not detected, layouts place as before")
        return None
    if not MODEL.exists():
        logger.warning("YuNet model missing at %s — faces are not detected", MODEL)
        return None
    try:
        return cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320),
                                         score_threshold=_SCORE)
    except Exception:  # noqa: BLE001 — a detector we cannot build is "no faces"
        logger.warning("could not create the YuNet detector", exc_info=True)
        return None


def available() -> bool:
    """Whether face detection can run at all here. For diagnostics, not control
    flow — callers should simply act on an empty list."""
    return _detector() is not None


def faces(img: Image.Image) -> list[dict]:
    """Face rectangles as FRACTIONS of the frame, largest first.

    Fractions, not pixels, because every box in a layout spec is fractional and
    reflows across canvas sizes; a pixel answer would be wrong the moment the
    same layout is drawn at another size. Detecting on a downscaled copy and
    dividing by THAT copy's dimensions yields the same fractions, so the
    downscale costs nothing in accuracy of placement.

    Returns [] for no faces, no detector, or any failure.
    """
    det = _detector()
    if det is None or img is None:
        return []
    try:
        import cv2
        import numpy as np

        w, h = img.size
        if w < 2 or h < 2:
            return []
        scale = _DETECT_EDGE / max(w, h)
        if scale < 1.0:
            small = img.convert("RGB").resize(
                (max(1, round(w * scale)), max(1, round(h * scale))),
                Image.Resampling.BILINEAR)
        else:
            small = img.convert("RGB")
        arr = np.asarray(small)[:, :, ::-1]          # PIL RGB -> OpenCV BGR
        sh, sw = arr.shape[:2]
        # setInputSize must match the array EXACTLY or detect() raises.
        det.setInputSize((sw, sh))
        _n, raw = det.detect(arr)
        if raw is None:
            return []
        out = []
        for row in raw:
            fx, fy, fw, fh = (float(v) for v in row[:4])
            if fw <= 0 or fh <= 0:
                continue
            # YuNet landmarks: right eye, left eye, nose, mouth corners.
            eye_y = (float(row[5]) + float(row[7])) / 2 if len(row) > 7 else fy + fh * 0.4
            out.append({
                "x": max(0.0, fx / sw), "y": max(0.0, fy / sh),
                "w": min(1.0, fw / sw), "h": min(1.0, fh / sh),
                "eye_y": max(0.0, min(1.0, eye_y / sh)),
                "score": float(row[14]) if len(row) > 14 else 1.0,
            })
        out.sort(key=lambda f: -(f["w"] * f["h"]))
        return out
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.warning("face detection failed; placing as if the photo had none", exc_info=True)
        return []


def keep_out(face: dict) -> tuple[float, float, float, float]:
    """A face box grown to the HEAD it belongs to, clamped to the frame.

    Returned as (x, y, w, h) fractions — the same shape the renderer's zone
    arithmetic already speaks."""
    x = face["x"] - _PAD_X * face["w"]
    y = face["y"] - _PAD_TOP * face["h"]
    w = face["w"] * (1 + 2 * _PAD_X)
    h = face["h"] * (1 + _PAD_TOP + _PAD_BOTTOM)
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(1.0, x + w), min(1.0, y + h)
    return x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)


def overlap(box: tuple[float, float, float, float],
            zone: tuple[float, float, float, float]) -> float:
    """What FRACTION OF `box` the `zone` covers. Asymmetric on purpose: the
    question is always "how much of this text block is on a face", never the
    reverse."""
    bx, by, bw, bh = box
    zx, zy, zw, zh = zone
    if bw <= 0 or bh <= 0:
        return 0.0
    ix = max(0.0, min(bx + bw, zx + zw) - max(bx, zx))
    iy = max(0.0, min(by + bh, zy + zh) - max(by, zy))
    return (ix * iy) / (bw * bh)


__all__ = ["available", "faces", "keep_out", "overlap", "MODEL"]
