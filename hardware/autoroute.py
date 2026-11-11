"""Route everything gen_board.py left unrouted, then add ground stitching vias and fill the
pours. Edits fox.kicad_pcb in place; rerun gen_board.py first to start over.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" autoroute.py

A two-layer maze router (A* on a 0.1 mm grid, 45-degree moves, vias between layers).
Power nets go first with wider traces. The bottom layer stays solid ground under the RF
section, and nothing enters the ESP32 antenna keepout. The result passes KiCad's DRC but
is plainly machine-routed; tidy it in the PCB editor where it matters.
"""
import heapq
import math
import os
import sys

import numpy as np
import pcbnew

import silk

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "fox.kicad_pcb")

ORIGIN = (50.0, 50.0)          # board corner on the KiCad page (matches gen_board.py)
G = 0.1                        # grid pitch, mm
CLEAR = 0.15                   # copper clearance (the board's Default netclass)
EDGE = 0.3                     # copper to board edge
HOLE_CLEAR = 0.25              # copper to a non-plated hole
# Stamped copper covers cell centres strictly inside it, so its true edge can lie up to one
# cell beyond; keepout radii add one cell to stay clear after rounding.
VIA_D, VIA_DRILL = 0.6, 0.3
SIGNAL_W = 0.2
POWER_W = {"/VBUS": 0.6, "/BAT_P": 0.6, "/BAT_N": 0.6, "/Q_DRAIN": 0.6, "/VSYS": 0.6,
           "/+3V3": 0.3}             # 0.3 fits the radio's 0.65 mm-pitch pads
SKIP = {"/GND"}                # poured on both layers
# Keep the bottom layer an unbroken ground plane under the radio and matching network, and
# under the USB-C escape (signals squeezed through there cut off R2's ground).
NO_BOTTOM = [(0.0, 0.0, 49.0, 12.0), (6.0, 26.6, 14.5, 30.0)]
VIA_COST = 60                  # in grid steps
BOTTOM_COST = 1.3              # prefer the top layer
MAX_EXPANSIONS = 3_000_000
STITCH_PITCH = 3.0             # ground stitching via grid, mm

FREE, BLOCKED = -1, -2


class Grid:
    def __init__(self, board):
        box = board.GetBoardEdgesBoundingBox()
        self.w_mm = pcbnew.ToMM(box.GetWidth())
        self.h_mm = pcbnew.ToMM(box.GetHeight())
        self.nx = int(round(self.w_mm / G)) + 1
        self.ny = int(round(self.h_mm / G)) + 1
        self.owner = np.full((2, self.ny, self.nx), FREE, dtype=np.int32)
        self.hop = np.zeros((self.ny, self.nx), dtype=bool)    # vias and plated holes
        ys, xs = np.mgrid[0:self.ny, 0:self.nx]
        self.xs = xs * G
        self.ys = ys * G

    def window(self, x0, y0, x1, y1):
        i0 = max(int(math.floor(x0 / G)), 0)
        i1 = min(int(math.ceil(x1 / G)) + 1, self.nx)
        j0 = max(int(math.floor(y0 / G)), 0)
        j1 = min(int(math.ceil(y1 / G)) + 1, self.ny)
        return slice(j0, j1), slice(i0, i1)

    def stamp(self, layer, mask_fn, x0, y0, x1, y1, value, overwrite=True, hop=False):
        sl = self.window(x0, y0, x1, y1)
        m = mask_fn(self.xs[sl], self.ys[sl])
        if hop:
            self.hop[sl] |= m
        layers = (0, 1) if layer is None else (layer,)
        for lay in layers:
            view = self.owner[lay][sl]
            if overwrite:
                view[m] = value
            else:
                view[m & (view == FREE)] = value

    def rect(self, layer, x0, y0, x1, y1, value, hop=False):
        self.stamp(layer, lambda X, Y: (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1),
                   x0, y0, x1, y1, value, hop=hop)

    def disk(self, layer, cx, cy, r, value, hop=False):
        self.stamp(layer, lambda X, Y: (X - cx) ** 2 + (Y - cy) ** 2 < r * r - 1e-9,
                   cx - r, cy - r, cx + r, cy + r, value, hop=hop)

    def capsule(self, layer, ax, ay, bx, by, r, value):
        def mask(X, Y):
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            if L2 == 0:
                t = 0
            else:
                t = np.clip(((X - ax) * dx + (Y - ay) * dy) / L2, 0, 1)
            return (X - ax - t * dx) ** 2 + (Y - ay - t * dy) ** 2 < r * r - 1e-9
        self.stamp(layer, mask, min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r,
                   max(ay, by) + r, value)


