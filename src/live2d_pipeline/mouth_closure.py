"""Opt-in common-closure authoring math; never changes the automatic binder.

Curves share left-to-right source-column correspondence u, not arbitrary nearest
pixels. All geometry passed to the field must be in the same parent-local frame.
Caller records source/renderer hashes and reviews the extracted painted seam.
"""

from bisect import bisect_left
from dataclasses import dataclass
import math

from PIL import Image


@dataclass(frozen=True)
class Curve:
    points: tuple[tuple[float, float], ...]

    def __post_init__(self):
        if len(self.points) < 2 or any(not math.isfinite(v) for p in self.points for v in p):
            raise ValueError("Curve needs at least two finite points")
        if any(b[0] <= a[0] for a, b in zip(self.points, self.points[1:])):
            raise ValueError("Curve columns must be strictly increasing")

    @property
    def width(self):
        return self.points[-1][0] - self.points[0][0]

    def sample(self, u):
        if not math.isfinite(u):
            raise ValueError("Nonfinite correspondence")
        x = self.points[0][0] + min(1, max(0, u)) * self.width
        index = min(len(self.points) - 1, max(1, bisect_left([p[0] for p in self.points], x)))
        a, b = self.points[index - 1 : index + 1]
        y = a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
        return x, y

    def coordinate(self, x):
        if not math.isfinite(x):
            raise ValueError("Nonfinite correspondence")
        return min(1, max(0, (x - self.points[0][0]) / self.width))


