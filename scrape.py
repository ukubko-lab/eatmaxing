"""Stahne akce z letaku Albert, Lidl, Billa a zapise deals.json.

Spousti se kazdy den v GitHub Actions, ale data skutecne obnovi jen tehdy, kdyz jsou
starsi nez ~2 dny (nebo kdyz se pusti s --force). Kdyz jeden obchod selze, zustanou jeho stara data.

Skript zacne na uvodni strance obchodu, najde odkazy na akce/letaky/nabidky, projde je
a z kazde stranky vytahne potraviny s cenou a puvodni cenou. Do logu vypisuje, co nasel.
Pri selhani ulozi do slozky debug/ screenshot, HTML a prehled JSON odpovedi (Artifact v Actions)."""
import json, re, sys, pathlib, datetime as dt
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

OUT = pathlib.Path("deals.json")
MAX_AGE = dt.timedelta(hours=40)   # denni cron + prah 40 h = beh kazdy 2. den
MAX_PAGES = 10                     # kolik nalezenych odkazu na akce se projde
FOOD_ONLY = True                   # nechat jen potraviny a napoje (False = vse)

STORES = {
    "Albert": {"origin": "https://www.albert.cz", "pdf": True, "starts": ["https://www.albert.cz/aktualni-letaky", "https://www.albert.cz/", "https://www.albert.cz/shop"]},
    "Lidl":   {"origin": "https://www.lidl.cz",   "starts": ["https://www.lidl.cz/"]},
    "Billa":  {"origin": "https://www.billa.cz",  "viewer": True, "starts": ["https://www.billa.cz/"]},
}
CARD = "article, [class*='product'], [class*='Product'], [class*='offer'], [class*='Offer']"
KEY = re.compile(r"akc|nab[ií]dk|slev|let[aá]k|super-cen|ceny-v-klidu|hity", re.I)
PRIORITY = re.compile(r"nabidka|hity|ceny-v-klidu|super-cen|potravin|slevy|akce", re.I)
SKIP = re.compile(r"\.(pdf|jpe?g|png|zip)(\?|$)|^(mailto|tel):|login|prihlas|kosik|obchodni-podm|cookie|"
                  r"moda|query|magazin|whatsapp|kuchyn|bydlen|sport|detsk|dilna|zahrada|nabytek|vanocni-svet", re.I)
BAD_NAME = re.compile(r"chevron|favorite|^[a-z]+(_[a-z]+)+$|hvězdič|hodnocení|sleva|ušetř|Lidl Plus|Cenový hit|%|Kč|CZK|do vyprodání|novinka|zobrazit|"
                      r"přidat|košík|zdarma|^\(?\d|^-", re.I)
FOOD = re.compile(
    r"maso|kuř|vepř|hověz|krůt|šunk|salám|klobás|párek|párk|uzen|slanin|kost|řízek|mlet|ryb|losos|tuňák|krevet|"
    r"sýr|eidam|gouda|mozzarell|camembert|tvaroh|jogurt|mléko|mléč|smetan|máslo|margarín|vejce|kefír|pudink|šlehačk|"
    r"chléb|rohlík|houska|pečiv|bageta|croissant|koláč|buchta|dort|knedlík|těstovin|špaget|nudle|rýž|mouka|mouky|"
    r"cukr|sůl|olej|ocet|kečup|hořčic|majonéz|omáčk|polévk|konzerv|zelenin|ovoc|jabl|hrušk|banán|pomeranč|citron|"
    r"hrozn|jahod|borůvk|broskv|meruň|švestk|rajč|okurk|paprik|cibul|česnek|brambor|mrkev|zelí|salát|špenát|brokolic|"
    r"květák|hub[ay]|žampion|ořech|mandl|arašíd|pistác|čokolád|sušenk|bonbon|oplatk|zmrzlin|nanuk|pizza|müsli|vločk|"
    r"cereál|káva|kávy|čaj|voda|džus|limonád|cola|pivo|víno|prosecco|med\b|džem|marmelád|nutella|pomazánk|tofu|"
    r"hummus|oliv|sardink|chips|brambůrk|tyčink|dýn|celer|kukuřic|fazol|čočk|cizrn|kapust|ředkv|pórek|petrželk|"
    r"žervé|tavený|nugget|hranolk|lasagne|těsto|pomeranč|mandarink|kiwi|avokád|ananas|meloun|mango|hotov|jídlo",
    re.I)
