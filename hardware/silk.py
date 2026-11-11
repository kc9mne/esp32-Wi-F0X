"""Place reference labels so none overlap a pad, a via, a hole, another part or another
label. Each label tries spots around its part (small text first at the side, then
rotated); a label that fits nowhere moves to the Fab layer, which still shows it on the
assembly drawing but leaves it off the silkscreen. Run with KiCad's Python after
autoroute.py (autoroute.py calls it), since vias count as obstacles.
"""
import pcbnew

BOARD = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)),
                                   "fox.kicad_pcb")
SIZE = 0.8          # mm text height for parts with small pads
BIG = 1.0           # parts bigger than BIG_PART get 1 mm text
BIG_PART = 4.0
SMALL = 0.8         # JLCPCB wants silk text at least 0.8 mm tall; nothing smaller
GAP = 0.15          # clearance from silk to anything else
EDGE = 0.4


def mm(v):
    return pcbnew.ToMM(v)


class Rect:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @staticmethod
    def of(box, grow=0.0):
        return Rect(mm(box.GetLeft()) - grow, mm(box.GetTop()) - grow,
                    mm(box.GetRight()) + grow, mm(box.GetBottom()) + grow)

    def hits(self, o):
        return self.x0 < o.x1 and o.x0 < self.x1 and self.y0 < o.y1 and o.y0 < self.y1


def obstacles(board, top):
    """Rectangles silk must avoid on the given side."""
    layer = pcbnew.F_Cu if top else pcbnew.B_Cu
    out = []
    for fp in board.Footprints():
        for pad in fp.Pads():
            if pad.IsOnLayer(layer) or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                out.append(Rect.of(pad.GetBoundingBox(), GAP))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            out.append(Rect.of(t.GetBoundingBox(), GAP))
    return out


def bodies(board, top):
    """Each part's courtyard (or its bounding box), by reference."""
    out = {}
    for fp in board.Footprints():
        if (fp.GetLayer() == pcbnew.F_Cu) != top:
            continue
        cy = fp.GetCourtyard(pcbnew.F_CrtYd if top else pcbnew.B_CrtYd)
        box = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
        out[fp.GetReference()] = Rect.of(box, 0.05)
    return out


def text_rect(text):
    return Rect.of(text.GetBoundingBox(), GAP / 2)


def tidy(board):
    edge = board.GetBoardEdgesBoundingBox()
    bx0, by0 = mm(edge.GetLeft()) + EDGE, mm(edge.GetTop()) + EDGE
    bx1, by1 = mm(edge.GetRight()) - EDGE, mm(edge.GetBottom()) - EDGE
    placed, moved, hidden = [], 0, []
    fps = sorted(board.Footprints(), key=lambda f: -f.GetBoundingBox(False, False).GetArea())
    for top in (True, False):
        obs = obstacles(board, top)
        body = bodies(board, top)
        silk = pcbnew.F_SilkS if top else pcbnew.B_SilkS
        fab = pcbnew.F_Fab if top else pcbnew.B_Fab
        for fp in fps:
            if (fp.GetLayer() == pcbnew.F_Cu) != top:
                continue
            ref = fp.Reference()
            own = body.get(fp.GetReference())
            box = own or Rect.of(fp.GetBoundingBox(False, False))
            size = BIG if max(box.x1 - box.x0, box.y1 - box.y0) > BIG_PART else SIZE
            ref.SetLayer(silk)
            ref.SetMirrored(not top)
            ref.SetKeepUpright(True)
            others = [r for k, r in body.items() if k != fp.GetReference()]
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            spots = []
            for s, angle in [(size, 0), (size, 90), (SMALL, 0), (SMALL, 90)]:
                ref.SetTextSize(pcbnew.VECTOR2I_MM(s, s))
                ref.SetTextThickness(pcbnew.FromMM(s * 0.15))
                ref.SetTextAngleDegrees(angle)
                ref.SetPosition(pcbnew.VECTOR2I_MM(0, 0))
                t = text_rect(ref)
                hw, hh = (t.x1 - t.x0) / 2, (t.y1 - t.y0) / 2
                for d in (0.0, 0.4, 0.8, 1.4, 2.0):
                    for x, y in [(cx, box.y0 - hh - d), (cx, box.y1 + hh + d),
                                 (box.x0 - hw - d, cy), (box.x1 + hw + d, cy),
                                 (box.x0 + hw, box.y0 - hh - d), (box.x1 - hw, box.y0 - hh - d),
                                 (box.x0 + hw, box.y1 + hh + d), (box.x1 - hw, box.y1 + hh + d)]:
                        spots.append((s, angle, x, y))
            ok = None
            for s, angle, x, y in spots:
                ref.SetTextSize(pcbnew.VECTOR2I_MM(s, s))
                ref.SetTextThickness(pcbnew.FromMM(s * 0.15))
                ref.SetTextAngleDegrees(angle)
                ref.SetPosition(pcbnew.VECTOR2I_MM(x, y))
                t = text_rect(ref)
                if t.x0 < bx0 or t.y0 < by0 or t.x1 > bx1 or t.y1 > by1:
                    continue
                if any(t.hits(o) for o in obs) or any(t.hits(o) for o in others) \
                        or any(t.hits(o) for o in placed):
                    continue
                ok = t
                break
            if ok:
                placed.append(ok)
                moved += 1
            else:
                ref.SetTextAngleDegrees(0)
                ref.SetPosition(pcbnew.VECTOR2I_MM(cx, cy))
                ref.SetLayer(fab)
                hidden.append(fp.GetReference())
    return moved, hidden


def main():
    board = pcbnew.LoadBoard(BOARD)
    moved, hidden = tidy(board)
    pcbnew.SaveBoard(BOARD, board)
    print("labels placed on silk: %d; on Fab only: %s" % (moved, ", ".join(sorted(hidden)) or "none"))


if __name__ == "__main__":
    main()
