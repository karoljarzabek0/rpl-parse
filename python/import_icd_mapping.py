"""
ICD-11 & ICD-10 Mapping Pipeline for RPL Medicines & Wikidata Conditions.

Pipeline Workflow:
1. Ingests official WHO/CeZ Polish ICD-11 XML (`icd11_2026-01_pl_in.xml`) -> Foundation IDs, MMS Codes, Official PL Titles, Browser URLs.
2. Ingests official ICD-10 to ICD-11 mapping table (`10To11MapdowieluKategorii.xlsx`).
3. Fetches ICD-10 (P494, P4229) and ICD-11 (P7807 Foundation ID, P7329 MMS Code) properties from Wikidata SPARQL for all disease/condition QIDs.
4. Multi-Layer Cross-Referencing & Translation:
   - Primary: Match via Foundation ID in Polish ICD-11 XML.
   - Secondary: Match via MMS Code in Polish ICD-11 XML.
   - Tertiary: Translate ICD-10 -> ICD-11 via 10To11 official cross-walk table.
   - Quaternary: Reverse-map ICD-11 -> ICD-10.
5. Updates SQLite `data/rpl.db` (`wikidata_conditions` table) with structured ICD metadata.
"""

import os
import sys
import time
import re
import sqlite3
import argparse
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Set
import requests
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

DEFAULT_DB_PATH = "data/rpl.db"
DEFAULT_ICD11_XML = "/home/karol/Pobrane/icd11_2026-01_pl_in.xml"
DEFAULT_ICD10_11_EXCEL = "data/icd_raw/10To11MapdowieluKategorii.xlsx"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; icd-mapping@example.com) Python-requests",
    "Accept": "application/sparql-results+json"
}


def load_icd11_xml(xml_path: str):
    console.print(f"[cyan]Parsing official Polish ICD-11 XML: {xml_path}...[/cyan]")
    t0 = time.time()
    tree = ET.parse(xml_path)
    root = tree.getroot()

    icd11_by_fid = {}
    icd11_by_code = {}

    for el in root.findall(".//element"):
        el_id = el.findtext("id", "")
        code = el.findtext("code", "")
        title_el = el.find(".//title/value")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        b_url = el.findtext("browserUrl", "")

        m = re.search(r"/mms/(\d+)", el_id)
        if m:
            fid = m.group(1)
            if fid not in icd11_by_fid or (title and not icd11_by_fid[fid].get("title")):
                icd11_by_fid[fid] = {
                    "code": code,
                    "title": title,
                    "url": (b_url.replace("/mms/pl", "/mms/en") if b_url else f"https://icd.who.int/browse/2026-01/mms/en#{fid}"),
                    "fid": fid
                }
        if code:
            if code not in icd11_by_code or (title and not icd11_by_code[code].get("title")):
                icd11_by_code[code] = {
                    "code": code,
                    "title": title,
                    "url": (b_url.replace("/mms/pl", "/mms/en") if b_url else (f"https://icd.who.int/browse/2026-01/mms/en#{m.group(1)}" if m else "")),
                    "fid": m.group(1) if m else ""
                }

    elapsed = time.time() - t0
    console.print(f"[green]Indexed {len(icd11_by_fid)} Foundation IDs & {len(icd11_by_code)} MMS codes in {elapsed:.2f}s.[/green]")
    return icd11_by_fid, icd11_by_code


def load_icd10_to_11_mapping(excel_path: str):
    console.print(f"[cyan]Loading ICD-10 -> ICD-11 mapping from {excel_path}...[/cyan]")
    t0 = time.time()
    df_map = pd.read_excel(excel_path)

    icd10_to_11 = {}
    icd11_to_10 = {}

    for _, row in df_map.dropna(subset=["icd10Code"]).iterrows():
        c10 = str(row["icd10Code"]).strip().upper()
        c11 = str(row["icd11Code"]).strip() if pd.notna(row["icd11Code"]) else ""
        f_uri = str(row["ICD-11 Foundation URI"]).strip() if pd.notna(row["ICD-11 Foundation URI"]) else ""
        fid = f_uri.split("/")[-1] if f_uri else ""
        t10 = str(row["icd10Title"]).strip() if pd.notna(row["icd10Title"]) else ""
        t11 = str(row["icd11Title"]).strip() if pd.notna(row["icd11Title"]) else ""

        entry = {
            "icd10_code": c10,
            "icd11_code": c11,
            "fid": fid,
            "title10": t10,
            "title11": t11
        }

        icd10_to_11.setdefault(c10, []).append(entry)
        if c11:
            icd11_to_10.setdefault(c11, []).append(entry)
        if fid:
            icd11_to_10.setdefault(fid, []).append(entry)

    elapsed = time.time() - t0
    console.print(f"[green]Loaded {len(icd10_to_11)} ICD-10 cross-walk rules in {elapsed:.2f}s.[/green]")
    return icd10_to_11, icd11_to_10


