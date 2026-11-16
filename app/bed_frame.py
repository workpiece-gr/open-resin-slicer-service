"""The bed frame: PrusaSlicer bed millimetres (lower-left origin) versus UVtools raster ("display") millimetres.

Shared by the order project (plate placement) and the orientation finalists (centring the slice), so both use ONE
measured conversion.
"""
from __future__ import annotations

from .placement import Envelope2D
from .profiles import Profile


class BedFrameError(ValueError):
    pass


def printer_flag(printer: Profile, key: str) -> bool:
    """A boolean printer setting (``display_mirror_x = 1``) from the printer's PrusaSlicer ini."""
    try:
        for line in printer.config.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == key:
                return value.strip() == "1"
    except OSError as exc:
        raise BedFrameError(f"Cannot read the printer configuration: {exc}") from exc
    return False


def bed_envelope_from_native_rectangle(rectangle, *, bed_width_mm: float, bed_depth_mm: float, mirror_x: bool, mirror_y: bool) -> Envelope2D:
    """The measured cured footprint as a rectangle in PrusaSlicer bed millimetres (lower-left origin).

    UVtools reports the whole-print rectangle in raster ("display") millimetres: the origin is the top-left
    pixel and Y grows downward, and the printer's ``display_mirror_x`` mirrors the raster horizontally. The
    PrusaSlicer bed (and every 3MF build-item transform) has its origin at the lower-left corner with Y growing
    upward. Measured on a real Mars 2 project (``display_mirror_x = 1``, ``display_mirror_y = 0``, 2026-11-12):
    the cured footprint seen in the raster at x 38.5..82.62, y 66.25..93.08 belongs to an object whose 3MF
    centre is at bed (21.31, 50.28), so ``display_x = bed_width - bed_x`` and ``display_y = bed_depth - bed_y``.
    Only that combination has been measured; any other combination is refused rather than guessed.

    The footprint is NOT assumed to be centred on the bed: PrusaSlicer's exported item sits wherever its
    placement put it, and supports and the pad shift the cured rectangle against the mesh.
    """
    if not mirror_x or mirror_y:
        raise BedFrameError("The bed-to-raster mapping is only measured for printers with display_mirror_x = 1 and display_mirror_y = 0.")
    return Envelope2D(
        min_x_mm=round(bed_width_mm - rectangle.max_x_mm, 6),
        max_x_mm=round(bed_width_mm - rectangle.x_mm, 6),
        min_y_mm=round(bed_depth_mm - rectangle.max_y_mm, 6),
        max_y_mm=round(bed_depth_mm - rectangle.y_mm, 6),
    )
