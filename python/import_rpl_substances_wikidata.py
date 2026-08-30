"""
Robust Parallel RPL Active Substances (substancje_czynne) -> Wikidata Substance Entity Mapper.

Features:
- Pure active substance isolation: strictly excludes combination drugs (Q1779868) and multi-substance entities.
- Full INN / Latin / Polish pharmacy declension normalizer (removes 40+ pharmaceutical salt/hydrate endings).
- Multi-threaded SPARQL execution with retries and real-time unbuffered progress logging (flush=True).
- Safe idempotent database upserts: populates `rpl_substance_wikidata (nazwa_substancji, wikidata_id, substance_name)`.
- Discovers and maps treated medical conditions (`P2175`).
"""

import sqlite3
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, List, Set

DB_PATH = "data/rpl.db"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "rpl-parse/1.0 (https://github.com/karol/rpl-parse; rpl-substance-mapper@example.com)",
    "Accept": "application/sparql-results+json"
}

SALT_SUFFIXES = [
    r"hydrochloridum.*", r"hydrobromidum.*", r"sulfas.*", r"natricum.*", r"kalicum.*",
    r"calcicum.*", r"mesilas.*", r"besilas.*", r"maleas.*", r"tartras.*", r"fumaras.*",
    r"succinas.*", r"citras.*", r"acetas.*", r"propionicum.*", r"dipropionas.*",
    r"valeras.*", r"butyras.*", r"hexacetonidum.*", r"acetonidum.*", r"furoas.*",
    r"monohydricum.*", r"dihydricum.*", r"trihydricum.*", r"hemihydricum.*",
    r"propylenglycolum.*", r"polistirex.*", r"trometamolum.*", r"argininum.*",
    r"ethexil.*", r"medoxomil.*", r"axetil.*", r"cilexetil.*", r"sodium.*", r"potassium.*",
    r"chloridum.*", r"bromidum.*", r"iodidum.*", r"nitras.*", r"phosphas.*",
    r"lactas.*", r"gluconas.*", r"laurilsulfas.*", r"estolas.*", r"edisil.*",
    r"adipinas.*", r"benzoas.*", r"salicylas.*", r"oleas.*", r"stearas.*",
    r"dihydricus.*", r"anhydricum.*", r"hemihydricus.*", r"trihydricus.*"
]
SALT_REGEX = re.compile(r"\b(" + "|".join(SALT_SUFFIXES) + r")\b", re.IGNORECASE)


def is_combo_name(text: str) -> bool:
    """Detects combination drug patterns in substance titles."""
    return bool(re.search(r"[/+&]| and | with | w połączeniu", text, re.IGNORECASE))


def is_valid_stem(stem: str) -> bool:
    if not stem or len(stem) < 3:
        return False
    if len(re.findall(r"[a-zA-Z]", stem)) < 2:
        return False
    if any(c in stem for c in ["%", "/", "\\", '"', "<", ">", "{", "}", "^", ";"]):
        return False
    return True


