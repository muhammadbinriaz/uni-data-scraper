import csv, json, random, re, time, urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, urlencode, parse_qs
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

NAV_TIMEOUT = 45000
SETTLE_MS = 1500
MAX_PAGES_DEFAULT = 100
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GENERIC_EMAIL = re.compile(
    r"^(noreply|no-reply|webmaster|postmaster|admin|info|contact|support|media|communications|help|sales)@",
    re.I,
)

JS_EXTRACT = r"""
(forcedSel) => {
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','HEAD','META','LINK','TITLE']);
  const nameRe = /^[A-Z][\p{L}'\u2019.\-]+(?:\s+[A-Z][\p{L}'\u2019.\-]+){1,3}$/u;
  const nameLike = t => nameRe.test((t || '').trim());
  const LABEL_RE = /^(graduate\s+student|ph\.?\s?d\.?\s+(student|candidate)|doctoral\s+student|master'?s\s+student|undergraduate|faculty|professor|postdoc|alumni|researcher|lecturer|staff|student)$/i;

  const groups = new Map();
  if (document.body) {
    for (const el of document.body.querySelectorAll('*')) {
      if (SKIP.has(el.tagName)) continue;
      const hasMail = !!el.querySelector('a[href^="mailto:"]');
      const linkCount = el.querySelectorAll('a[href]').length;
      if (!hasMail && linkCount === 0) continue;
      const tc = el.textContent || '';
      if (tc.length < 5 || tc.length > 3000) continue;
      const cls = Array.from(el.classList).sort().join('.');
      const key = el.tagName.toLowerCase() + '|' + cls;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(el);
    }
  }

  const results = [];
  for (const [key, els] of groups) {
    if (els.length < 3) continue;
    const filtered = els.filter(e => !els.some(o => o !== e && e.contains(o)));
    if (filtered.length < 3) continue;
    const withMail = filtered.filter(e => e.querySelector('a[href^="mailto:"]')).length;
    const withName = filtered.filter(e => Array.from(e.querySelectorAll('a')).some(a => nameLike(a.innerText))).length;
    const withBoth = filtered.filter(e =>
      e.querySelector('a[href^="mailto:"]') &&
      Array.from(e.querySelectorAll('a')).some(a => nameLike(a.innerText))
    ).length;
    if (withMail === 0 && withName / filtered.length < 0.5) continue;
    const score = (withMail * 2 + withName) * Math.sqrt(filtered.length) * (1 + withBoth / filtered.length);
    results.push({ key, count: filtered.length, withMail, withName, withBoth, score });
  }
  results.sort((a, b) => b.score - a.score);

  let sel = forcedSel || null;
  let keyLabel = forcedSel ? 'override:' + forcedSel : null;
  if (!sel) {
    const best = results[0];
    if (!best) return { candidates: [], best: null, usedSelector: null, key: null, rows: [] };
    const parts = best.key.split('|');
    sel = parts[0];
    if (parts[1]) sel += '.' + parts[1].split('.').filter(Boolean).join('.');
    keyLabel = best.key;
  }

  let els = [];
  try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { els = []; }
  const filtered = els.filter(e => !els.some(o => o !== e && e.contains(o)));

  const rows = filtered.map(el => {
    const text = el.innerText || '';
    const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
    const mailA = el.querySelector('a[href^="mailto:"]');
    let email = null;
    if (mailA) {
      email = (mailA.getAttribute('href') || '').replace(/^mailto:/i, '').split('?')[0].trim() || null;
    }

    let name = null;
    // 1. Link text
    for (const a of el.querySelectorAll('a')) {
      const t = (a.innerText || '').trim();
      if (t && !LABEL_RE.test(t) && nameLike(t)) { name = t; break; }
    }
    // 2. Headings
    if (!name) {
      for (const h of el.querySelectorAll('h1,h2,h3,h4,h5')) {
        const t = (h.innerText || '').trim();
        if (t && !LABEL_RE.test(t) && nameLike(t)) { name = t; break; }
      }
    }
    // 3. Strong / bold / name-class elements
    if (!name) {
      for (const nEl of el.querySelectorAll('strong, b, [class*="name"], [class*="person"]')) {
        const t = (nEl.innerText || '').trim();
        if (t && !LABEL_RE.test(t) && nameLike(t)) { name = t; break; }
      }
    }
    // 4. Image alt
    if (!name) {
      const img = el.querySelector('img[alt]');
      if (img) {
        const alt = (img.getAttribute('alt') || '').trim();
        if (alt && !LABEL_RE.test(alt) && nameLike(alt)) name = alt;
      }
    }
    // 5. Any line
    if (!name) {
      for (const l of lines) {
        if (!LABEL_RE.test(l) && nameLike(l)) { name = l; break; }
      }
    }
    // 6. Strip label prefix
    if (!name && lines.length) {
      const stripped = lines[0].replace(LABEL_RE, '').replace(/^[,\s\-:]+/, '').trim();
      if (nameLike(stripped)) name = stripped;
    }

    let department = null;
    for (const l of lines) {
      if (/graduate\s+student|ph\.?\s?d|doctora|master'?s|department of|program in|school of|in\s+[A-Z]/i.test(l)) {
        department = l; break;
      }
    }
    return { name, email, department, lines: lines.slice(0, 8) };
  }).filter(r => r.name || r.email);

  return { candidates: results.slice(0, 8), best: results[0] || null, usedSelector: sel, key: keyLabel, rows };
}
"""