def dark_ridge(image: Image.Image, viewport, threshold=0.2):
    """Isolated painted seam, fixed alpha-weighted darkness score; not a face detector.

    Returns canvas coordinates. Reject missing contours; never substitute a
    hidden/empty image for an aligned seam. Viewport is left, top, width, height.
    """
    left, top, width, height = viewport
    if not 0 < threshold < 1 or min(width, height) <= 0:
        raise ValueError("Invalid threshold or viewport")
    image = image.convert("RGBA")
    sx, sy = image.width / width, image.height / height
    columns = []
    peak_all = 0
    for x in range(image.width):
        scores = []
        for y in range(image.height):
            r, g, b, a = image.getpixel((x, y))
            scores.append((a / 255) * (1 - (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255))
        peak = max(scores)
        peak_all = max(peak_all, peak)
        columns.append((peak, scores))
    if peak_all < 0.01:
        raise ValueError("No visible dark contour; inspect forced opacity and layer ownership")
    points = []
    for x, (peak, scores) in enumerate(columns):
        if peak < threshold * peak_all:
            continue
        # A uniformly coloured stroke has a flat maximum. Selecting its first
        # darkest pixel biases the estimated seam towards its upper edge, by an
        # amount that grows with stroke thickness. Use the whole peak plateau
        # and its antialiased neighbours. Quantized colour can introduce tiny
        # dips within one stroke. Equal maxima separated by a below-threshold
        # gap belong to different ink components and remain ambiguous.
        peak_rows = [y for y, score in enumerate(scores) if score == peak]
        first, last = peak_rows[0], peak_rows[-1]
        if any(score < threshold * peak for score in scores[first : last + 1]):
            raise ValueError("Ambiguous isolated contour: separated equally dark ridges")
        rows = range(max(0, first - 1), min(image.height, last + 2))
        total = sum(scores[i] for i in rows)
        yy = sum((i + 0.5) * scores[i] for i in rows) / total
        points.append(((x + 0.5) / sx + left, yy / sy + top))
    return Curve(tuple(points))


@dataclass(frozen=True)
class Frame:
    x_scale: float
    y_scale: float
    x_offset: float
    y_offset: float

    def local(self, point):
        return ((point[0] - self.x_offset) / self.x_scale, (point[1] - self.y_offset) / self.y_scale)

    def canvas(self, point):
        return (point[0] * self.x_scale + self.x_offset, point[1] * self.y_scale + self.y_offset)

    @classmethod
    def fit(cls, local, canvas, tolerance=0.01):
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Tolerance must be finite and positive")
        if len(local) != len(canvas) or len(local) < 3:
            raise ValueError("Need corresponding local/canvas samples")
        if any(not math.isfinite(v) for p in local + canvas for v in p):
            raise ValueError("Nonfinite frame samples")
        means = [sum(p[i] for p in local) / len(local) for i in (0, 1)]
        xx = sum((p[0] - means[0]) ** 2 for p in local)
        yy = sum((p[1] - means[1]) ** 2 for p in local)
        xy = sum((p[0] - means[0]) * (p[1] - means[1]) for p in local)
        if xx * yy <= 1e-24 or xx * yy - xy * xy <= 1e-10 * xx * yy:
            raise ValueError("Frame samples need two-dimensional rank; collinear mapping is undetermined")
        scales, offsets = [], []
        for axis in (0, 1):
            a = [p[axis] for p in local]
            b = [p[axis] for p in canvas]
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            variance = sum((v - ma) ** 2 for v in a)
            if variance < 1e-12:
                raise ValueError("Degenerate frame samples")
            scale = sum((u - ma) * (v - mb) for u, v in zip(a, b)) / variance
            if scale <= 0:
                raise ValueError("Expected positive axis-aligned neutral frame")
            scales.append(scale)
            offsets.append(mb - scale * ma)
        frame = cls(*scales, *offsets)
        if max(math.dist(frame.canvas(a), b) for a, b in zip(local, canvas)) > tolerance:
            raise ValueError("Neutral parent is not axis aligned; supply an explicit inverse mapping")
        return frame


def ribbon_curve(points, paired_vertices):
    """Extract centre of an inspected paired ribbon, excluding caps/join patches.

    Caller verifies the pinned topology and supplies its paired vertex count.
    Repeated tip-overlap columns collapse only when their closed y agrees.
    """
    if paired_vertices < 4 or paired_vertices % 2 or paired_vertices > len(points):
        raise ValueError("Invalid paired ribbon topology")
    centers = sorted(
        ((points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2)
        for i in range(0, paired_vertices, 2)
    )
    result = []
    for x, y in centers:
        if result and abs(x - result[-1][0]) < 1e-6:
            if abs(y - result[-1][1]) > 1e-4:
                raise ValueError("Ribbon is not a single closed curve at this pose")
            continue
        result.append((x, y))
    return Curve(tuple(result))


def smooth_weight(opening):
    if not 0 <= opening <= 1:
        raise ValueError("Opening outside [0,1]")
    return 1 - opening * opening * (3 - 2 * opening)


def static_displacement(reference_dynamic, form_dynamic, u):
    """C(F,u)-C0(u), preserving exact zero displacement at neutral Form."""
    a, b = reference_dynamic.sample(u), form_dynamic.sample(u)
    return b[0] - a[0], b[1] - a[1]


def dynamic_displacement(painted, reference_dynamic, current_x_bounds, point, opening):
    """Shared delta C0(u)-G(0,u), applied to every dynamic mouth component.

    C(F)=C0+G(F)-G(0), so C(F)-G(F)=C0-G(0). The current x bounds
    invert the existing horizontal deformation to recover the same source u.
    """
    left, right = current_x_bounds
    if right <= left or any(not math.isfinite(v) for v in [left, right, *point]):
        raise ValueError("Invalid current correspondence bounds")
    u = min(1, max(0, (point[0] - left) / (right - left)))
    a, b = painted.sample(u), reference_dynamic.sample(u)
    weight = smooth_weight(opening)
    return weight * (a[0] - b[0]), weight * (a[1] - b[1])


def compare_curves(static, dynamic, samples=21):
    """Same-u geometry plus support widths; inspect independently owned contours."""
    if samples < 3:
        raise ValueError("Need endpoint and interior samples")
    distances = [
        math.dist(static.sample(i / (samples - 1)), dynamic.sample(i / (samples - 1))) for i in range(samples)
    ]
    return {"max_correspondence_distance": max(distances), "width_ratio": dynamic.width / static.width}