def generate_substance_candidates(latin_name: str) -> List[str]:
    """Generates INN English, Polish transliterations, and Latin declensions for an active substance."""
    # 0. Strip leading percentages / counts (e.g. '25% Lactobacillus...', '2,1 % woda...')
    s = re.sub(r"^\d+([.,]\d+)?\s*%?\s*", "", latin_name.strip())
    if not s:
        s = latin_name.strip()

    # 1. Acidum X-icum -> X-ic acid / kwas X-owy
    m = re.match(r"Acidum\s+([a-z0-9\-]+)icum", s, re.IGNORECASE)
    if m:
        base = m.group(1).lower()
        res = [f"{base}ic acid", f"acidum {base}icum", f"kwas {base}owy", base]
        if "acetylsalicyl" in base:
            res.extend(["aspirin", "kwas acetylosalicylowy", "acetylsalicylic acid"])
        return [c for c in res if is_valid_stem(c)]

    # 2. Strip pharmaceutical salt/hydrate modifiers
    cleaned = re.sub(SALT_REGEX, "", s).strip()
    words = cleaned.split()
    first = words[0] if words else s

    # 3. Strip Latin noun/adjective case endings (-ii, -i, -um, -a, -is, -ium, -as, -es)
    base = re.sub(r"(ii|i|um|a|is|ium|as|es)$", "", first, flags=re.IGNORECASE).lower()

    candidates = [
        base,
        base + "e",       # amlodipine, lamivudine, zidovudine
        base + "in",      # insulin
        base + "ine",     # caffeine
        base + "um",      # latin
        first.lower()
    ]

    # 4. Polish phonetic transliterations (v -> w, s between vowels -> z)
    pl_base = base.replace("v", "w")
    candidates.extend([pl_base, pl_base + "a", pl_base + "ina", pl_base + "yna"])

    pl_z = re.sub(r"([aeiouy])s([aeiouy])", r"\1z\2", pl_base)
    if pl_z != pl_base:
        candidates.extend([pl_z, pl_z + "e", pl_z + "a", pl_z + "ina", pl_z + "yna"])

    # 5. Specialized pharmaceutical aliases
    if base.startswith("coffe"):
        candidates.extend(["caffeine", "kofeina"])
    if base.startswith("paracetamol"):
        candidates.extend(["paracetamol", "acetaminophen"])
    if base.startswith("cholecalciferol"):
        candidates.extend(["vitamin d3", "witamina d3", "cholekalcyferol"])
    if base.startswith("ergocalciferol"):
        candidates.extend(["vitamin d2", "witamina d2"])
    if base.startswith("zidovudin"):
        candidates.extend(["azidothymidine", "azydotymidyna", "zidovudine"])
    if base.startswith("cyanocobalamin") or base.startswith("cyjanokobalamin"):
        candidates.extend(["vitamin b12", "witamina b12", "cyanocobalamin"])
    if base.startswith("thiamin") or base.startswith("tiamin"):
        candidates.extend(["vitamin b1", "witamina b1", "thiamine"])
    if base.startswith("riboflavin") or base.startswith("ryboflawin"):
        candidates.extend(["vitamin b2", "witamina b2", "riboflavin"])
    if base.startswith("pyridoxin") or base.startswith("pirydoksyn"):
        candidates.extend(["vitamin b6", "witamina b6", "pyridoxine"])

    return list(dict.fromkeys(c for c in candidates if is_valid_stem(c)))


