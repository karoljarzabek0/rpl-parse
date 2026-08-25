"""
Builds and populates the `podpowiedzi` (search suggestions / autocomplete) table in SQLite `data/rpl.db`.

Aggregates:
1. Leki (Medicines from RPL with brand names, doses, and forms)
2. Substancje czynne (Active substances with mapped drug counts)
3. Klasyfikacja ATC (ATC groups and levels from Wikipedia hierarchy)
4. Wskazania i objawy (Clinical conditions & indications from refundation and medical lexicon)
"""

import os
import re
import sqlite3
import argparse
from rich.console import Console
from rich.table import Table

console = Console()
DEFAULT_DB_PATH = "data/rpl.db"


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower().strip()
    trans = str.maketrans({
        'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n',
        'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'ä': 'a', 'ö': 'o', 'ü': 'u', 'ß': 'ss',
        'é': 'e', 'è': 'e', 'á': 'a', 'à': 'a'
    })
    t_unaccent = t.translate(trans)
    t_clean = re.sub(r'[^a-z0-9\s]', ' ', t_unaccent)
    return re.sub(r'\s+', ' ', t_clean).strip()


COMMON_INDICATIONS = [
    "ból głowy", "ból zęba", "ból gardła", "ból brzucha", "ból kręgosłupa", "ból stawów", "ból mięśni",
    "gorączka", "przeziębienie", "grypa", "zapalenie zatok", "kaszel suchy", "kaszel mokry", "katar",
    "nadciśnienie tętnicze", "niewydolność serca", "choroba wieńcowa", "miażdżyca", "zaburzenia rytmu serca",
    "cukrzyca typu 2", "cukrzyca typu 1", "otyłość", "dna moczanowa", "niedoczynność tarczycy", "nadczynność tarczycy",
    "refluks żołądkowo-przełykowy", "choroba wrzodowa", "zgaga", "nudności i wymioty", "biegunka", "zaparcia",
    "astma oskrzelowa", "przewlekła obturacyjna choroba płuc (POChP)", "alergiczne zapalenie błony śluzowej nosa",
    "alergia", "pokrzywka", "atopowe zapalenie skóry (AZS)", "łuszczyca", "trądzik", "grzybica",
    "bezsenność", "zaburzenia lękowe", "depresja", "migrena", "padaczka", "choroba Parkinsona", "otępienie",
    "jaskra", "zapalenie spojówek", "zespół suchego oka", "zakażenie układu moczowego", "łagodny rozrost gruczołu krokowego",
    "osteoporoza", "zapalenie ucha", "niedokrwistość z niedoboru żelaza", "niedobór witaminy D"
]