def update_db_schema(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute("PRAGMA table_info(wikidata_conditions);")
    existing_cols = set(row[1] for row in c.fetchall())

    new_columns = [
        ("official_pl_name", "TEXT"),
        ("icd11_mms", "TEXT"),
        ("icd11_foundation_id", "TEXT"),
        ("icd11_url", "TEXT"),
        ("icd10_codes", "TEXT"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            console.print(f"[yellow]Adding column {col_name} ({col_type}) to wikidata_conditions...[/yellow]")
            conn.execute(f"ALTER TABLE wikidata_conditions ADD COLUMN {col_name} {col_type};")

    conn.commit()


def fetch_wikidata_icd_properties(all_qids: List[str]) -> Dict[str, Dict[str, Set[str]]]:
    chunk_size = 60
    chunks = [all_qids[i:i + chunk_size] for i in range(0, len(all_qids), chunk_size)]
    wikidata_props: Dict[str, Dict[str, Set[str]]] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Fetching ICD codes from Wikidata SPARQL...", total=len(chunks))

        for ch in chunks:
            qid_str = " ".join(f"wd:{qid}" for qid in ch)
            query = f"""
            SELECT ?condition ?p ?val WHERE {{
              VALUES ?condition {{ {qid_str} }}
              VALUES ?p {{ wdt:P494 wdt:P4229 wdt:P7807 wdt:P7329 }}
              ?condition ?p ?val .
            }}
            """
            for attempt in range(3):
                try:
                    r = requests.get(WIKIDATA_SPARQL_URL, params={"query": query, "format": "json"}, headers=HEADERS, timeout=20)
                    if r.status_code == 200:
                        for row in r.json().get("results", {}).get("bindings", []):
                            qid = row.get("condition", {}).get("value", "").split("/")[-1]
                            prop = row.get("p", {}).get("value", "").split("/")[-1]
                            val = row.get("val", {}).get("value", "").strip()
                            if qid not in wikidata_props:
                                wikidata_props[qid] = {"icd10": set(), "icd10cm": set(), "icd11fid": set(), "icd11mms": set()}
                            if prop == "P494":
                                wikidata_props[qid]["icd10"].add(val)
                            elif prop == "P4229":
                                wikidata_props[qid]["icd10cm"].add(val)
                            elif prop == "P7807":
                                wikidata_props[qid]["icd11fid"].add(val)
                            elif prop == "P7329":
                                wikidata_props[qid]["icd11mms"].add(val)
                        break
                except Exception as e:
                    time.sleep(1.0 * (attempt + 1))
            time.sleep(0.15)
            progress.advance(task)

    return wikidata_props


def map_and_save_conditions(
    conn: sqlite3.Connection,
    db_conditions: Dict[str, str],
    wikidata_props: Dict[str, Dict[str, Set[str]]],
    icd11_by_fid: Dict[str, Any],
    icd11_by_code: Dict[str, Any],
    icd10_to_11: Dict[str, Any],
    icd11_to_10: Dict[str, Any]
):
    console.print("[cyan]Resolving multi-layer ICD mappings & official Polish titles...[/cyan]")
    resolved_rows = []
    mapped_count = 0

    for qid, default_name in db_conditions.items():
        props = wikidata_props.get(qid, {"icd10": set(), "icd10cm": set(), "icd11fid": set(), "icd11mms": set()})

        icd10_codes = sorted(list(props["icd10"] or props["icd10cm"]))
        icd11_fids = sorted(list(props["icd11fid"]))
        icd11_mms_codes = sorted(list(props["icd11mms"]))

        official_pl_name = ""
        icd11_code_final = ""
        icd11_fid_final = ""
        icd11_url_final = ""

        # Priority 1: Match via Foundation ID in Polish XML
        for fid in icd11_fids:
            if fid in icd11_by_fid:
                node = icd11_by_fid[fid]
                if node.get("title"):
                    official_pl_name = node["title"]
                icd11_code_final = node.get("code") or icd11_code_final
                icd11_fid_final = fid
                icd11_url_final = node.get("url", f"https://icd.who.int/browse/2026-01/mms/en#{fid}")
                break

        # Priority 2: Match via MMS Code in Polish XML
        if not official_pl_name:
            for code in icd11_mms_codes:
                if code in icd11_by_code:
                    node = icd11_by_code[code]
                    if node.get("title"):
                        official_pl_name = node["title"]
                    icd11_code_final = code
                    icd11_fid_final = node.get("fid") or icd11_fid_final
                    icd11_url_final = node.get("url", f"https://icd.who.int/browse/2026-01/mms/en#{icd11_fid_final}" if icd11_fid_final else "")
                    break

        # Priority 3: Translate ICD-10 -> ICD-11 via 10To11 mapping table
        if not icd11_code_final:
            for c10 in icd10_codes:
                base10 = c10.split(".")[0]
                for key in [c10, base10]:
                    if key in icd10_to_11:
                        m = icd10_to_11[key][0]
                        icd11_code_final = m["icd11_code"]
                        icd11_fid_final = m["fid"]
                        if m.get("title11") and not official_pl_name:
                            official_pl_name = m["title11"]
                        if icd11_fid_final and not icd11_url_final:
                            icd11_url_final = f"https://icd.who.int/browse/2026-01/mms/en#{icd11_fid_final}"
                        break
                if icd11_code_final:
                    break

        # Reverse Mapping: If ICD-10 is missing, reverse-map from ICD-11
        if not icd10_codes:
            for k in [icd11_code_final, icd11_fid_final]:
                if k and k in icd11_to_10:
                    matched_10 = icd11_to_10[k][0]["icd10_code"]
                    if matched_10:
                        icd10_codes = [matched_10]
                        break

        # Fallback names
        if not official_pl_name:
            official_pl_name = default_name

        if icd11_code_final or icd11_fid_final or icd10_codes:
            mapped_count += 1

        resolved_rows.append((
            official_pl_name,
            icd11_code_final or (icd11_mms_codes[0] if icd11_mms_codes else ""),
            icd11_fid_final or (icd11_fids[0] if icd11_fids else ""),
            icd11_url_final,
            ", ".join(icd10_codes),
            qid
        ))

    console.print(f"[cyan]Updating database with {len(resolved_rows)} mapped conditions...[/cyan]")
    cursor = conn.cursor()
    cursor.executemany("""
        UPDATE wikidata_conditions
        SET official_pl_name = ?,
            icd11_mms = ?,
            icd11_foundation_id = ?,
            icd11_url = ?,
            icd10_codes = ?
        WHERE condition_wikidata_id = ?
    """, resolved_rows)
    conn.commit()

    pct = (mapped_count / len(db_conditions)) * 100
    console.print(f"[green]Successfully mapped {mapped_count} / {len(db_conditions)} conditions ({pct:.1f}%) to ICD-11 & ICD-10![/green]")

    # Print Summary Table
    table = Table(title="Sample Mapped Medical Indications (Wikidata + Polish ICD-11 / ICD-10)")
    table.add_column("QID", style="cyan")
    table.add_column("Official Polish Title (ICD-11)", style="green bold")
    table.add_column("ICD-11 MMS", style="magenta")
    table.add_column("Foundation ID", style="blue")
    table.add_column("ICD-10", style="yellow")

    for row in resolved_rows[:15]:
        table.add_row(row[5], row[0], row[1] or "-", row[2] or "-", row[4] or "-")

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Import ICD-11 & ICD-10 mappings for Wikidata conditions")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--icd11-xml", type=str, default=DEFAULT_ICD11_XML, help="Path to Polish ICD-11 XML")
    parser.add_argument("--icd10-excel", type=str, default=DEFAULT_ICD10_11_EXCEL, help="Path to ICD-10 to 11 Excel map")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    update_db_schema(conn)

    icd11_by_fid, icd11_by_code = load_icd11_xml(args.icd11_xml)
    icd10_to_11, icd11_to_10 = load_icd10_to_11_mapping(args.icd10_excel)

    cursor = conn.cursor()
    cursor.execute("SELECT condition_wikidata_id, name FROM wikidata_conditions")
    db_conditions = {r[0]: r[1] for r in cursor.fetchall()}
    console.print(f"[cyan]Loaded {len(db_conditions)} unique conditions from {args.db}.[/cyan]")

    wikidata_props = fetch_wikidata_icd_properties(list(db_conditions.keys()))

    map_and_save_conditions(
        conn,
        db_conditions,
        wikidata_props,
        icd11_by_fid,
        icd11_by_code,
        icd10_to_11,
        icd11_to_10
    )

    conn.close()


if __name__ == "__main__":
    main()
