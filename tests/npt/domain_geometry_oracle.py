#!/usr/bin/env python3
"""Independent, deliberately exhaustive ideal-geometry oracle; not runtime code.

Enumerates periodic images explicitly rather than using a minimum-image routine.
Coordinates are lifted reference coordinates; this does not bound GPU rounding.
"""
import itertools
import math


def edges(points, box, radii, padding=0.0):
    if any(not math.isfinite(x) or x <= 0 for x in box):
        raise ValueError('invalid box')
    result = set()
    # A finite image range derived from the maximum radius, not an MIC assumption.
    extent = max(radii.values(), default=0.0) + padding
    for i, (ti, pi) in enumerate(points):
        for j, (tj, pj) in enumerate(points):
            if i == j:
                continue
            cutoff = radii[ti, tj] + padding
            ranges = [range(math.floor((pi[d] - pj[d] - extent) / box[d]) - 1,
                            math.ceil((pi[d] - pj[d] + extent) / box[d]) + 2)
                      for d in range(3)]
            for image in itertools.product(*ranges):
                delta = [pj[d] - pi[d] + image[d] * box[d] for d in range(3)]
                if math.sqrt(sum(x*x for x in delta)) <= cutoff:
                    result.add((i, j, image))
    return result


def paths(force_edges, dependency_edges):
    """Enumerate directed two-hop paths with accumulated periodic image."""
    return {(i, j, k, tuple(a[d] + b[d] for d in range(3)))
            for i, j, a in force_edges for jj, k, b in dependency_edges if j == jj}


def reusable(scale, displacement, rstar, skin):
    """Ideal real-arithmetic criterion; fail closed for invalid inputs."""
    values = (scale, displacement, rstar, skin)
    if not all(math.isfinite(x) for x in values) or scale <= 0 or min(values[1:]) < 0:
        return False
    budget = skin - max(0.0, 1.0 / scale - 1.0) * rstar
    return budget >= 0 and 2 * displacement <= budget


def assert_edge_closure(ref_f, ref_d, cur_f, cur_d):
    """Compare actual cached edge sets with an independently enumerated snapshot."""
    if not cur_f <= ref_f or not cur_d <= ref_d:
        raise AssertionError('missing reference edge')
    if not paths(cur_f, cur_d) <= paths(ref_f, ref_d):
        raise AssertionError('missing reference two-hop path')


def assert_closure(reference, current, box0, scale, force, dependency, skin):
    """Raise on any missing reference edge or directed two-hop path."""
    ref_f = edges(reference, box0, force, skin)
    ref_d = edges(reference, box0, dependency, skin)
    current_box = tuple(scale * x for x in box0)
    cur_f = edges(current, current_box, force)
    cur_d = edges(current, current_box, dependency)
    assert_edge_closure(ref_f, ref_d, cur_f, cur_d)
