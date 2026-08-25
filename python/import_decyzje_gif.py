"""
Imports Chief Pharmaceutical Inspectorate (GIF / RDG) official regulatory decisions
(Wycofania, Wstrzymania w obrocie, Zakazy wprowadzania) into SQLite database `data/rpl.db`.
"""

import os
import sys
import sqlite3
import argparse
import requests
import xml.etree.ElementTree as ET
from rich.console import Console
from rich.table import Table

console = Console()

RDG_XML_URL = "https://rdg.ezdrowie.gov.pl/Decision/DownloadPublicXml"
DEFAULT_XML_PATH = "data/decyzje_gif.xml"
DEFAULT_DB_PATH = "data/rpl.db"


def fetch_rdg_xml(url: str, output_path: str) -> str:
    console.print(f"[cyan]Fetching latest RDG XML decisions from: {url}...[/cyan]")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(resp.content)
    console.print(f"[green]Saved XML ({len(resp.content):,} bytes) to {output_path}.[/green]")
    return output_path


def init_decyzje_table(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS decyzje_gif;")
    conn.execute("""
    CREATE TABLE decyzje_gif (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numer_decyzji TEXT NOT NULL,
        data_decyzji TEXT,
        rodzaj_decyzji TEXT NOT NULL,
        nazwa_produktu TEXT,
        moc TEXT,
        postac TEXT,
        podmiot_odpowiedzialny TEXT,
        kod_gtin TEXT,
        kod_gtin_norm TEXT,
        numer_serii TEXT,
        data_waznosci TEXT,
        link_decyzja TEXT,
        produkt_id INTEGER
    );
    """)
    conn.execute("CREATE INDEX idx_decyzje_gtin_norm ON decyzje_gif(kod_gtin_norm);")
    conn.execute("CREATE INDEX idx_decyzje_produkt_id ON decyzje_gif(produkt_id);")
    conn.execute("CREATE INDEX idx_decyzje_rodzaj ON decyzje_gif(rodzaj_decyzji);")
    conn.commit()


def parse_and_import_decyzje(xml_path: str, db_path: str):
    console.print(f"[cyan]Parsing decisions XML from: {xml_path}...[/cyan]")
    tree = ET.parse(xml_path)
    root = tree.getroot()

    conn = sqlite3.connect(db_path)
    init_decyzje_table(conn)

    # Pre-fetch lookup maps from SQLite for matching
    c = conn.cursor()
    c.execute("SELECT ltrim(kod_gtin, '0'), produkt_id FROM opakowania WHERE kod_gtin IS NOT NULL")
    gtin_to_pid = {r[0]: r[1] for r in c.fetchall() if r[0]}

    c.execute("SELECT lower(nazwa_produktu), id FROM produkty_lecznicze")
    name_to_pid = {}
    for r in c.fetchall():
        name_to_pid.setdefault(r[0], []).append(r[1])

    records = []
    matched_count = 0

    for dec in root.findall("Decyzja"):
        data_dec = (dec.findtext("DataDecyzji") or "").strip()
        nr_dec = (dec.findtext("NumerDecyzji") or "").strip()
        link_dec = (dec.findtext("LinkDoPobraniaDecyzji") or "").strip()

        przyczyna_el = dec.find("Przyczyna")
        rodzaj_dec = "Inna decyzja"
        nazwa_prod = ""
        moc = ""
        postac = ""
        podmiot = ""

        if przyczyna_el is not None:
            rodzaj_dec = (przyczyna_el.findtext("PrzyczynaNazwa") or "Inna decyzja").strip()
            prod_el = przyczyna_el.find("Produkt")
            if prod_el is not None:
                nazwa_prod = (prod_el.findtext("NazwaProduktuLeczniczego") or "").strip()
                moc = (prod_el.findtext("Moc") or "").strip()
                postac = (prod_el.findtext("Postac") or "").strip()
                podmiot = (prod_el.findtext("NazwaPodmiotuOdpowiedzialnego") or "").strip()

        # Extract unique GTINs, Series, Expirations
        gtins = list(dict.fromkeys([g.text.strip() for g in dec.findall(".//GTIN") if g.text and g.text.strip()]))
        series = list(dict.fromkeys([s.text.strip() for s in dec.findall(".//NumerSerii") if s.text and s.text.strip()]))
        exp_dates = list(dict.fromkeys([d.text.strip().split("T")[0] for d in dec.findall(".//DataWaznosci") if d.text and d.text.strip()]))

        seria_str = ", ".join(series) if series else ""
        exp_str = ", ".join(exp_dates) if exp_dates else ""

        # Determine matched product ID
        matched_pid = None
        matched_gtin = ""
        for g in gtins:
            norm_g = g.replace(" ", "").lstrip("0")
            if norm_g in gtin_to_pid:
                matched_pid = gtin_to_pid[norm_g]
                matched_gtin = g
                break

        if matched_pid is None and nazwa_prod and nazwa_prod.lower() in name_to_pid:
            matched_pid = name_to_pid[nazwa_prod.lower()][0]

        if matched_pid is not None:
            matched_count += 1

        norm_gtin = matched_gtin.replace(" ", "").lstrip("0") if matched_gtin else (gtins[0].replace(" ", "").lstrip("0") if gtins else "")
        raw_gtin = matched_gtin or (gtins[0] if gtins else "")

        records.append((
            nr_dec,
            data_dec.split("T")[0] if data_dec else "",
            rodzaj_dec,
            nazwa_prod,
            moc,
            postac,
            podmiot,
            raw_gtin,
            norm_gtin,
            seria_str,
            exp_str,
            link_dec,
            matched_pid
        ))

    console.print(f"[cyan]Inserting {len(records)} decision rows into SQLite...[/cyan]")
    conn.executemany("""
    INSERT INTO decyzje_gif (
        numer_decyzji, data_decyzji, rodzaj_decyzji, nazwa_produktu, moc, postac,
        podmiot_odpowiedzialny, kod_gtin, kod_gtin_norm, numer_serii, data_waznosci,
        link_decyzja, produkt_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()

    # Summary
    c.execute("SELECT rodzaj_decyzji, count(*) FROM decyzje_gif GROUP BY rodzaj_decyzji")
    rows = c.fetchall()
    c.execute("SELECT count(DISTINCT produkt_id) FROM decyzje_gif WHERE produkt_id IS NOT NULL")
    unique_prods = c.fetchone()[0]

    conn.close()

    table = Table(title="Statystyki zaimportowanych decyzji GIF (RDG)")
    table.add_column("Rodzaj decyzji", style="cyan")
    table.add_column("Liczba decyzji", justify="right", style="green")
    for r in rows:
        table.add_row(r[0], str(r[1]))
    table.add_row("[bold]Łącznie powiązanych leków w RPL[/bold]", f"[bold yellow]{unique_prods}[/bold yellow]")

    console.print(table)
    console.print(f"[bold green]Import decyzji GIF zakończony pomyślnie![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Import GIF / RDG Drug Decisions XML into SQLite")
    parser.add_argument("--xml", type=str, default=DEFAULT_XML_PATH, help="Path to downloaded XML file")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--fetch", action="store_true", help="Download fresh XML from eZdrowie RDG")
    args = parser.parse_args()

    xml_file = args.xml
    if args.fetch or not os.path.exists(xml_file):
        xml_file = fetch_rdg_xml(RDG_XML_URL, xml_file)

    parse_and_import_decyzje(xml_file, args.db)


if __name__ == "__main__":
    main()
