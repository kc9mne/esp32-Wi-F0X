"""Tiny s-expression reader/writer for KiCad files."""
import re

TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+)')


class Q(str):
    """A string that must be written quoted."""


def parse(text):
    stack = [[]]
    pos = 0
    while True:
        m = TOKEN.match(text, pos)
        if not m:
            break
        tok = m.group(1)
        pos = m.end()
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(Q(tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")))
        else:
            stack[-1].append(tok)
    return stack[0]


def dump(node, indent=0):
    if isinstance(node, list):
        if not node:
            return "()"
        simple = all(not isinstance(x, list) for x in node)
        if simple:
            return "(" + " ".join(dump(x) for x in node) + ")"
        out = "(" + dump(node[0])
        for x in node[1:]:
            if isinstance(x, list):
                out += "\n" + "\t" * (indent + 1) + dump(x, indent + 1)
            else:
                out += " " + dump(x)
        return out + "\n" + "\t" * indent + ")"
    if isinstance(node, Q):
        return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(node)


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def load_symbol(lib_path, name):
    """Return the top-level (symbol name ...) node from a .kicad_sym library."""
    with open(lib_path, encoding="utf-8") as f:
        text = f.read()
    start = text.find('\n\t(symbol "%s"\n' % name)
    if start < 0:
        raise KeyError(name)
    depth = 0
    i = start + 1
    while True:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return parse(text[start + 1:i + 1])[0]


def pins(sym):
    """Yield (number, name, x, y, angle, length, etype) for every pin in a symbol node."""
    for unit in find(sym, "symbol"):
        for p in find(unit, "pin"):
            at = first(p, "at")
            num = first(p, "number")[1]
            name = first(p, "name")[1]
            length = float(first(p, "length")[1])
            yield (str(num), str(name), float(at[1]), float(at[2]), int(float(at[3])), length, p[1])