# U letaku Albertu (z PDF) opacna logika: vyradi se jen zjevne nepotraviny, vse ostatni zustane.
NONFOOD = re.compile(
    r"\bpsy\b|\bpsů\b|kočk|pochoutky|granule|krmiv|stelivo|zubní|šampon|kondicion|sprchov|deodorant|aviváž|\bprací|"
    r"myčk|čistič|\bwc\b|osvěžovač|plenk|přebalov|ubrousk|toaletní|utěrk|kapesník|svíčka|holicí|baterie|\blego|"
    r"pěna na mytí|mýdlo|pleťov|vložky|tampon|strojek|vysavač|kitchisimo|kompatibiln|sendvičovač|kuchyňský robot|předplacenka|"
    r"prodlužovač|ostřikovač|povlečení|hrnek|\bled\b|polštář|bob sulov|kartáček|vonné|perličky|vlasy|sérum|"
    r"fixační|elmex|odol|sensodyne|parodontax|lacalut|houbové|spontex|sanytol|domestos|\bbref\b|cillit|vanish|"
    r"woolite|persil|perwoll|\bsilan\b|\blenor\b|\bariel\b|\bjar\b|somat|\bfinish\b|\bclin\b|\bpur\b|lovela|k2r|"
    r"air wick|glade|ambipur|dr\. devil|\bfa\b|\bdove\b|\baxe\b|old spice|gliss|elseve|nivea|astrid|palmolive|"
    r"\btepe\b|pantene|head & shoulders|\balways\b|alpa francovka|papír na pečení|colour catcher|rex prací|"
    r"kojenecká výživa|dětské plenky|pěna|krém\*|zubní|kuchyňské|aviváž|detergent|pletivo|žárovk|nářadí", re.I)

EMOJI = {"kuř": "🍗", "maso": "🥩", "mlet": "🥩", "sýr": "🧀", "eidam": "🧀", "mléko": "🥛", "smetan": "🥛",
         "rajč": "🍅", "brambor": "🥔", "cibul": "🧅", "mrkv": "🥕", "vejce": "🥚", "rýž": "🍚",
         "těst": "🍝", "špaget": "🍝", "žampion": "🍄", "chléb": "🍞", "jabl": "🍎", "ryb": "🐟",
         "jogurt": "🥛", "banán": "🍌", "hrozn": "🍇", "pomeranč": "🍊", "čokolád": "🍫", "káva": "☕", "pivo": "🍺"}
CUR = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:Kč|CZK|,-)")
PRO = re.compile(r"\s+pro\s+\d+(?:[.,]\d+)?\s*(?:CZK|Kč)\s*$", re.I)
VALID = re.compile(r"do\s+(\d{1,2})\.\s?(\d{1,2})\.")

# Najde nejvetsi blok textu (15-350 znaku), ve kterem jsou 2-4 ruzne ceny = typicka karta produktu.
GENERIC_JS = r"""() => {
  const P = /\d{1,4}(?:[,.]\d{1,2})?\s*(?:Kč|CZK|,-)/g;
  const ok = el => {
    if (!el || el === document.body) return false;
    const t = el.innerText || '';
    if (t.length < 15 || t.length > 400) return false;
    const u = new Set((t.match(P) || []).map(x => x.replace(/\s/g, '')));
    return u.size >= 2 && u.size <= 5;
  };
  const out = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (ok(el) && !ok(el.parentElement)) {
      const img = el.querySelector('img');
      out.push({text: el.innerText, img: img ? (img.currentSrc || img.src || img.getAttribute('data-src')) : null});
    }
  }
  return out;
}"""
LINKS_JS = r"""() => [...document.querySelectorAll('a[href]')].map(a => ({h: a.href, t: (a.innerText || '').trim().slice(0, 60)}))"""
dropped = {"nepotravina": 0}


