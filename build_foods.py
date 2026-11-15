"""Stahne ceske produkty z Open Food Facts a ulozi foods.json pro kaloricke tabulky v aplikaci.

Zdroj: Open Food Facts (https://world.openfoodfacts.org), data jsou pod licenci ODbL, je potreba uvest zdroj.
Pouziti:  python build_foods.py [pocet_stran]     (jedna strana = 100 produktu, vychozi 60 = az 6000 produktu)

Format foods.json: [{"n": nazev, "b": znacka, "k": kcal, "p": bilkoviny, "t": tuky, "s": sacharidy, "f": vlaknina, "c": carovy kod}]
vse na 100 g. OFF doporucuje max. 10 dotazu za minutu na vyhledavani, proto mezi stranami cekame."""
import json, sys, time, pathlib, urllib.request, urllib.parse, urllib.error

API = "https://world.openfoodfacts.org/api/v2/search"
UA = "EatMaxing/1.0 (osobni kaloricke tabulky; kontakt: viz repozitar)"
FIELDS = "code,product_name,product_name_cs,brands,nutriments"
OUT = pathlib.Path("foods.json")


def fetch(page, retries=4):
    q = urllib.parse.urlencode({"countries_tags_en": "czech-republic", "fields": FIELDS, "page_size": 100,
                                "page": page, "sort_by": "unique_scans_n"})
    for i in range(retries):
        try:
            req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            wait = 45 * (i + 1) if e.code in (429, 503) else 20
            print(f"  strana {page}: HTTP {e.code}, cekam {wait} s")
            time.sleep(wait)
        except Exception as e:
            print(f"  strana {page}: {e}, cekam 10 s")
            time.sleep(10)
    return None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def convert(p):
    n = p.get("nutriments") or {}
    name = (p.get("product_name_cs") or p.get("product_name") or "").strip()
    kcal, prot, fat, carb = (num(n.get(k)) for k in
                             ("energy-kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g"))
    if not (2 <= len(name) <= 80) or None in (kcal, prot, fat, carb):
        return None
    fiber = num(n.get("fiber_100g")) or 0
    if not (0 <= kcal <= 950) or min(prot, fat, carb) < 0 or prot + fat + carb > 105:
        return None
    brand = (p.get("brands") or "").split(",")[0].strip()
    return {"n": name, "b": brand[:40], "k": round(kcal), "p": round(prot, 1), "t": round(fat, 1),
            "s": round(carb, 1), "f": round(fiber, 1), "c": p.get("code", "")}


def main():
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    out, seen = [], set()
    if OUT.exists():                                      # pridava se k tomu, co uz v databazi je
        try:
            for item in json.loads(OUT.read_text("utf-8")):
                key = (item["n"].lower(), item.get("b", "").lower())
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        except Exception:
            pass
    print("na zacatku v databazi:", len(out))
    fails = 0
    for page in range(1, pages + 1):
        data = fetch(page)
        if data is None:                                  # OFF docasne odmita, zkusime dalsi stranu
            fails += 1
            print(f"strana {page}: preskakuji (selhani po sobe: {fails})")
            if fails >= 3:
                print("OFF ted odmita dotazy, koncim. Spust znovu pozdeji, databaze se doplni.")
                break
            time.sleep(60)
            continue
        fails = 0
        if not data.get("products"):
            print(f"strana {page}: konec dat")
            break
        added = 0
        for p in data["products"]:
            item = convert(p)
            key = item and (item["n"].lower(), item["b"].lower())
            if item and key not in seen:
                seen.add(key)
                out.append(item)
                added += 1
        print(f"strana {page}: +{added} (celkem {len(out)})")
        time.sleep(12)
    if len(out) >= 200:
        OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
        print("ulozeno", len(out), "produktu do", OUT)
    else:
        print("prilis malo produktu, foods.json se nemeni")


if __name__ == "__main__":
    main()
