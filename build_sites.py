import csv, re, sys
from urllib.parse import urlparse

def clean_url(u):
    u = u.strip().strip('"').strip("'")
    if not u.lower().startswith("http"):
        return None
    p = urlparse(u)
    if "." not in p.netloc or " " in p.netloc:
        return None
    return u

def main(src="sites.tsv", out="sites.csv"):
    with open(src, newline="", encoding="utf-8-sig") as f:
        sample = f.read(8192); f.seek(0)
        delim = "\t" if sample.count("\t") >= sample.count(",") else ","
        rows = list(csv.reader(f, delimiter=delim))
    header = [h.strip() for h in rows[0]]
    current_uni = None
    out_rows, dropped = [], []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        uni = (r[0] if r else "").strip()
        if uni:
            current_uni = uni
        for ci in range(1, len(header)):
            if ci >= len(r):
                continue
            cell = r[ci].strip()
            if not cell or re.fullmatch(r"N\\?/?A|NA|-", cell, re.I):
                continue
            for token in cell.split():
                u = clean_url(token)
                if not u:
                    if token.lower().startswith("http"):
                        dropped.append((current_uni, header[ci], token))
                    continue
                out_rows.append({"university": current_uni, "department": header[ci], "url": u})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["university", "department", "url"])
        w.writeheader(); w.writerows(out_rows)
    print(f"wrote {len(out_rows)} rows -> {out}")
    if dropped:
        with open("sites_dropped.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["university", "department", "raw"]); w.writerows(dropped)
        print(f"dropped {len(dropped)} malformed URLs -> sites_dropped.csv")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sites.tsv")