_robots = {}
def robots_allows(url, ua="StudentDirectoryResearchBot/1.0"):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read(); _robots[base] = rp
        except Exception:
            _robots[base] = None
    rp = _robots[base]
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:
        return True

def clean_department(s):
    if not s:
        return None
    s = s.strip().strip(',').strip()
    m = re.match(r"^(?:graduate|ph\.?\s?d\.?|doctoral|master'?s)\s+students?[,\s:\-]+(.+)$", s, re.I)
    if m:
        s = m.group(1).strip()
    s = re.sub(r"^(?:in|of)\s+", "", s, flags=re.I).strip()
    s = re.sub(r"\s+person$", "", s, flags=re.I).strip()
    return s or None

NAV_BLOCKLIST = re.compile(
    r"^(our\s+faculty|ccb\s+research\s+areas?|graduate\s+students?|undergraduate\s+programs?|"
    r"graduate\s+program|graduate\s+student\s+guide|student\s+organizations?|"
    r"postdoctoral\s+program|career\s+resources?|green\s+labs?|harvard\s+library|"
    r"ccb\s+safety|our\s+community|ccb\s+prize\s+lectures?|ccb\s+magazine|"
    r"dean'?s\s+message|standing\s+committees?|mbta\s+bus|pedestrian\s+transportation|"
    r"bicycle\s+transportation|enterprise\s+research\s+campus|lactation\s+rooms?|"
    r"applied\s+mathematics|applied\s+physics|engineering\s+sciences?|"
    r"prospective\s+students?|visit\s+us|trademark\s+notice|digital\s+accessibility|"
    r"privacy\s+statement|contact\s+us|home|people|faculty|research|about|"
    r"sort\s*&\s*filters?|results?|department\s+graduate\s+students?|course\s+development\s+assistant)$",
    re.I,
)

NON_NAME_TOKENS = re.compile(
    r"\b(labs?|laboratory|laboratories|departments?|dept|programs?|centres?|centers?|"
    r"institutes?|schools?|colleges?|groups?|teams?|offices?|contacts?|"
    r"accessibilit\w*|privacy|terms|help|support|faculty|staff|students?|"
    r"graduates?|undergraduates?|admissions?|alumni|resources?|"
    r"library|safety|community|campus|universit\w+|committees?|"
    r"messages?|transportation|rooms?|notices?|statements?|headshot|photo)\b",
    re.I,
)

LABEL_NAME = re.compile(
    r"^(graduate\s+students?|ph\.?\s?d\.?\s+(students?|candidates?)|doctoral\s+students?|"
    r"master'?s\s+students?|undergraduate|faculty|professor|postdoc|alumni|"
    r"researcher|lecturer|staff|student|phd|ph\.d\.?|course\s+development\s+assistant)$",
    re.I,
)

