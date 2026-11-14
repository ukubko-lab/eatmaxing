"""Stahne akce z letaku Albert, Lidl, Billa a zapise deals.json.

Spousti se kazdy den v GitHub Actions, ale data skutecne obnovi jen tehdy, kdyz jsou
starsi nez ~2 dny (nebo kdyz se pusti s --force). Kdyz jeden obchod selze, zustanou jeho stara data.
Pri selhani ulozi do slozky debug/ screenshot a HTML stranky (stahnes v Actions jako Artifact)."""
import json, re, sys, pathlib, datetime as dt
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

OUT = pathlib.Path("deals.json")
MAX_AGE = dt.timedelta(hours=40)   # denni cron + prah 40 h = beh kazdy 2. den

# URL si over v prohlizeci: otevri stranku s akcemi obchodu a zkopiruj adresu.
STORES = {
    "Albert": {"url": "https://www.albert.cz/akce", "card": "article, [class*='product'], [class*='Product']"},
    "Lidl":   {"url": "https://www.lidl.cz/c/nabidky/s10008433", "card": "article, [class*='product'], [class*='Product']"},
    "Billa":  {"url": "https://www.billa.cz/akce", "card": "article, [class*='product'], [class*='Product']"},
}
EMOJI = {"kuř": "🍗", "maso": "🥩", "mlet": "🥩", "sýr": "🧀", "eidam": "🧀", "mléko": "🥛", "smetan": "🥛",
         "rajč": "🍅", "brambor": "🥔", "cibul": "🧅", "mrkv": "🥕", "vejce": "🥚", "rýž": "🍚",
         "těst": "🍝", "špaget": "🍝", "žampion": "🍄", "chléb": "🍞", "jablk": "🍎", "ryb": "🐟"}
PRICE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:Kč|,-)?")
VALID = re.compile(r"do\s+(\d{1,2})\.\s?(\d{1,2})\.")

# Vseobecny hledac: najde nejvetsi blok textu (15-350 znaku), ve kterem jsou 2-4 ruzne ceny.
# To je typicky karta produktu, bez ohledu na nazvy CSS trid.
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
        return
    if img and not img.startswith("data:"):
        d["img"] = urljoin(base, img)
    seen.add(d["n"])
    deals.append(d)


def scrape(page, cfg):
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    accept_cookies(page)
    for _ in range(8):                      # dociteni lazy-load produktu
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(600)
    deals, seen = [], set()
    for el in page.query_selector_all(cfg["card"]):
        try:
            img = el.query_selector("img")
            src = img and (img.get_attribute("src") or img.get_attribute("data-src"))
            add(deals, seen, el.inner_text(), src, page.url)
        except Exception:
            pass
    n1 = len(deals)
    if n1 < 5:                              # nahradni vseobecne hledani
        for it in page.evaluate(GENERIC_JS):
            add(deals, seen, it["text"], it["img"], page.url)
    print(f"   selektor: {n1} akci, po vseobecnem hledani: {len(deals)}")
    return deals[:80]


def debug(page, name):
    pathlib.Path("debug").mkdir(exist_ok=True)
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    print(f"   [{name}] title={page.title()!r} url={page.url}")
    print(f"   [{name}] znaku 'Kč' v textu: {body.count('Kč')}; zacatek stranky: {body[:300]!r}")
    try:
        page.screenshot(path=f"debug/{name}.png")
        pathlib.Path(f"debug/{name}.html").write_text(page.content()[:600000], "utf-8")
    except Exception as e:
        print("   debug se nepodarilo ulozit:", e)


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
                deals = scrape(page, cfg)
                if len(deals) < 5:
                    debug(page, name)
                    raise RuntimeError(f"nasel jen {len(deals)} akci, selektory asi nesedi")
                data["stores"][name] = {"updated": now.isoformat(), "valid": "aktuální", "status": "ok", "deals": deals}
                print(name, "OK", len(deals))
            except Exception as e:
                print(name, "CHYBA:", e)
                if not pathlib.Path(f"debug/{name}.png").exists():
                    debug(page, name)
                old["status"] = "error"
                data["stores"][name] = old or {"status": "error", "deals": []}
        browser.close()
    if any(s.get("status") == "ok" and s.get("updated") == now.isoformat() for s in data["stores"].values()):
        data["run"] = now.isoformat()      # pri uplnem selhani se zkusi znovu hned dalsi den
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
