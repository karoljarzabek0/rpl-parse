"""
Scrapes the full 5-level ATC (Anatomical Therapeutic Chemical) classification hierarchy
from Polish Wikipedia (pl.wikipedia.org).

Stores the hierarchy in:
- `data/atc_hierarchy.json` (Structured dictionary for fast in-memory lookup)
- SQLite `data/rpl.db` in table `atc_klasyfikacja`
"""

import os
import sys
import re
import json
import time
import sqlite3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

MAIN_ATC_URL = "https://pl.wikipedia.org/wiki/Klasyfikacja_anatomiczno-terapeutyczno-chemiczna"
HEADERS = {
    "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; medical-atc-parser@example.com) Python-requests"
}

MAIN_GROUPS = {
    "A": "Przewód pokarmowy i metabolizm",
    "B": "Krew i układ krwiotwórczy",
    "C": "Układ sercowo-naczyniowy",
    "D": "Dermatologia",
    "G": "Układ moczowo-płciowy i hormony płciowe",
    "H": "Leki hormonalne do stosowania wewnętrznego (bez hormonów płciowych)",
    "J": "Leki stosowane w zakażeniach (przeciwinfekcyjne)",
    "L": "Leki przeciwnowotworowe i immunomodulujące",
    "M": "Układ mięśniowo-szkieletowy",
    "N": "Ośrodkowy układ nerwowy",
    "P": "Leki przeciwpasożytnicze, owadobójcze i repelenty",
    "R": "Układ oddechowy",
    "S": "Narządy wzroku i słuchu",
    "V": "Różne (varia)"
}


def get_level(code: str) -> int:
    length = len(code)
    if length == 1:
        return 1
    elif length == 3:
        return 2
    elif length == 4:
        return 3
    elif length == 5:
        return 4
    elif length == 7:
        return 5
    return 0


def get_parent_code(code: str) -> str:
    level = get_level(code)
    if level == 5:
        return code[:5]
    elif level == 4:
        return code[:4]
    elif level == 3:
        return code[:3]
    elif level == 2:
        return code[:1]
    return ""


def discover_subgroup_urls() -> list:
    console.print("[cyan]Discovering all ATC subgroup pages on Polish Wikipedia...[/cyan]")
    subgroups = set()
    for letter in MAIN_GROUPS.keys():
        url = f"https://pl.wikipedia.org/wiki/ATC_({letter})"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    m = re.search(r"/wiki/ATC_\(([A-Z]\d{2})\)", href)
                    if m:
                        subgroups.add(m.group(1))
        except Exception as e:
            console.print(f"[yellow]Warning fetching main group {letter}: {e}[/yellow]")
    
    # Fallback to standard known list if any failed
    if len(subgroups) < 80:
        console.print("[yellow]Expanding with standard known ATC groups...[/yellow]")
        standard = [
            "A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10", "A11", "A12", "A13", "A14", "A15", "A16",
            "B01", "B02", "B03", "B05", "B06",
            "C01", "C02", "C03", "C04", "C05", "C07", "C08", "C09", "C10",
            "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11",
            "G01", "G02", "G03", "G04",
            "H01", "H02", "H03", "H04", "H05",
            "J01", "J02", "J04", "J05", "J06", "J07",
            "L01", "L02", "L03", "L04",
            "M01", "M02", "M03", "M04", "M05", "M09",
            "N01", "N02", "N03", "N04", "N05", "N06", "N07",
            "P01", "P02", "P03",
            "R01", "R02", "R03", "R05", "R06", "R07",
            "S01", "S02", "S03",
            "V01", "V03", "V04", "V06", "V07", "V08", "V09", "V10", "V20"
        ]
        subgroups.update(standard)

    sorted_subgroups = sorted(list(subgroups))
    console.print(f"[green]Found {len(sorted_subgroups)} ATC subgroup pages to scrape.[/green]")
    return sorted_subgroups


def scrape_atc_page(subgroup_code: str) -> list:
    url = f"https://pl.wikipedia.org/wiki/ATC_({subgroup_code})"
    results = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return results
        
        soup = BeautifulSoup(r.text, "html.parser")
        body = soup.find("div", class_="mw-parser-output")
        if not body:
            return results

        lines = body.get_text().split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 3:
                continue

            # Remove edit tags like [edytuj | edytuj kod]
            line = re.sub(r"\[edytuj.*?\]", "", line).strip()

            # Matches format: 'A 01 AA 01 – fluorek sodu' or 'A 01 A – Preparaty stomatologiczne' or 'A01AA01 - ...'
            m = re.match(r"^([A-Z]\s*(?:\d{2}(?:\s*[A-Z](?:\s*[A-Z](?:\s*\d{2})?)?)?)?)\s*[–—\-:]\s*(.+)$", line)
            if m:
                raw_code = m.group(1).strip()
                name = m.group(2).strip()
                code = re.sub(r"\s+", "", raw_code)

                # Clean name from references like [1], [2]
                name = re.sub(r"\[\d+\]", "", name).strip()
                # Clean header concatenation if another code follows in text
                name = re.split(r"[A-Z]\s*\d{2}\s*[A-Z]", name)[0].strip()

                if code and len(code) in (1, 3, 4, 5, 7) and name:
                    results.append((code, name))
    except Exception as e:
        console.print(f"[yellow]Error scraping {subgroup_code}: {e}[/yellow]")

    return results