def postprocess(row):
    name = (row.get("name") or "").strip().strip(',').strip() or None
    email = (row.get("email") or "").strip() or None
    if email and GENERIC_EMAIL.match(email):
        email = None

    # Names that are actually labels → null them (but keep the email row)
    if name and LABEL_NAME.match(name):
        name = None
    # Strip trailing "Headshot"/"Photo" etc.
    if name:
        name = re.sub(r"\s+(headshot|photo)$", "", name, flags=re.I).strip() or None

    if name and NAV_BLOCKLIST.match(name):
        return None
    if name and not email and NON_NAME_TOKENS.search(name):
        return None
    if name and not email and len(name.split()) < 2:
        return None
    if not name and not email:
        return None

    dept = clean_department(row.get("department"))
    return {"name": name, "email": email, "extracted_department": dept}

def find_next_url(page, current_url):
    try:
        hrefs = page.eval_on_selector_all(
            'a[rel="next"]', 'els => els.map(e => e.href).filter(Boolean)'
        )
        if hrefs:
            return hrefs[0]
    except Exception:
        pass

    pager_selectors = [
        'li.pager__item--next a',
        'a.pager-next',
        'a[aria-label*="Next"]',
        'a[title*="Next"]',
        'a.next.page-numbers',
    ]
    for sel in pager_selectors:
        try:
            hrefs = page.eval_on_selector_all(sel, 'els => els.map(e => e.href).filter(Boolean)')
            if hrefs:
                return hrefs[0]
        except Exception:
            continue

    try:
        cand = page.eval_on_selector_all(
            'a[href]',
            '''els => els.filter(a => {
                const t = (a.innerText || '').trim();
                return /^next$/i.test(t) || /\\bnext\\b/i.test(t) || /^›$/.test(t) || /^»$/.test(t) || /^→$/.test(t);
            }).map(a => a.href)'''
        )
        if cand:
            return cand[0]
    except Exception:
        pass

    parts = urlsplit(current_url)
    q = parse_qs(parts.query)
    if "page" in q:
        try:
            q["page"] = [str(int(q["page"][0]) + 1)]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q, doseq=True), parts.fragment))
        except Exception:
            pass

    return None

