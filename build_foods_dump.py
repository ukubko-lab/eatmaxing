"""Doplni foods.json z oficialniho exportu Open Food Facts (CSV, ODbL) - bez API, takze bez limitu dotazu.
Stahuje se proudove (cca 1 GB), filtruje se jen Ceska republika. Pouziti: python build_foods_dump.py [cesta_nebo_url]"""
import csv, gzip, io, json, pathlib, sys, urllib.request

URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
UA = "EatMaxing/1.0 (osobni kaloricke tabulky)"
OUT = pathlib.Path("foods.json")
csv.field_size_limit(10**9)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def convert(r):
    name = (r.get("product_name_cs") or r.get("product_name") or "").strip()
    kcal, prot, fat, carb = (num(r.get(k)) for k in
                             ("energy-kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g"))
    if not (2 <= len(name) <= 80) or None in (kcal, prot, fat, carb):
        return None
    if not (0 <= kcal <= 950) or min(prot, fat, carb) < 0 or prot + fat + carb > 105:
        return None
    brand = (r.get("brands") or "").split(",")[0].strip()
    return {"n": name, "b": brand[:40], "k": round(kcal), "p": round(prot, 1), "t": round(fat, 1),
            "s": round(carb, 1), "f": round(num(r.get("fiber_100g")) or 0, 1), "c": r.get("code", "")}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else URL
    raw = urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": UA}), timeout=120) \
        if src.startswith("http") else open(src, "rb")
    out, seen = [], set()
    if OUT.exists():
        for it in json.loads(OUT.read_text("utf-8")):
            k = (it["n"].lower(), it.get("b", "").lower())
            if k not in seen:
                seen.add(k); out.append(it)
    print("na zacatku:", len(out))
    rd = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=raw), encoding="utf-8", errors="replace", newline=""),
                        delimiter="\t")
    n = 0
    for r in rd:
        n += 1
        if "czech-republic" not in (r.get("countries_tags") or ""):
            continue
        it = convert(r)
        if it:
            k = (it["n"].lower(), it["b"].lower())
            if k not in seen:
                seen.add(k); out.append(it)
        if n % 500000 == 0:
            print("zpracovano radku:", n, "v databazi:", len(out), flush=True)
    if len(out) >= 200:
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
        print("ulozeno", len(out), "produktu")


if __name__ == "__main__":
    main()