def to_mm(v):
    return pcbnew.ToMM(v.x) - ORIGIN[0], pcbnew.ToMM(v.y) - ORIGIN[1]


def copper_layers(item):
    return [i for i, lay in enumerate((pcbnew.F_Cu, pcbnew.B_Cu)) if item.IsOnLayer(lay)]


def rasterize(board, grid, nets):
    """Stamp every existing copper item, hole and keepout into the grid."""
    # board edge band
    e = EDGE
    for lay in (0, 1):
        o = grid.owner[lay]
        n = int(math.ceil(e / G))
        o[:n, :] = BLOCKED
        o[-n:, :] = BLOCKED
        o[:, :n] = BLOCKED
        o[:, -n:] = BLOCKED
    for fp in board.GetFootprints():
        for z in fp.Zones():
            if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
                b = z.GetBoundingBox()
                x0, y0 = to_mm(b.GetOrigin())
                x1, y1 = to_mm(b.GetEnd())
                grid.rect(None, x0, y0, x1, y1, BLOCKED)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            cx, cy = to_mm(pad.GetPosition())
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                # holes need 0.25 mm, more than copper clearance
                r = pcbnew.ToMM(pad.GetDrillSize().x) / 2 + HOLE_CLEAR - CLEAR
                grid.disk(None, cx, cy, r, BLOCKED)
                continue
            code = pad.GetNetCode() if pad.GetNetCode() > 0 else BLOCKED
            plated = pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            for lay in copper_layers(pad):
                if pad.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
                    grid.disk(lay, cx, cy, pcbnew.ToMM(pad.GetSize().x) / 2, code, hop=plated)
                else:
                    b = pad.GetBoundingBox()
                    x0, y0 = to_mm(b.GetOrigin())
                    x1, y1 = to_mm(b.GetEnd())
                    grid.rect(lay, x0, y0, x1, y1, code, hop=plated)
    for t in board.GetTracks():
        code = t.GetNetCode()
        if t.Type() == pcbnew.PCB_VIA_T:
            cx, cy = to_mm(t.GetPosition())
            grid.disk(None, cx, cy, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2, code, hop=True)
        else:
            ax, ay = to_mm(t.GetStart())
            bx, by = to_mm(t.GetEnd())
            lay = 0 if t.GetLayer() == pcbnew.F_Cu else 1
            grid.capsule(lay, ax, ay, bx, by, pcbnew.ToMM(t.GetWidth()) / 2, code)


def disk_offsets(r_cells):
    r = int(math.ceil(r_cells))
    return [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1)
            if dx * dx + dy * dy <= r_cells * r_cells]