def scrape_url(page, url, forced_sel=None, max_pages=MAX_PAGES_DEFAULT, delay=(2.0, 4.5), debug_dir=None, slug="page"):
    rows, pages_done, keys = [], 0, []
    seen, cur = set(), url
    for i in range(max_pages):
        try:
            page.goto(cur, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except PWTimeout:
            raise RuntimeError(f"navigation timeout on {cur}")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        page.wait_for_timeout(SETTLE_MS)
        res = page.evaluate(JS_EXTRACT, forced_sel)
        keys.append(res.get("key"))
        page_rows = res.get("rows") or []

        if i > 0 and len(page_rows) > 3:
            emails_on_page = sum(1 for r in page_rows if r.get("email"))
            if emails_on_page == 0:
                if os.environ.get("SCRAPER_DEBUG"):
                    print(f"    page {i+1}: no emails on page, treating as nav junk — stopping")
                pages_done = i
                break

        new = 0
        for r in page_rows:
            pr = postprocess(r)
            if not pr:
                continue
            k = ((pr["name"] or "").lower(), (pr["email"] or "").lower())
            if k in seen:
                continue
            seen.add(k); rows.append({**pr, "page": i + 1}); new += 1

        if i == 0 and new < 3 and debug_dir:
            try:
                (Path(debug_dir) / f"{slug}.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(Path(debug_dir) / f"{slug}.png"), full_page=True)
                (Path(debug_dir) / f"{slug}.candidates.json").write_text(
                    json.dumps(res.get("candidates", [])[:10], indent=2), encoding="utf-8")
            except Exception:
                pass

        pages_done = i + 1
        if new == 0 and i > 0:
            break
        nxt = find_next_url(page, cur)
        if os.environ.get("SCRAPER_DEBUG"):
            print(f"    page {i+1}: next_url = {nxt}")
        if not nxt or nxt == cur:
            break
        cur = nxt
        time.sleep(random.uniform(*delay))
    return rows, pages_done, keys

def load_overrides(path="overrides.json"):
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f"WARNING: {path} is not valid JSON ({e}). Treating as empty.")
        return {}

def launch_browser(p, headless):
    # Try real Chrome first (much less bot-detectable), fall back to bundled Chromium
    if headless:
        try:
            return p.chromium.launch(
                headless=True, channel="chrome",
                args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception as e:
            print(f"    (chrome channel unavailable: {e}; using bundled chromium)")
    return p.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"]
    )

def run_batch(args):
    out = Path(args.out)
    (out / "per_url").mkdir(parents=True, exist_ok=True)
    (out / "debug").mkdir(parents=True, exist_ok=True)
    overrides = load_overrides()

    with open(args.sites, newline="", encoding="utf-8") as f:
        sites = list(csv.DictReader(f))
    if args.only:
        sites = [s for s in sites if args.only.lower() in (s.get("url", "") + " " + s.get("university", "")).lower()]
    if args.limit:
        sites = sites[: args.limit]

    summary, all_rows = [], []
    with sync_playwright() as p:
        browser = launch_browser(p, headless=not args.headed)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        try:
            for idx, s in enumerate(sites, 1):
                url = s["url"].strip(); uni = s.get("university", "").strip(); dept = s.get("department", "").strip()
                host = urlparse(url).netloc.lower()
                slug = re.sub(r"[^a-z0-9]+", "-", f"{uni}-{dept}".lower()).strip("-")[:70] + f"-{idx}"
                print(f"[{idx}/{len(sites)}] {uni} / {dept} -> {url}")
                status, note, rows, pages, key = "ok", "", [], 0, None
                if not args.ignore_robots and not robots_allows(url):
                    status, note = "robots-disallowed", "robots.txt disallows this path"
                else:
                    # One retry on transient failures
                    for attempt in (1, 2):
                        try:
                            rows, pages, keys = scrape_url(
                                page, url, forced_sel=overrides.get(host),
                                max_pages=args.max_pages, debug_dir=out / "debug", slug=slug)
                            key = keys[0] if keys else None
                            if not rows:
                                status, note = "empty", "no student cards detected"
                            else:
                                status, note = "ok", ""
                            break
                        except Exception as e:
                            if attempt == 1:
                                time.sleep(5)
                                continue
                            status, note = "fetch-error", str(e)[:300]

                for r in rows:
                    all_rows.append({"university": uni, "sheet_department": dept, "url": url,
                                     "page": r.get("page"), "name": r.get("name"), "email": r.get("email"),
                                     "extracted_department": r.get("extracted_department"), "cluster": key})
                if rows:
                    with open(out / "per_url" / f"{slug}.csv", "w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=["name", "email", "extracted_department", "page"])
                        w.writeheader()
                        for r in rows:
                            w.writerow({k: r.get(k) for k in ["name", "email", "extracted_department", "page"]})
                summary.append({"university": uni, "sheet_department": dept, "url": url, "status": status,
                                "rows": len(rows), "pages": pages, "cluster": key, "note": note})
                print(f"    -> {status}, {len(rows)} rows, {pages} page(s)")
                # Write summary incrementally so a crash doesn't lose progress
                with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=["university","sheet_department","url","status","rows","pages","cluster","note"])
                    w.writeheader(); w.writerows(summary)
                time.sleep(random.uniform(1.0, 2.5))
        finally:
            try: ctx.close()
            except Exception: pass
            try: browser.close()
            except Exception: pass

    with open(out / "students.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["university","sheet_department","url","page","name","email","extracted_department","cluster"])
        w.writeheader(); w.writerows(all_rows)

    ok = sum(1 for r in summary if r["status"] == "ok")
    print(f"\nDone. {ok}/{len(summary)} URLs produced rows. Total students: {len(all_rows)}")
    print(f"Output: {out/'students.csv'} and {out/'summary.csv'}")
    print("Failed URLs (retry individually with --only <host>):")
    for r in summary:
        if r["status"] != "ok":
            print(f"  [{r['status']}] {r['university']} / {r['sheet_department']} -> {r['url']}")