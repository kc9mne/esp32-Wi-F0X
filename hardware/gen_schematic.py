"""Generate fox.kicad_sch from the part and net list below.

The schematic is label-connected: every pin gets a net label at its end instead of
drawn wires, so the connections here are the single source of truth. Rerun after
editing:  python gen_schematic.py
"""
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sexp import Q, dump, find, first, load_symbol, pins  # noqa: E402

KICAD_SYMBOLS = r"C:\Program Files\KiCad\9.0\share\kicad\symbols"
PROJECT = "fox"
ROOT_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "kc9mne-fox-schematic"))

R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
L0402 = "Inductor_SMD:L_0402_1005Metric"
LED0603 = "LED_SMD:LED_0603_1608Metric"

# Symbols not in the stock KiCad libraries: (pins, footprint, body half-width, half-height)
CUSTOM = {
    "TP4056": ([("1", "TEMP", "input"), ("2", "PROG", "passive"), ("3", "GND", "power_in"),
                ("4", "VCC", "power_in"), ("5", "BAT", "power_out"), ("6", "~{STDBY}", "open_collector"),
                ("7", "~{CHRG}", "open_collector"), ("8", "CE", "input"), ("9", "EP", "passive")],
               "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm"),
    # SOT-23-6 pinout from the Tech Public FS8205A datasheet (LCSC C2830320)
    "FS8205A": ([("1", "S1", "passive"), ("2", "D12", "passive"), ("3", "S2", "passive"),
                 ("4", "G2", "input"), ("5", "D12", "passive"), ("6", "G1", "input")],
                "Package_TO_SOT_SMD:SOT-23-6"),
    # Epson TG2520SMN: pin 1 must be open or grounded; output needs an external DC-cut cap
    "TCXO_32M": ([("1", "NC", "passive"), ("2", "GND", "power_in"), ("3", "OUT", "output"),
                  ("4", "VCC", "power_in")],
                 "Oscillator:Oscillator_SMD_SeikoEpson_TG2520SMN-xxx-xxxxxx-4Pin_2.5x2.0mm"),
}

# LCSC numbers for generic passives, by (value, footprint). All checked on lcsc.com.
LCSC_PASSIVES = {
    ("5.1k", R0402): "C25905", ("2.4k", R0402): "C25882", ("1k", R0402): "C11702",
    ("100", R0402): "C25076", ("100k", R0402): "C25741", ("10k", R0402): "C25744",
    ("4.7k", R0402): "C25900", ("0", R0402): "C17168",
    ("100nF", C0402): "C1525", ("1uF", C0402): "C52923", ("10nF", C0402): "C15195",
    ("10uF", C0805): "C15850", ("22uF", C0805): "C45783",
    # RF: C0G +-0.25 pF or 2 %, Murata LQW15AN wirewound inductors
    ("6.8pF", C0402): "C1576", ("9.1pF", C0402): "C64366", ("18pF", C0402): "C1549",
    ("22pF", C0402): "C1555", ("39pF", C0402): "C1864552", ("270pF", C0402): "C161546",
    ("39nH", L0402): "C98059", ("56nH", L0402): "C167482", ("68nH", L0402): "C108965",
    ("91nH", L0402): "C113121", ("120nH", L0402): "C113123",
}


def part(lib_id, value, pinmap, footprint=None, lcsc="", note=""):
    if not lcsc and footprint:
        lcsc = LCSC_PASSIVES.get((value, footprint), "")
    return dict(lib_id=lib_id, value=value, pins=pinmap, footprint=footprint, lcsc=lcsc, note=note)


def r(value, a, b, fp=R0402):
    return part("Device:R", value, {"1": a, "2": b}, fp)


def c(value, a, b="GND", fp=C0402):
    return part("Device:C", value, {"1": a, "2": b}, fp)


def ind(value, a, b):
    return part("Device:L", value, {"1": a, "2": b}, L0402)


