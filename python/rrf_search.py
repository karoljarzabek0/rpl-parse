"""
Reciprocal Rank Fusion (RRF) Hybrid Search Engine for Polish Medicinal Products (RPL).
Combines:
  1. Dense Vector Search: sqlite-vec (vec0) with PolDense-400M (1024-d mean chunk embeddings)
  2. Sparse Full-Text Search: SQLite FTS5 with custom Morfeusz Polish lemmatizer
  3. Relational Metadata: XML data (Active substances, ATC classification, Packaging, Manufacturer)
"""

import os
import sys
import json
import sqlite3
import argparse
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

MODEL_NAME = "OPI-PIB/PolDense-400M"
DEFAULT_DB_PATH = "data/rpl.db"
DEFAULT_ATC_MAP_PATH = "data/atc_map.json"
VEC_EXT_PATH = "extensions/vec0.so"
MORFEUSZ_EXT_PATH = "extensions/morfeusz.so"


class RPLHybridRRFSearch:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        atc_map_path: str = DEFAULT_ATC_MAP_PATH,
        vec_ext_path: str = VEC_EXT_PATH,
        morfeusz_ext_path: str = MORFEUSZ_EXT_PATH,
        device: Optional[str] = None
    ):
        # Resolve paths
        for path_attr, path_val in [
            ("db_path", db_path),
            ("atc_map_path", atc_map_path),
            ("vec_ext_path", vec_ext_path),
            ("morfeusz_ext_path", morfeusz_ext_path)
        ]:
            if not os.path.exists(path_val) and os.path.exists(f"../{path_val}"):
                setattr(self, path_attr, f"../{path_val}")
            else:
                setattr(self, path_attr, path_val)

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        # Load ATC mapping
        self.atc_map = {}
        if os.path.exists(self.atc_map_path):
            try:
                with open(self.atc_map_path, "r", encoding="utf-8") as f:
                    self.atc_map = json.load(f)
            except Exception as e:
                console.print(f"[yellow]Warning loading ATC map: {e}[/yellow]")

        # Initialize SQLite Connection with extensions
        self.conn = self._create_db_connection()

        # Load embedding model
        console.print(f"[cyan]Loading PolDense-400M model on {self.device}...[/cyan]")
        self.model = SentenceTransformer(
            MODEL_NAME,
            device=self.device,
            model_kwargs={
                "torch_dtype": torch.bfloat16 if self.device == "cuda" else torch.float32,
            }
        )
        console.print("[green]PolDense-400M & SQLite RRF Search Engine ready![/green]\n")

    def _create_db_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)

        if not os.path.exists(self.vec_ext_path):
            raise FileNotFoundError(f"sqlite-vec extension not found at: {self.vec_ext_path}")
        if not os.path.exists(self.morfeusz_ext_path):
            raise FileNotFoundError(f"sqlite-morfeusz extension not found at: {self.morfeusz_ext_path}")

        conn.load_extension(self.vec_ext_path)
        conn.load_extension(self.morfeusz_ext_path)
        conn.enable_load_extension(False)
        return conn

    def describe_atc_code(self, code: str) -> str:
        """Translates an ATC code (e.g., 'M05BA08') into a human-readable Polish clinical description."""
        if not code or not self.atc_map:
            return code
        code_clean = code.strip().upper()
        if not code_clean:
            return ""

        group_key = code_clean[0]
        subgroup_key = code_clean[:3] if len(code_clean) >= 3 else ""

        group_info = self.atc_map.get(group_key, {})
        group_name = group_info.get("name", "")
        subgroups = group_info.get("subgroups", {})
        subgroup_name = subgroups.get(subgroup_key, "")

        if subgroup_name and group_name:
            return f"[yellow]{code_clean}[/yellow] → [bold]{subgroup_name}[/bold] [dim]({group_name})[/dim]"
        elif subgroup_name:
            return f"[yellow]{code_clean}[/yellow] → [bold]{subgroup_name}[/bold]"
        elif group_name:
            return f"[yellow]{code_clean}[/yellow] → [bold]{group_name}[/bold]"
        return f"[yellow]{code_clean}[/yellow]"

    def get_medicine_metadata(self, produkt_id: int) -> Dict[str, Any]:
        """Fetch rich product metadata from SQLite."""
        c = self.conn.cursor()
        c.execute("""
            SELECT id, nazwa_produktu, nazwa_powszechnie_stosowana, moc,
                   nazwa_postaci_farmaceutycznej, podmiot_odpowiedzialny, typ_procedury
            FROM produkty_lecznicze WHERE id = ?
        """, (produkt_id,))
        prod = c.fetchone()
        if not prod:
            return {"id": produkt_id, "nazwa_produktu": f"Produkt #{produkt_id}"}

        res = dict(prod)

        c.execute("SELECT nazwa_substancji, ilosc_substancji, jednostka_miary_ilosci_substancji FROM substancje_czynne WHERE produkt_id = ?", (produkt_id,))
        subst = c.fetchall()
        res["substancje"] = [f"{s['nazwa_substancji']} ({s['ilosc_substancji']} {s['jednostka_miary_ilosci_substancji']})" for s in subst]

        c.execute("SELECT kod_atc FROM kody_atc WHERE produkt_id = ?", (produkt_id,))
        atc = c.fetchall()
        res["atc"] = [a["kod_atc"] for a in atc]

        return res

    def search_rrf(
        self,
        query: str,
        top_k: int = 5,
        vec_weight: float = 1.0,
        fts_weight: float = 1.0,
        rrf_k: int = 60,
        candidate_pool: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Executes a Reciprocal Rank Fusion (RRF) search query combining vector search (sqlite-vec)
        and full-text search (FTS5 with Morfeusz lemmatizer).
        """
        query_clean = query.strip()
        if not query_clean:
            return []

        # 1. Embed query vector for dense search (with [query]: prefix)
        prefixed_query = f"[query]: {query_clean}"
        query_vec = self.model.encode(
            prefixed_query,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True
        ).astype(np.float32)
        query_vec_blob = query_vec.tobytes()

        # 2. Prepare FTS query string (sanitize special FTS5 operators if needed)
        fts_query = " ".join([w for w in query_clean.replace("\"", "").replace("'", "").split() if len(w) > 1])
        if not fts_query:
            fts_query = query_clean

        # 3. Combined RRF query using SQLite CTEs
        rrf_sql = """
        WITH vec_candidates AS (
            SELECT
                produkt_id,
                distance as vec_distance,
                ROW_NUMBER() OVER (ORDER BY distance ASC) as rank_vec
            FROM vec_dokumenty
            WHERE embedding MATCH ? AND k = ?
        ),
        fts_candidates AS (
            SELECT
                CAST(produkt_id AS INTEGER) as produkt_id,
                rank as fts_score,
                ROW_NUMBER() OVER (ORDER BY rank ASC) as rank_fts
            FROM fts_dokumenty
            WHERE fts_dokumenty MATCH ?
            LIMIT ?
        ),
        all_ids AS (
            SELECT produkt_id FROM vec_candidates
            UNION
            SELECT produkt_id FROM fts_candidates
        ),
        rrf_ranked AS (
            SELECT
                a.produkt_id,
                v.rank_vec,
                f.rank_fts,
                v.vec_distance,
                f.fts_score,
                (COALESCE(? / (? + v.rank_vec), 0.0) + COALESCE(? / (? + f.rank_fts), 0.0)) as rrf_score
            FROM all_ids a
            LEFT JOIN vec_candidates v ON a.produkt_id = v.produkt_id
            LEFT JOIN fts_candidates f ON a.produkt_id = f.produkt_id
        )
        SELECT
            produkt_id,
            rrf_score,
            rank_vec,
            rank_fts,
            vec_distance,
            fts_score
        FROM rrf_ranked
        ORDER BY rrf_score DESC
        LIMIT ?;
        """

        c = self.conn.cursor()
        c.execute(
            rrf_sql,
            (
                query_vec_blob, candidate_pool,
                fts_query, candidate_pool,
                vec_weight, float(rrf_k),
                fts_weight, float(rrf_k),
                top_k
            )
        )
        rows = c.fetchall()

        results = []
        for r in rows:
            prod_id = r["produkt_id"]
            meta = self.get_medicine_metadata(prod_id)
            meta["rrf_score"] = float(r["rrf_score"])
            meta["rank_vec"] = int(r["rank_vec"]) if r["rank_vec"] is not None else None
            meta["rank_fts"] = int(r["rank_fts"]) if r["rank_fts"] is not None else None
            meta["vec_distance"] = float(r["vec_distance"]) if r["vec_distance"] is not None else None
            meta["fts_score"] = float(r["fts_score"]) if r["fts_score"] is not None else None
            results.append(meta)

        return results

    def print_results(self, query: str, results: List[Dict[str, Any]]):
        """Renders RRF search results in a stylish Rich format."""
        console.rule(f"[bold cyan]Wyniki wyszukiwania hybrydowego RRF dla: \"{query}\"[/bold cyan]")

        if not results:
            console.print("[yellow]Brak pasujących wyników.[/yellow]\n")
            return

        for i, res in enumerate(results, 1):
            rrf_score = res["rrf_score"]
            rank_vec = res["rank_vec"]
            rank_fts = res["rank_fts"]

            # Contribution breakdown
            vec_info = f"[cyan]Wektor rank: #{rank_vec}[/cyan]" if rank_vec else "[dim]Wektor: poza top[/dim]"
            fts_info = f"[magenta]FTS5 (Morfeusz) rank: #{rank_fts}[/magenta]" if rank_fts else "[dim]FTS: poza top[/dim]"

            score_badge = f"[bold green]RRF Score: {rrf_score:.5f}[/bold green] | {vec_info} | {fts_info}"

            nazwa = res.get("nazwa_produktu", "Nieznana")
            subst = ", ".join(res.get("substancje", [])) or res.get("nazwa_powszechnie_stosowana", "Brak danych")
            moc = res.get("moc", "")
            postac = res.get("nazwa_postaci_farmaceutycznej", "")
            podmiot = res.get("podmiot_odpowiedzialny", "")

            atc_raw = res.get("atc", [])
            if atc_raw:
                atc_lines = [self.describe_atc_code(c) for c in atc_raw]
                atc_display = "\n  ".join(atc_lines)
            else:
                atc_display = "[dim]Brak przypisanego kodu ATC[/dim]"

            body = (
                f"[bold white]{i}. {nazwa}[/bold white]\n"
                f"• [cyan]Substancja czynna:[/cyan] {subst}\n"
                f"• [cyan]Moc i postać:[/cyan] {moc} | {postac}\n"
                f"• [cyan]Podmiot odpowiedzialny:[/cyan] {podmiot}\n"
                f"• [cyan]Klasyfikacja ATC:[/cyan]\n  {atc_display}\n"
                f"• [dim]ID Produktu: {res.get('id', 0)}[/dim]"
            )
            console.print(Panel(body, title=f" {score_badge} ", border_style="green" if i == 1 else "blue", expand=True))
        console.print()


def interactive_loop(engine: RPLHybridRRFSearch, top_k: int = 5):
    console.print(Panel.fit(
        "[bold green]PolDense-400M + Morfeusz FTS5 + sqlite-vec — Hybrid RRF Search[/bold green]\n"
        "Wpisz zapytanie w języku naturalnym (np. [italic]'lek na ból głowy i gorączkę', 'leczenie nadciśnienia', 'antybiotyk na płuca'[/italic]).\n"
        "Wpisz [bold red]'exit'[/bold red] lub [bold red]'q'[/bold red] aby zakończyć.",
        border_style="cyan"
    ))

    while True:
        try:
            query = Prompt.ask("\n[bold cyan]Wyszukaj lek (RRF)[/bold cyan]")
            if not query.strip():
                continue
            if query.strip().lower() in ["exit", "q", "quit"]:
                console.print("[yellow]Do widzenia![/yellow]")
                break

            results = engine.search_rrf(query, top_k=top_k)
            engine.print_results(query, results)
        except KeyboardInterrupt:
            console.print("\n[yellow]Przerwano.[/yellow]")
            break


def main():
    parser = argparse.ArgumentParser(description="Reciprocal Rank Fusion Hybrid Search (PolDense + Morfeusz FTS5)")
    parser.add_argument("--query", "-q", type=str, default=None, help="Query string for instant search")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="Number of results to return")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--atc-map", type=str, default=DEFAULT_ATC_MAP_PATH, help="Path to atc_map.json")
    parser.add_argument("--vec-weight", type=float, default=1.0, help="Weight for vector search in RRF")
    parser.add_argument("--fts-weight", type=float, default=1.0, help="Weight for FTS search in RRF")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF smoothing constant k")
    args = parser.parse_args()

    engine = RPLHybridRRFSearch(db_path=args.db, atc_map_path=args.atc_map)

    if args.query:
        results = engine.search_rrf(
            args.query,
            top_k=args.top_k,
            vec_weight=args.vec_weight,
            fts_weight=args.fts_weight,
            rrf_k=args.rrf_k
        )
        engine.print_results(args.query, results)
    else:
        interactive_loop(engine, top_k=args.top_k)


if __name__ == "__main__":
    main()
