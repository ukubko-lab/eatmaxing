"""Stahne akce z letaku Albert, Lidl, Billa a zapise deals.json.

Spousti se kazdy den v GitHub Actions, ale data skutecne obnovi jen tehdy, kdyz jsou
starsi nez ~2 dny (nebo kdyz se pusti s --force). Kdyz jeden obchod selze, zustanou jeho stara data.

Skript zacne na uvodni strance obchodu, najde odkazy na akce/letaky/nabidky, projde je
a z kazde stranky vytahne produkty s cenou a puvodni cenou. Do logu vypisuje, co nasel.
Pri selhani ulozi do slozky debug/ screenshot a HTML (v Actions ke stazeni jako Artifact)."""
import json, re, sys, pathlib, datetime as dt
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

OUT = pathlib.Path("deals.json")
MAX_AGE = dt.timedelta(hours=40)   # denni cron + prah 40 h = beh kazdy 2. den
MAX_PAGES = 8                      # kolik nalezenych odkazu na akce se projde

STORES = {
    "Albert": {"origin": "https://www.albert.cz", "url": "https://www.albert.cz/aktualni-letaky"},
    "Lidl":   {"origin": "https://www.lidl.cz",   "url": "https://www.lidl.cz/"},
    "Billa":  {"origin": "https://www.billa.cz",  "url": "https://www.billa.cz/"},
}
CARD = "article, [class*='product'], [class*='Product'], [class*='offer'], [class*='Offer']"
KEY = re.compile(r"akc|nab[ií]dk|slev|let[aá]k|super-cen|ceny-v-klidu", re.I)
SKIP = re.compile(r"\.(pdf|jpe?g|png|zip)(\?|$)|^(mailto|tel):|login|prihlas|kosik|obchodni-podm|cookie", re.I)
EMOJI = {"kuř": "🍗", "maso": "🥩", "mlet": "🥩", "sýr": "🧀", "eidam": "🧀", "mléko": "🥛", "smetan": "🥛",
         "rajč": "🍅", "brambor": "🥔", "cibul": "🧅", "mrkv": "🥕", "vejce": "🥚", "rýž": "🍚",
         "těst": "🍝", "špaget": "🍝", "žampion": "🍄", "chléb": "🍞", "jablk": "🍎", "ryb": "🐟"}
PRICE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:Kč|,-)?")
VALID = re.compile(r"do\s+(\d{1,2})\.\s?(\d{1,2})\.")

# Najde nejvetsi blok textu (15-350 znaku), ve kterem jsou 2-4 ruzne ceny = typicka karta produktu.
GENERIC_JS = r"""() => {
  const P = /\d{1,4}(?:[,.]\d{1,2})?\s*(?:Kč|,-)/g;
  const ok = el => {
    if (!el || el === document.body) return false;
    const t = el.innerText || '';
    if (t.length < 15 || t.length > 350) return false;
    const u = new Set((t.match(P) || []).map(x => x.replace(/\s/g, '')));
    return u.size >= 2 && u.size <= 4;
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


def num(s):
    return float(s.replace(",", "."))


def parse_card(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = next((l for l in lines if len(l) > 3 and not re.fullmatch(r"[\d\s.,%Kč−-]+", l)), None)
    prices = [num(m.group(1)) for l in lines if re.search(r"Kč|,-", l) for m in PRICE.finditer(l)]
    prices = [p for p in prices if 1 <= p < 5000]
    if not name or len(set(prices)) < 2:
        return None
    emoji = next((e for k, e in EMOJI.items() if k in name.lower()), "🛒")
    deal = {"n": name[:60], "e": emoji, "p": min(prices), "o": max(prices)}
    m = VALID.search(text)
    if m:
        deal["v"] = f"do {int(m.group(1))}.{int(m.group(2))}."
    return deal


def accept_cookies(page):
    try:
        page.get_by_role("button", name=re.compile("Přijmout|Souhlas|Povolit|Accept", re.I)).first.click(timeout=3000)
        page.wait_for_timeout(1000)
    except Exception:
        pass


def add(deals, seen, text, img, base):
    d = parse_card(text)
    if not d or d["n"] in seen:
        return 0
    if img and not img.startswith("data:"):
        d["img"] = urljoin(base, img)
    seen.add(d["n"])
    deals.append(d)
    return 1


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


def scrape(page, name, cfg):
    deals, seen = [], set()
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    n = extract(page, deals, seen)
    print(f"   start {page.url}: {n} akci")
    if n < 5:
        debug(page, name)
    cand = promo_links(page, cfg["origin"])
    print(f"   nalezene odkazy na akce ({len(cand)}):")
    for u in cand[:20]:
        print("     ", u)
    for u in cand[:MAX_PAGES]:
        if len(deals) >= 60:
            break
        try:
            page.goto(u, wait_until="domcontentloaded", timeout=60000)
            print(f"   {u} -> {extract(page, deals, seen)} akci")
        except Exception as e:
            print(f"   {u} chyba: {str(e)[:80]}")
    return deals[:80]


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
            print(name, cfg["url"])
            try:
                deals = scrape(page, name, cfg)
                if len(deals) < 5:
                    raise RuntimeError(f"nasel jen {len(deals)} akci")
                data["stores"][name] = {"updated": now.isoformat(), "valid": "aktuální", "status": "ok", "deals": deals}
                print(name, "OK", len(deals))
            except Exception as e:
                print(name, "CHYBA:", e)
                old["status"] = "error"
                data["stores"][name] = old or {"status": "error", "deals": []}
        browser.close()
    if any(s.get("status") == "ok" and s.get("updated") == now.isoformat() for s in data["stores"].values()):
        data["run"] = now.isoformat()      # pri uplnem selhani se zkusi znovu hned dalsi den
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
