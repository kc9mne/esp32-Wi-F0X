"""Check the Semtech AN1200.19 RFO_LF/RFI_LF single-path network, original 169 MHz
values versus values scaled to 2 m. Ideal parts; RFI_LF treated as open during TX."""
import cmath
import math

R0 = 50.0


def zl(l, f):
    return 1j * 2 * math.pi * f * l


def zc(c, f):
    return 1 / (1j * 2 * math.pi * f * c)


def par(*zs):
    return 1 / sum(1 / z for z in zs)


def ladder(v, f, load=R0):
    """Impedance looking from the RFO node toward the antenna (through L1, Cc, LPF)."""
    z = load
    z = par(z, zc(v["C4"], f))
    z = z + zl(v["L3"], f)
    z = par(z, zc(v["C3"], f))
    z = z + zl(v["L2"], f)
    z = par(z, zc(v["C2"], f))
    z = z + zc(v["Cc"], f) + zl(v["L1"], f)
    return z


def at_pin(v, f):
    shunt_lp = zl(v["Lp"], f) + zc(v["Cd"], f)
    shunt_rx = zc(v["C10"], f) + zl(v["L0"], f)
    return par(ladder(v, f), zc(v["C1"], f), shunt_lp, shunt_rx)


def abcd_series(z):
    return (1, z, 0, 1)


def abcd_shunt(z):
    return (1, 0, 1 / z, 1)


def mul(a, b):
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3])


def filter_loss_db(v, f):
    """Insertion loss of the C2-L2-C3-L3-C4 low-pass alone between 50 ohm ends."""
    m = (1, 0, 0, 1)
    for el in (abcd_shunt(zc(v["C2"], f)), abcd_series(zl(v["L2"], f)),
               abcd_shunt(zc(v["C3"], f)), abcd_series(zl(v["L3"], f)),
               abcd_shunt(zc(v["C4"], f))):
        m = mul(m, el)
    a, b, c, d = m
    s21 = 2 / (a + b / R0 + c * R0 + d)
    return -20 * math.log10(abs(s21))


def transfer_db(v, f, zs):
    """Power into the 50 ohm antenna relative to the power available from a source
    whose impedance is the conjugate-matched optimum (i.e. mismatch + harmonic loss)."""
    zin = at_pin(v, f)
    gamma = (zin - zs.conjugate()) / (zin + zs)
    return 10 * math.log10(1 - abs(gamma) ** 2), zin


p, n = 1e-12, 1e-9
orig = dict(Cc=220 * p, Cd=220 * p, C1=15 * p, C2=33 * p, C3=33 * p, C4=8.2 * p, C10=5.6 * p,
            L0=82 * n, Lp=100 * n, L1=56 * n, L2=47 * n, L3=33 * n)
scaled = dict(Cc=270 * p, Cd=270 * p, C1=18 * p, C2=39 * p, C3=39 * p, C4=9.1 * p, C10=6.8 * p,
              L0=91 * n, Lp=120 * n, L1=68 * n, L2=56 * n, L3=39 * n)

z169 = 47.8 + 28.9j
for name, vals, f0 in (("169 MHz original", orig, 169e6), ("2 m scaled", scaled, 146e6)):
    print("==", name)
    zin = at_pin(vals, f0)
    print("  Z at RFO_LF pin at %.0f MHz: %.1f %+.1fj  (Semtech optimum at 169: 47.8 +28.9j)"
          % (f0 / 1e6, zin.real, zin.imag))
    for f in (144e6, 146e6, 148e6, 169e6):
        print("  LPF loss %5.0f MHz: %5.2f dB" % (f / 1e6, filter_loss_db(vals, f)))
    for h in (2, 3, 4, 5):
        f = h * 146e6
        print("  LPF loss  H%d %4.0f MHz: %5.1f dB" % (h, f / 1e6, filter_loss_db(vals, f)))

def lpf2_loss_db(f, c_end=22e-12, l=68e-9, c_mid=39e-12):
    """Extra 5-pole 50 ohm Chebyshev low-pass in front of the SMA."""
    m = (1, 0, 0, 1)
    for el in (abcd_shunt(zc(c_end, f)), abcd_series(zl(l, f)), abcd_shunt(zc(c_mid, f)),
               abcd_series(zl(l, f)), abcd_shunt(zc(c_end, f))):
        m = mul(m, el)
    a, b, c, d = m
    return -20 * math.log10(abs(2 / (a + b / R0 + c * R0 + d)))


print("== added 5-pole LPF 22p-68n-39p-68n-22p, and both filters together")
for f in (144e6, 146e6, 148e6, 292e6, 438e6, 584e6, 730e6):
    extra = lpf2_loss_db(f)
    print("  %4.0f MHz: added %5.2f dB, Semtech LPF + added %5.1f dB"
          % (f / 1e6, extra, extra + filter_loss_db(scaled, f)))

# Sweep: how far does the scaled network move across 144-148 MHz
print("== scaled pin impedance across the band")
for f in (144e6, 145e6, 146e6, 147e6, 148e6):
    z = at_pin(scaled, f)
    print("  %5.1f MHz: %6.1f %+6.1fj" % (f / 1e6, z.real, z.imag))