def init_table(conn: sqlite3.Connection):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS rpl_substance_wikidata (
        nazwa_substancji TEXT PRIMARY KEY,
        wikidata_id TEXT NOT NULL,
        substance_name TEXT,
        FOREIGN KEY (wikidata_id) REFERENCES wikidata_substances(wikidata_id)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rpl_sub_wd ON rpl_substance_wikidata(wikidata_id);")
    conn.commit()


def query_sparql_chunk(chunk_idx: int, total_chunks: int, chunk_items: List[str]) -> List[Tuple[str, str, str]]:
    stem_to_subs: Dict[str, List[str]] = {}
    for s in chunk_items:
        for stem in generate_substance_candidates(s):
            stem_to_subs.setdefault(stem.lower(), []).append(s)

    if not stem_to_subs:
        return []

    labels_literals = []
    for stem in stem_to_subs.keys():
        clean_stem = stem.replace('"', '\\"')
        labels_literals.append(f'"{clean_stem}"@en')
        labels_literals.append(f'"{clean_stem}"@pl')

    labels_str = " ".join(labels_literals)
    query = f"""
    SELECT ?substance ?substanceLabel ?label ?atc ?cid ?cond ?inter ?cas WHERE {{
      VALUES ?label {{ {labels_str} }}
      ?substance rdfs:label ?label .
      FILTER NOT EXISTS {{ ?substance wdt:P31 wd:Q1779868 }}
      OPTIONAL {{ ?substance wdt:P267 ?atc . }}
      OPTIONAL {{ ?substance wdt:P662 ?cid . }}
      OPTIONAL {{ ?substance wdt:P2175 ?cond . }}
      OPTIONAL {{ ?substance wdt:P769 ?inter . }}
      OPTIONAL {{ ?substance wdt:P231 ?cas . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    for attempt in range(5):
        try:
            r = requests.get(WIKIDATA_SPARQL_URL, params={"query": query, "format": "json"}, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                best_chunk_matches: Dict[str, Tuple[int, int, str, str]] = {}
                for row in r.json().get("results", {}).get("bindings", []):
                    qid = row.get("substance", {}).get("value", "").split("/")[-1]
                    name = row.get("substanceLabel", {}).get("value", "")
                    if is_combo_name(name):
                        continue
                    matched_lbl = row.get("label", {}).get("value", "").lower()
                    if matched_lbl in stem_to_subs:
                        has_atc = bool(row.get("atc"))
                        has_cid = bool(row.get("cid"))
                        has_cond = bool(row.get("cond"))
                        has_inter = bool(row.get("inter"))
                        has_cas = bool(row.get("cas"))
                        score = (3 if has_atc else 0) + (3 if has_cond else 0) + (2 if has_cid else 0) + (2 if has_inter else 0) + (1 if has_cas else 0)
                        if score == 0:
                            continue
                        qid_num = int(qid[1:]) if qid.startswith("Q") and qid[1:].isdigit() else 999999999
                        cand = (score, -qid_num, qid, name)
                        for original_sub in stem_to_subs[matched_lbl]:
                            if original_sub not in best_chunk_matches or cand > best_chunk_matches[original_sub]:
                                best_chunk_matches[original_sub] = cand

                results = [(s, qid, name) for s, (_, _, qid, name) in best_chunk_matches.items()]
                print(f"[{chunk_idx+1:02d}/{total_chunks:02d}] Fetched SPARQL batch ({len(chunk_items)} items) -> {len(results)} matches", flush=True)
                return results
            else:
                time.sleep(2.0 * (attempt + 1))
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    print(f"[{chunk_idx+1:02d}/{total_chunks:02d}] ⚠️ Failed to query chunk ({len(chunk_items)} items)", flush=True)
    return []


def query_conditions_chunk(chunk_idx: int, total_chunks: int, qids: List[str]) -> List[Tuple[str, str, str]]:
    qid_str = " ".join(f"wd:{qid}" for qid in qids)
    query = f"""
    SELECT ?substance ?condition ?conditionLabel WHERE {{
      VALUES ?substance {{ {qid_str} }}
      ?substance wdt:P2175 ?condition .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    for attempt in range(3):
        try:
            r = requests.get(WIKIDATA_SPARQL_URL, params={"query": query, "format": "json"}, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                results = []
                for row in r.json().get("results", {}).get("bindings", []):
                    sub_qid = row.get("substance", {}).get("value", "").split("/")[-1]
                    cond_qid = row.get("condition", {}).get("value", "").split("/")[-1]
                    cond_label = row.get("conditionLabel", {}).get("value", cond_qid)
                    if cond_qid and cond_label:
                        results.append((sub_qid, cond_qid, cond_label))
                print(f"[Conditions {chunk_idx+1:02d}/{total_chunks:02d}] Found {len(results)} condition links", flush=True)
                return results
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return []


def main():
    print("=" * 70, flush=True)
    print("Connecting to SQLite database: data/rpl.db", flush=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    init_table(conn)

    c.execute("SELECT DISTINCT nazwa_substancji FROM substancje_czynne WHERE nazwa_substancji IS NOT NULL")
    rpl_subs = [r[0].strip() for r in c.fetchall() if r[0].strip()]
    print(f"Loaded {len(rpl_subs)} distinct active substance names from RPL.", flush=True)

    # 1. Match from local cache (ONLY pure single substances, no combination entities)
    c.execute("SELECT wikidata_id, name FROM wikidata_substances")
    db_subs = c.fetchall()
    pure_cache = {}
    for qid, name in db_subs:
        if is_combo_name(name):
            continue
        pure_cache[name.strip().lower()] = (qid, name)

    matched: Dict[str, Tuple[str, str]] = {}
    unmatched: List[str] = []

    for s in rpl_subs:
        found = False
        for cand in generate_substance_candidates(s):
            if cand in pure_cache:
                matched[s] = pure_cache[cand]
                found = True
                break
        if not found:
            unmatched.append(s)

    print(f"Step 1: Matched {len(matched)} / {len(rpl_subs)} substances from local pure Wikidata cache.", flush=True)
    print(f"Step 2: Querying Wikidata SPARQL for remaining {len(unmatched)} substances...", flush=True)

    chunk_size = 15
    chunks = [unmatched[i:i + chunk_size] for i in range(0, len(unmatched), chunk_size)]
    total_chunks = len(chunks)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(query_sparql_chunk, i, total_chunks, ch): ch for i, ch in enumerate(chunks)}
        for future in as_completed(futures):
            res = future.result()
            for s, qid, name in res:
                if s not in matched:
                    matched[s] = (qid, name)

    print("=" * 70, flush=True)
    pct = (len(matched) / len(rpl_subs)) * 100
    print(f"Total matched RPL substances: {len(matched)} / {len(rpl_subs)} ({pct:.1f}%)!", flush=True)

    # Clean old mappings and write verified pure single substances
    c.execute("DELETE FROM rpl_substance_wikidata;")
    rows_to_insert = [(s, qid, name) for s, (qid, name) in matched.items()]
    c.executemany("INSERT OR REPLACE INTO rpl_substance_wikidata (nazwa_substancji, wikidata_id, substance_name) VALUES (?, ?, ?)", rows_to_insert)
    conn.commit()

    # Step 3: Fetch conditions for all mapped substances
    all_qids = list(set(qid for _, (qid, _) in matched.items()))
    print(f"Step 3: Fetching medical conditions (P2175) for {len(all_qids)} substances...", flush=True)
    q_chunks = [all_qids[i:i + 60] for i in range(0, len(all_qids), 60)]
    total_q_chunks = len(q_chunks)

    new_conditions = {}
    new_sub_cond_links = set()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(query_conditions_chunk, i, total_q_chunks, qch): qch for i, qch in enumerate(q_chunks)}
        for future in as_completed(futures):
            res = future.result()
            for sub_qid, cond_qid, cond_label in res:
                new_conditions[cond_qid] = cond_label
                new_sub_cond_links.add((sub_qid, cond_qid, "condition_treated"))

    print(f"Discovered {len(new_conditions)} conditions and {len(new_sub_cond_links)} condition links.", flush=True)

    for qid, name in new_conditions.items():
        c.execute("""
        INSERT INTO wikidata_conditions (condition_wikidata_id, name) VALUES (?, ?)
        ON CONFLICT(condition_wikidata_id) DO UPDATE SET
            name = COALESCE(wikidata_conditions.name, excluded.name)
        """, (qid, name))

    for sub_qid, cond_qid, u_type in new_sub_cond_links:
        c.execute("INSERT OR REPLACE INTO wikidata_substance_conditions (substance_wikidata_id, condition_wikidata_id, use_type) VALUES (?, ?, ?)", (sub_qid, cond_qid, u_type))

    conn.commit()

    # Verification on Gripex Control Duo (100443332) & Lamivudine + Zidovudine Accord (100389762)
    print("=" * 70, flush=True)
    for test_id, test_name in [(100443332, "Gripex Control Duo"), (100389762, "Lamivudine + Zidovudine Accord")]:
        c.execute("""
            SELECT DISTINCT p.nazwa_produktu, sc.nazwa_substancji, rsw.wikidata_id, rsw.substance_name, wc.name, wc.icd11_mms, wc.icd10_codes
            FROM produkty_lecznicze p
            JOIN substancje_czynne sc ON sc.produkt_id = p.id
            JOIN rpl_substance_wikidata rsw ON rsw.nazwa_substancji = sc.nazwa_substancji
            JOIN wikidata_substance_conditions wsc ON wsc.substance_wikidata_id = rsw.wikidata_id
            JOIN wikidata_conditions wc ON wc.condition_wikidata_id = wsc.condition_wikidata_id
            WHERE p.id = ?
        """, (test_id,))
        rows = c.fetchall()
        print(f"Verification on {test_name} (#{test_id}) -> {len(rows)} conditions mapped:", flush=True)
        for r in rows:
            print(f"  • {r[0]} | Substance: {r[1]} -> Pure Wikidata: {r[3]} ({r[2]}) -> Condition: {r[4]} | ICD-11: {r[5] or '-'} | ICD-10: {r[6] or '-'}", flush=True)

    conn.close()
    print("=" * 70, flush=True)
    print("Robust RPL substance mapping complete!", flush=True)


if __name__ == "__main__":
    main()