GND = "GND"
PARTS = {
    # ---- USB-C, ESD, charger ----
    "J1": part("Connector:USB_C_Receptacle_USB2.0_16P", "USB-C",
               {"A4": "VBUS", "A9": "VBUS", "B4": "VBUS", "B9": "VBUS",
                "A1": GND, "A12": GND, "B1": GND, "B12": GND, "S1": GND,
                "A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP",
                "A7": "USB_DN", "B7": "USB_DN", "A8": None, "B8": None},
               "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", "C165948"),
    "R1": r("5.1k", "CC1", GND),
    "R2": r("5.1k", "CC2", GND),
    "U6": part("Power_Protection:USBLC6-2P6", "USBLC6-2SC6",
               {"1": "USB_DP", "6": "USB_DP", "3": "USB_DN", "4": "USB_DN", "5": "VBUS", "2": GND},
               "Package_TO_SOT_SMD:SOT-23-6", "C7519"),
    "C1": c("10uF", "VBUS", fp=C0805),
    "U3": part("fox:TP4056", "TP4056",
               {"1": GND, "2": "PROG", "3": GND, "4": "VBUS", "5": "BAT_P",
                "6": "STDBY_N", "7": "CHRG_N", "8": "VBUS", "9": GND},
               lcsc="C16581", note="Charge current 1200 V / R_PROG = 500 mA"),
    "R3": r("2.4k", "PROG", GND),
    "D1": part("Device:LED", "green: charged", {"1": "STDBY_N", "2": "LED_STDBY"}, LED0603, "C965804"),
    "R4": r("1k", "LED_STDBY", "VBUS"),
    "D2": part("Device:LED", "red: charging", {"1": "CHRG_N", "2": "LED_CHRG"}, LED0603, "C2286"),
    "R5": r("1k", "LED_CHRG", "VBUS"),
    "C2": c("10uF", "BAT_P", fp=C0805),
    # ---- 18650 cell and protection (Samsung 35E is unprotected) ----
    "BT1": part("Device:Battery_Cell", "18650", {"1": "BAT_P", "2": "BAT_N"},
                "fox:BAT-TH_BH-18650-A1AJ005", "C49118395"),
    "U4": part("Battery_Management:DW01A", "DW01A",
               {"5": "DW_VCC", "6": "BAT_N", "1": "DW_OD", "3": "DW_OC", "2": "DW_CS", "4": None},
               "Package_TO_SOT_SMD:SOT-23-6", "C351410"),
    "R6": r("100", "BAT_P", "DW_VCC"),
    "C3": c("100nF", "DW_VCC", "BAT_N"),
    "R7": r("1k", "DW_CS", GND),
    "Q1": part("fox:FS8205A", "FS8205A",
               {"1": "BAT_N", "2": "Q_DRAIN", "3": GND, "4": "DW_OC", "5": "Q_DRAIN", "6": "DW_OD"},
               lcsc="C2830320"),
    # ---- power switch, 3.3 V, sensing ----
    "SW1": part("Switch:SW_SPDT", "POWER", {"2": "BAT_P", "1": "VSYS", "3": None},
                "fox:SW-TH_MSK12C02", "C431540"),
    "U5": part("Regulator_Linear:ME6211C33M5", "ME6211C33",
               {"1": "VSYS", "3": "VSYS", "2": GND, "5": "+3V3", "4": None},
               "Package_TO_SOT_SMD:SOT-23-5", "C82942"),
    "C4": c("10uF", "VSYS", fp=C0805),
    "C5": c("10uF", "+3V3", fp=C0805),
    "R8": r("100k", "VSYS", "VBAT_SENSE"),
    "R9": r("100k", "VBAT_SENSE", GND),
    "C6": c("100nF", "VBAT_SENSE"),
    "R10": r("100k", "VBUS", "VBUS_SENSE"),
    "R11": r("100k", "VBUS_SENSE", GND),
    # ---- ESP32-S3 ----
    "U1": part("RF_Module:ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1-N8",
               {"1": GND, "40": GND, "41": GND, "2": "+3V3", "3": "EN", "27": "BOOT",
                "39": "RADIO_DIO1", "38": "RADIO_DIO0", "4": "RADIO_DIO2", "5": "RADIO_RST",
                "6": "VBAT_SENSE", "7": "LED_STATUS", "18": "RADIO_NSS", "19": "RADIO_MOSI",
                "20": "RADIO_SCK", "21": "RADIO_MISO", "9": "VBUS_SENSE", "10": "OLED_SDA",
                "11": "OLED_SCL", "13": "USB_DN", "14": "USB_DP", "28": "SD_MOSI",
                "29": "SD_SCK", "30": "SD_MISO", "24": "SD_CS", "36": None, "37": None,
                "15": None, "8": None, "12": None, "16": None, "17": None, "22": None,
                "23": None, "25": None, "26": None, "31": None, "32": None, "33": None,
                "34": None, "35": None},
               "RF_Module:ESP32-S3-WROOM-1", "C2913198",
               note="Pin map matches the T-Beam Supreme, so the firmware keeps its radio, SD and OLED pins"),
    "C7": c("22uF", "+3V3", fp=C0805),
    "C8": c("100nF", "+3V3"),
    "R12": r("10k", "+3V3", "EN"),
    "C9": c("1uF", "EN"),
    "SW2": part("Switch:SW_Push", "RESET", {"1": "EN", "2": GND},
                "fox:SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5", "C318884"),
    "R13": r("10k", "+3V3", "BOOT"),
    "SW3": part("Switch:SW_Push", "BOOT", {"1": "BOOT", "2": GND},
                "fox:SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5", "C318884"),
    "R14": r("1k", "LED_STATUS", "LED_S"),
    "D3": part("Device:LED", "blue: status", {"2": "LED_S", "1": GND}, LED0603, "C965807"),
    # No UART header: the USB-C port gives the ESP32-S3 its download mode and serial
    # console (USB-Serial-JTAG), and a header at the board edge would hit the tube.
    # ---- OLED and SD ----
    "J3": part("Connector_Generic:Conn_01x04", "OLED 0.96in I2C",
               {"1": GND, "2": "+3V3", "3": "OLED_SCL", "4": "OLED_SDA"},
               "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"),
    "R15": r("4.7k", "+3V3", "OLED_SDA"),
    "R16": r("4.7k", "+3V3", "OLED_SCL"),
    "J4": part("Connector:Micro_SD_Card", "microSD",
               {"1": None, "2": "SD_CS", "3": "SD_MOSI", "4": "+3V3", "5": "SD_SCK",
                "6": GND, "7": "SD_MISO", "8": None, "9": GND},
               "fox:TF-SMD_TF-01A", "C91145"),
    "R17": r("10k", "+3V3", "SD_CS"),
    "R18": r("10k", "+3V3", "SD_MISO"),
    "C10": c("10uF", "+3V3", fp=C0805),
    "C11": c("100nF", "+3V3"),
    # ---- SX1278 and 32 MHz TCXO ----
    "U2": part("RF:SX1278", "SX1278IMLTRT",
               {"2": "VR_ANA", "3": "+3V3", "4": "VR_DIG", "14": "+3V3", "24": "+3V3",
                "15": GND, "21": GND, "22": GND, "23": GND, "26": GND, "29": GND,
                "5": "XTA", "6": None, "7": "RADIO_RST", "8": "RADIO_DIO0", "9": "RADIO_DIO1",
                "10": "RADIO_DIO2", "11": None, "12": None, "13": None, "16": "RADIO_SCK",
                "17": "RADIO_MISO", "18": "RADIO_MOSI", "19": "RADIO_NSS", "20": None,
                "25": "VR_PA", "27": None, "28": "RF_PIN", "1": "RF_RX"},
               lcsc="C67678", note="Single-path RFO_LF/RFI_LF, Semtech AN1200.19 Fig. 6, values scaled 169 -> 146 MHz"),
    "C12": c("100nF", "+3V3"),
    "C13": c("100nF", "+3V3"),
    "C14": c("100nF", "+3V3"),
    "C15": c("10uF", "+3V3", fp=C0805),
    "C16": c("100nF", "VR_ANA"),
    "C17": c("100nF", "VR_DIG"),
    "Y1": part("fox:TCXO_32M", "TG2520SMN 32MHz", {"1": GND, "2": GND, "3": "TCXO_OUT", "4": "+3V3"},
               lcsc="C3007516"),
    "C18": c("100nF", "+3V3"),
    "C19": c("10nF", "TCXO_OUT", "XTA"),
    # ---- 146 MHz match (AN1200.19 169 MHz BOM scaled by 169/146) ----
    "L1": ind("120nH", "RF_PIN", "VR_PA"),            # Lp, PA bias feed
    "C20": c("270pF", "VR_PA"),                       # Cd
    "C21": c("10nF", "VR_PA"),
    "C22": c("18pF", "RF_PIN"),                       # C1
    "C23": c("6.8pF", "RF_PIN", "RF_RX"),             # C10
    "L2": ind("91nH", "RF_RX", GND),                  # L0
    "L3": ind("68nH", "RF_PIN", "RF_A"),              # L1
    "C24": c("270pF", "RF_A", "RF_B"),                # Cc
    "C25": c("39pF", "RF_B"),                         # C2
    "L4": ind("56nH", "RF_B", "RF_C"),                # L2
    "C26": c("39pF", "RF_C"),                         # C3
    "L5": ind("39nH", "RF_C", "RF_D"),                # L3
    "C27": c("9.1pF", "RF_D"),                        # C4
    # ---- extra 5-pole harmonic filter, 0 ohm link lets it be tested alone ----
    "R19": r("0", "RF_D", "RF_E"),
    "C28": c("22pF", "RF_E"),
    "L6": ind("68nH", "RF_E", "RF_F"),
    "C29": c("39pF", "RF_F"),
    "L7": ind("68nH", "RF_F", "RF_ANT"),
    "C30": c("22pF", "RF_ANT"),
    "J2": part("Connector:Conn_Coaxial", "SMA", {"1": "RF_ANT", "2": GND},
               "fox:SMA-SMD_BWSMA-KE-P001", "C496550"),
}

