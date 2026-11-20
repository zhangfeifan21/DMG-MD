#!/usr/bin/env python3
import math
import random
import struct
import unittest
from domain_geometry_oracle import assert_closure, assert_edge_closure, edges, paths, reusable


class GeometryOracle(unittest.TestCase):
    def test_two_hop_typewise_pbc(self):
        # i→j crosses the periodic face; j→k is a different typewise edge.
        q = [(0, (9.7, 0, 0)), (1, (0.2, 0, 0)), (2, (1.0, 0, 0))]
        f = {(i, j): (0.6 if (i, j) == (0, 1) else 0.1) for i in range(3) for j in range(3)}
        d = {(i, j): (0.9 if (i, j) == (1, 2) else 0.1) for i in range(3) for j in range(3)}
        ef, ed = edges(q, (10, 10, 10), f), edges(q, (10, 10, 10), d)
        self.assertIn((0, 1, 2, (1, 0, 0)), paths(ef, ed))
        self.assertNotIn((0, 1, 2, (1, 0, 0)), paths(ef, set()))
        assert_closure(q, q, (10, 10, 10), 1, f, d, 0.2)

        # Remove only k's dependency edge from the reference cache. The direct
        # i→j force edge remains, so a one-hop-only oracle would accept it.
        ref_f = edges(q, (10, 10, 10), f, 0.2)
        ref_d = edges(q, (10, 10, 10), d, 0.2)
        missing_dependency = (1, 2, (0, 0, 0))
        self.assertIn(missing_dependency, ref_d)
        damaged = ref_d - {missing_dependency}
        self.assertLessEqual(ef, ref_f)
        self.assertNotIn((0, 1, 2, (1, 0, 0)), paths(ref_f, damaged))
        with self.assertRaisesRegex(AssertionError, 'missing reference edge'):
            assert_edge_closure(ref_f, damaged, ef, ed)

    def test_compression_expansion_and_drift(self):
        q = [(0, (1.0, 1, 1)), (0, (2.3, 1, 1)), (0, (3.6, 1, 1))]
        radii = {(0, 0): 1.0}
        for scale, drift in [(0.9, 0.02), (1.1, 0.02), (1.0, 0.0)]:
            self.assertTrue(reusable(scale, drift, 1, .3))
            current = [(t, tuple(scale * (x + (drift if i == 0 and j == 0 else 0))
                                   for j, x in enumerate(p))) for i, (t, p) in enumerate(q)]
            assert_closure(q, current, (12, 12, 12), scale, radii, radii, .3)
        self.assertFalse(reusable(.5, 0, 1, .3))  # negative budget, even though its square is positive
        self.assertFalse(reusable(float('nan'), 0, 1, .3))

    def test_seeded_two_hop_closure_under_deformation(self):
        rng = random.Random(731)
        box = (12.0, 12.0, 12.0)
        radii = {(0, 0): 1.2, (0, 1): 1.8, (1, 0): 1.6, (1, 1): 1.4}
        for scale in (0.92, 0.97, 1.0, 1.08):
            for _ in range(15):
                q = [(rng.randrange(2), tuple(rng.uniform(0, 12) for _ in range(3)))
                     for _ in range(5)]
                drift = [tuple(rng.uniform(-0.015, 0.015) for _ in range(3)) for _ in q]
                delta = max(math.sqrt(sum(x*x for x in u)) for u in drift)
                self.assertTrue(reusable(scale, delta, 1.8, .3))
                x = [(t, tuple(scale * (p[d] + u[d]) for d in range(3)))
                     for (t, p), u in zip(q, drift)]
                assert_closure(q, x, box, scale, radii, radii, .3)

    def test_sequential_box_cycles_and_small_compressions(self):
        # Update the actual binary64 box and lifted positions at every epoch.
        # Rebuild whenever the existing cache's geometric budget expires;
        # check every epoch, including when an atom crosses a periodic face.
        radii = {(0, 0): 1.05, (0, 1): 1.2, (1, 0): 1.1, (1, 1): 1.0}
        box = [10.0, 10.0, 10.0]
        points = [(0, [9.98, 1., 1.]), (1, [0.25, 1., 1.]),
                  (0, [1.1, 1., 1.]), (1, [2.1, 1., 1.])]
        ref = [(t, tuple(p)) for t, p in points]; ref_box = tuple(box)
        rebuilt = 0; crossed = False; active_paths = 0
        factors = ([.9998] * 150 + [1.0002] * 150) * 3 + [.9999] * 1000
        for factor in factors:
            box = [factor * v for v in box]
            points = [(t, [factor * p[d] + (0.0002 if i == 0 and d == 0 else
                                             -0.000025 if i == 1 and d == 1 else 0.)
                           for d in range(3)]) for i, (t, p) in enumerate(points)]
            current = [(t, tuple(p)) for t, p in points]
            crossed |= points[0][1][0] >= box[0]
            scale = box[0] / ref_box[0]
            displacement = max(math.dist(tuple(x / scale for x in p), ref[i][1])
                               for i, (_, p) in enumerate(points))
            if not reusable(scale, displacement, 1.2, .3):
                ref, ref_box = current, tuple(box); scale = 1.; rebuilt += 1
            assert_closure(ref, current, ref_box, scale, radii, radii, .3)
            if paths(edges(current, box, radii), edges(current, box, radii)):
                active_paths += 1
        self.assertTrue(crossed)
        self.assertGreater(rebuilt, 0)
        self.assertEqual(active_paths, len(factors))

    def test_wrapping_and_latest_factor_do_not_prove_cache_safety(self):
        radii = {(0, 0): 1.0}; box = (10., 10., 10.)
        ref = [(0, (1., 1., 1.)), (0, (2., 1., 1.)), (0, (2.8, 1., 1.))]
        # This deliberately violates the runtime's multi-box-step guard. MIC
        # hides the displacement, while explicit lifted image labels reveal it.
        displaced = [(0, (21., 1., 1.)), *ref[1:]]
        wrapped_delta = (displaced[0][1][0] - ref[0][1][0]) % box[0]
        self.assertTrue(reusable(1., wrapped_delta, 1., .2))
        self.assertFalse(reusable(1., 20., 1., .2))
        with self.assertRaisesRegex(AssertionError, 'reference edge'):
            assert_closure(ref, displaced, box, 1., radii, radii, .2)
        # A single tiny compression is safe, but the accumulated compression
        # brings a previously uncached force edge and two-hop path into range.
        ref = [(0, (1., 1., 1.)), (0, (2.15, 1., 1.)), (0, (3.0, 1., 1.))]
        current = [(t, list(p)) for t, p in ref]; current_box = list(box)
        factor = .9998
        for _ in range(1000):
            current_box = [v * factor for v in current_box]
            current = [(t, [v * factor for v in p]) for t, p in current]
        scale = current_box[0] / box[0]
        self.assertTrue(reusable(factor, 0., 1., .1))
        self.assertFalse(reusable(scale, 0., 1., .1))
        with self.assertRaisesRegex(AssertionError, 'reference edge'):
            assert_closure(ref, [(t, tuple(p)) for t, p in current], box, scale, radii, radii, .1)

    def test_cutoff_ulp(self):
        radius = 1.0
        r = {(0, 0): radius}
        for distance, expected in [(math.nextafter(radius, 0), True), (radius, True),
                                   (math.nextafter(radius, math.inf), False)]:
            p = [(0, (0, 0, 0)), (0, (distance, 0, 0))]
            self.assertEqual(any(i == 0 and j == 1 for i, j, _ in edges(p, (10, 10, 10), r)), expected)

    def test_float_mic_axis_error_envelope(self):
        # Emulate RN binary32 at each float operation, independently of CUDA.
        f32 = lambda x: struct.unpack('f', struct.pack('f', x))[0]
        rng = random.Random(173)
        unit = 2.0**-24
        for _ in range(2000):
            length = 10**rng.uniform(-3, 6)
            a, b = rng.uniform(0, length), rng.uniform(0, length)
            delta = b-a
            # Exclude the image-switch ambiguity, as the runtime must.
            if abs(abs(delta) - length/2) < 16*unit*length:
                continue
            wrapped = delta-length if delta > length/2 else delta+length if delta < -length/2 else delta
            flen, fd = f32(length), f32(delta)
            half = f32(flen * .5)
            actual = f32(fd-flen) if fd > half else f32(fd+flen) if fd < -half else fd
            self.assertLessEqual(abs(actual-wrapped), 8*unit*length)

    def test_periodic_cell_folding_near_boundaries(self):
        # Independent 1-D cell enumeration for the ±2 scan, including a
        # partial final bin folded into cell zero and one-ULP bin boundaries.
        h = 4.0
        for length in (math.nextafter(32.0, 0), 32.0,
                       math.nextafter(32.0, math.inf), 35.0):
            n = math.floor(length / h)
            candidates = {0.0, math.nextafter(length, 0.0)}
            for k in range(1, n + 1):
                x = k*h
                if x < length:
                    candidates.update((math.nextafter(x, 0), x, math.nextafter(x, math.inf)))
            for x in candidates:
                for y in candidates:
                    distance = min(abs(x-y), length-abs(x-y))
                    if distance < 2*h - 1e-10:
                        a, b = math.floor(x/h) % n, math.floor(y/h) % n
                        self.assertLessEqual(min((a-b) % n, (b-a) % n), 2,
                                             (length, x, y, a, b))

    def test_missing_zbl_or_nep5_radius_is_detected(self):
        q = [(0, (0, 0, 0)), (1, (1.5, 0, 0))]
        short = {(i, j): 1.0 for i in range(2) for j in range(2)}
        actual = dict(short, **{})
        actual[0, 1] = 1.6  # abstract longer ZBL/NEP5 consumption edge
        self.assertFalse(edges(q, (10, 10, 10), actual) <= edges(q, (10, 10, 10), short, .1))


if __name__ == '__main__':
    unittest.main()