def build_suggestions_table(db_path: str):
    console.print(f"[cyan]Connecting to {db_path} to build suggestions index...[/cyan]")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    conn.execute("DROP TABLE IF EXISTS podpowiedzi;")
    conn.execute("""
    CREATE TABLE podpowiedzi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fraza TEXT NOT NULL,
        fraza_norm TEXT NOT NULL,
        kategoria TEXT NOT NULL,
        podtytul TEXT,
        payload_id TEXT,
        popularnosc INTEGER DEFAULT 1
    );
    """)

    conn.execute("CREATE INDEX idx_podp_norm ON podpowiedzi(fraza_norm, popularnosc DESC);")
    conn.execute("CREATE INDEX idx_podp_kat ON podpowiedzi(kategoria);")

    suggestions = []
    seen = set()  # (fraza_norm, kategoria)

    # 1. Medicines (Leki)
    console.print("[cyan]Extracting medicines from produkty_lecznicze...[/cyan]")
    c.execute("""
        SELECT 
            p.id, 
            p.nazwa_produktu, 
            p.nazwa_powszechnie_stosowana, 
            p.moc, 
            p.nazwa_postaci_farmaceutycznej,
            (SELECT count(*) FROM opakowania o WHERE o.produkt_id = p.id) as pkg_count,
            (SELECT count(*) FROM refundacja r JOIN opakowania o ON ltrim(o.kod_gtin, '0') = r.kod_gtin_norm WHERE o.produkt_id = p.id) as ref_count
        FROM produkty_lecznicze p
    """)
    for row in c.fetchall():
        pid, name, common, moc, form, pkg_count, ref_count = row
        name_clean = name.strip()
        norm = normalize_text(name_clean)
        if not norm:
            continue

        moc_str = f" ({moc})" if moc else ""
        form_str = f" • {form}" if form else ""
        common_str = common or ""
        podtytul = f"{common_str}{moc_str}{form_str}".strip(" •")

        pop = 100 + (pkg_count or 0) * 5 + (50 if ref_count and ref_count > 0 else 0)

        key = (norm, "lek", str(pid))
        if key not in seen:
            seen.add(key)
            suggestions.append((name_clean, norm, "lek", podtytul, str(pid), pop))

    # 2. Active Substances (Substancje czynne)
    console.print("[cyan]Extracting active substances...[/cyan]")
    c.execute("""
        SELECT 
            nazwa_substancji, 
            count(DISTINCT produkt_id) as drug_count 
        FROM substancje_czynne 
        WHERE nazwa_substancji IS NOT NULL AND nazwa_substancji != ''
        GROUP BY nazwa_substancji
    """)
    for row in c.fetchall():
        sub_name, drug_count = row
        sub_clean = sub_name.strip()
        norm = normalize_text(sub_clean)
        if not norm:
            continue

        podtytul = f"Substancja czynna ({drug_count} leków w bazie)"
        pop = 80 + min(drug_count * 2, 50)

        key = (norm, "substancja", "")
        if key not in seen:
            seen.add(key)
            suggestions.append((sub_clean, norm, "substancja", podtytul, None, pop))

    # 3. ATC Classification Groups
    console.print("[cyan]Extracting ATC classification hierarchy groups...[/cyan]")
    c.execute("""
        SELECT 
            kod_atc, 
            poziom, 
            poziom_nazwa, 
            nazwa,
            (SELECT count(DISTINCT produkt_id) FROM kody_atc k WHERE k.kod_atc LIKE a.kod_atc || '%') as drug_count
        FROM atc_klasyfikacja a
        WHERE length(nazwa) >= 3
    """)
    for row in c.fetchall():
        atc_code, level, level_name, atc_name, drug_count = row
        atc_name_clean = atc_name.strip()
        norm = normalize_text(atc_name_clean)
        if not norm:
            continue

        podtytul = f"ATC: {atc_code} ({level_name})"
        pop = 70 - (level * 4) + min((drug_count or 0) * 2, 40)

        key = (norm, "atc", atc_code)
        if key not in seen:
            seen.add(key)
            suggestions.append((atc_name_clean, norm, "atc", podtytul, atc_code, pop))

    # 4. Clinical Indications & Symptoms from refundacja and expanded lexicon
    console.print("[cyan]Extracting clinical indications from refundacja and medical lexicon...[/cyan]")
    c.execute("""
        SELECT DISTINCT zakres_wskazan 
        FROM refundacja 
        WHERE zakres_wskazan IS NOT NULL AND zakres_wskazan != ''
    """)
    raw_indications = [r[0] for r in c.fetchall()]

    indications_set = set(COMMON_INDICATIONS)
    for raw in raw_indications:
        cleaned = re.sub(r'<\d+>', '', raw)
        parts = re.split(r'[;]', cleaned)
        for part in parts:
            p = part.strip()
            p = re.split(r'\s+(?:u\s+dorosłych|u\s+pacjentów|w\s+początkowym|po\s+przebytej|w\s+przypadku|w\s+II\s+rzucie|z\s+widocznymi)', p, flags=re.IGNORECASE)[0].strip()
            p = re.sub(r'^[\s\d\.\-\–\:]+', '', p).strip()
            if len(p) >= 3 and len(p) <= 65 and not p.lower().startswith('we wszystkich'):
                indications_set.add(p)

    # Specific disease aliases and colloquial names
    disease_aliases = [
        ("Alzheimer", "Choroba Alzheimera / otępienie"),
        ("Choroba Alzheimera", "Wskazanie kliniczne / neurodegeneracja"),
        ("Parkinson", "Choroba i zespół Parkinsona"),
        ("Choroba Parkinsona", "Wskazanie kliniczne / układ nerwowy"),
        ("Hashimoto", "Choroba Hashimoto / zapalenie tarczycy"),
        ("Choroba Hashimoto", "Wskazanie kliniczne / endokrynologia"),
        ("Crohn", "Choroba Leśniowskiego-Crohna"),
        ("Choroba Crohna", "Choroba zapalna jelit"),
        ("Choroba Leśniowskiego-Crohna", "Wskazanie kliniczne / gastroenterologia"),
        ("ADHD", "Zespół nadpobudliwości psychoruchowej"),
        ("RZS", "Reumatoidalne zapalenie stawów"),
        ("POChP", "Przewlekła obturacyjna choroba płuc"),
        ("SM", "Stwardnienie rozsiane (Sclerosis Multiplex)"),
        ("Stwardnienie rozsiane", "Wskazanie kliniczne / neurologia"),
        ("AZS", "Atopowe zapalenie skóry"),
        ("ZZSK", "Zesztywniające zapalenie stawów kręgosłupa"),
        ("Choroba wieńcowa", "Wskazanie kliniczne / kardiologia"),
        ("Miażdżyca", "Wskazanie kliniczne / kardiologia"),
        ("Cukrzyca", "Wskazanie kliniczne / diabetologia"),
        ("Cukrzyca typu 2", "Wskazanie kliniczne / diabetologia"),
        ("Cukrzyca typu 1", "Wskazanie kliniczne / diabetologia"),
        ("Nadciśnienie tętnicze", "Wskazanie kliniczne / kardiologia"),
        ("Jaskra", "Wskazanie kliniczne / okulistyka"),
        ("Zaćma", "Wskazanie kliniczne / okulistyka"),
        ("Astma", "Wskazanie kliniczne / pulmonologia"),
        ("Astma oskrzelowa", "Wskazanie kliniczne / pulmonologia"),
        ("Schizofrenia", "Wskazanie kliniczne / psychiatria"),
        ("Depresja", "Wskazanie kliniczne / psychiatria"),
        ("Bezsenność", "Wskazanie kliniczne / zaburzenia snu"),
        ("Migrena", "Wskazanie kliniczne / neurologia"),
        ("Padaczka", "Wskazanie kliniczne / neurologia"),
        ("Łuszczyca", "Wskazanie kliniczne / dermatologia"),
        ("Refluks", "Choroba refluksowa przełyku (GERD)"),
        ("Zgaga", "Wskazanie kliniczne / gastroenterologia"),
        ("Wrzody żołądka", "Choroba wrzodowa żołądka i dwunastnicy")
    ]

    for phrase, subtitle in disease_aliases:
        norm = normalize_text(phrase)
        key = (norm, "objaw", "")
        if key not in seen:
            seen.add(key)
            suggestions.append((phrase, norm, "objaw", subtitle, None, 120))

    for ind in indications_set:
        norm = normalize_text(ind)
        key = (norm, "objaw", "")
        if key not in seen:
            seen.add(key)
            suggestions.append((ind, norm, "objaw", "Wskazanie kliniczne / objaw", None, 95))

    console.print(f"[green]Inserting {len(suggestions)} suggestions into table podpowiedzi...[/green]")
    conn.executemany("""
    INSERT INTO podpowiedzi (fraza, fraza_norm, kategoria, podtytul, payload_id, popularnosc)
    VALUES (?, ?, ?, ?, ?, ?)
    """, suggestions)

    conn.commit()
    conn.close()

    table = Table(title="Statystyki wygenerowanych podpowiedzi Autocomplete")
    table.add_column("Kategoria", style="cyan")
    table.add_column("Liczba podpowiedzi", justify="right", style="green")
    
    cat_counts = {}
    for s in suggestions:
        cat_counts[s[2]] = cat_counts.get(s[2], 0) + 1

    for cat, count in sorted(cat_counts.items()):
        table.add_row(cat, f"{count:,}")
    table.add_row("[bold]Łącznie[/bold]", f"[bold yellow]{len(suggestions):,}[/bold yellow]")
    console.print(table)
    console.print("[bold green]Indeks podpowiedzi utworzony pomyślnie![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Build autocomplete suggestions table")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite database")
    args = parser.parse_args()

    build_suggestions_table(args.db)


if __name__ == "__main__":
    main()
