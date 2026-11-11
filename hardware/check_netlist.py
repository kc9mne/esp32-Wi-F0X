"""Compare the netlist KiCad exported (fox.net) with the connections in gen_schematic.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gen_schematic import PARTS  # noqa: E402
from sexp import find, first, parse  # noqa: E402

with open(os.path.join(HERE, "fox.net"), encoding="utf-8") as f:
    tree = parse(f.read())[0]

got = {}
for net in find(first(tree, "nets"), "net"):
    name = str(first(net, "name")[1]).lstrip("/")
    for node in find(net, "node"):
        ref = str(first(node, "ref")[1])
        if ref.startswith("#"):
            continue
        got[(ref, str(first(node, "pin")[1]))] = name

problems = 0
for ref, p in PARTS.items():
    for pin, want in p["pins"].items():
        have = got.get((ref, pin))
        if want is None:
            if have and not have.startswith("unconnected-"):
                print("%s.%s should be unconnected, is on %s" % (ref, pin, have))
                problems += 1
        elif have != want:
            print("%s.%s expected %s, KiCad has %s" % (ref, pin, want, have))
            problems += 1

nets = {}
for (ref, pin), name in got.items():
    nets.setdefault(name, []).append("%s.%s" % (ref, pin))
for name, nodes in sorted(nets.items()):
    if len(nodes) == 1 and not name.startswith("unconnected-"):
        print("single-pin net %s: %s" % (name, nodes[0]))
        problems += 1
print("%d nets, %d pins checked, %d problems" % (len(nets), len(got), problems))
