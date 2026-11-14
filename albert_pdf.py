"""Cteni akcniho letaku Albert z PDF: najde cenovky (font Draft22) a spoji je s nejblizsim nazvem produktu."""
import re, math
import pdfplumber

PRICE_FONT = "Draft22"
NAME_FONT = "BoldCondensed"
REG_FONT = "RegularCondensed"
MEAS = re.compile(r"(?:\d+\s?[×x]\s?)?\d+(?:[,.]\d+)?\s?(?:kg|g|ml|l)\b", re.I)
PRICE_WORD = re.compile(r"^(\d{1,4})(?:,(\d{2}|-))?[/,]?-?/?$")


def _num(s):
    return float(s.replace(",", "."))


def _group_lines(words, tol=2.5, gap=14):
    """Slova stejne fontove tridy slouci do radku (podle top a vzdalenosti)."""
    words = sorted(words, key=lambda w: (round(w["top"] / tol), w["x0"]))
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for ln in lines:
            if abs(ln["top"] - w["top"]) <= tol and 0 <= w["x0"] - ln["x1"] <= gap:
                ln["text"] += " " + w["text"]
                ln["x1"] = w["x1"]
                ln["bottom"] = max(ln["bottom"], w["bottom"])
                break
        else:
            lines.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"],
                          "bottom": w["bottom"], "size": w["size"]})
    return lines


def _merge_blocks(lines, xtol=6):
    """Radky pod sebou se stejnym x0 slouci do bloku (viceradkovy nazev)."""
    blocks = []
    for ln in sorted(lines, key=lambda l: (l["x0"], l["top"])):
        for b in blocks:
            if abs(b["x0"] - ln["x0"]) <= xtol and -2 <= ln["top"] - b["bottom"] <= ln["size"] * 0.9:
                b["text"] += " " + ln["text"]
                b["bottom"] = ln["bottom"]
                b["x1"] = max(b["x1"], ln["x1"])
                break
        else:
            blocks.append(dict(ln))
    return blocks


def _rect_gap(w, m):
    """Svisla vzdalenost slova w od hlavni ceny m: nad ni (slovo je nad cenou), pod ni, nebo None kdyz nesedi."""
    if w["x1"] < m["x0"] - 45 or w["x0"] > m["x1"] + 70:
        return None
    if w["bottom"] <= m["top"] + 6:
        return m["top"] - w["bottom"]
    if w["top"] >= m["bottom"] - 6:
        return w["top"] - m["bottom"]
    return None


def _tags(price_words, cutoff):
    """Cenovky: najde hlavni ceny (nejvetsi pismo) a k nim prida nejblizsi dalsi ceny (nad a pod hlavni cenou)."""
    used, mains = set(), []
    for w in sorted(price_words, key=lambda w: -w["size"]):
        if w["size"] < cutoff or id(w) in used:
            continue
        m = re.fullmatch(r"(\d{1,4})(,?)(-?)", w["text"])
        if not m:
            continue
        dec = None
        if not m.group(2) and not m.group(3):
            dec = next((x for x in price_words if id(x) not in used and re.fullmatch(r"\d{2}", x["text"])
                        and x["size"] < w["size"] and 0 <= x["x0"] - w["x1"] < 15
                        and abs(x["top"] - w["top"]) < w["size"]), None)
        price = _num(f"{m.group(1)}.{dec['text']}") if dec else float(m.group(1))
        box = [w] + ([dec] if dec else [])
        used.add(id(w))
        if dec:
            used.add(id(dec))
        mains.append({"p": price, "x0": min(b["x0"] for b in box), "x1": max(b["x1"] for b in box),
                      "top": min(b["top"] for b in box), "bottom": max(b["bottom"] for b in box),
                      "alts": [price], "app": False, "box": list(box)})
    # ostatni ceny (preskrtnuta, bezna, bez aplikace)
    rest = [w for w in price_words if id(w) not in used]
    extras = []                                           # (hodnota, slovo)
    consumed = set()
    for w in rest:
        t = w["text"]
        mm = re.fullmatch(r"(\d{1,4}),(\d{2}|-)?/?", t)
        if mm:
            val = _num(f"{mm.group(1)}.{mm.group(2)}") if mm.group(2) and mm.group(2) != "-" else float(mm.group(1))
            extras.append((val, w))
            consumed.add(id(w))
    for w in rest:
        if id(w) in consumed or not re.fullmatch(r"\d{1,4}", w["text"]):
            continue
        d = next((x for x in rest if id(x) not in consumed and re.fullmatch(r"\d{2}", x["text"]) and x is not w
                  and 0 <= x["x0"] - w["x1"] < 15 and abs(x["top"] - w["top"]) < w["size"] and x["size"] <= w["size"]), None)
        if d is not None:
            consumed.add(id(w))
            consumed.add(id(d))
            merged = dict(w)
            merged["x1"] = d["x1"]
            extras.append((_num(f"{w['text']}.{d['text']}"), merged))
    for val, w in extras:
        best, bd = None, 50
        for m in mains:
            g = _rect_gap(w, m)
            if g is not None and g < bd:
                best, bd = m, g
        if best is not None:
            best["alts"].append(val)
            best["box"].append(w)
            if w["top"] >= best["bottom"] - 6:
                best["app"] = True
    out = []
    for m in mains:
        others = [a for a in m["alts"][1:]]
        bigger = [o for o in others if o > m["p"]]
        xs0 = min(b["x0"] for b in m["box"])
        xs1 = max(b["x1"] for b in m["box"])
        ys0 = min(b["top"] for b in m["box"])
        ys1 = max(b["bottom"] for b in m["box"])
        out.append({"p": m["p"], "o": max(bigger) if bigger else None, "alts": m["alts"],
                    "cx": (xs0 + xs1) / 2, "cy": (ys0 + ys1) / 2, "app": m["app"]})
    return out


