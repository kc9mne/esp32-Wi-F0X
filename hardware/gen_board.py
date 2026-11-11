"""Create fox.kicad_pcb from fox.net: board outline sized for the tube, every footprint
placed in its group with its nets, and ground pours. Routing is left to do by hand.
Run with KiCad's Python:

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen_board.py

Board coordinates below are in mm from the board's top-left corner. x runs along the
tube: x = 0 is the cap end (SMA, USB-C, power switch), x = LENGTH is the far end, where
the ESP32's Wi-Fi antenna sits past the end of the battery.

Every part is on the top side except the 18650 holder, which is on the bottom. Its two
through-hole pins and centre peg hole come up through the board, so top parts avoid them.

Fit in the 37.6 mm tube: with the board's top surface about 3 mm above the tube's centre
line, the cell (18.4 mm) and holder (20.95 mm wide) clear the tube below, and a 0.96 in
OLED on a normal header clears it above. A 1.3 in OLED (35 mm wide) does not fit.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import find, first, parse  # noqa: E402

KICAD_FP = r"C:\Program Files\KiCad\9.0\share\kicad\footprints"
ORIGIN = (50.0, 50.0)          # where the board's corner sits on the KiCad page
WIDTH = 34.0                   # tube is 37.6 mm inside
LENGTH = 110.0
BATTERY_X = 45.0               # holder centre (bottom side): pins at x = 10 and 80, peg at 45
ESP_X = LENGTH - 12.75         # module centre, so its antenna end is at the board end

# ref: (x, y, rotation in degrees). Edge parts are positioned by EDGE below instead.
PLACE = {
    # ---- RF chain on a line at y = RF_Y, running left from the SX1278's RF pin ----
    # Series parts sit on the line (rotated 180 so pin 1 faces the radio); shunt caps hang
    # below it (rotated 270 so pin 1 is on top) with a ground via under each.
    "C30": (18.1, 4.9, 270), "L7": (19.6, 3.6, 180), "C29": (21.1, 4.9, 270), "L6": (22.6, 3.6, 180),
    "C28": (24.1, 4.9, 270), "R19": (25.6, 3.6, 180), "C27": (27.1, 4.9, 270), "L5": (28.6, 3.6, 180),
    "C26": (30.1, 4.9, 270), "L4": (31.6, 3.6, 180), "C25": (33.1, 4.9, 270), "C24": (34.6, 3.6, 180),
    "L3": (36.5, 3.6, 180),
    # RF pin node: shunt C22, RX branch C23 then L2 to ground, bias feed L1 up to VR_PA
    "C22": (38.1, 4.9, 270), "C23": (41.0, 4.9, 270), "L2": (40.0, 6.35, 180),
    # VR_ANA and VR_DIG decoupling, straight out from pins 2 and 4
    "C16": (40.3, 7.4, 180), "C17": (40.3, 8.5, 180),
    "L1": (43.05, 2.4, 90), "C20": (46.0, 2.8, 270), "C21": (47.2, 2.8, 270),
    "U2": (45.0, 8.6, 0),
    # SX1278 supply decoupling and TCXO (holder peg hole at (45, 17))
    "C12": (49.6, 14.2, 90), "C13": (51.0, 14.2, 90),
    "C14": (52.4, 14.2, 90), "C15": (52.0, 9.0, 90), "Y1": (40.6, 14.8, 180), "C18": (38.4, 15.6, 90),
    "C19": (39.9, 11.8, 90),
    # ---- power: USB-C, ESD, charger, protection, LDO, sensing (y 17-33) ----
    # Battery pin at (10, 17) comes through here.
    "U6": (12.8, 23.6, 90), "R1": (9.7, 24.75, 0), "R2": (9.7, 27.75, 0), "C1": (9.5, 31.4, 0),
    "U3": (18.2, 26.4, 0), "R3": (17.0, 31.6, 0), "C2": (21.5, 31.4, 0),
    "D2": (15.4, 19.4, 0), "R5": (15.4, 21.0, 0), "D1": (19.2, 19.4, 0), "R4": (19.2, 21.0, 0),
    "U4": (26.0, 22.0, 0), "R7": (22.0, 19.0, 0), "R6": (26.0, 19.0, 0), "C3": (29.0, 19.0, 0),
    "Q1": (26.0, 27.0, 0),
    "U5": (32.5, 27.0, 0), "C4": (32.5, 31.4, 0), "C5": (36.5, 31.4, 0),
    "R8": (35.6, 22.0, 90), "R9": (37.0, 22.0, 90), "C6": (38.4, 22.0, 90),
    "R10": (39.8, 22.0, 90), "R11": (41.2, 22.0, 90),
    # ---- SD card, OLED, buttons, LED, headers ----
    # The 0.96 in OLED module (27.3 x 27.8 mm) sits landscape across the board like the
    # T-Beam's: its header (J3) runs along y, centred on the board (and the tube), and the
    # module reaches from the header toward the cap end, over x = 48.9-76.7, y = 3.4-30.6.
    # Only low parts go under it: nothing there needs pressing or seeing.
    # microSD: card slot opens at the top edge (y = 0), clear of the module's edge.
    "J4": (66.0, 9.6, 180), "R17": (59.4, 20.6, 90), "R18": (60.8, 20.6, 90), "C10": (62.6, 21.0, 90),
    "C11": (64.4, 20.6, 90),
    "J3": (75.4, WIDTH / 2 - 3.81, 0),   # OLED header; pin 1 (GND) toward y = 0
    "R15": (71.4, 21.0, 0), "R16": (71.4, 23.0, 0),
    # RESET and BOOT, stacked beside the module where they can be pressed
    "SW2": (45.0, 25.4, 0), "SW3": (45.0, 30.9, 0),
    "R14": (79.0, 27.0, 90), "D3": (79.0, 30.6, 90),   # status LED, outside the module
    # ---- ESP32-S3 module, antenna pointing out the far end ----
    "U1": (ESP_X, WIDTH / 2, 270),
    "C7": (82.5, 12.4, 90), "C8": (82.6, 9.4, 90), "R12": (82.6, 22.5, 90), "C9": (82.6, 25.0, 90),
    "R13": (82.6, 27.6, 90),
    # ---- 18650 holder, bottom side ----
    "BT1": (BATTERY_X, WIDTH / 2, 0),
}
BOTTOM = {"BT1"}

# Parts that must hang over the cap-end edge (x = 0), at this y.
EDGE = {"J2": 7.0, "SW1": 15.0, "J1": 26.0}
EDGE_INSET = 0.4               # pads stop this far inside the edge

# ---- RF routing ----
# A point is (x, y) in board mm or (ref, pad number), which resolves to that pad's centre.
# At 146 MHz these traces are electrically short (the longest, SMA to filter, is about 0.02
# wavelength in FR4), so width matters less than keeping them short over solid ground.
RF_Y = 3.6
RF_W = 0.6                     # matches the 0402 pads
FEED_W = 1.0                   # SMA to filter: coplanar line over the bottom ground pour
SIG_W = 0.3                    # into the QFN's 0.3 mm pads, and the clock
VIA = (0.6, 0.3)               # diameter, drill


ROUTES = [
    # SMA centre pin, 45-degree jog up to the RF line, along to C30 and L7
    ("/RF_ANT", FEED_W, [("J2", "1"), (5.6, 7.0), (9.0, RF_Y), (18.1, RF_Y)]),
    ("/RF_ANT", RF_W, [(18.1, RF_Y), ("L7", "2")]),
    ("/RF_ANT", RF_W, [(18.1, RF_Y), ("C30", "1")]),
    ("/RF_F", RF_W, [("L7", "1"), ("L6", "2")]), ("/RF_F", RF_W, [(21.1, RF_Y), ("C29", "1")]),
    ("/RF_E", RF_W, [("L6", "1"), ("R19", "2")]), ("/RF_E", RF_W, [(24.1, RF_Y), ("C28", "1")]),
    ("/RF_D", RF_W, [("R19", "1"), ("L5", "2")]), ("/RF_D", RF_W, [(27.1, RF_Y), ("C27", "1")]),
    ("/RF_C", RF_W, [("L5", "1"), ("L4", "2")]), ("/RF_C", RF_W, [(30.1, RF_Y), ("C26", "1")]),
    ("/RF_B", RF_W, [("L4", "1"), ("C24", "2")]), ("/RF_B", RF_W, [(33.1, RF_Y), ("C25", "1")]),
    ("/RF_A", RF_W, [("C24", "1"), ("L3", "2")]),
    # RF pin node at (43.05, RF_Y): line from L3, shunt C22, RX cap C23, bias choke L1
    ("/RF_PIN", RF_W, [("L3", "1"), (43.05, RF_Y)]),
    ("/RF_PIN", RF_W, [(38.1, RF_Y), ("C22", "1")]),
    ("/RF_PIN", RF_W, [(41.0, RF_Y), ("C23", "1")]),
    ("/RF_PIN", SIG_W, [("L1", "1"), (43.05, RF_Y), ("U2", "28")]),
    # RX branch: C23 down to RFI_LF, L2 to ground
    ("/RF_RX", SIG_W, [("C23", "2"), (41.0, 6.35), ("U2", "1")]),
    ("/RF_RX", SIG_W, [("L2", "1"), (41.0, 6.35)]),
    # regulator decoupling
    ("/VR_ANA", SIG_W, [("U2", "2"), ("C16", "1")]),
    ("/VR_DIG", SIG_W, [("U2", "4"), ("C17", "1")]),
    # VR_PA: choke L1 to pin 25, decoupled by C20 and C21
    ("/VR_PA", SIG_W, [("L1", "2"), (45.0, 1.92), ("U2", "25")]),
    ("/VR_PA", SIG_W, [(45.0, 1.92), (47.2, 1.92), ("C21", "1")]),
    ("/VR_PA", SIG_W, [(46.0, 1.92), ("C20", "1")]),
    # TCXO: output through C19 to XTA
    ("/TCXO_OUT", SIG_W, [("Y1", "3"), ("C19", "1")]),
    ("/XTA", SIG_W, [("C19", "2"), (39.9, 10.0), (40.65, 9.25), ("U2", "5")]),
    ("/+3V3", SIG_W, [("Y1", "4"), (38.4, 15.85), ("C18", "1")]),
    # SX1278 ground pins into the exposed pad
    ("/GND", SIG_W, [("U2", "26"), (44.35, 6.6)]),
    ("/GND", SIG_W, [("U2", "23"), (46.3, 6.6)]),
    ("/GND", SIG_W, [("U2", "22"), (46.95, 6.6)]),
    ("/GND", SIG_W, [("U2", "21"), (47.0, 6.65)]),
    ("/GND", SIG_W, [("U2", "15"), (47.0, 10.55)]),
    # shunt parts to their ground vias
    ("/GND", RF_W, [("C22", "2"), (38.1, 6.2)]),
    ("/GND", SIG_W, [("L2", "2"), (39.0, 6.9)]),
    ("/GND", SIG_W, [("C16", "2"), (39.0, 6.9)]),
    ("/GND", SIG_W, [("C17", "2"), (39.0, 8.5)]),
    ("/GND", SIG_W, [("C20", "2"), (46.0, 4.3)]),
    ("/GND", SIG_W, [("C21", "2"), (47.2, 4.3)]),
    ("/GND", SIG_W, [("C18", "2"), (38.4, 14.2)]),
    ("/GND", SIG_W, [("Y1", "1"), (42.4, 15.85)]),
    ("/GND", SIG_W, [("Y1", "2"), (42.4, 13.75)]),
] + [("/GND", RF_W, [(c, "2"), (x, 6.3)]) for c, x in
     (("C30", 18.1), ("C29", 21.1), ("C28", 24.1), ("C27", 27.1), ("C26", 30.1), ("C25", 33.1))]

RF_VIAS = (
    # ground vias for the shunt parts and radio decoupling
    [(x, 6.3) for x in (18.1, 21.1, 24.1, 27.1, 30.1, 33.1)]
    + [(38.1, 6.2), (39.0, 6.9), (39.0, 8.5), (46.0, 4.3), (47.2, 4.3), (38.4, 14.2), (42.4, 15.85),
       (42.4, 13.75)]
    # in the SMA's ground pads, top and bottom legs
    + [(1.6, 4.45), (4.0, 4.45), (1.6, 9.55), (4.0, 9.55)]
    # stitching both sides of the SMA-to-filter line
    + [(x, 2.0) for x in (8.0, 11.0, 14.0, 17.0)] + [(x, 5.3) for x in (11.0, 14.0)]
    # CC pull-down resistors
    + [(10.3, 23.95), (11.0, 27.75)]
    # SD and ESP32 decoupling: their own ground vias, so routing can't strand them
    + [(62.6, 19.15), (64.4, 19.2), (81.5, 11.45)]
)

# ---- USB-C escape ----
# The receptacle's pins are 0.5 mm apart and each data line appears twice (A and B rows,
# for a reversible plug). D+ joins on top, threading between the two D- vias; D- joins on
# the bottom. Traces next to the pins are 0.15 mm so they clear the 0.5 mm vias.
USB_W = 0.15
USB_VIA = (0.5, 0.3)
USB_VIAS = [(7.6, 25.25, "/USB_DN"), (7.6, 26.25, "/USB_DN"), (13.75, 26.25, "/USB_DN")]
ROUTES += [
    ("/USB_DP", USB_W, [("J1", "A6"), (8.4, 25.75), (8.4, 26.75), ("J1", "B6")]),
    ("/USB_DP", 0.2, [(8.4, 25.75), (11.1, 25.75), (11.85, 25.0), ("U6", "1")]),
    ("/USB_DN", USB_W, [("J1", "B7"), (7.6, 25.25)]),
    ("/USB_DN", USB_W, [("J1", "A7"), (7.6, 26.25)]),
    ("/USB_DN", 0.2, [(7.6, 25.25), (7.6, 26.25), (13.75, 26.25)], "B"),
    ("/USB_DN", 0.2, [(13.75, 26.25), ("U6", "3")]),
    ("/CC1", USB_W, [("J1", "A5"), ("R1", "1")]),
    ("/CC2", USB_W, [("J1", "B5"), ("R2", "1")]),
    ("/GND", 0.2, [("R1", "2"), (10.3, 23.95)]),
    ("/GND", 0.2, [("R2", "2"), (11.0, 27.75)]),
    ("/GND", 0.3, [("C10", "2"), (62.6, 19.15)]),
    ("/GND", 0.3, [("C11", "2"), (64.4, 19.2)]),
    ("/GND", 0.3, [("C7", "2"), (81.5, 11.45)]),
    # VBUS: the A4/B9 pin to the ESD diode, the A9/B4 pin to C1
    ("/VBUS", 0.6, [("J1", "A4"), (8.6, 23.55), (9.6, 22.55), (9.6, 21.2), (12.8, 21.2), ("U6", "5")]),
    ("/VBUS", 0.6, [("J1", "A9"), (7.9, 28.45), (8.55, 29.1), ("C1", "1")]),
    # SX1278 pin 3 (+3V3): out between C16 and C17 and the two ground vias, down to C18
    ("/+3V3", 0.15, [("U2", "3"), (37.6, 7.95)]),
    ("/+3V3", 0.3, [(37.6, 7.95), (37.6, 16.08), ("C18", "1")]),
]


def mm(x, y):
    return pcbnew.VECTOR2I_MM(ORIGIN[0] + x, ORIGIN[1] + y)


def load_netlist():
    with open(os.path.join(HERE, "fox.net"), encoding="utf-8") as f:
        tree = parse(f.read())[0]
    comps = {}
    for comp in find(first(tree, "components"), "comp"):
        ref = str(first(comp, "ref")[1])
        comps[ref] = dict(value=str(first(comp, "value")[1]),
                          footprint=str(first(comp, "footprint")[1]),
                          uuid=str(first(comp, "tstamps")[1]), pads={})
    nets = []
    for net in find(first(tree, "nets"), "net"):
        name = str(first(net, "name")[1])
        nets.append(name)
        for node in find(net, "node"):
            comps[str(first(node, "ref")[1])]["pads"][str(first(node, "pin")[1])] = name
    return comps, nets


def load_footprint(lib_id):
    lib, name = lib_id.split(":")
    path = os.path.join(HERE, "lib", "fox.pretty") if lib == "fox" else \
        os.path.join(KICAD_FP, lib + ".pretty")
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit("footprint not found: " + lib_id)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def body_box(fp):
    """Footprint extent without text, in board mm."""
    box = fp.GetBoundingBox(False, False)
    return (pcbnew.ToMM(box.GetLeft()) - ORIGIN[0], pcbnew.ToMM(box.GetTop()) - ORIGIN[1],
            pcbnew.ToMM(box.GetRight()) - ORIGIN[0], pcbnew.ToMM(box.GetBottom()) - ORIGIN[1])


def pad_box(fp):
    xs, ys = [], []
    for pad in fp.Pads():
        b = pad.GetBoundingBox()
        xs += [pcbnew.ToMM(b.GetLeft()), pcbnew.ToMM(b.GetRight())]
        ys += [pcbnew.ToMM(b.GetTop()), pcbnew.ToMM(b.GetBottom())]
    return min(xs) - ORIGIN[0], min(ys) - ORIGIN[1], max(xs) - ORIGIN[0], max(ys) - ORIGIN[1]


def place_on_edge(fp, y):
    """Rotate so the body overhangs toward -x the most, then put the pads just inside x = 0."""
    best = None
    for angle in (0, 90, 180, 270):
        fp.SetOrientationDegrees(angle)
        fp.SetPosition(mm(0, 0))
        bx0, _, bx1, _ = body_box(fp)
        px0, _, px1, _ = pad_box(fp)
        overhang = (px0 - bx0) - (bx1 - px1)
        if best is None or overhang > best[0]:
            best = (overhang, angle)
    fp.SetOrientationDegrees(best[1])
    fp.SetPosition(mm(0, 0))
    px0, py0, _, py1 = pad_box(fp)
    fp.SetPosition(mm(EDGE_INSET - px0, y - (py0 + py1) / 2))


def add_rect(board, layer, x0, y0, x1, y1, width=0.1):
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_RECT)
    shape.SetStart(mm(x0, y0))
    shape.SetEnd(mm(x1, y1))
    shape.SetLayer(layer)
    shape.SetWidth(pcbnew.FromMM(width))
    board.Add(shape)


def add_ground_pour(board, layer, net):
    zone = pcbnew.ZONE(board)
    ls = pcbnew.LSET()
    ls.AddLayer(layer)
    zone.SetLayerSet(ls)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((0, 0), (LENGTH, 0), (LENGTH, WIDTH), (0, WIDTH)):
        outline.Append(mm(x, y))
    zone.SetNet(net)
    zone.SetMinThickness(pcbnew.FromMM(0.2))
    zone.SetLocalClearance(pcbnew.FromMM(0.25))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)   # solid: no starved thermal spokes
    board.Add(zone)


def pad_xy(board, ref, number):
    pad = board.FindFootprintByReference(ref).FindPadByNumber(number)
    pos = pad.GetPosition()
    return pcbnew.ToMM(pos.x) - ORIGIN[0], pcbnew.ToMM(pos.y) - ORIGIN[1]


def add_routes(board, nets):
    for route in ROUTES:
        net, width, points = route[:3]
        layer = pcbnew.B_Cu if route[3:] == ("B",) else pcbnew.F_Cu
        xy = [pad_xy(board, *p) if isinstance(p[0], str) else p for p in points]
        for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
            if (x0, y0) == (x1, y1):
                continue
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(mm(x0, y0))
            track.SetEnd(mm(x1, y1))
            track.SetWidth(pcbnew.FromMM(width))
            track.SetLayer(layer)
            track.SetNet(nets[net])
            board.Add(track)
    for x, y in RF_VIAS:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(mm(x, y))
        via.SetWidth(pcbnew.FromMM(VIA[0]))
        via.SetDrill(pcbnew.FromMM(VIA[1]))
        via.SetNet(nets["/GND"])
        board.Add(via)
    for x, y, net in USB_VIAS:
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(mm(x, y))
        via.SetWidth(pcbnew.FromMM(USB_VIA[0]))
        via.SetDrill(pcbnew.FromMM(USB_VIA[1]))
        via.SetNet(nets[net])
        board.Add(via)


def build():
    comps, net_names = load_netlist()
    board = pcbnew.CreateEmptyBoard()
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(2)
    # JLCPCB standard 2-layer capabilities
    ds.m_TrackMinWidth = pcbnew.FromMM(0.15)
    ds.m_MinClearance = pcbnew.FromMM(0.15)
    ds.m_MinThroughDrill = pcbnew.FromMM(0.2)
    ds.m_CopperEdgeClearance = pcbnew.FromMM(0.3)
    # 0.15 mm clearance (JLCPCB does 0.1) so 0.2 mm traces can leave the USB-C's 0.5 mm pitch
    ds.m_NetSettings.GetDefaultNetclass().SetClearance(pcbnew.FromMM(0.15))

    nets = {}
    for name in net_names:
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = item

    missing = set(comps) - set(PLACE) - set(EDGE)
    if missing:
        raise SystemExit("no position for " + ", ".join(sorted(missing)))

    for ref, c in comps.items():
        fp = load_footprint(c["footprint"])
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        fp.SetPath(pcbnew.KIID_PATH("/" + c["uuid"]))
        board.Add(fp)
        for pad in fp.Pads():
            name = c["pads"].get(pad.GetNumber())
            if name:
                pad.SetNet(nets[name])
        if ref in EDGE:
            place_on_edge(fp, EDGE[ref])
        else:
            x, y, angle = PLACE[ref]
            fp.SetOrientationDegrees(angle)
            fp.SetPosition(mm(x, y))
            if ref in BOTTOM:
                fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)

    add_routes(board, nets)
    add_rect(board, pcbnew.Edge_Cuts, 0, 0, LENGTH, WIDTH)
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        add_ground_pour(board, layer, nets["/GND"])
    # Zones are filled by KiCad (press B); the filler crashes when run from a standalone
    # script. The ESP32 footprint carries its own antenna keepout area.

    path = os.path.join(HERE, "fox.kicad_pcb")
    pcbnew.SaveBoard(path, board)
    print("wrote %s: %d footprints, %d nets, %.0f x %.0f mm"
          % (path, len(comps), len(nets), LENGTH, WIDTH))


if __name__ == "__main__":
    build()
