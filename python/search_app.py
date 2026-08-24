"""
Interactive Vector Similarity Search for Polish Medicinal Products (RPL).
Queries PolDense-400M embeddings with cosine similarity and displays matched medicines with rich metadata.
"""

import os
import sys
import json
import sqlite3
import argparse
from typing import List, Dict, Any, Optional

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

MODEL_NAME = "OPI-PIB/PolDense-400M"


class RPLVectorSearch:
    def __init__(
        self,
        npz_path: str = "data/embeddings.npz",
        meta_db_path: str = "data/demo_rpl.db",
        atc_map_path: str = "data/atc_map.json"
    ):
        if not os.path.exists(npz_path) and os.path.exists(f"../{npz_path}"):
            npz_path = f"../{npz_path}"
        if not os.path.exists(meta_db_path) and os.path.exists(f"../{meta_db_path}"):
            meta_db_path = f"../{meta_db_path}"
        if not os.path.exists(atc_map_path) and os.path.exists(f"../{atc_map_path}"):
            atc_map_path = f"../{atc_map_path}"

        self.npz_path = npz_path
        self.meta_db_path = meta_db_path
        self.atc_map_path = atc_map_path
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # Load ATC mapping
        self.atc_map = {}
        if os.path.exists(atc_map_path):
            try:
                with open(atc_map_path, "r", encoding="utf-8") as f:
                    self.atc_map = json.load(f)
                console.print(f"[green]Loaded ATC classifications from {atc_map_path}.[/green]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load ATC map from {atc_map_path}: {e}[/yellow]")
        
        console.print(f"[cyan]Loading embeddings from {npz_path}...[/cyan]")
        data = np.load(npz_path)
        self.ids = data["ids"]  # shape (N,)
        self.embeddings = data["embeddings"]  # shape (N, 1024)
        self.chunk_counts = data["chunk_counts"] if "chunk_counts" in data else np.ones(len(self.ids))
        self.token_counts = data["token_counts"] if "token_counts" in data else np.zeros(len(self.ids))

        # Normalize document embeddings if not already normalized
        norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.embeddings = self.embeddings / norms

        console.print(f"[green]Loaded {len(self.ids)} document embeddings (dimension: {self.embeddings.shape[1]}).[/green]")

        console.print(f"[cyan]Loading PolDense-400M model on {self.device}...[/cyan]")
        self.model = SentenceTransformer(
            MODEL_NAME,
            device=self.device,
            model_kwargs={
                "torch_dtype": torch.bfloat16 if self.device == "cuda" else torch.float32,
            }
        )
        console.print("[green]PolDense-400M model ready for semantic queries![/green]\n")

    def describe_atc_code(self, code: str) -> str:
        """
        Translates an ATC code (e.g., 'M05BA08') into a human-readable Polish clinical description.
        Level 1 (e.g. 'M'): Anatomical main group
        Level 2 (e.g. 'M05'): Therapeutic subgroup
        """
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
        """Fetch metadata from demo_rpl.db for given product ID."""
        if not os.path.exists(self.meta_db_path):
            return {"id": produkt_id, "nazwa_produktu": f"Produkt #{produkt_id}"}

        try:
            conn = sqlite3.connect(self.meta_db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Main product info
            c.execute("""
                SELECT id, nazwa_produktu, nazwa_powszechnie_stosowana, moc,
                       nazwa_postaci_farmaceutycznej, podmiot_odpowiedzialny, typ_procedury
                FROM produkty_lecznicze WHERE id = ?
            """, (produkt_id,))
            prod = c.fetchone()

            if not prod:
                conn.close()
                return {"id": produkt_id, "nazwa_produktu": f"Produkt #{produkt_id}"}

            res = dict(prod)

            # Active substances
            c.execute("SELECT nazwa_substancji, ilosc_substancji, jednostka_miary_ilosci_substancji FROM substancje_czynne WHERE produkt_id = ?", (produkt_id,))
            subst = c.fetchall()
            res["substancje"] = [f"{s['nazwa_substancji']} ({s['ilosc_substancji']} {s['jednostka_miary_ilosci_substancji']})" for s in subst]

            # ATC codes
            c.execute("SELECT kod_atc FROM kody_atc WHERE produkt_id = ?", (produkt_id,))
            atc = c.fetchall()
            res["atc"] = [a["kod_atc"] for a in atc]

            conn.close()
            return res
        except Exception as e:
            return {"id": produkt_id, "nazwa_produktu": f"Produkt #{produkt_id}", "error": str(e)}

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Embeds the query with `[query]: ` prefix, computes cosine similarity against all document embeddings,
        and returns the top-K ranked medicines.
        """
        # PolDense query prefix specification
        prefixed_query = f"[query]: {query.strip()}"
        query_vec = self.model.encode(
            prefixed_query,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True
        )

        # Dot product with normalized document embeddings gives cosine similarity: [-1.0, 1.0]
        similarities = np.dot(self.embeddings, query_vec)

        # Top-K indices
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            prod_id = int(self.ids[idx])
            score = float(similarities[idx])
            meta = self.get_medicine_metadata(prod_id)
            meta["similarity"] = score
            meta["chunk_count"] = int(self.chunk_counts[idx])
            meta["total_tokens"] = int(self.token_counts[idx])
            results.append(meta)

        return results

    def print_results(self, query: str, results: List[Dict[str, Any]]):
        """Renders search results in a stylish Rich format."""
        console.rule(f"[bold cyan]Wyniki wyszukiwania dla: \"{query}\"[/bold cyan]")

        for i, res in enumerate(results, 1):
            score = res["similarity"]
            score_pct = score * 100
            if score >= 0.40:
                score_badge = f"[bold green]{score_pct:.1f}% dopasowania[/bold green]"
                border_style = "green"
            elif score >= 0.30:
                score_badge = f"[bold yellow]{score_pct:.1f}% dopasowania[/bold yellow]"
                border_style = "yellow"
            else:
                score_badge = f"[bold red]{score_pct:.1f}% dopasowania[/bold red]"
                border_style = "red"

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

            doc_info = f"{res.get('chunk_count', 1)} chunków ({res.get('total_tokens', 0):,} tokenów w ChPL)"

            body = (
                f"[bold white]{i}. {nazwa}[/bold white]\n"
                f"• [cyan]Substancja czynna:[/cyan] {subst}\n"
                f"• [cyan]Moc i postać:[/cyan] {moc} | {postac}\n"
                f"• [cyan]Podmiot odpowiedzialny:[/cyan] {podmiot}\n"
                f"• [cyan]Klasyfikacja ATC:[/cyan]\n  {atc_display}\n"
                f"• [dim]ID: {res.get('id', 0)} | {doc_info}[/dim]"
            )
            console.print(Panel(body, title=f" {score_badge} ", border_style=border_style, expand=True))
        console.print()


def interactive_loop(engine: RPLVectorSearch, top_k: int = 5):
    console.print(Panel.fit(
        "[bold green]PolDense-400M RPL Vector Search Demo[/bold green]\n"
        "Wpisz zapytanie w języku naturalnym (np. [italic]'lek na ból głowy i gorączkę', 'leczenie nadciśnienia tętniczego', 'antybiotyk na zapalenie oskrzeli'[/italic]).\n"
        "Wpisz [bold red]'exit'[/bold red] lub [bold red]'q'[/bold red] aby zakończyć.",
        border_style="cyan"
    ))

    while True:
        try:
            query = Prompt.ask("\n[bold cyan]Wyszukaj lek[/bold cyan]")
            if not query.strip():
                continue
            if query.strip().lower() in ["exit", "q", "quit"]:
                console.print("[yellow]Do widzenia![/yellow]")
                break

            results = engine.search(query, top_k=top_k)
            engine.print_results(query, results)
        except KeyboardInterrupt:
            console.print("\n[yellow]Przerwano.[/yellow]")
            break


def main():
    parser = argparse.ArgumentParser(description="PolDense Vector Similarity Search for Polish Medicines")
    parser.add_argument("--query", "-q", type=str, default=None, help="Query string for instant search")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="Number of results to return")
    parser.add_argument("--npz", type=str, default="data/embeddings.npz", help="Path to NPZ embeddings file")
    parser.add_argument("--meta-db", type=str, default="data/demo_rpl.db", help="Path to demo_rpl.db SQLite database")
    parser.add_argument("--atc-map", type=str, default="data/atc_map.json", help="Path to atc_map.json ATC dictionary")
    args = parser.parse_args()

    engine = RPLVectorSearch(npz_path=args.npz, meta_db_path=args.meta_db, atc_map_path=args.atc_map)

    if args.query:
        results = engine.search(args.query, top_k=args.top_k)
        engine.print_results(args.query, results)
    else:
        interactive_loop(engine, top_k=args.top_k)


if __name__ == "__main__":
    main()

