"""
Ingests drug-drug interactions and ATC mappings from Wikidata SPARQL into SQLite `data/rpl.db`.

Pipeline mapping:
  ATC code -> substance name -> substance interactions -> interacting ATC codes -> example RPL drugs
"""

import os
import sys
import sqlite3
import argparse
import requests
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_DB_PATH = "data/rpl.db"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"


def init_wikidata_tables(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS wikidata_atc_substance;")
    conn.execute("DROP TABLE IF EXISTS wikidata_interactions;")
    conn.execute("DROP TABLE IF EXISTS wikidata_substances;")

    conn.execute("""
    CREATE TABLE wikidata_substances (
        wikidata_id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    """)

    conn.execute("""
    CREATE TABLE wikidata_atc_substance (
        atc_code TEXT NOT NULL,
        substance_wikidata_id TEXT NOT NULL,
        PRIMARY KEY (atc_code, substance_wikidata_id),
        FOREIGN KEY (substance_wikidata_id) REFERENCES wikidata_substances(wikidata_id)
    );
    """)

    conn.execute("""
    CREATE TABLE wikidata_interactions (
        substance_wikidata_id TEXT NOT NULL,
        interacts_with_wikidata_id TEXT NOT NULL,
        PRIMARY KEY (substance_wikidata_id, interacts_with_wikidata_id),
        FOREIGN KEY (substance_wikidata_id) REFERENCES wikidata_substances(wikidata_id),
        FOREIGN KEY (interacts_with_wikidata_id) REFERENCES wikidata_substances(wikidata_id)
    );
    """)

    conn.execute("CREATE INDEX idx_w_atc ON wikidata_atc_substance(atc_code);")
    conn.execute("CREATE INDEX idx_w_sub_atc ON wikidata_atc_substance(substance_wikidata_id);")
    conn.execute("CREATE INDEX idx_w_inter_1 ON wikidata_interactions(substance_wikidata_id);")
    conn.execute("CREATE INDEX idx_w_inter_2 ON wikidata_interactions(interacts_with_wikidata_id);")
    conn.commit()


def extract_qid(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/").split("/")[-1]


def fetch_wikidata_interactions():
    console.print("[cyan]Fetching drug interactions and ATC mappings from Wikidata SPARQL...[/cyan]")
    
    # SPARQL query fetching interactions and ATC codes with Polish/English labels
    query = """
    SELECT ?substance ?substanceLabel ?substanceAtc ?interactsWith ?interactsWithLabel ?interactsWithAtc WHERE {
      ?substance wdt:P769 ?interactsWith .
      OPTIONAL { ?substance wdt:P267 ?substanceAtc . }
      OPTIONAL { ?interactsWith wdt:P267 ?interactsWithAtc . }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "pl,en". }
    }
    """
    
    headers = {
        "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; contact@example.com) Python-requests",
        "Accept": "application/sparql-results+json"
    }
    
    resp = requests.post(WIKIDATA_SPARQL_URL, data={"query": query}, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", {}).get("bindings", [])


def fetch_all_atc_substances():
    console.print("[cyan]Fetching broader ATC -> Substance mappings from Wikidata SPARQL...[/cyan]")
    query = """
    SELECT ?substance ?substanceLabel ?atc WHERE {
      ?substance wdt:P267 ?atc .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "pl,en". }
    }
    """
    headers = {
        "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; contact@example.com) Python-requests",
        "Accept": "application/sparql-results+json"
    }
    try:
        resp = requests.post(WIKIDATA_SPARQL_URL, data={"query": query}, headers=headers, timeout=90)
        if resp.status_code == 200:
            return resp.json().get("results", {}).get("bindings", [])
    except Exception as e:
        console.print(f"[yellow]Warning: broader ATC mapping query failed: {e}[/yellow]")
    return []


def import_wikidata_data(db_path: str):
    conn = sqlite3.connect(db_path)
    init_wikidata_tables(conn)

    bindings_interactions = fetch_wikidata_interactions()
    bindings_atc = fetch_all_atc_substances()

    substances = {}  # qid -> label
    atc_mappings = set()  # (atc_code, qid)
    interactions = set()  # (qid1, qid2)

    console.print(f"[green]Parsing {len(bindings_interactions)} interaction records...[/green]")
    for b in bindings_interactions:
        s_qid = extract_qid(b.get("substance", {}).get("value", ""))
        s_name = b.get("substanceLabel", {}).get("value", s_qid)
        s_atc = b.get("substanceAtc", {}).get("value", "").strip().upper()

        iw_qid = extract_qid(b.get("interactsWith", {}).get("value", ""))
        iw_name = b.get("interactsWithLabel", {}).get("value", iw_qid)
        iw_atc = b.get("interactsWithAtc", {}).get("value", "").strip().upper()

        if s_qid and s_name:
            substances[s_qid] = s_name
        if iw_qid and iw_name:
            substances[iw_qid] = iw_name

        if s_atc and s_qid:
            atc_mappings.add((s_atc, s_qid))
        if iw_atc and iw_qid:
            atc_mappings.add((iw_atc, iw_qid))

        if s_qid and iw_qid:
            # Symmetrical interaction
            interactions.add((s_qid, iw_qid))
            interactions.add((iw_qid, s_qid))

    console.print(f"[green]Parsing {len(bindings_atc)} ATC records...[/green]")
    for b in bindings_atc:
        s_qid = extract_qid(b.get("substance", {}).get("value", ""))
        s_name = b.get("substanceLabel", {}).get("value", s_qid)
        atc = b.get("atc", {}).get("value", "").strip().upper()

        if s_qid and s_name:
            if s_qid not in substances or substances[s_qid].startswith("Q"):
                substances[s_qid] = s_name
        if atc and s_qid:
            atc_mappings.add((atc, s_qid))

    console.print(f"[cyan]Inserting into database: {len(substances)} substances, {len(atc_mappings)} ATC links, {len(interactions)} interaction pairs...[/cyan]")

    conn.executemany(
        "INSERT OR REPLACE INTO wikidata_substances (wikidata_id, name) VALUES (?, ?)",
        list(substances.items())
    )

    conn.executemany(
        "INSERT OR REPLACE INTO wikidata_atc_substance (atc_code, substance_wikidata_id) VALUES (?, ?)",
        list(atc_mappings)
    )

    conn.executemany(
        "INSERT OR REPLACE INTO wikidata_interactions (substance_wikidata_id, interacts_with_wikidata_id) VALUES (?, ?)",
        list(interactions)
    )

    conn.commit()

    # Verify matching with local RPL database
    c = conn.cursor()
    c.execute("""
        SELECT count(DISTINCT p.id)
        FROM produkty_lecznicze p
        JOIN kody_atc a ON a.produkt_id = p.id
        JOIN wikidata_atc_substance w ON w.atc_code = a.kod_atc
        JOIN wikidata_interactions wi ON wi.substance_wikidata_id = w.substance_wikidata_id
    """)
    matched_rpl_drugs = c.fetchone()[0]

    conn.close()

    table = Table(title="Statystyki zaimportowanych danych Wikidata")
    table.add_column("Kategoria", style="cyan")
    table.add_column("Liczba rekordów", justify="right", style="green")
    table.add_row("Substancje czynne (Wikidata)", f"{len(substances):,}")
    table.add_row("Mapowania kodów ATC", f"{len(atc_mappings):,}")
    table.add_row("Pary interakcji lekowych", f"{len(interactions):,}")
    table.add_row("[bold]Leki w RPL z interakcjami Wikidata[/bold]", f"[bold yellow]{matched_rpl_drugs:,}[/bold yellow]")
    console.print(table)
    console.print("[bold green]Import interakcji z Wikidata zakończony pomyślnie![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Import drug interactions and ATC mappings from Wikidata")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    args = parser.parse_args()

    import_wikidata_data(args.db)


if __name__ == "__main__":
    main()