# Nets with only passive or power_in pins need a flag for ERC.
POWER_FLAGS = [GND, "VBUS", "VSYS", "BAT_N", "DW_VCC", "VR_ANA", "VR_DIG"]


def uid(*parts):
    return str(uuid.uuid5(uuid.UUID(ROOT_UUID), "/".join(parts)))


def custom_symbol(name):
    pin_list, footprint = CUSTOM[name]
    left = pin_list[: (len(pin_list) + 1) // 2]
    right = pin_list[(len(pin_list) + 1) // 2:]
    height = max(len(left), len(right)) * 2.54
    top = height / 2
    graphics = ["symbol", Q(name + "_0_1"),
                ["rectangle", ["start", -7.62, top + 1.27], ["end", 7.62, -top - 1.27],
                 ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]]
    pin_nodes = ["symbol", Q(name + "_1_1")]
    for side, group in ((-1, left), (1, right)):
        for i, (num, pname, etype) in enumerate(group):
            y = round(top - 1.27 - i * 2.54, 2)
            x = side * 10.16
            angle = 0 if side < 0 else 180
            pin_nodes.append(["pin", etype, "line", ["at", x, y, angle], ["length", 2.54],
                              ["name", Q(pname), ["effects", ["font", ["size", 1.27, 1.27]]]],
                              ["number", Q(num), ["effects", ["font", ["size", 1.27, 1.27]]]]])
    return ["symbol", Q(name), ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            ["property", Q("Reference"), Q("U"), ["at", 0, top + 2.54, 0],
             ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["property", Q("Value"), Q(name), ["at", 0, -top - 2.54, 0],
             ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["property", Q("Footprint"), Q(footprint), ["at", 0, 0, 0],
             ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]],
            ["property", Q("Datasheet"), Q(""), ["at", 0, 0, 0],
             ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]],
            graphics, pin_nodes]


def library_symbol(lib_id):
    lib, name = lib_id.split(":")
    if lib == "fox":
        return custom_symbol(name)
    return load_symbol(os.path.join(KICAD_SYMBOLS, lib + ".kicad_sym"), name)


def bbox(sym):
    xs, ys = [0.0], [0.0]
    for _, _, x, y, _, _, _ in pins(sym):
        xs.append(x)
        ys.append(y)
    return min(xs), max(xs), min(ys), max(ys)


def label_for(net, x, y, pin_angle):
    # A pin's angle points from its end toward the body; the label faces away from the body.
    angle = {0: 180, 180: 0, 90: 270, 270: 90}[pin_angle]
    justify = "left" if angle in (0, 90) else "right"
    return ["label", Q(net), ["at", x, y, angle], ["fields_autoplaced", "yes"],
            ["effects", ["font", ["size", 1.0, 1.0]], ["justify", justify, "bottom"]],
            ["uuid", Q(uid("label", net, str(x), str(y)))]]


def snap(v):
    return round(round(v / 1.27) * 1.27, 2)


def build():
    lib_ids = sorted({p["lib_id"] for p in PARTS.values()} | {"power:PWR_FLAG"})
    symbols = {lid: library_symbol(lid) for lid in lib_ids}

    lib_nodes = []
    for lid, sym in symbols.items():
        node = [sym[0], Q(lid)] + sym[2:]
        lib_nodes.append(node)

    body = []
    # Row packing on an A2 sheet, in the order PARTS is written (functional groups).
    x0, y0, x_max = 25.0, 25.0, 560.0
    x, y, row_h = x0, y0, 0.0
    placed = []
    for ref, p in PARTS.items():
        minx, maxx, miny, maxy = bbox(symbols[p["lib_id"]])
        w = (maxx - minx) + 32
        h = (maxy - miny) + 22
        if x + w > x_max:
            x, y, row_h = x0, y + row_h, 0.0
        cx, cy = snap(x - minx + 20), snap(y + maxy + 11)
        placed.append((ref, p, cx, cy))
        x += w
        row_h = max(row_h, h)

    flag_x, flag_y = x0, y + row_h + 20
    for ref, p, cx, cy in placed:
        sym = symbols[p["lib_id"]]
        footprint = p["footprint"] or str(first(sym, "property") and next(
            (pr[2] for pr in find(sym, "property") if pr[1] == "Footprint"), ""))
        p["resolved_footprint"] = footprint
        props = [("Reference", ref, cx, cy - 4), ("Value", p["value"], cx, cy + 4),
                 ("Footprint", footprint, cx, cy), ("Datasheet", "", cx, cy),
                 ("LCSC", p["lcsc"], cx, cy)]
        if p["note"]:
            props.append(("Note", p["note"], cx, cy))
        node = ["symbol", ["lib_id", Q(p["lib_id"])], ["at", cx, cy, 0], ["unit", 1],
                ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                ["uuid", Q(uid("sym", ref))]]
        for i, (k, v, px, py) in enumerate(props):
            hidden = k not in ("Reference", "Value")
            eff = ["effects", ["font", ["size", 1.27, 1.27]]]
            if hidden:
                eff.append(["hide", "yes"])
            node.append(["property", Q(k), Q(v), ["at", px, py, 0], eff])
        seen = set()
        for num, _, px, py, pang, _, _ in pins(sym):
            if num in seen:
                continue
            seen.add(num)
            node.append(["pin", Q(num), ["uuid", Q(uid("pin", ref, num))]])
            if num not in p["pins"]:
                raise SystemExit("%s pin %s has no net (use None for no-connect)" % (ref, num))
            net = p["pins"][num]
            ex, ey = round(cx + px, 2), round(cy - py, 2)
            if net is None:
                body.append(["no_connect", ["at", ex, ey], ["uuid", Q(uid("nc", ref, num))]])
            else:
                body.append(label_for(net, ex, ey, pang))
        unknown = set(p["pins"]) - seen
        if unknown:
            raise SystemExit("%s: pins %s not on symbol %s" % (ref, sorted(unknown), p["lib_id"]))
        node.append(["instances", ["project", Q(PROJECT),
                                   ["path", Q("/" + ROOT_UUID), ["reference", Q(ref)], ["unit", 1]]]])
        body.insert(0, node)

    for i, net in enumerate(POWER_FLAGS):
        ref = "#FLG%02d" % (i + 1)
        fx, fy = snap(flag_x + i * 25), snap(flag_y)
        body.append(["symbol", ["lib_id", Q("power:PWR_FLAG")], ["at", fx, fy, 0], ["unit", 1],
                     ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                     ["uuid", Q(uid("sym", ref))],
                     ["property", Q("Reference"), Q(ref), ["at", fx, fy + 3, 0],
                      ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]],
                     ["property", Q("Value"), Q("PWR_FLAG"), ["at", fx, fy - 3, 0],
                      ["effects", ["font", ["size", 1.27, 1.27]]]],
                     ["pin", Q("1"), ["uuid", Q(uid("pin", ref, "1"))]],
                     ["instances", ["project", Q(PROJECT),
                                    ["path", Q("/" + ROOT_UUID), ["reference", Q(ref)], ["unit", 1]]]]])
        body.append(label_for(net, fx, fy, 90))

    sch = ["kicad_sch", ["version", 20250114], ["generator", Q("eeschema")],
           ["generator_version", Q("9.0")], ["uuid", Q(ROOT_UUID)], ["paper", Q("A2")],
           ["title_block", ["title", Q("2 m fox: ESP32-S3 + SX1278")], ["company", Q("KC9MNE")],
            ["comment", 1, Q("Generated by gen_schematic.py - edit the script, not this file")]],
           ["lib_symbols"] + lib_nodes] + body + [
        ["sheet_instances", ["path", Q("/"), ["page", Q("1")]]], ["embedded_fonts", "no"]]

    with open(os.path.join(HERE, PROJECT + ".kicad_sch"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dump(sch) + "\n")

    custom = ["kicad_symbol_lib", ["version", 20241209], ["generator", Q("gen_schematic")],
              ["generator_version", Q("9.0")]] + [custom_symbol(n) for n in CUSTOM]
    with open(os.path.join(HERE, "fox.kicad_sym"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dump(custom) + "\n")
    write_bom()
    print("wrote %d parts" % len(PARTS))


# Parts kept off the JLCPCB assembly BOM and soldered by hand. J2, the edge-launch SMA,
# clamps both sides of the board edge, which JLCPCB only does as extra-cost hand soldering;
# fitting it ourselves also leaves the RF output bare for NanoVNA tuning first.
HAND_SOLDER = {"J2"}


def write_bom():
    """JLCPCB assembly BOM. Parts without an LCSC number (pin headers) and HAND_SOLDER parts
    are left off."""
    groups = {}
    for ref, p in PARTS.items():
        if not p["lcsc"] or ref in HAND_SOLDER:
            continue
        # One row per LCSC number: JLCPCB rejects the same part on two rows (RESET and BOOT
        # are the same switch with different values).
        g = groups.setdefault(p["lcsc"], dict(values=[], fp=p["resolved_footprint"], refs=[]))
        if p["value"] not in g["values"]:
            g["values"].append(p["value"])
        g["refs"].append(ref)
    with open(os.path.join(HERE, "fox_bom_jlc.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("Comment,Designator,Footprint,LCSC Part #\n")
        for lcsc, g in sorted(groups.items(), key=lambda kv: kv[1]["refs"][0]):
            f.write('"%s","%s","%s",%s\n' % ("/".join(g["values"]), ",".join(g["refs"]),
                                              g["fp"].split(":")[-1], lcsc))


if __name__ == "__main__":
    build()