# --- cteni produktu primo z JSON odpovedi, ktere stranka stahuje na pozadi ---
NAMEK = ("fullTitle", "title", "name", "productName", "label")
CURK = {"price", "saleprice", "discountprice", "currentprice", "discountedprice", "specialprice", "pricenow"}
OLDK = {"oldprice", "regularprice", "originalprice", "beforeprice", "strikeprice", "listprice", "normalprice",
        "previousprice", "pricebefore", "strikethroughprice"}
rej = []


def reject(reason, text):
    if len(rej) < 14:
        rej.append(reason + ": " + " | ".join(text.split("\n"))[:150])


def nk(k):
    return re.sub(r"[^a-z]", "", k.lower())


def find_price(d, depth=0):
    cur = old = None
    for k, v in d.items():
        n = nk(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if n in CURK and cur is None:
                cur = float(v)
            elif n in OLDK and old is None:
                old = float(v)
        elif isinstance(v, dict) and depth < 3:
            c, o = find_price(v, depth + 1)
            cur = cur if cur is not None else c
            old = old if old is not None else o
    return cur, old


def img_of(d):
    for k, v in d.items():
        if "image" in k.lower():
            if isinstance(v, str) and v.startswith("http"):
                return v
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0].startswith("http"):
                return v[0]
    return None


def walk(node, out, depth=0):
    if depth > 12:
        return
    if isinstance(node, dict):
        name = next((node[k] for k in NAMEK if isinstance(node.get(k), str) and 3 < len(node[k]) < 120), None)
        if name:
            c, o = find_price(node)
            if c and o and o > c and o / c <= 6:
                out.append({"n": name.strip(), "p": c, "o": o, "img": img_of(node)})
        for v in node.values():
            walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node[:600]:
            walk(v, out, depth + 1)


def attach(page, store):
    def on(resp):
        try:
            if resp.request.resource_type in ("xhr", "fetch") and "json" in (resp.headers.get("content-type") or ""):
                if len(store) < 120:
                    store.append((resp.url, resp.json()))
        except Exception:
            pass
    page.on("response", on)
    return on


def num(s):
    return float(s.replace(",", "."))


UNIT = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l|ml|ks)\b", re.I)