def _discount_ids(words):
    """Slova tvoriaci slevu '-40 %': cislo a '-' vlevo od znaku %."""
    ids = set()
    for pct in (w for w in words if w["text"] == "%"):
        for w in words:
            if (w is not pct and abs(w["top"] - pct["top"]) < pct["size"] and 0 <= pct["x0"] - w["x1"] < 8
                    and re.fullmatch(r"\d{1,2}", w["text"])):
                ids.add(id(w))
                for m in words:
                    if m["text"] == "-" and abs(m["x1"] - w["x0"]) < 10 and abs(m["top"] - w["top"]) < w["size"]:
                        ids.add(id(m))
    return ids


def _qty(desc):
    """Velikost baleni v g/ml z popisu ('150 g', '2× 100 g', '0,5 l', '1 kg'), nebo None."""
    d = desc.split("=")[0] if "=" in desc else desc
    m = re.search(r"(\d+)\s?[×x]\s?(\d+(?:[,.]\d+)?)\s?(kg|g|ml|l)\b", d)
    if m:
        n, q, u = int(m.group(1)), _num(m.group(2)), m.group(3)
    else:
        m = re.search(r"(\d+(?:[,.]\d+)?)\s?(kg|g|ml|l)\b", d)
        if not m:
            return None
        n, q, u = 1, _num(m.group(1)), m.group(2)
    return n * q * (1000 if u in ("kg", "l") else 1)


def _expected(desc):
    """Ocekavane ceny balení z jednotkove ceny v popisu ('100 g = 7,94 Kč bez Aplikace / 6,60 Kč Aplikace')."""
    qty = _qty(desc)
    m = re.search(r"(\d+)\s?(kg|g|ml|l)\s?=\s?([^•▼]*)", desc)
    if not qty or not m:
        return []
    unit = int(m.group(1)) * (1000 if m.group(2) in ("kg", "l") else 1)
    vals = [_num(x) for x in re.findall(r"(\d+(?:,\d+)?)\s?Kč", m.group(3))]
    return [v * qty / unit for v in vals]


def _consistent(tag, exp):
    return any(abs(a - e) <= max(0.6, 0.03 * e) for a in tag["alts"] for e in exp)


