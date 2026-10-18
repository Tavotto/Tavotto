"""Measure real colour lookup arrays retained by production raster binding (#546).

This runs in the scientific worker interpreter. Bytes are ndarray payloads and tracemalloc
allocations held after binding; they are not process RSS or native desktop frame timings.
"""

from __future__ import annotations

import gc
import json
import sys
import tracemalloc
import weakref
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ENGINE = Path(__file__).resolve().parents[2] / "src" / "tavotto" / "engine"
sys.path.insert(0, str(ENGINE))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import ListedColormap, Normalize  # noqa: E402

import colorbarmodel as C  # noqa: E402


def palette(n=8192):
    colours = np.random.default_rng(546).choice(1 << 24, size=n, replace=False).astype(np.uint32)
    rgb = np.stack([colours >> 16, (colours >> 8) & 255, colours & 255], axis=1).astype(np.uint8)
    return ListedColormap(rgb / 255.0, name="large_discrete"), rgb.reshape(128, n // 128, 3)


def panels(count, cmap, rgb):
    fig, axes = plt.subplots(1, count, squeeze=False)
    images, bars = [], {}
    for ax in axes.ravel():
        images.append(ax.imshow(rgb, interpolation="nearest"))
        cb = fig.colorbar(ScalarMappable(norm=Normalize(0, 1), cmap=cmap), ax=ax)
        bars[cb.ax] = cb
    return fig, images, bars


def retained(count):
    cmap, rgb = palette()
    fig, images, bars = panels(count, cmap, rgb)
    gc.collect()
    tracemalloc.start()
    try:
        C.bind_raster_fields(bars, fig.axes)
        fields = [image._mm_field for image in images]
        tubes = {id(field.tube): field.tube for field in fields}
        payload = [
            sum(getattr(tube, name).nbytes for name in ("keys", "entry", "lut", "t"))
            for tube in tubes.values()
        ]
        keys = [key for key, tube in C._COLOUR_TUBES.items() if id(tube) in tubes]
        current, peak = tracemalloc.get_traced_memory()
        return {
            "fields": len(fields),
            "unique_tubes": len(tubes),
            "unique_payload_bytes": sum(payload),
            "single_tube_payload_bytes": payload[0],
            "cache_key_count": len(keys),
            "cache_key_digest_bytes": sum(len(part) for key in keys for part in key[1:]),
            # Shallow key tuple + bytes objects; the tolerance scalar is shared. The trace
            # above also includes the cache's weakref/dictionary and per-field bookkeeping.
            "cache_key_object_bytes": sum(
                sys.getsizeof(key) + sum(sys.getsizeof(part) for part in key[1:]) for key in keys
            ),
            "tracemalloc_retained_bytes": current,
            "tracemalloc_peak_bytes": peak,
        }
    finally:
        tracemalloc.stop()
        plt.close(fig)


def cache_semantics():
    cmap = ListedColormap(["#123456", "#398759", "#aabb12", "#ddee34"], name="first")
    first = C._shared_colour_tube(cmap)
    same = C._shared_colour_tube(ListedColormap(cmap.colors, name="another_name"))
    copied_payload_shared = first is same
    cmap.colors = list(reversed(cmap.colors))
    cmap._init()
    changed = C._shared_colour_tube(cmap)
    mutated_palette_separate = changed is not first

    positions_a = C._shared_colour_tube(ListedColormap(["red", "green", "blue"]))
    positions_b = C._shared_colour_tube(ListedColormap(["red", "red", "green", "blue"]))
    positions_separate = (
        np.array_equal(positions_a.lut, positions_b.lut)
        and not np.array_equal(positions_a.t, positions_b.t)
        and positions_a is not positions_b
    )
    near_a = np.array([[100.499, 100.499, 100.499], [100.501, 100.501, 100.501]])
    near_b = np.array([[100.499, 99.51, 100.499], [100.501, 101.49, 100.501]])
    assert np.array_equal(np.rint(near_a), np.rint(near_b))
    floats_a = C._shared_colour_tube(ListedColormap(near_a / 255.0))
    floats_b = C._shared_colour_tube(ListedColormap(near_b / 255.0))
    query = C._pack(np.array([[100, 101, 100]], dtype=np.uint8))
    float_nearest = [int(floats_a.lookup(query)[0]), int(floats_b.lookup(query)[0])]

    tolerance = C._FIELD_TOL
    try:
        C._FIELD_TOL = 1
        narrower = C._shared_colour_tube(cmap)
        tolerance_separate = narrower is not changed and len(narrower.keys) < len(changed.keys)
    finally:
        C._FIELD_TOL = tolerance

    dense = first.dense()
    key = int(first.keys[0])
    dense[key] = C._OFF_MAP
    dense_independent = int(first.dense()[key]) == int(first.entry[0])

    temporary = C._shared_colour_tube(ListedColormap(["#fa12bc", "#1901ef"]))
    reference = weakref.ref(temporary)
    del temporary
    gc.collect()
    weakly_released = reference() is None
    return {
        "copied_payload_shared": copied_payload_shared,
        "mutated_palette_separate": mutated_palette_separate,
        "positions_separate": positions_separate,
        "float_nearest_entries": float_nearest,
        "float_tubes_separate": floats_a is not floats_b,
        "tolerance_separate": tolerance_separate,
        "dense_independent": dense_independent,
        "weakly_released": weakly_released,
    }


def independent_recolour():
    cmap = matplotlib.colormaps["viridis"]
    rgb = np.rint(cmap(np.linspace(0, 1, 64))[None, :, :3] * 255).astype(np.uint8)
    fig, images, bars = panels(2, cmap, rgb)
    try:
        C.bind_raster_fields(bars, fig.axes)
        fields = [image._mm_field for image in images]
        shared = fields[0].tube is fields[1].tube
        fields[0].cb.mappable.set_cmap("plasma")
        fields[0].sync()
        only_first_changed = (
            not np.array_equal(images[0].get_array(), fields[0].original)
            and images[1].get_array() is fields[1].original
        )
        fields[1].cb.mappable.set_cmap("magma")
        fields[1].sync()
        separate_buffers = fields[0]._buf is not fields[1]._buf and not np.array_equal(
            images[0].get_array(), images[1].get_array()
        )
        fields[0].cb.mappable.set_cmap(cmap)
        fields[0].sync()
        restored = images[0].get_array() is fields[0].original
        other_still_changed = not np.array_equal(images[1].get_array(), fields[1].original)
        return {
            "shared": shared,
            "only_first_changed": only_first_changed,
            "separate_buffers": separate_buffers,
            "restored_original_reference": restored,
            "other_still_changed": other_still_changed,
        }
    finally:
        plt.close(fig)


def direct_fields():
    cmap = matplotlib.colormaps["viridis"]
    rgb = np.rint(cmap(np.linspace(0, 1, 64))[None, :, :3] * 255).astype(np.uint8)
    fig, images, bars = panels(2, cmap, rgb)
    try:
        fields = [C.RasterField(image, cb) for image, cb in zip(images, bars.values())]
        return fields[0].tube is fields[1].tube
    finally:
        plt.close(fig)


class InPlaceInverse(Normalize):
    """A user norm may reuse its writable input as the inverse's output."""

    def inverse(self, value):
        self.last_inverse_input = np.asarray(value, dtype=float)
        self.last_inverse_input *= self.vmax - self.vmin
        self.last_inverse_input += self.vmin
        return self.last_inverse_input


def cross_figure_callbacks():
    cmap = matplotlib.colormaps["viridis"]
    rgb = np.rint(cmap(np.linspace(0, 1, 64))[None, :, :3] * 255).astype(np.uint8)

    def pair(shared):
        figures, images, fields = [], [], []
        try:
            for norm in (InPlaceInverse(0, 2), Normalize(0, 1)):
                fig, ax = plt.subplots()
                figures.append(fig)
                image = ax.imshow(rgb, interpolation="nearest")
                cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax)
                if shared:
                    C.bind_raster_fields({cb.ax: cb}, fig.axes)
                    field = image._mm_field
                else:
                    field = C.RasterField(image, cb, tube=C._ColourTube(cmap))
                images.append(image)
                fields.append(field)
            initial = fields[1].tube.t.copy()
            fields[0].cb.mappable.set_cmap("plasma")
            fields[0].sync()
            positions_unchanged = np.array_equal(initial, fields[1].tube.t)
            callback_input_isolated = not np.shares_memory(
                fields[0].norm0.last_inverse_input, fields[0].tube.t
            )
            other_original_untouched = images[1].get_array() is fields[1].original
            fields[1].cb.mappable.set_cmap("plasma")
            fields[1].sync()
            return {
                "shared": fields[0].tube is fields[1].tube,
                "positions_unchanged": positions_unchanged,
                "callback_input_isolated": callback_input_isolated,
                "other_original_untouched": other_original_untouched,
            }, [np.asarray(image.get_array()).copy() for image in images]
        finally:
            for figure in figures:
                plt.close(figure)

    _, independent = pair(False)
    facts, shared = pair(True)
    facts["mutating_figure_matches_independent"] = np.array_equal(shared[0], independent[0])
    facts["other_figure_matches_independent"] = np.array_equal(shared[1], independent[1])
    return facts


if __name__ == "__main__":
    first = retained(1)
    gc.collect()
    four = retained(4)
    gc.collect()
    print(
        json.dumps(
            {
                "module": C.__file__,
                "one": first,
                "four": four,
                "cache": cache_semantics(),
                "recolour": independent_recolour(),
                "direct_fields_shared": direct_fields(),
                "cross_figure_callbacks": cross_figure_callbacks(),
            }
        )
    )
