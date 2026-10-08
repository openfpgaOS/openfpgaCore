#!/usr/bin/env python3
"""Exact integer producer for the private 0x61 prepared triangle contract.

No floating-point geometry and no borrowed RTL result is used here. Inputs are
the projected attributes at the original GPU triangle derivation entrance.
The optional normalization matches indexed/xform commands, including its limit
of 16 shifts. Commands carrying explicit Q29 planes are intentionally excluded.
"""
from dataclasses import dataclass


def signed(value, width=32):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def sat32(value):
    return max(-(1 << 31), min((1 << 31) - 1, value))


def word(value):
    return value & 0xffffffff


@dataclass(frozen=True)
class Vertex:
    x: int
    y: int
    s: int
    t: int
    zi: int
    depth: int
    rgb: int
    additive: int = 0


def order(vertices):
    """Stable three strict swaps; equal-Y order is part of the contract."""
    result = list(vertices)
    for a, b in ((0, 1), (1, 2), (0, 1)):
        if result[b].y < result[a].y:
            result[a], result[b] = result[b], result[a]
    return result


def derive_planes(vertices, *, subpixel=True, normalize=False, combine=False):
    vertices = order(vertices)
    perspective = [[signed((v.s * v.zi) >> 16),
                    signed((v.t * v.zi) >> 16), v.zi] for v in vertices]
    if normalize:
        for _ in range(16):
            if any(not -(1 << 28) <= x < (1 << 28)
                   for attributes in perspective for x in attributes):
                break
            perspective = [[signed(x << 1) for x in attributes]
                           for attributes in perspective]
    attributes = []
    for v, p in zip(vertices, perspective):
        attributes.append(p + [((v.rgb >> 5) & 63) << 16,
                               ((v.rgb >> 11) & 31) << 16,
                               (v.rgb & 31) << 16, v.depth])
        if combine:
            attributes[-1] += [((v.additive >> 11) & 31) << 16,
                               ((v.additive >> 5) & 63) << 16,
                               (v.additive & 31) << 16]
    a, b, c = vertices
    dx1, dx2 = b.x - a.x, c.x - a.x
    dy1, dy2 = b.y - a.y, c.y - a.y
    determinant = dx1 * dy2 - dx2 * dy1
    magnitude = max(1, abs(determinant))
    scale = 31 if subpixel and magnitude <= 8192 else 44
    reciprocal = min(((1 << scale) + (magnitude >> 1)) // magnitude,
                     (1 << 31) - 1)
    sign = -1 if determinant < 0 else 1
    result = []
    for index, (a0, a1, a2) in enumerate(zip(*attributes)):
        da1, da2 = sat32(a1 - a0), sat32(a2 - a0)
        nx = (da1 * dy2 - da2 * dy1) << 4
        ny = (da2 * dx1 - da1 * dx2) << (4 if subpixel else 0)
        du = sat32(((nx * reciprocal) >> scale) * sign)
        dv = sat32(((ny * reciprocal) >> scale) * sign)
        origin = signed(a0 - ((du * a.x) >> 4)
                        - ((dv * a.y) >> (4 if subpixel else 0))
                        + (du >> 1) + (dv >> 1))
        width = 32 if index in (0, 1, 2, 6) else 24
        result.extend(word(signed(x, width)) for x in (origin, du, dv))
    return result


def prestep_shared32(slope, distance, subpixel):
    """A signed33×signed17 prestep expressed using the engine's 32×32 DSP.

    The correction is essential: a positive slope beyond INT32_MAX otherwise
    changes sign. The upper slope bit can be discarded only AFTER the shift.
    This function gives the exact low32 bits needed by the DDA seed.
    """
    low = signed(slope)
    correction = (slope - low) // (1 << 32)
    shift = 4 if subpixel else 0
    return signed(((low * distance) >> shift)
                  + correction * (distance << (32 - shift)))


def prepare_edges(vertices, clip, *, subpixel=True):
    a, b, c = order(vertices)
    cx0, cx1, cy0, cy1 = clip
    scan = lambda y: (y + 7) >> 4 if subpixel else y
    start, middle, end = max(scan(a.y), cy0), scan(b.y), min(scan(c.y), cy1)
    if start >= end:
        return None
    long_left = (b.x - a.x) * (c.y - a.y) > (c.x - a.x) * (b.y - a.y)
    slopes = []
    for u, v in ((a, c), (a, b), (b, c)):
        dx, dy = v.x - u.x, v.y - u.y
        # Integer-Y RTL leaves four zero bits in its 28-beat divider; the
        # quotient still equals (abs(dx)<<12)/dy, not floor(Q16)/16.
        slope = 0 if dy == 0 else (abs(dx) << (16 if subpixel else 12)) // dy
        slopes.append(-slope if dx < 0 else slope)
    bottom = start >= middle
    distance = lambda line, vertex: signed((line << 4) + 8 - vertex, 17) if subpixel else signed(line - vertex, 17)
    xlong = (a.x << 12) + prestep_shared32(slopes[0], distance(start, a.y), subpixel)
    short_vertex = b if bottom else a
    xshort = (short_vertex.x << 12) + prestep_shared32(slopes[2 if bottom else 1], distance(start, short_vertex.y), subpixel)
    bottom_offset = prestep_shared32(slopes[2], distance(middle, b.y), True) if subpixel and not bottom else 0
    xl, xr = (xlong, xshort) if long_left else (xshort, xlong)
    return [word(xl), word(xr), *map(word, slopes), word((b.x << 12) + bottom_offset),
            ((middle & 65535) << 16) | (start & 65535),
            (int(subpixel) << 18) | (int(long_left) << 17) | (int(bottom) << 16) | (end & 65535),
            ((cx1 & 65535) << 16) | (cx0 & 65535)]


def prepared_packet(vertices, clip, *, subpixel=True, normalize=False, combine=False):
    edge_words = prepare_edges(vertices, clip, subpixel=subpixel)
    if edge_words is None:
        return []
    planes = derive_planes(vertices, subpixel=subpixel, normalize=normalize, combine=combine)
    return [0x61000000 | (len(edge_words) + len(planes)), *edge_words, *planes]


def decode_inputs(words):
    assert len(words) == 24
    flags, clipx, clipy = words[:3]
    vertices = []
    for start in range(3, 24, 7):
        xy, s, t, zi, depth, rgb, additive = words[start:start + 7]
        vertices.append(Vertex(signed(xy, 16), signed(xy >> 16, 16),
                               signed(s), signed(t), signed(zi), signed(depth), rgb, additive))
    clip = (signed(clipx, 16), signed(clipx >> 16, 16),
            signed(clipy, 16), signed(clipy >> 16, 16))
    options = dict(subpixel=bool(flags & 1), normalize=bool(flags & 2), combine=bool(flags & 4))
    return vertices, clip, options
