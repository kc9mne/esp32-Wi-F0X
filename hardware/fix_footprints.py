"""Copy the footprints easyeda2kicad downloaded (lib/fox_lcsc.pretty) into lib/fox.pretty,
renumbering pads to match the schematic symbols and making 3D model paths
project-relative. Reads the downloads untouched, so it is safe to rerun.

Download (once):  easyeda2kicad --footprint --3d --lcsc_id <C...> --output lib/fox_lcsc
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "lib", "fox_lcsc.pretty")
DST = os.path.join(HERE, "lib", "fox.pretty")

# footprint: {downloaded pad number: schematic pin number, "" = mechanical only}
RENUMBER = {
    # Spring (negative) end is pad 1; the "+" mark is at pad 2. Battery_Cell pin 1 is +.
    "BAT-TH_BH-18650-A1AJ005": {"1": "2", "2": "1"},
    # Centre pin is pad 5; pads 1-4 are the ground legs (top and bottom).
    "SMA-SMD_BWSMA-KE-P001": {"5": "1", "1": "2", "2": "2", "3": "2", "4": "2"},
    # Pins 1-8 match Micro_SD_Card; 9 is card detect (unused); 10-13 are the shell.
    "TF-SMD_TF-01A": {"9": "", "10": "9", "11": "9", "12": "9", "13": "9"},
    # Pads 4 are the mounting tabs.
    "SW-TH_MSK12C02": {"4": ""},
    # TS-1187A: A-B (pads 1, 2) and C-D (pads 3, 4) are joined inside; the switch is between.
    "SW-SMD_4P-L5.1-W5.1-P3.70-LS6.5-TL_H1.5": {"1": "1", "2": "1", "3": "2", "4": "2"},
}

# 3D model placement that easyeda2kicad got wrong: (offset xyz, rotate xyz).
# The SMA's model came out with its barrel over the board; this puts the barrel off the
# board edge with the 1.6 mm board between its top and bottom legs.
MODEL_FIX = {
    "SMA-SMD_BWSMA-KE-P001": ((-1.7, 0, 0.9), (0, 90, 180)),
    # The slide switch's model sat 1.4 mm back from its pads, hiding the lever.
    "SW-TH_MSK12C02": ((0, -1.4, 0), (0, 0, 180)),
}

os.makedirs(DST, exist_ok=True)
for name, mapping in RENUMBER.items():
    with open(os.path.join(SRC, name + ".kicad_mod"), encoding="utf-8") as f:
        text = f.read()

    def swap(m):
        num = m.group(2)
        return m.group(1) + '"%s"' % mapping[num] if num in mapping else m.group(0)

    text = re.sub(r'(\(pad )"?([0-9]+)"?', swap, text)
    # Unnumbered holes are locating pegs: make them non-plated with no copper ring.
    text = text.replace('(pad "" thru_hole', '(pad "" np_thru_hole')
    # Board-edge drawings inside a footprint would add to the board outline.
    text = "\n".join(line for line in text.split("\n") if "Edge.Cuts" not in line)
    text = re.sub(r'\(module easyeda2kicad:', "(module fox:", text)
    text = re.sub(r'\(model "[^"]*/(fox_lcsc\.3dshapes/[^"]+)"',
                  r'(model "${KIPRJMOD}/lib/\1"', text)
    if name in MODEL_FIX:
        (ox, oy, oz), (rx, ry, rz) = MODEL_FIX[name]
        text = re.sub(r"\(offset \(xyz [^)]*\)\)", "(offset (xyz %g %g %g))" % (ox, oy, oz), text)
        text = re.sub(r"\(rotate \(xyz [^)]*\)\)", "(rotate (xyz %g %g %g))" % (rx, ry, rz), text)
    with open(os.path.join(DST, name + ".kicad_mod"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote fox:" + name)
