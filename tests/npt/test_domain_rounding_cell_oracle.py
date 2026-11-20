#!/usr/bin/env python3
"""Independent CPU arithmetic/boundary checks for the affine proof.

The fraction oracle treats each stored binary64 input as exact. This checks
selected operations and adversarial boundaries; it is not a CUDA proof.
"""
import math
import random
import unittest
from fractions import Fraction as Q

U = 2.0 ** -53
I32_MAX = 2**31 - 1


def exact(x):
    return Q.from_float(x)


def parameters(length, a, b, radius):
    # Same orthogonal, non-FMA operation tree as Box, with independent exact
    # references below. Zero matrix terms vanish exactly.
    area = math.sqrt((a * b) * (a * b))
    volume = length * (a * b)
    inv = (a * b) / volume
    thickness = volume / area
    h = radius * 0.5
    n = math.floor(thickness / h)
    return inv, thickness, 1.0 / h, n


def cell(x, inv, thickness, reciprocal_h, n):
    return math.floor(((inv * x) * thickness) * reciprocal_h) % n


class CellRoundingOracle(unittest.TestCase):
    def test_counterexample_without_cell_margin(self):
        # A plausible positive one-ULP slope error makes the 0 and 3 bins
        # contain points strictly less than two ideal widths apart. A proof
        # based solely on the exact-cell lemma would miss this pair.
        slope = math.nextafter(0.25, math.inf)
        x = math.nextafter(1.0 / slope, 0.0)
        y = 3.0 / slope
        self.assertEqual((math.floor(x * slope), math.floor(y * slope)), (0, 3))
        self.assertLess(exact(y) - exact(x), Q(8))
        self.assertLess(Q(8) - (exact(y) - exact(x)), exact(128 * U * 32))

    def test_smaller_typewise_radius_under_expansion(self):
        # The old R*-endpoint expression misses force-error consumption for
        # small typewise radii when alpha > 1, even in exact arithmetic.
        alpha, rmax, radius = Q(2), Q(10), Q(1, 100)
        force_error, build_error = Q(1, 5), Q(0)
        twice_delta, skin = Q(49, 50), Q(1)
        old = twice_delta + max(Q(0), (rmax + force_error) / alpha - rmax) + build_error
        required = twice_delta + (radius + force_error) / alpha - radius + build_error
        conservative = (twice_delta + max(Q(0), 1 / alpha - 1) * rmax
                        + force_error / min(alpha, 1) + build_error)
        self.assertLess(old, skin)
        self.assertGreater(required, skin)
        self.assertGreaterEqual(conservative, required)

    def test_int_product_guard_is_not_enough(self):
        nx, ny, nz = 2000, 2000, 536
        count = nx * ny * nz
        self.assertLessEqual(count, I32_MAX)
        self.assertGreater(count - 1 + 2 * nx * ny + 2 * nx + 2, I32_MAX)
        self.assertGreater(count, I32_MAX // 4)

    def test_actual_operation_tree_and_cell_closure(self):
        rng = random.Random(20261106)
        for _ in range(400):
            length = 10 ** rng.uniform(1, 6)
            a, b = (10 ** rng.uniform(-3, 6) for _ in range(2))
            k = rng.randrange(5, 150)
            h = length / k
            h = rng.choice((h, math.nextafter(h, 0), math.nextafter(h, math.inf)))
            radius = h * 2
            inv, thickness, ri, n = parameters(length, a, b, radius)
            self.assertGreaterEqual(n, 4)
            # Full tree errors in physical units. These are looser than the
            # proof's per-operation gamma bounds, not an empirical fit.
            tol = exact(64 * U * length)
            self.assertLessEqual(abs(exact(thickness) - exact(length)), tol)
            self.assertLessEqual(abs(exact(inv) * exact(length) - 1), exact(16 * U))
            candidates = {0.0, length, math.nextafter(length, 0)}
            for j in (1, 2, 3, n // 2, n - 1, n):
                boundary = j * h
                if boundary <= length:
                    candidates.update((boundary, math.nextafter(boundary, 0),
                                       min(length, math.nextafter(boundary, math.inf))))
            for x in candidates:
                z = ((inv * x) * thickness) * ri
                self.assertLessEqual(abs((exact(z) - exact(x) / exact(h)) * exact(h)), tol)
                for y in candidates:
                    dist = min(abs(exact(x) - exact(y)),
                               exact(length) - abs(exact(x) - exact(y)))
                    if dist < exact(radius) - 2 * tol:
                        i, j = cell(x, inv, thickness, ri, n), cell(y, inv, thickness, ri, n)
                        self.assertLessEqual(min((i - j) % n, (j - i) % n), 2)

    def test_slab_rounding_error_near_boundaries(self):
        rng = random.Random(23)
        for _ in range(2000):
            length = 10 ** rng.uniform(-3, 6)
            p = rng.randrange(2, 10000)
            rank = rng.randrange(p)
            lo = (float(rank) / p) * length
            hi = lo + length / p
            ideal_lo = Q(rank, p) * exact(length)
            ideal_hi = Q(rank + 1, p) * exact(length)
            tolerance = exact(8 * U * length)
            self.assertLessEqual(abs(exact(lo) - ideal_lo), tolerance)
            self.assertLessEqual(abs(exact(hi) - ideal_hi), tolerance)


if __name__ == '__main__':
    unittest.main()