def dilate(mask, r_cells):
    out = mask.copy()
    ny, nx = mask.shape
    for dy, dx in disk_offsets(r_cells):
        if dy == 0 and dx == 0:
            continue
        ys0, ys1 = max(dy, 0), ny + min(dy, 0)
        xs0, xs1 = max(dx, 0), nx + min(dx, 0)
        out[ys0:ys1, xs0:xs1] |= mask[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return out


def components(grid, code):
    """Connected copper of one net: list of sets of flat indices (layer-joined at vias/TH)."""
    nyx = grid.ny * grid.nx
    cells = set()
    for lay in (0, 1):
        idx = np.flatnonzero(grid.owner[lay] == code)
        cells.update((idx + lay * nyx).tolist())
    comps, seen = [], set()
    for start in cells:
        if start in seen:
            continue
        stack, comp = [start], set()
        seen.add(start)
        while stack:
            c = stack.pop()
            comp.add(c)
            lay, rem = divmod(c, nyx)
            y, x = divmod(rem, grid.nx)
            nbrs = [c + 1 if x + 1 < grid.nx else None, c - 1 if x > 0 else None,
                    c + grid.nx if y + 1 < grid.ny else None, c - grid.nx if y > 0 else None,
                    (c + nyx if lay == 0 else c - nyx) if grid.hop.flat[rem] else None]
            for n in nbrs:
                if n is not None and n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        comps.append(comp)
    return comps


def route_net(grid, code, width, joined=None):
    """Connect every copper island of this net. Returns (paths, failures). Islands for
    which joined(island) is true count as connected already."""
    nyx = grid.ny * grid.nx
    other = [(grid.owner[lay] != FREE) & (grid.owner[lay] != code) for lay in (0, 1)]
    r_track = (CLEAR + width / 2) / G + 1
    r_via = (CLEAR + VIA_D / 2) / G + 1
    blocked = np.stack([dilate(other[0], r_track), dilate(other[1], r_track)])
    via_ok = ~(dilate(other[0], r_via) | dilate(other[1], r_via))
    for x0, y0, x1, y1 in NO_BOTTOM:
        sl = grid.window(x0, y0, x1, y1)
        blocked[1][sl] = True
        via_ok[sl] = False
    blocked_f = blocked.reshape(-1)
    via_ok_f = via_ok.reshape(-1)

    comps = components(grid, code)
    if len(comps) <= 1:
        return [], 0
    comps.sort(key=len, reverse=True)
    tree = set(comps[0])
    remaining = comps[1:]
    if joined:
        for c in [c for c in remaining if joined(c)]:
            tree |= c
            remaining.remove(c)
    paths, failures = [], 0
    nx = grid.nx
    while remaining:
        target = np.zeros(2 * nyx, dtype=bool)
        centres = []
        for comp in remaining:
            arr = np.fromiter(comp, dtype=np.int64)
            target[arr] = True
            rem = arr % nyx
            centres.append((float(np.mean(rem % nx)), float(np.mean(rem // nx))))
        path = astar(grid, tree, target, centres, blocked_f, via_ok_f)
        if path is None:
            failures += len(remaining)
            break
        end = path[-1]
        hit = next(c for c in remaining if end in c)
        remaining.remove(hit)
        tree |= hit
        tree.update(path)
        paths.append(path)
        stamp_path(grid, path, code, width)
    return paths, failures


def astar(grid, sources, target, centres, blocked, via_ok):
    nyx = grid.ny * grid.nx
    nx = grid.nx
    g = np.full(2 * nyx, np.inf, dtype=np.float32)
    came = np.full(2 * nyx, -1, dtype=np.int64)

    def h(idx):
        rem = idx % nyx
        x, y = rem % nx, rem // nx
        best = math.inf
        for cx, cy in centres:
            dx, dy = abs(x - cx), abs(y - cy)
            best = min(best, (dx + dy) + (1.414 - 2) * min(dx, dy))
        return best

    heap = []
    for s in sources:
        g[s] = 0
        heapq.heappush(heap, (h(s), s))
    steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))
    expansions = 0
    while heap:
        f, c = heapq.heappop(heap)
        gc = g[c]
        if f - h(c) > gc + 1e-3:
            continue
        if target[c] and gc > 0:
            path = [c]
            while came[path[-1]] >= 0:
                path.append(int(came[path[-1]]))
            return path[::-1]
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            return None
        lay, rem = divmod(c, nyx)
        y, x = divmod(rem, nx)
        mult = BOTTOM_COST if lay == 1 else 1.0
        for dx, dy, cost in steps:
            xx, yy = x + dx, y + dy
            if not (0 <= xx < nx and 0 <= yy < grid.ny):
                continue
            n = lay * nyx + yy * nx + xx
            if blocked[n] and not target[n]:
                continue
            if dx and dy:
                # no corner cutting past blocked cells
                if blocked[lay * nyx + y * nx + xx] or blocked[lay * nyx + yy * nx + x]:
                    continue
            ng = gc + cost * mult
            if ng < g[n]:
                g[n] = ng
                came[n] = c
                heapq.heappush(heap, (ng + h(n), n))
        if via_ok[rem]:
            n = (1 - lay) * nyx + rem
            if not blocked[n] or target[n]:
                ng = gc + VIA_COST
                if ng < g[n]:
                    g[n] = ng
                    came[n] = c
                    heapq.heappush(heap, (ng + h(n), n))
    return None


def path_geometry(grid, path):
    """Turn a cell path into (segments per layer, via positions), all in mm."""
    nyx = grid.ny * grid.nx
    pts = []
    for c in path:
        lay, rem = divmod(c, nyx)
        y, x = divmod(rem, grid.nx)
        pts.append((lay, x * G, y * G))
    segments, vias = [], []
    run = [pts[0]]
    for p in pts[1:]:
        if p[0] != run[-1][0]:
            vias.append((p[1], p[2]))
            segments.append(run)
            run = [p]
        else:
            run.append(p)
    segments.append(run)
    out = []
    for run in segments:
        if len(run) < 2:
            continue
        verts = [run[0]]
        for a, b, c in zip(run, run[1:], run[2:]):
            if (b[1] - a[1], b[2] - a[2]) != (c[1] - b[1], c[2] - b[2]):
                verts.append(b)
        verts.append(run[-1])
        out.append((run[0][0], [(v[1], v[2]) for v in verts]))
    return out, vias


def stamp_path(grid, path, code, width):
    segs, vias = path_geometry(grid, path)
    for lay, verts in segs:
        for (ax, ay), (bx, by) in zip(verts, verts[1:]):
            grid.capsule(lay, ax, ay, bx, by, width / 2, code)
    for x, y in vias:
        grid.disk(None, x, y, VIA_D / 2, code, hop=True)


def add_to_board(board, grid, net, path, width):
    segs, vias = path_geometry(grid, path)
    for lay, verts in segs:
        for (ax, ay), (bx, by) in zip(verts, verts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I_MM(ORIGIN[0] + ax, ORIGIN[1] + ay))
            t.SetEnd(pcbnew.VECTOR2I_MM(ORIGIN[0] + bx, ORIGIN[1] + by))
            t.SetWidth(pcbnew.FromMM(width))
            t.SetLayer(pcbnew.F_Cu if lay == 0 else pcbnew.B_Cu)
            t.SetNet(net)
            board.Add(t)
    for x, y in vias:
        add_via(board, net, x, y)


def add_via(board, net, x, y):
    pos = pcbnew.VECTOR2I_MM(ORIGIN[0] + x, ORIGIN[1] + y)
    if any(t.Type() == pcbnew.PCB_VIA_T and t.GetPosition() == pos for t in board.GetTracks()):
        return
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I_MM(ORIGIN[0] + x, ORIGIN[1] + y))
    v.SetWidth(pcbnew.FromMM(VIA_D))
    v.SetDrill(pcbnew.FromMM(VIA_DRILL))
    v.SetNet(net)
    board.Add(v)


def ground_via_ok(grid, gnd, margin=1.0):
    other = [(grid.owner[lay] != FREE) & (grid.owner[lay] != gnd.GetNetCode()) for lay in (0, 1)]
    r = (0.25 + VIA_D / 2) / G + margin             # pour clearance is 0.25
    return ~(dilate(other[0], r) | dilate(other[1], r))


def ground_groups(board, code):
    """Split everything on the ground net into connected groups. Pour pieces join through
    vias, plated holes, pads and traces, the way KiCad's own connectivity check sees it.
    Returns (pieces, groups): pieces are (layer index, outline); each group is a dict with
    the pieces it holds and its contact points as (position, layers)."""
    pieces = []
    for zone in board.Zones():
        if zone.GetNetCode() != code:
            continue
        lay = 0 if zone.GetFirstLayer() == pcbnew.F_Cu else 1
        polys = zone.GetFilledPolysList(zone.GetFirstLayer())
        pieces += [(lay, polys.Outline(k)) for k in range(polys.OutlineCount())]
    items = []                          # (contact points [(pos, layers)], kind)
    for t in board.GetTracks():
        if t.GetNetCode() != code:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            items.append(([(t.GetPosition(), (0, 1))], "via"))
        else:
            lay = (0 if t.GetLayer() == pcbnew.F_Cu else 1,)
            items.append(([(t.GetStart(), lay), (t.GetEnd(), lay)], "track"))
    pads = [p for p in board.GetPads() if p.GetNetCode() == code]
    for p in pads:
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH,):
            items.append(([(p.GetPosition(), (0, 1))], "pth"))
        else:
            lay = (0 if p.IsOnLayer(pcbnew.F_Cu) else 1,)
            items.append(([(p.GetPosition(), lay)], "pad"))

    n = len(pieces)
    parent = list(range(n + len(items)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i, j):
        parent[root(i)] = root(j)
    for k, (contacts, _) in enumerate(items):
        for pos, lays in contacts:
            for i, (lay, outline) in enumerate(pieces):
                if lay in lays and outline.PointInside(pos):
                    join(n + k, i)
    # traces meet pads, vias and other traces at their ends
    pad_items = [(k, p) for k, p in zip(range(len(items) - len(pads), len(items)), pads)]
    by_point = {}
    for k, (contacts, kind) in enumerate(items):
        for pos, lays in contacts:
            for lay in lays:
                by_point.setdefault((pos.x, pos.y, lay), []).append(k)
    for ks in by_point.values():
        for k in ks[1:]:
            join(n + k, n + ks[0])
    for k, (contacts, kind) in enumerate(items):
        if kind != "track":
            continue
        for pos, lays in contacts:
            for pk, p in pad_items:
                if p.HitTest(pos) and (p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                                       or (0 if p.IsOnLayer(pcbnew.F_Cu) else 1) in lays):
                    join(n + k, n + pk)

    groups = {}
    for i in range(n + len(items)):
        g = groups.setdefault(root(i), {"pieces": [], "points": [], "anchors": [], "pads": 0})
        if i < n:
            g["pieces"].append(i)
        else:
            contacts, kind = items[i - n]
            g["points"] += contacts
            if kind in ("via", "pth"):
                g["anchors"] += [pos for pos, _ in contacts]
            if kind in ("pad", "pth"):
                g["pads"] += 1
    return pieces, list(groups.values())


def join_islands(board, grid, gnd, passes=6):
    """Fill the pours and split the ground net into connected groups. Any group cut off
    from the main one gets a via down to the main pour on the other layer, or failing
    that a ground trace out to one of the main group's vias. Repeat until it is one group."""
    code = gnd.GetNetCode()
    for _ in range(passes):
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pieces, groups = ground_groups(board, code)
        groups = [g for g in groups if g["pads"] or g["pieces"]]
        main = max(groups, key=lambda g: (g["pads"], sum(abs(pieces[i][1].Area()) for i in g["pieces"])))
        if len(groups) == 1:
            break
        added = 0
        for g in groups:
            if g is main:
                continue
            spot = None
            for margin in (1.0, 0.5):
                ok = ground_via_ok(grid, gnd, margin)
                for i in g["pieces"]:
                    lay, outline = pieces[i]
                    landing = [pieces[j][1] for j in main["pieces"] if pieces[j][0] != lay]
                    spot = island_spot(grid, ok, outline, landing)
                    if spot:
                        break
                if spot:
                    break
            if spot:
                add_via(board, gnd, *spot)
                grid.disk(None, spot[0], spot[1], VIA_D / 2, code, hop=True)
                added += 1
            elif main["anchors"] and bridge(board, grid, gnd, g["points"], main["anchors"]):
                added += 1
        print("ground groups: %d, fixes: %d" % (len(groups), added))
        if not added:
            break
    remove_orphan_vias(board, code)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def bridge(board, grid, gnd, sources, targets, width=0.3):
    """Route a ground trace from any source contact, (position, layers), to any of the
    target points (vias or plated holes, so either layer will do)."""
    code = gnd.GetNetCode()
    nyx = grid.ny * grid.nx
    other = [(grid.owner[lay] != FREE) & (grid.owner[lay] != code) for lay in (0, 1)]
    r_track = (CLEAR + width / 2) / G + 1
    r_via = (CLEAR + VIA_D / 2) / G + 1
    blocked = np.stack([dilate(other[0], r_track), dilate(other[1], r_track)]).reshape(-1)
    via_ok = (~(dilate(other[0], r_via) | dilate(other[1], r_via))).reshape(-1)

    def cells(points):
        out = set()
        for p, lays in points:
            x, y = to_mm(p)
            i, j = int(round(x / G)), int(round(y / G))
            if 0 <= i < grid.nx and 0 <= j < grid.ny:
                for lay in lays:
                    out.add(lay * nyx + j * grid.nx + i)
        return out
    src = cells(sources)
    cx = np.mean([to_mm(p)[0] for p, _ in sources]) / G
    cy = np.mean([to_mm(p)[1] for p, _ in sources]) / G
    near = sorted(targets, key=lambda p: (to_mm(p)[0] / G - cx) ** 2
                  + (to_mm(p)[1] / G - cy) ** 2)[:12]
    target = np.zeros(2 * nyx, dtype=bool)
    target[list(cells([(p, (0, 1)) for p in near]))] = True
    centres = [(to_mm(p)[0] / G, to_mm(p)[1] / G) for p in near]
    for s in src:
        blocked[s] = False
    path = astar(grid, src, target, centres, blocked, via_ok)
    if path is None:
        print("could not bridge a ground island")
        return False
    add_to_board(board, grid, gnd, path, width)
    stamp_path(grid, path, code, width)
    print("ground island bridged with a %.1f mm trace" % (len(path) * G))
    return True


def remove_orphan_vias(board, code):
    """Drop stitching vias that ended up on no pour (pour removed around them as a sliver)."""
    pieces = []
    for zone in board.Zones():
        if zone.GetNetCode() == code:
            polys = zone.GetFilledPolysList(zone.GetFirstLayer())
            pieces += [polys.Outline(k) for k in range(polys.OutlineCount())]
    ends = set()
    for t in board.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            ends.add((t.GetStart().x, t.GetStart().y))
            ends.add((t.GetEnd().x, t.GetEnd().y))
    gone = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetCode() == code
            and (v.GetPosition().x, v.GetPosition().y) not in ends
            and not any(o.PointInside(v.GetPosition()) for o in pieces)]
    for v in gone:
        board.Remove(v)
    if gone:
        print("removed vias on no pour:", len(gone))


def island_spot(grid, ok, outline, landing):
    """A via position well inside this piece and inside one of the landing pieces."""
    box = outline.BBox()
    x0, y0 = to_mm(box.GetOrigin())
    x1, y1 = to_mm(box.GetEnd())
    r = VIA_D / 2
    for j in range(int(y0 / G), int(y1 / G) + 1):
        for i in range(int(x0 / G), int(x1 / G) + 1):
            if not (0 <= j < grid.ny and 0 <= i < grid.nx) or not ok[j, i]:
                continue
            x, y = i * G, j * G
            pts = [pcbnew.VECTOR2I_MM(ORIGIN[0] + px, ORIGIN[1] + py) for px, py in
                   ((x, y), (x - r, y), (x + r, y), (x, y - r), (x, y + r))]
            if all(outline.PointInside(p) for p in pts) and \
                    any(all(o.PointInside(p) for p in pts) for o in landing):
                return x, y
    return None


def holes(board):
    """(x, y, drill) in board mm for every drilled hole: vias and through-hole pads."""
    out = []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = to_mm(t.GetPosition())
            out.append((x, y, pcbnew.ToMM(t.GetDrillValue())))
    for p in board.GetPads():
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            x, y = to_mm(p.GetPosition())
            out.append((x, y, pcbnew.ToMM(min(p.GetDrillSize().x, p.GetDrillSize().y))))
    return out


def hole_room(existing, x, y):
    """True if a via drilled at (x, y) keeps its copper off other vias' copper and its
    hole HOLE_GAP from every other hole."""
    for hx, hy, d in existing:
        need = max(VIA_DRILL / 2 + d / 2 + HOLE_GAP, VIA_D)
        if (hx - x) ** 2 + (hy - y) ** 2 < need ** 2:
            return False
    return True


HOLE_GAP = 0.3       # hole edge to hole edge (KiCad's default rule is 0.25)


def stitch_ground(board, grid, gnd):
    """Ground vias on a regular grid wherever there is room on both layers."""
    ok = ground_via_ok(grid, gnd)
    existing = holes(board)
    count = 0
    y = STITCH_PITCH / 2
    while y < grid.h_mm:
        x = STITCH_PITCH / 2
        while x < grid.w_mm:
            j, i = int(round(y / G)), int(round(x / G))
            if 0 <= j < grid.ny and 0 <= i < grid.nx and ok[j, i] and \
                    grid.owner[0][j, i] in (FREE, gnd.GetNetCode()) and                     hole_room(existing, i * G, j * G):
                add_via(board, gnd, i * G, j * G)
                existing.append((i * G, j * G, VIA_DRILL))
                grid.disk(None, i * G, j * G, VIA_D / 2, gnd.GetNetCode(), hop=True)
                count += 1
            x += STITCH_PITCH
        y += STITCH_PITCH
    return count


ATTEMPTS = 12


def load():
    board = pcbnew.LoadBoard(BOARD)
    grid = Grid(board)
    nets = {n.GetNetname(): n for n in board.GetNetsByName().values() if n.GetNetCode() > 0}
    rasterize(board, grid, nets)
    return board, grid, nets


# Ground is poured, but in the crowded digital half signal traces can fence a ground pad
# off on both layers. So before any signal, every ground pad from here on is tied into the
# rest with a real trace. The radio half (x < GND_ROUTE_X) is left to the pours and its
# hand-placed vias.
GND_ROUTE_X = 48.0
GND_W = 0.2                     # continuity only; the pours carry the current


def route_ground(board, grid, gnd):
    nyx = grid.ny * grid.nx
    x0 = int(GND_ROUTE_X / G)

    def radio_side(comp):
        return max((c % nyx) % grid.nx for c in comp) < x0
    paths, fails = route_net(grid, gnd.GetNetCode(), GND_W, joined=radio_side)
    for p in paths:
        add_to_board(board, grid, gnd, p, GND_W)
    return fails


def attempt(order):
    board, grid, nets = load()
    failed, missing = [], 0
    gnd_fails = route_ground(board, grid, nets["/GND"])
    if gnd_fails:
        print("  ground traces: %d pads left to the pours" % gnd_fails)
    for name in order:
        width = POWER_W.get(name, SIGNAL_W)
        paths, fails = route_net(grid, nets[name].GetNetCode(), width)
        for p in paths:
            add_to_board(board, grid, nets[name], p, width)
        if fails:
            failed.append(name)
            missing += fails
    return board, grid, nets, failed, missing


def main():
    board, grid, nets = load()
    names = [n for n in nets if n not in SKIP and not n.startswith("unconnected-")]

    def span(name):
        code = nets[name].GetNetCode()
        ys, xs = np.nonzero((grid.owner[0] == code) | (grid.owner[1] == code))
        return (xs.max() - xs.min()) + (ys.max() - ys.min()) if len(xs) else 0
    # power first, then the radio's lines (they all squeeze out of one side of a small QFN)
    radio = {p.GetNetname() for p in board.FindFootprintByReference("U2").Pads()}
    base = sorted(names, key=lambda n: (n not in POWER_W, n not in radio, span(n)))

    # Route; if anything fails, start over with the failed nets first.
    best, order = None, base
    for i in range(ATTEMPTS):
        result = attempt(order)
        failed, missing = result[3], result[4]
        print("attempt %d: %d unrouted links in %s" % (i + 1, missing, failed or "none"))
        sys.stdout.flush()
        if best is None or missing < best[4]:
            best = result
        if not failed:
            break
        order = failed + [n for n in order if n not in failed]

    board, grid, nets, failed, missing = best
    n = stitch_ground(board, grid, nets["/GND"])
    print("ground stitching vias:", n)
    join_islands(board, grid, nets["/GND"])
    moved, fab_only = silk.tidy(board)
    print("labels on silk: %d; Fab only: %s" % (moved, ", ".join(sorted(fab_only)) or "none"))
    pcbnew.SaveBoard(BOARD, board)
    print("left unrouted:", failed or "none")


if __name__ == "__main__":
    main()
