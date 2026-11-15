import pandas as pd
from pathlib import Path
import re

LABELS = re.compile(
    r"^(graduate\s+students?|ph\.?\s?d\.?(\s+(student|candidate))?|doctoral\s+students?|"
    r"master'?s\s+students?|undergraduate|faculty|professor|postdoc|alumni|"
    r"researcher|lecturer|staff|student|phd|course\s+development\s+assistant)$",
    re.I,
)
DEPT_WORDS = re.compile(
    r"^(chemistry|physics|biology|mathematics|engineering|computer\s+science|"
    r"economics|psychology|sociology|political\s+science|history|philosophy|"
    r"english|classics|neuroscience|astrophysics|statistics|data\s+science|"
    r"applied\s+\w+|electrical\s+engineering|mechanical\s+engineering|"
    r"chemical\s+engineering|bioengineering|government|public\s+policy|"
    r"aerospace\s+engineering|environmental\s+\w+|robotics|materials\s+science|"
    r"in\s+\w|department\s+of)",
    re.I,
)

def looks_like_dept(s):
    return bool(s) and bool(DEPT_WORDS.match(str(s).strip()))

# 1. Per-URL stats
print("=" * 90)
print("PER-URL STATS (sorted worst-first by missing emails)")
print("=" * 90)
rows = []
for f in sorted(Path('out_full/per_url').glob('*.csv')):
    try:
        d = pd.read_csv(f)
        rows.append({
            'file': f.name[:60],
            'n': len(d),
            'name%': round(d['name'].notna().mean(), 2) if 'name' in d.columns else 0,
            'email%': round(d['email'].notna().mean(), 2) if 'email' in d.columns else 0,
            'dept%': round(d['extracted_department'].notna().mean(), 2) if 'extracted_department' in d.columns else 0,
        })
    except Exception:
        rows.append({'file': f.name[:60], 'n': -1, 'name%': 0, 'email%': 0, 'dept%': 0})

stats = pd.DataFrame(rows)
stats.to_csv('out_full/per_url_stats.csv', index=False)
worst = stats[(stats['n'] >= 5) & ((stats['email%'] < 0.3) | (stats['name%'] < 0.5))].sort_values('email%').head(25)
print(worst.to_string(index=False))
print(f"\n(Full list of {len(stats)} files written to out_full/per_url_stats.csv)")

# 2. Label-as-name problem
print("\n" + "=" * 90)
print("LABEL-AS-NAME (rows where 'name' is actually 'Graduate Student' etc.)")
print("=" * 90)
df = pd.read_csv('out_full/students.csv')
df['name_str'] = df['name'].astype(str).str.strip()
labels = df[df['name_str'].str.match(LABELS, na=False)]
print(f"Total: {len(labels)} rows")
if len(labels):
    print(labels.groupby(['university', 'sheet_department']).size().sort_values(ascending=False).head(15).to_string())

# 3. Dept-in-name problem
print("\n" + "=" * 90)
print("DEPT-IN-NAME (rows where 'name' looks like 'Chemistry', 'Physics', etc.)")
print("=" * 90)
dept_names = df[df['name_str'].apply(lambda s: looks_like_dept(s) and len(s.split()) <= 4)]
print(f"Total: {len(dept_names)} rows")
if len(dept_names):
    print(dept_names.groupby(['university', 'sheet_department']).size().sort_values(ascending=False).head(15).to_string())

# 4. Dept column polluted with labels
print("\n" + "=" * 90)
print("LABEL-AS-DEPT ('extracted_department' is 'Graduate Student' etc.)")
print("=" * 90)
df['dept_str'] = df['extracted_department'].astype(str).str.strip()
label_depts = df[df['dept_str'].str.match(LABELS, na=False)]
print(f"Total: {len(label_depts)} rows")
if len(label_depts):
    print(label_depts.groupby(['university']).size().sort_values(ascending=False).head(10).to_string())

# 5. Sample rows from 5 random files so I can see the format
print("\n" + "=" * 90)
print("SAMPLE ROWS (5 random files, 3 rows each)")
print("=" * 90)
import random
random.seed(42)
sample_files = random.sample(list(Path('out_full/per_url').glob('*.csv')), min(5, len(list(Path('out_full/per_url').glob('*.csv')))))
for f in sample_files:
    print(f"\n--- {f.name} ---")
    d = pd.read_csv(f).head(3)
    print(d.to_string(index=False))