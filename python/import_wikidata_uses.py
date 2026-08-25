"""
Ingests medical uses / conditions treated (wdt:P2175 & wdt:P366) and ATC mappings 
from Wikidata SPARQL into SQLite `data/rpl.db`.

Pipeline mapping:
  Medicine -> ATC codes -> Wikidata substances -> Medical conditions treated (Zastosowanie lecznicze)
"""

import os
import sys
import sqlite3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

DEFAULT_DB_PATH = "data/rpl.db"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; medical-uses@example.com) Python-requests",
    "Accept": "application/sparql-results+json"
}


def init_wikidata_uses_tables(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS wikidata_substance_conditions;")
    conn.execute("DROP TABLE IF EXISTS wikidata_conditions;")

    # Ensure base tables exist
    conn.execute("""
    CREATE TABLE IF NOT EXISTS wikidata_substances (
        wikidata_id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS wikidata_atc_substance (
        atc_code TEXT NOT NULL,
        substance_wikidata_id TEXT NOT NULL,
        PRIMARY KEY (atc_code, substance_wikidata_id),
        FOREIGN KEY (substance_wikidata_id) REFERENCES wikidata_substances(wikidata_id)
    );
    """)

    conn.execute("""
    CREATE TABLE wikidata_conditions (
        condition_wikidata_id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    """)

    conn.execute("""
    CREATE TABLE wikidata_substance_conditions (
        substance_wikidata_id TEXT NOT NULL,
        condition_wikidata_id TEXT NOT NULL,
        use_type TEXT DEFAULT 'condition_treated',
        PRIMARY KEY (substance_wikidata_id, condition_wikidata_id),
        FOREIGN KEY (substance_wikidata_id) REFERENCES wikidata_substances(wikidata_id),
        FOREIGN KEY (condition_wikidata_id) REFERENCES wikidata_conditions(condition_wikidata_id)
    );
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_w_atc ON wikidata_atc_substance(atc_code);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_w_sub_atc ON wikidata_atc_substance(substance_wikidata_id);")
    conn.execute("CREATE INDEX idx_w_sub_cond ON wikidata_substance_conditions(substance_wikidata_id);")
    conn.execute("CREATE INDEX idx_w_cond ON wikidata_substance_conditions(condition_wikidata_id);")
    conn.commit()


def extract_qid(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/").split("/")[-1]


def fetch_conditions_chunk(chunk_atc: list) -> list:
    atc_str = " ".join(f'"{code}"' for code in chunk_atc)
    query = f"""
    SELECT ?substance ?substanceLabel ?atc ?condition ?conditionLabel WHERE {{
      VALUES ?atc {{ {atc_str} }}
      ?substance wdt:P267 ?atc ;
                 wdt:P2175 ?condition .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    try:
        r = requests.get(WIKIDATA_SPARQL_URL, params={"query": query, "format": "json"}, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json().get("results", {}).get("bindings", [])
    except Exception as e:
        console.print(f"[yellow]Warning querying chunk: {e}[/yellow]")
    return []


def import_wikidata_uses(db_path: str):
    console.print(f"[cyan]Connecting to SQLite database: {db_path}[/cyan]")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    init_wikidata_uses_tables(conn)

    c.execute("SELECT DISTINCT kod_atc FROM kody_atc WHERE kod_atc IS NOT NULL AND kod_atc != ''")
    atc_codes = [r[0].strip().upper() for r in c.fetchall()]
    console.print(f"[cyan]Found {len(atc_codes)} unique ATC codes in RPL database.[/cyan]")

    chunk_size = 80
    chunks = [atc_codes[i:i + chunk_size] for i in range(0, len(atc_codes), chunk_size)]

    all_raw_results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Fetching medical conditions (P2175) from Wikidata...", total=len(chunks))
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_chunk = {executor.submit(fetch_conditions_chunk, ch): ch for ch in chunks}
            for future in as_completed(future_to_chunk):
                res = future.result()
                all_raw_results.extend(res)
                progress.advance(task)

    console.print(f"[green]Retrieved {len(all_raw_results)} total condition-substance bindings.[/green]")

    substances = {}
    atc_substance_links = set()
    conditions = {}
    substance_condition_links = set()

    for item in all_raw_results:
        sub_url = item.get("substance", {}).get("value", "")
        sub_qid = extract_qid(sub_url)
        sub_label = item.get("substanceLabel", {}).get("value", sub_qid)

        cond_url = item.get("condition", {}).get("value", "")
        cond_qid = extract_qid(cond_url)
        cond_label = item.get("conditionLabel", {}).get("value", cond_qid)

        atc_code = (item.get("atc", {}).get("value") or "").strip().upper()

        if sub_qid and sub_label:
            substances[sub_qid] = sub_label
        if cond_qid and cond_label:
            conditions[cond_qid] = cond_label
        if atc_code and sub_qid:
            atc_substance_links.add((atc_code, sub_qid))
        if sub_qid and cond_qid:
            substance_condition_links.add((sub_qid, cond_qid, "condition_treated"))

    console.print(f"[cyan]Ingesting {len(substances)} substances, {len(conditions)} conditions, {len(atc_substance_links)} ATC links, and {len(substance_condition_links)} uses into database...[/cyan]")

    conn.execute("BEGIN TRANSACTION;")
    try:
        for qid, name in substances.items():
            conn.execute("INSERT OR REPLACE INTO wikidata_substances (wikidata_id, name) VALUES (?, ?)", (qid, name))

        for atc, qid in atc_substance_links:
            conn.execute("INSERT OR REPLACE INTO wikidata_atc_substance (atc_code, substance_wikidata_id) VALUES (?, ?)", (atc, qid))

        for qid, name in conditions.items():
            conn.execute("INSERT OR REPLACE INTO wikidata_conditions (condition_wikidata_id, name) VALUES (?, ?)", (qid, name))

        for sub_qid, cond_qid, use_type in substance_condition_links:
            conn.execute("INSERT OR REPLACE INTO wikidata_substance_conditions (substance_wikidata_id, condition_wikidata_id, use_type) VALUES (?, ?, ?)", (sub_qid, cond_qid, use_type))

        conn.commit()
        console.print("[bold green]Wikidata medical uses successfully imported into SQLite![/bold green]")
    except Exception as e:
        conn.rollback()
        console.print(f"[red]Error importing uses into database: {e}[/red]")
        conn.close()
        return

    # Check how many RPL medicines now have mapped Wikidata uses
    c.execute("""
        SELECT count(DISTINCT p.id)
        FROM produkty_lecznicze p
        JOIN kody_atc ka ON ka.produkt_id = p.id
        JOIN wikidata_atc_substance was ON was.atc_code = ka.kod_atc
        JOIN wikidata_substance_conditions wsc ON wsc.substance_wikidata_id = was.substance_wikidata_id
    """)
    matched_drugs = c.fetchone()[0]

    table = Table(title="Statystyki zaimportowanych danych Zastosowania (Wikidata)")
    table.add_column("Metryka", style="cyan")
    table.add_column("Wartość", justify="right", style="green")
    table.add_row("Unikalne substancje lecznicze", f"{len(substances):,}")
    table.add_row("Unikalne leczone stany / wskazania (P2175)", f"{len(conditions):,}")
    table.add_row("Powiązania substancja ↔ zastosowanie", f"{len(substance_condition_links):,}")
    table.add_row("Powiązania kod ATC ↔ substancja", f"{len(atc_substance_links):,}")
    table.add_row("[bold]Leki w RPL z danymi o zastosowaniu[/bold]", f"[bold yellow]{matched_drugs:,}[/bold yellow]")
    console.print(table)

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import medical uses from Wikidata")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    args = parser.parse_args()

    import_wikidata_uses(args.db)


if __name__ == "__main__":
    main()