def parse_page(page):
    words = page.extract_words(extra_attrs=["fontname", "size"])
    price_words = [w for w in words if PRICE_FONT in w["fontname"]]
    disc = _discount_ids(price_words)
    price_words = [w for w in price_words if id(w) not in disc]
    bold = [w for w in words if NAME_FONT in w["fontname"] and w["size"] >= 9
            and not re.fullmatch(r"\d+|ks|bal\.?|NAVÍC|CENA|\+1", w["text"])]
    if not bold or not price_words:
        return []
    sizes = {}
    for w in bold:
        sizes[round(w["size"], 1)] = sizes.get(round(w["size"], 1), 0) + 1
    ns = max(sizes, key=sizes.get)                      # typicka velikost nazvu produktu na teto strane
    names = [w for w in bold if abs(w["size"] - ns) <= 1.5]
    regs = [w for w in words if REG_FONT in w["fontname"] and 0.5 * ns <= w["size"] <= 0.85 * ns]
    maxp = max(w["size"] for w in price_words)
    blocks = _merge_blocks(_group_lines(names))
    reg_lines = _group_lines(regs, gap=10)
    for b in blocks:
        desc, last = [], b["bottom"]
        for ln in sorted((l for l in reg_lines if abs(l["x0"] - b["x0"]) <= 6 and l["top"] >= b["bottom"] - 2),
                         key=lambda l: l["top"]):
            if ln["top"] - last > ns * 0.9:
                break
            desc.append(ln["text"])
            last = ln["bottom"]
        b["desc"] = " ".join(desc)
    tags = _tags(price_words, 0.5 * maxp)
    import numpy as np
    try:
        from scipy.optimize import linear_sum_assignment
    except Exception:
        linear_sum_assignment = None
    if not tags or not blocks:
        return []
    BIG = 1e6
    C = np.full((len(tags), len(blocks)), BIG)
    D = np.zeros((len(tags), len(blocks)))
    for i, t in enumerate(tags):
        for j, b in enumerate(blocks):
            d = math.hypot(t["cx"] - (b["x0"] + b["x1"]) / 2, t["cy"] - (b["top"] + b["bottom"]) / 2)
            cost = d + (160 if b["x0"] > t["cx"] - 4 else 0)
            exp = _expected(b["desc"])
            if exp:
                cost += -90 if _consistent(t, exp) else 70
            D[i, j] = d
            C[i, j] = cost
    res = []
    if linear_sum_assignment is not None:
        rows, cols = linear_sum_assignment(C)
        for i, j in zip(rows, cols):
            if C[i, j] <= 330:
                res.append((tags[i], blocks[j], D[i, j]))
    else:
        order = sorted(((C[i, j], i, j) for i in range(len(tags)) for j in range(len(blocks))))
        ui, uj = set(), set()
        for c, i, j in order:
            if c <= 330 and i not in ui and j not in uj:
                ui.add(i)
                uj.add(j)
                res.append((tags[i], blocks[j], D[i, j]))
    return res


def clean_name(b):
    name = re.sub(r"\s+", " ", b["text"]).strip()
    m = MEAS.search(b.get("desc", ""))
    return f"{name} {m.group(0)}".strip() if m else name


def parse_pdf(path, pages=None):
    """Vrati produkty z letaku. Titulni strana (kde jsou nazvy a cenovky ve volnem rozlozeni) se bere az nakonec
    a jen pro produkty, ktere se jinde nevyskytuji."""
    deals, seen, cover = [], set(), []
    with pdfplumber.open(path) as pdf:
        valid = None
        for i, page in enumerate(pdf.pages):
            if pages and i not in pages:
                continue
            if i == 0:
                m = re.search(r"do\s+(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", page.extract_text() or "")
                if m:
                    valid = f"do {int(m.group(1))}.{int(m.group(2))}."
            for t, b, d in parse_page(page):
                item = {"page": i + 1, "n": clean_name(b), "p": t["p"], "o": t["o"], "d": round(d), "app": t["app"]}
                if i == 0:
                    cover.append(item)
                    continue
                key = item["n"].lower()
                if key in seen:
                    continue
                seen.add(key)
                deals.append(item)
    for item in cover:
        key = item["n"].lower()
        if key not in seen and item["d"] <= 110:
            seen.add(key)
            deals.append(item)
    for d in deals:
        d["v"] = valid
    return deals


if __name__ == "__main__":
    import sys
    for x in parse_pdf(sys.argv[1], {int(a) - 1 for a in sys.argv[2:]} or None):
        print(x)