def parse_card(text):
    """Z bloku textu vezme PRVNI produkt (blok muze obsahovat i dalsi) a vrati nazev, cenu a puvodni cenu."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    m = re.search(r"\bpro\s+(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:CZK|Kč)", text)
    cur = num(m.group(1)) if m else None
    name, prices = None, []
    for l in lines:
        l2 = PRO.sub("", l).strip()
        if len(re.findall(r"[A-Za-zÀ-ž]", l2)) >= 5 and not BAD_NAME.search(l2):
            if name is None:
                name = l2
            elif l2 != name and len(set(prices)) >= 2:
                break                                   # zacina dalsi produkt
            continue
        if re.search(r"sleva|ušetř", l, re.I) or UNIT.search(l):   # vyse slevy / cena za kg nejsou cena
            continue
        prices += [num(x.group(1)) for x in CUR.finditer(l)]
    prices = [p for p in prices if 1 <= p < 5000]
    if not name or not prices:
        return None
    p = cur if cur else min(prices)
    higher = [x for x in prices if x > p]
    if not higher:
        return None
    o = max(higher)
    if o / p > 6:
        return None
    emoji = next((e for k, e in EMOJI.items() if k in name.lower()), "🛒")
    deal = {"n": name[:70], "e": emoji, "p": p, "o": o}
    v = VALID.search(text)
    if v:
        deal["v"] = f"do {int(v.group(1))}.{int(v.group(2))}."
    return deal


def accept_cookies(page):
    try:
        page.get_by_role("button", name=re.compile("Přijmout|Souhlas|Povolit|Accept", re.I)).first.click(timeout=3000)
        page.wait_for_timeout(1000)
    except Exception:
        pass


def add_item(deals, seen, it, base):
    name = PRO.sub("", it["n"]).strip()
    if not name or name in seen or not it.get("o"):
        return 0
    is_food = (not NONFOOD.search(name)) if it.get("src") == "pdf" else bool(FOOD.search(name))
    if FOOD_ONLY and not is_food:
        dropped["nepotravina"] += 1
        reject("nepotravina", f"{name} {it['p']}/{it['o']}")
        return 0
    d = {"n": name[:70], "e": next((e for k, e in EMOJI.items() if k in name.lower()), "🛒"), "p": it["p"], "o": it["o"]}
    if it.get("v"):
        d["v"] = it["v"]
    if it.get("img") and not it["img"].startswith("data:"):
        d["img"] = urljoin(base, it["img"])
    seen.add(name)
    deals.append(d)
    return 1


def add(deals, seen, text, img, base):
    d = parse_card(text)
    if not d:
        if CUR.search(text):
            reject("parse", text)
        return 0
    d["img"] = img
    return add_item(deals, seen, d, base)


def extract(page, deals, seen):
    """Vytahne produkty z aktualni stranky, vrati pocet novych."""
    page.wait_for_timeout(2500)
    accept_cookies(page)
    for _ in range(8):                      # dociteni lazy-load produktu
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(500)
    n = 0
    for el in page.query_selector_all(CARD):
        try:
            img = el.query_selector("img")
            src = img and (img.get_attribute("src") or img.get_attribute("data-src"))
            n += add(deals, seen, el.inner_text(), src, page.url)
        except Exception:
            pass
    for it in page.evaluate(GENERIC_JS):
        n += add(deals, seen, it["text"], it["img"], page.url)
    return n


def promo_links(page, origin):
    out, seen = [], {page.url.split("#")[0]}
    for l in page.evaluate(LINKS_JS):
        h = l["h"].split("#")[0]
        if not h.startswith(origin) or h in seen or SKIP.search(h):
            continue
        if KEY.search(h) or KEY.search(l["t"]):
            seen.add(h)
            out.append(h)
    return out


def debug(page, name):
    pathlib.Path("debug").mkdir(exist_ok=True)
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    print(f"   [{name}] title={page.title()!r} url={page.url}")
    print(f"   [{name}] znaku 'Kč' v textu: {body.count('Kč')}; zacatek: {body[:200]!r}")
    try:
        page.screenshot(path=f"debug/{name}.png")
        pathlib.Path(f"debug/{name}.html").write_text(page.content()[:600000], "utf-8")
    except Exception as e:
        print("   debug se nepodarilo ulozit:", e)


def drain(js, deals, seen, base, log):
    n = 0
    for url, body in js:
        found = []
        walk(body, found)
        got = sum(add_item(deals, seen, it, base) for it in found)
        try:
            size = len(json.dumps(body))
        except Exception:
            size = 0
        log.append((url, got, len(found), size, body))
        n += got
    js.clear()
    return n


def pdf_urls(log):
    """Z JSON odpovedi (napr. Albert GetLeaflets) vybere odkazy na PDF aktualnich letaku."""
    found, today = [], dt.date.today()

    def rec(n):
        if isinstance(n, dict):
            u = n.get("downloadUrl")
            if isinstance(u, str) and ".pdf" in u and n.get("documentType", "LEAFLET") == "LEAFLET":
                end = n.get("validityEndDateFormatted", "")
                try:
                    ok = dt.datetime.strptime(end, "%d.%m.%Y").date() >= today
                except ValueError:
                    ok = True
                if ok and u not in found:
                    found.append(u)
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)
    for _, _, _, _, body in log:
        rec(body)
    return found[:3]


def from_pdf(url, deals, seen):
    """Stahne PDF letaku a precte z nej produkty (modul albert_pdf.py)."""
    import tempfile, urllib.request
    try:
        import albert_pdf
    except Exception as e:
        print("   albert_pdf.py nejde nacist:", e)
        return 0
    if not url.startswith("http"):                       # mistni soubor (zaloha: leaflets/albert.pdf)
        items = albert_pdf.parse_pdf(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (EatMaxing leaflet bot)"})
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            with urllib.request.urlopen(req, timeout=180) as r:
                f.write(r.read())
            f.flush()
            items = albert_pdf.parse_pdf(f.name)
    n = 0
    for it in items:
        n += add_item(deals, seen, {"n": it["n"], "p": it["p"], "o": it["o"], "v": it.get("v"), "src": "pdf"}, url)
    print(f"   PDF: precteno {len(items)} cenovek, pridano {n} potravin")
    return n


def scrape(page, name, cfg):
    deals, seen, cand, js, log = [], set(), [], [], []
    rej.clear()
    on = attach(page, js)
    try:
        for i, start in enumerate(cfg["starts"]):
            page.goto(start, wait_until="domcontentloaded", timeout=60000)
            n = extract(page, deals, seen)
            m = drain(js, deals, seen, page.url, log)
            print(f"   start {start}: {n} z DOM, {m} z JSON")
            if n + m < 5 and i == 0:
                debug(page, name)
            for u in promo_links(page, cfg["origin"]):
                if u not in cand:
                    cand.append(u)
        cand.sort(key=lambda u: 0 if PRIORITY.search(u) else 1)
        print(f"   nalezene odkazy na akce ({len(cand)}):")
        for u in cand[:20]:
            print("     ", u)
        for u in cand[:MAX_PAGES]:
            if len(deals) >= 400:
                break
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=60000)
                n = extract(page, deals, seen)
                m = drain(js, deals, seen, page.url, log)
                print(f"   {u} -> {n} z DOM, {m} z JSON")
            except Exception as e:
                print(f"   {u} chyba: {str(e)[:80]}")
    finally:
        page.remove_listener("response", on)
    if cfg.get("pdf"):
        got = 0
        for u in pdf_urls(log):
            try:
                print(f"   leták PDF: {u[:90]}")
                got += from_pdf(u, deals, seen)
            except Exception as e:
                print(f"   PDF chyba: {str(e)[:100]}")
        local = pathlib.Path("leaflets") / f"{name.lower()}.pdf"
        if not got and local.exists():                   # zaloha: PDF nahrane rucne do repozitare
            print(f"   pouzivam ulozeny PDF {local}")
            from_pdf(str(local), deals, seen)
    if cfg.get("viewer"):                                # hledani odkazu na PDF v prohlizeci letaku (Publitas)
        viewers = sorted({u.split("/spreads.json")[0] for u, *_ in log if "publitas.com" in u and "spreads.json" in u})
        for v in viewers[:3]:
            found = []

            def on_pdf(resp, found=found):
                if "pdf" in (resp.headers.get("content-type") or "").lower():
                    found.append(resp.url)
            try:
                page.on("response", on_pdf)
                page.goto(v + "/", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                hrefs = [h for h in page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.href)") if ".pdf" in h]
                try:
                    page.get_by_role("button", name=re.compile("Stáhnout|Download|PDF", re.I)).first.click(timeout=3000)
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                print(f"   prohlizec {v}\n      pdf odkazy: {hrefs[:3]}\n      pdf v siti: {found[:3]}")
            except Exception as e:
                print(f"   prohlizec {v} chyba: {str(e)[:80]}")
            finally:
                page.remove_listener("response", on_pdf)
    print(f"   JSON odpovedi: {len(log)}, z nich s produkty: {sum(1 for x in log if x[2])}")
    print(f"   vyrazeno jako nepotraviny (celkem do ted): {dropped['nepotravina']}")
    for r in rej:
        print("   zamitnuto ->", r)
    if len(deals) < 20:                      # ukazka JSON odpovedi, aby sel zjistit format dat
        for url, got, found, size, body in sorted(log, key=lambda x: -x[3])[:3]:
            print(f"   JSON {size} B {url}\n      {json.dumps(body, ensure_ascii=False)[:900]}")
    pathlib.Path("debug").mkdir(exist_ok=True)
    with open(f"debug/json_{name}.txt", "w", encoding="utf-8") as f:
        for url, got, found, size, body in sorted(log, key=lambda x: -x[3]):
            f.write(f"{size:>8} B  produktu:{found} pridano:{got}  {url}\n")
        for url, got, found, size, body in sorted(log, key=lambda x: -x[3])[:4]:
            f.write(f"\n=== {url}\n{json.dumps(body, ensure_ascii=False)[:3000]}\n")
    return deals[:1000]


def update_prices(data, now):
    """Historie beznych cen (cena pred slevou) z letaku. Appka je pouzije, kdyz zbozi zrovna neni v akci."""
    hist = {f'{x["s"]}|{x["n"]}': x for x in data.get("prices", [])}
    day = now.date().isoformat()
    added = 0
    for store, st in data["stores"].items():
        for d in st.get("deals") or []:
            try:
                o, p = float(d.get("o") or 0), float(d.get("p") or 0)
            except (TypeError, ValueError):
                continue
            if o <= p or o <= 0 or not d.get("n"):
                continue
            key = f'{store}|{d["n"]}'
            if key not in hist:
                added += 1
            hist[key] = {"s": store, "n": d["n"], "p": round(o, 2), "t": day}
    limit = (now - dt.timedelta(days=150)).date().isoformat()
    rows = sorted((x for x in hist.values() if x.get("t", "") >= limit), key=lambda x: x["t"], reverse=True)[:4000]
    data["prices"] = rows
    print(f"historie beznych cen: {len(rows)} polozek (+{added} novych)")


def main():
    force = "--force" in sys.argv
    data = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {"stores": {}}
    now = dt.datetime.now(dt.timezone.utc)
    last = data.get("run")
    if last and not force and now - dt.datetime.fromisoformat(last) < MAX_AGE:
        print("Data jsou cerstva, preskakuji. (--force vynuti aktualizaci)")
        return
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="cs-CZ", viewport={"width": 1366, "height": 900},
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        page = ctx.new_page()
        for name, cfg in STORES.items():
            old = data["stores"].get(name, {})
            print(name, cfg["starts"][0])
            try:
                deals = scrape(page, name, cfg)
                if len(deals) < 5:
                    raise RuntimeError(f"nasel jen {len(deals)} potravin")
                old_n = len(old.get("deals") or [])
                if old_n >= 20 and len(deals) < old_n * 0.5:      # podezrele maly vysledek -> nechame stara data
                    raise RuntimeError(f"nasel {len(deals)} potravin misto drivejsich {old_n}, ponechavam stara data")
                data["stores"][name] = {"updated": now.isoformat(), "valid": "aktuální", "status": "ok", "deals": deals}
                print(name, "OK", len(deals))
            except Exception as e:
                print(name, "CHYBA:", e)
                old["status"] = "error"
                data["stores"][name] = old or {"status": "error", "deals": []}
        browser.close()
    update_prices(data, now)
    if any(s.get("status") == "ok" and s.get("updated") == now.isoformat() for s in data["stores"].values()):
        data["run"] = now.isoformat()      # pri uplnem selhani se zkusi znovu hned dalsi den
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
