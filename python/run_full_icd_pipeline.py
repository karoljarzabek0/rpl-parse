"""
Master Pipeline Runner for RPL Medical Conditions & ICD-11 / ICD-10 Resolution.

Executes:
1. `python/import_wikidata_uses.py` - ATC -> Wikidata + combination drug parts traversal (P527, P3781).
2. `python/import_rpl_substances_wikidata.py` - Direct RPL active substance (substancje_czynne) -> Wikidata mapping.
3. `python/import_icd_mapping.py` - Official WHO/CeZ ICD-11 XML + ICD-10 cross-walk table -> ICD-11 MMS, Foundation ID, ICD-10, official Polish diagnostic titles.
"""

import os
import sys
import subprocess
import time
from rich.console import Console
from rich.panel import Panel

console = Console()
PYTHON_BIN = sys.executable


def run_step(step_num: int, title: str, script_path: str, extra_args: list = None):
    console.print(Panel(f"[bold cyan]Step {step_num}: {title}[/bold cyan]\n[dim]Running: {script_path}[/dim]"))
    cmd = [PYTHON_BIN, script_path] + (extra_args or [])
    t0 = time.time()
    res = subprocess.run(cmd)
    if res.returncode != 0:
        console.print(f"[bold red]❌ Step {step_num} failed with return code {res.returncode}[/bold red]")
        sys.exit(res.returncode)
    console.print(f"[bold green]✔ Step {step_num} finished in {time.time() - t0:.2f}s.[/bold green]\n")


def main():
    t_start = time.time()
    console.print(Panel.fit(
        "[bold magenta]RPL Medical Indications & ICD-11 / ICD-10 End-to-End Pipeline[/bold magenta]\n"
        "[dim]Maps all registered medicines in Poland to conditions, ICD-11 MMS, Foundation IDs & ICD-10[/dim]"
    ))

    # Step 1: ATC -> Wikidata & Combination parts
    run_step(1, "ATC Hierarchy & Combination Drug Traversal (Wikidata)", "python/import_wikidata_uses.py")

    # Step 2: Direct RPL Active Substances -> Wikidata
    run_step(2, "Direct RPL Substance Mapping (substancje_czynne -> Wikidata)", "python/import_rpl_substances_wikidata.py")

    # Step 3: Polish ICD-11 XML & ICD-10 Translation Table
    run_step(3, "WHO ICD-11 XML & ICD-10 Cross-Walk Mapping", "python/import_icd_mapping.py")

    console.print(Panel.fit(
        f"[bold green]🎉 Entire ICD-11 & ICD-10 Pipeline Completed Successfully in {time.time() - t_start:.2f}s![/bold green]"
    ))


if __name__ == "__main__":
    main()
