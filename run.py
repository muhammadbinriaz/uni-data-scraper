import argparse
from scraper import run_batch

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="sites.csv")
    ap.add_argument("--out", default="out")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default=None)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--ignore-robots", action="store_true")
    run_batch(ap.parse_args())

if __name__ == "__main__":
    main()