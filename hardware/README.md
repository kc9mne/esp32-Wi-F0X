# 2 m fox board: ESP32-S3 + SX1278

A dedicated board to replace the T-Beam Supreme: same firmware features (phone setup over
Wi-Fi, SD voice clips, MO patterns, Morse ID), 25 mW on 144–148 MHz, one 18650 cell,
target cost about $20 assembled at JLCPCB.

## Files

| File | What it is |
|---|---|
| `gen_schematic.py` | **Source of truth.** Every part and every connection. Edit this, then rerun it. |
| `fox.kicad_sch` | Generated schematic. Parts are connected by net labels on each pin, not drawn wires. |
| `fox.kicad_sym`, `sym-lib-table` | TP4056, FS8205A and TCXO symbols, which the stock KiCad libraries lack. |
| `fox.pdf`, `fox.net` | Exported schematic and netlist. |
| `fox_bom_jlc.csv` | JLCPCB assembly BOM (Comment, Designator, Footprint, LCSC Part #), one row per LCSC number. The OLED header J3 and the edge SMA J2 (C496550, clamps both sides of the edge) are left off and soldered by hand. |
| `check_netlist.py` | Confirms KiCad's netlist matches the script, pin by pin. |
| `rf_calc.py` | Matching-network and low-pass filter calculations quoted below. |
| `gen_board.py`, `autoroute.py`, `fox.kicad_pcb` | Board generator and router (KiCad's Python), and the routed board. |
| `silk.py` | Places reference labels clear of pads, vias and each other (autoroute.py runs it). |
| `fix_footprints.py`, `lib/`, `fp-lib-table` | LCSC footprints and 3D models, pads fixed to match the schematic. |
| `renders/` | 3D renders of the board. |
| `sexp.py` | Small KiCad file reader/writer used by the scripts. |

Regenerate and check:

```
python gen_schematic.py
kicad-cli sch erc fox.kicad_sch
kicad-cli sch export netlist --format kicadsexpr -o fox.net fox.kicad_sch
python check_netlist.py
```

Current state: ERC 0 violations; netlist check 83 nets, 265 pins, 0 problems.

## Blocks

**Power.** USB-C (5.1 k CC pull-downs, USBLC6 ESD) feeds a TP4056 charging the cell at
500 mA (R3 = 2.4 k). DW01A + FS8205A protect the unprotected Samsung 35E against
over-discharge (about 2.4 V), overcharge and short circuit. The slide switch sits after the
charger, so the cell charges with the fox switched off. An ME6211 LDO makes 3.3 V.
Below about 3.4 V the 3.3 V rail starts to sag; the firmware already stops transmitting at
3.3 V.

**Sensing.** VSYS/2 on GPIO 6 for battery voltage (replaces the AXP2101 readings).
VBUS/2 on GPIO 16 to tell when USB is plugged in. Charge state is on the red/green LEDs.

**ESP32-S3-WROOM-1-N8.** Radio, SD and OLED use the same GPIOs as the T-Beam Supreme, so
those parts of the firmware carry over unchanged:

| Function | GPIO |
|---|---|
| Radio SCK / MISO / MOSI / NSS | 12 / 13 / 11 / 10 |
| Radio RESET / DIO0 / DIO1 / DIO2 | 5 / 2 / 1 / 4 |
| SD SCK / MISO / MOSI / CS | 36 / 37 / 35 / 47 |
| OLED SDA / SCL | 17 / 18 |
| Battery sense / USB sense / status LED | 6 / 16 / 7 |
| BOOT button | 0 |

GPIO 35–37 are free only because the N8 module has no octal PSRAM; don't substitute an
N8R8 or N16R8 module.

**Radio.** SX1278 with an Epson TG2520SMN 32 MHz clipped-sine TCXO (±0.5 ppm, about
±75 Hz at 146 MHz) AC-coupled into XTA through 10 nF, XTB open. TCXO pin 1 is grounded:
Epson says it must be open or grounded. The firmware must set `RegTcxo` (0x4B) bit 4
before starting the radio, and switch output from PA_BOOST to the RFO pin.

**OLED firmware change.** The T-Beam has a 1.3 in SH1106; 0.96 in modules are usually
SSD1306, so the U8g2 setup call changes from `sh1106` to `ssd1306` (otherwise the image
is shifted by 2 pixels). Read upright, the module's header is at the top of the image,
which here is the ESP32 end. If the fox hangs cap end up, use `U8G2_R2` to turn the image.

The FS8205A is the SOT-23-6 version (the TSSOP-8 one is out of stock). Its pinout is from
the Tech Public datasheet: 1 S1, 2 D, 3 S2, 4 G2, 5 D, 6 G1.

## RF network

Semtech AN1200.19 Figure 6, the single-path RFO_LF/RFI_LF design: transmit and receive
share one port, so no RF switch is needed. Semtech warns against combining PA_BOOST with
RFI this way, which is why output is the RFO pin at up to +14 dBm (25 mW) rather than
PA_BOOST at +17 dBm.

Every value is Semtech's 169 MHz BOM scaled by 169/146 and rounded to a standard value:

| Semtech | 169 MHz | This board | 146 MHz | Role |
|---|---|---|---|---|
| Lp | 100 nH | L1 | 120 nH | PA bias feed from VR_PA |
| Cd | 220 pF | C20 | 270 pF | VR_PA decoupling (+ C21 10 nF) |
| C1 | 15 pF | C22 | 18 pF | shunt at the RF pin |
| C10 | 5.6 pF | C23 | 6.8 pF | RX branch |
| L0 | 82 nH | L2 | 91 nH | RX branch to ground |
| L1 | 56 nH | L3 | 68 nH | series match |
| Cc | 220 pF | C24 | 270 pF | DC block |
| C2 / C3 | 33 pF | C25 / C26 | 39 pF | low-pass |
| L2 | 47 nH | L4 | 56 nH | low-pass |
| L3 | 33 nH | L5 | 39 nH | low-pass |
| C4 | 8.2 pF | C27 | 9.1 pF | low-pass |

Calculated with ideal parts (`rf_calc.py`):

- Impedance the network presents to the RF pin: 47.4 − j30.6 Ω at 146 MHz, against
  52.7 − j28.0 Ω for Semtech's own values at 169 MHz. Semtech's table gives the optimum as
  47.8 + j28.9, the conjugate, so the scaled network lands in the same place.
- Across 144–148 MHz it moves only from 51 to 44 Ω real, so one build covers the band.

After C27 there is a 0 Ω link (R19) and a second 5-pole 0.1 dB Chebyshev low-pass
(22 pF – 68 nH – 39 pF – 68 nH – 22 pF, cutoff about 170 MHz) in front of the SMA:

| | 146 MHz | 2nd (292) | 3rd (438) | 4th (584) |
|---|---|---|---|---|
| Semtech low-pass | 0.34 dB | 20.9 dB | 39.7 dB | 52.9 dB |
| Both filters | 0.4 dB | 50.4 dB | 88.9 dB | 115 dB |

That is filter attenuation only, on top of whatever the chip's own harmonics are.
§97.307(e) asks for spurious emissions at least 40 dB below the carrier on 2 m at this
power. Real inductors lose some of that at high harmonics because of self-resonance.

## Tuning the first board

The scaled values are a starting point: the chip's port impedance doesn't scale exactly
with frequency, and board layout adds its own reactance. Plan on adjusting part values.

1. Lift R19 and sweep the second filter alone on the NanoVNA (SMA to a probe at R19's pad).
   Expect under 0.5 dB loss at 146 MHz and a return loss better than 15 dB.
2. Refit R19. With the radio transmitting a carrier at +14 dBm, read output power and
   harmonics on the service monitor through an attenuator.
3. If power is low, adjust C22 (shunt) and L3 (series) first; they set the match.
4. Check receive later, when the control link is built.

## Cost

LCSC prices at about 50 pieces, checked 2026-11-02:

| | Per board |
|---|---|
| ESP32-S3-WROOM-1-N8 | $3.83 |
| SX1278IMLTRT | $3.52 |
| TCXO, charger, protection, LDO, ESD | about $0.75 |
| USB-C, microSD, SMA, 18650 holder, switches | about $0.90 |
| RF inductors and capacitors (Murata, ±2 %) | about $0.60 |
| Other passives and LEDs | about $0.20 |
| **Parts** | **about $9.80** |

JLCPCB adds a one-off loading fee per extended part type (about $3 each, roughly 27 types
here), plus setup, stencil and the bare PCBs. Spread over 10 boards that is roughly $10 more
each, so about $20 a board; over 30 boards about $14. Those fees are from memory, so check
JLC's quote. The OLED (about $2–3) and the cell are extra.

Stock to watch: the ESP32-S3-WROOM-1-N8 had only 395 at LCSC and 1215 at JLC.

## Board

`fox.kicad_pcb` is built in two steps, both run with KiCad's own Python
(`"C:\Program Files\KiCad\9.0\bin\python.exe"`):

1. `gen_board.py`: outline, every part placed with its nets, ground pours, and the
   hand-routed parts (RF section, USB-C escape, the radio's 3.3 V pin).
2. `autoroute.py`: first ties every ground pad in the digital half (x > 48) together with
   0.2 mm ground traces, so later signal traces can't fence a ground pad off on both
   layers; then routes everything else, adds ground stitching vias (kept 0.3 mm
   hole-to-hole from other holes), joins any cut-off pieces of ground pour, fills the
   pours and places the labels (`silk.py`). Takes several minutes.

Labels are 0.8 mm text (1 mm on big parts), JLCPCB's minimum. Where there is no clear spot
next to a part (the tight RF and radio area, and U1 under its own module), the label goes
on the Fab layer only: it shows on the assembly drawing but isn't printed.

**It is fully routed.** Rules check: 0 errors, 0 unconnected, and the board matches the
schematic exactly (`kicad-cli pcb drc --schematic-parity`). Clearance is 0.15 mm, tracks
0.2 mm (power 0.3–0.6 mm), vias 0.6/0.3 mm (0.5/0.3 at the USB-C), all within JLCPCB's
standard 2-layer process.

The autorouter is a simple maze router written for this board, so its traces wander more
than a person's would. Electrically that doesn't matter at these speeds (SPI, I2C, USB
full-speed), but tidy anything you care about in the PCB editor. It keeps the bottom layer
unbroken under the RF chain and stays out of the ESP32 antenna keepout.

**RF routing (done, in `ROUTES` in `gen_board.py`).** The matching parts sit in a line
right at the SX1278's RF pin (pin 28), series parts on the line and each shunt capacitor
hanging below it with its own ground via. The long run is the 1.0 mm line from the SMA to
the filter, with stitching vias both sides and vias in the SMA's ground legs. Also routed:
the RX branch to RFI_LF (pin 1), the VR_PA bias feed (L1, C20, C21), the TCXO through C19
to XTA, and the radio's ground pins into its exposed pad. At 146 MHz the longest trace is
about 0.02 wavelength, so keeping it short over solid ground matters more than its exact
impedance.

C16 and C17 (VR_ANA, VR_DIG decoupling) sit straight out from pins 2 and 4, with L2
moved up beside C23 to make room.

**USB-C (hand-routed).** The receptacle's pins are 0.5 mm apart and D+ and D- each appear
twice (A and B rows, for a reversible plug). D+ joins on top, threading between two D-
vias; D- joins on the bottom. CC1 and CC2 run straight out to their 5.1 k resistors.

- **34 × 110 mm, 2 layers.** All parts on top except the 18650 holder, which is on the
  bottom. Its two pins (x = 10 and 80) and centre peg hole come up through the board.
- **Cap end (x = 0):** SMA, slide switch and USB-C, reachable with the cap off.
- **Far end:** the ESP32 module, its Wi-Fi antenna flush with the board end and past the
  end of the battery. The footprint carries its own no-copper zone in front of the antenna.
- **RF chain** along the top edge from the SMA to the SX1278, in schematic order.
- **Tube fit (37.6 mm inside):** mount the board with its top face about 4–5 mm above the
  tube's centre line, so the holder (18.3 mm deep, 20.95 mm wide) and cell clear below.
  That leaves about 8.5 mm above the board, so the OLED must be mounted low (pins soldered
  straight through, short spacer), not on a normal female header. A 1.3 in OLED won't fit.
  The SMA is 10 mm off-centre, so the antenna hole in the cap is too.
- **Slide switch:** its lever sticks 1.5 mm past the cap-end edge (the part's full lever
  length), so it can be switched with the cap off.
- **microSD:** the slot opens at the top long edge (y = 0), so a card goes in and out with
  the board slid out of the tube.
- **OLED (J3):** a 0.96 in I2C module (27.3 × 27.8 mm), landscape across the board like the
  T-Beam's display. Its header runs across the board on the centre line at x = 75.4, and the
  module reaches back toward the cap end over the SD socket and small parts (x ≈ 49–77).
  Nothing under it needs pressing or seeing. RESET and BOOT (SW2, SW3) sit beside it at
  x = 45, the status LED beyond its header end.
- **No UART header.** Flashing and the serial console go over the USB-C port (the
  ESP32-S3's built-in USB-Serial-JTAG). A pin header near the long edge would also hit
  the tube wall.

Footprints for the holder, SMA, microSD, slide switch and tact switches come from LCSC's
EasyEDA library via `easyeda2kicad` (`lib/fox_lcsc.pretty`, with 3D models).
`fix_footprints.py` copies them to `lib/fox.pretty`, renumbering pads to match the
schematic (battery + and −, SMA centre pin, SD shell, tact switch pairs; each checked
against the datasheet) and making locating holes non-plated.

Renders are in `renders/` (`kicad-cli pcb render`). The SMA's downloaded 3D model was
backwards; `fix_footprints.py` corrects it so the barrel sits off the cap end with the board
between its legs (the footprint itself was always right). The USB-C connector has no 3D model in
this KiCad install, so it shows as bare pads.

### Ordering from JLCPCB

Install the **JLCPCB Tools** plugin (Plugin and Content Manager). It writes the Gerbers,
drill file, BOM and placement file; the LCSC numbers come from the schematic. The holder
is through-hole on the bottom: solder its two pins by hand from the top.