def build_full_hierarchy():
    atc_dict = {}  # code -> { code, level, name, parent, path }

    # 1. Add Level 1 (Main anatomical groups)
    for letter, name in MAIN_GROUPS.items():
        atc_dict[letter] = {
            "code": letter,
            "level": 1,
            "level_name": "Anatomiczna grupa główna",
            "name": name,
            "parent": None,
            "path": [name]
        }

    subgroups = discover_subgroup_urls()

    all_scraped = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Scraping Wikipedia ATC pages...", total=len(subgroups))
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_code = {executor.submit(scrape_atc_page, sg): sg for sg in subgroups}
            for future in as_completed(future_to_code):
                res = future.result()
                all_scraped.extend(res)
                progress.advance(task)

    console.print(f"[green]Scraped {len(all_scraped)} total raw ATC entries from Wikipedia.[/green]")

    # Sort entries by length (level 1 to 5) so parents are processed before children
    all_scraped.sort(key=lambda x: len(x[0]))

    level_names = {
        1: "Anatomiczna grupa główna",
        2: "Grupa terapeutyczna",
        3: "Podgrupa farmakologiczna",
        4: "Podgrupa chemiczna",
        5: "Substancja chemiczna"
    }

    # Populate dictionary
    for code, name in all_scraped:
        level = get_level(code)
        if level == 0:
            continue

        parent_code = get_parent_code(code)
        
        # If already exists and has better capitalized name, update
        if code in atc_dict and len(atc_dict[code]["name"]) > len(name):
            continue

        parent_info = atc_dict.get(parent_code)
        parent_path = parent_info["path"] if parent_info else []
        full_path = parent_path + [name]

        atc_dict[code] = {
            "code": code,
            "level": level,
            "level_name": level_names.get(level, f"Poziom {level}"),
            "name": name,
            "parent": parent_code if parent_code else None,
            "path": full_path
        }

    # Second pass: ensure all paths are complete
    for code, item in atc_dict.items():
        curr = code
        path = []
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            node = atc_dict.get(curr)
            if node:
                path.insert(0, node["name"])
                curr = node.get("parent")
            else:
                break
        item["path"] = path

    return atc_dict


def save_to_db(atc_dict: dict, db_path: str):
    console.print(f"[cyan]Saving {len(atc_dict)} ATC entries into database {db_path}...[/cyan]")
    conn = sqlite3.connect(db_path)
    
    conn.execute("DROP TABLE IF EXISTS atc_klasyfikacja;")
    conn.execute("""
    CREATE TABLE atc_klasyfikacja (
        kod_atc TEXT PRIMARY KEY,
        poziom INTEGER NOT NULL,
        poziom_nazwa TEXT NOT NULL,
        nazwa TEXT NOT NULL,
        kod_rodzica TEXT,
        pelna_sciezka_json TEXT NOT NULL
    );
    """)

    conn.execute("CREATE INDEX idx_atc_level ON atc_klasyfikacja(poziom);")
    conn.execute("CREATE INDEX idx_atc_parent ON atc_klasyfikacja(kod_rodzica);")

    rows = []
    for code, item in atc_dict.items():
        rows.append((
            item["code"],
            item["level"],
            item["level_name"],
            item["name"],
            item["parent"],
            json.dumps(item["path"], ensure_ascii=False)
        ))

    conn.executemany("""
    INSERT OR REPLACE INTO atc_klasyfikacja 
    (kod_atc, poziom, poziom_nazwa, nazwa, kod_rodzica, pelna_sciezka_json)
    VALUES (?, ?, ?, ?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()
    console.print("[bold green]ATC classification table created and populated successfully![/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Scrape full ATC code hierarchy from Polish Wikipedia")
    parser.add_argument("--db", type=str, default="data/rpl.db", help="Path to SQLite database")
    parser.add_argument("--out-json", type=str, default="data/atc_hierarchy.json", help="Path to output JSON")
    args = parser.parse_args()

    atc_dict = build_full_hierarchy()

    os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(atc_dict, f, ensure_ascii=False, indent=2)

    console.print(f"[green]Saved hierarchy JSON to {args.out_json} ({len(atc_dict)} codes).[/green]")

    save_to_db(atc_dict, args.db)


if __name__ == "__main__":
    main()
