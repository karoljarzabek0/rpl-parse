"""
FastAPI Backend Server for RPL Hybrid RRF Search Engine.
Keeps PolDense-400M in GPU VRAM and serves sub-10ms hybrid search queries with FTS5 Morfeusz snippets.
"""

import os
import sys
import json
import sqlite3
import re
from typing import List, Dict, Any, Optional

import numpy as np
import torch
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import uvicorn

app = FastAPI(title="RPL Hybrid RRF Search API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME = "OPI-PIB/PolDense-400M"
DB_PATH = "data/rpl.db"
ATC_MAP_PATH = "data/atc_map.json"
ATC_HIERARCHY_PATH = "data/atc_hierarchy.json"
VEC_EXT_PATH = "extensions/vec0.so"
MORFEUSZ_EXT_PATH = "extensions/morfeusz.so"

# Global state
state: Dict[str, Any] = {
    "model": None,
    "atc_map": {},
    "atc_hierarchy": {},
    "db_conn": None,
    "device": "cuda" if torch.cuda.is_available() else "cpu"
}


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    conn.load_extension(VEC_EXT_PATH)
    conn.load_extension(MORFEUSZ_EXT_PATH)
    conn.enable_load_extension(False)
    return conn


def describe_atc_code(code: str, atc_map: Dict[str, Any], atc_hierarchy: Dict[str, Any] = None) -> Dict[str, Any]:
    if not code:
        return {"code": "", "subgroup": "", "group": "", "display": "", "path_str": "", "path": [], "levels": []}
    code_clean = code.strip().upper()
    g = code_clean[0] if code_clean else ""
    sg = code_clean[:3] if len(code_clean) >= 3 else ""

    g_info = atc_map.get(g, {}) if atc_map else {}
    g_name = g_info.get("name", "")
    sg_name = g_info.get("subgroups", {}).get(sg, "")

    levels = []
    path_names = []
    if atc_hierarchy:
        prefixes = []
        if len(code_clean) >= 1: prefixes.append(code_clean[:1])
        if len(code_clean) >= 3: prefixes.append(code_clean[:3])
        if len(code_clean) >= 4: prefixes.append(code_clean[:4])
        if len(code_clean) >= 5: prefixes.append(code_clean[:5])
        if len(code_clean) >= 7: prefixes.append(code_clean[:7])

        for pfx in prefixes:
            node = atc_hierarchy.get(pfx)
            if node:
                levels.append({
                    "level": node.get("level"),
                    "level_name": node.get("level_name"),
                    "code": node.get("code"),
                    "name": node.get("name")
                })
                if node.get("name") not in path_names:
                    path_names.append(node.get("name"))

    if not path_names:
        if g_name: path_names.append(g_name)
        if sg_name: path_names.append(sg_name)

    display = f"{code_clean} → {sg_name} ({g_name})" if sg_name and g_name else code_clean
    hierarchy_str = " › ".join(path_names) if path_names else display

    return {
        "code": code_clean,
        "subgroup": sg_name or (levels[1]["name"] if len(levels) > 1 else ""),
        "group": g_name or (levels[0]["name"] if len(levels) > 0 else ""),
        "display": display,
        "path_str": hierarchy_str,
        "path": path_names,
        "levels": levels
    }


def get_medicine_metadata(conn: sqlite3.Connection, produkt_id: int, atc_map: Dict[str, Any], atc_hierarchy: Dict[str, Any] = None) -> Dict[str, Any]:
    c = conn.cursor()
    c.execute("""
        SELECT id, nazwa_produktu, rodzaj_preparatu, nazwa_powszechnie_stosowana, moc,
               nazwa_postaci_farmaceutycznej, podmiot_odpowiedzialny, typ_procedury,
               numer_pozwolenia, waznosc_pozwolenia, ulotka, charakterystyka, etykieto_ulotka
        FROM produkty_lecznicze WHERE id = ?
    """, (produkt_id,))
    prod = c.fetchone()
    if not prod:
        return {"id": produkt_id, "nazwa_produktu": f"Produkt #{produkt_id}"}

    res = dict(prod)

    # Active substances
    c.execute("SELECT nazwa_substancji, ilosc_substancji, jednostka_miary_ilosci_substancji FROM substancje_czynne WHERE produkt_id = ?", (produkt_id,))
    subst = c.fetchall()
    res["substancje"] = [
        {"nazwa": s["nazwa_substancji"], "ilosc": s["ilosc_substancji"], "jednostka": s["jednostka_miary_ilosci_substancji"]}
        for s in subst
    ]
    res["substancje_display"] = ", ".join([f"{s['nazwa_substancji']} ({s['ilosc_substancji']} {s['jednostka_miary_ilosci_substancji']})" for s in subst])

    # ATC codes
    c.execute("SELECT kod_atc FROM kody_atc WHERE produkt_id = ?", (produkt_id,))
    atc = c.fetchall()
    res["atc"] = [describe_atc_code(a["kod_atc"], atc_map, atc_hierarchy) for a in atc]

    # Packaging summary
    c.execute("SELECT count(*) as count, kategoria_dostepnosci FROM opakowania WHERE produkt_id = ? GROUP BY kategoria_dostepnosci", (produkt_id,))
    pkgs = c.fetchall()
    res["opakowania_info"] = ", ".join([f"{p['count']} op. ({p['kategoria_dostepnosci']})" for p in pkgs]) or "Brak"

    # Fast refundation check
    c.execute("""
        SELECT count(*) FROM opakowania o
        JOIN refundacja r ON ltrim(o.kod_gtin, '0') = r.kod_gtin_norm
        WHERE o.produkt_id = ?
    """, (produkt_id,))
    refund_count = c.fetchone()[0]
    res["is_refundowany"] = bool(refund_count > 0)

    # GIF / RDG Regulatory Decisions
    c.execute("""
        SELECT id, numer_decyzji, data_decyzji, rodzaj_decyzji, nazwa_produktu, moc, postac, numer_serii, data_waznosci, link_decyzja
        FROM decyzje_gif
        WHERE produkt_id = ?
        ORDER BY data_decyzji DESC
    """, (produkt_id,))
    decisions = [dict(d) for d in c.fetchall()]
    res["decyzje_gif"] = decisions

    if decisions:
        latest = decisions[0]
        res["gif_status"] = latest["rodzaj_decyzji"]
        res["has_gif_warning"] = latest["rodzaj_decyzji"] in [
            "Wycofanie z obrotu",
            "Wstrzymanie w obrocie",
            "Zakaz wprowadzania"
        ]
    else:
        res["gif_status"] = None
        res["has_gif_warning"] = False

    return res


@app.on_event("startup")
def startup_event():
    print(f"Loading PolDense-400M on {state['device']}...")
    state["model"] = SentenceTransformer(
        MODEL_NAME,
        device=state["device"],
        model_kwargs={
            "torch_dtype": torch.bfloat16 if state["device"] == "cuda" else torch.float32,
            "attn_implementation": "sdpa"
        }
    )
    if os.path.exists(ATC_MAP_PATH):
        with open(ATC_MAP_PATH, "r", encoding="utf-8") as f:
            state["atc_map"] = json.load(f)

    if os.path.exists(ATC_HIERARCHY_PATH):
        with open(ATC_HIERARCHY_PATH, "r", encoding="utf-8") as f:
            state["atc_hierarchy"] = json.load(f)
        print(f"Loaded {len(state['atc_hierarchy'])} ATC hierarchy nodes from {ATC_HIERARCHY_PATH}")

    state["db_conn"] = get_db_connection()
    print("API Server & PolDense-400M ready!")


import boto3
from botocore.config import Config

s3_client = boto3.client(
    "s3",
    endpoint_url="https://s3.waw.io.cloud.ovh.net",
    region_name="waw",
    config=Config(s3={"addressing_style": "path"})
)


@app.get("/api/stats")
def get_stats():
    conn = state["db_conn"]
    c = conn.cursor()
    c.execute("SELECT count(*) FROM produkty_lecznicze")
    total_products = c.fetchone()[0]
    c.execute("SELECT count(*) FROM vec_dokumenty")
    total_vectors = c.fetchone()[0]
    c.execute("SELECT count(*) FROM fts_dokumenty")
    total_fts = c.fetchone()[0]

    return {
        "total_products_xml": total_products,
        "indexed_vectors_vec0": total_vectors,
        "indexed_fts_morfeusz": total_fts,
        "model": MODEL_NAME,
        "device": state["device"]
    }


def get_wikidata_interactions(conn: sqlite3.Connection, produkt_id: int) -> Dict[str, Any]:
    c = conn.cursor()
    # 1. Get ATC codes for product
    c.execute("SELECT kod_atc FROM kody_atc WHERE produkt_id = ?", (produkt_id,))
    atc_rows = c.fetchall()
    atc_codes = [r["kod_atc"] for r in atc_rows if r["kod_atc"]]
    if not atc_codes:
        return {"source_substances": [], "interactions_count": 0, "interactions": []}

    # 2. Map ATC -> Wikidata substances
    placeholders = ",".join(["?"] * len(atc_codes))
    c.execute(f"""
        SELECT DISTINCT was.atc_code, ws.wikidata_id, ws.name
        FROM wikidata_atc_substance was
        JOIN wikidata_substances ws ON ws.wikidata_id = was.substance_wikidata_id
        WHERE was.atc_code IN ({placeholders})
    """, atc_codes)
    source_substances = [dict(r) for r in c.fetchall()]
    if not source_substances:
        return {"source_substances": [], "interactions_count": 0, "interactions": []}

    sub_qids = list(set(s["wikidata_id"] for s in source_substances))
    q_placeholders = ",".join(["?"] * len(sub_qids))

    # 3. Get interacting substances
    c.execute(f"""
        SELECT DISTINCT i.interacts_with_wikidata_id, ws.name as interacting_name
        FROM wikidata_interactions i
        JOIN wikidata_substances ws ON ws.wikidata_id = i.interacts_with_wikidata_id
        WHERE i.substance_wikidata_id IN ({q_placeholders})
        ORDER BY ws.name ASC
    """, sub_qids)
    interacting_rows = c.fetchall()

    interactions_list = []
    for row in interacting_rows:
        iw_qid = row["interacts_with_wikidata_id"]
        iw_name = row["interacting_name"]

        # 4. Get ATC codes of the interacting substance
        c.execute("SELECT atc_code FROM wikidata_atc_substance WHERE substance_wikidata_id = ?", (iw_qid,))
        iw_atcs = [r["atc_code"] for r in c.fetchall()]

        # 5. Find example drugs in RPL database that have this ATC code
        sample_drugs = []
        if iw_atcs:
            atc_ph = ",".join(["?"] * len(iw_atcs))
            c.execute(f"""
                SELECT DISTINCT p.id, p.nazwa_produktu, p.moc
                FROM produkty_lecznicze p
                JOIN kody_atc a ON a.produkt_id = p.id
                WHERE a.kod_atc IN ({atc_ph})
                ORDER BY p.nazwa_produktu ASC
                LIMIT 4
            """, iw_atcs)
            sample_drugs = [dict(d) for d in c.fetchall()]

        interactions_list.append({
            "wikidata_id": iw_qid,
            "substance_name": iw_name,
            "atc_codes": iw_atcs,
            "sample_drugs": sample_drugs
        })

    return {
        "source_substances": source_substances,
        "interactions_count": len(interactions_list),
        "interactions": interactions_list
    }


@app.get("/api/medicine/{produkt_id}")
def get_medicine_detail(produkt_id: int):
    conn = state["db_conn"]
    atc_map = state["atc_map"]
    atc_hierarchy = state.get("atc_hierarchy", {})
    c = conn.cursor()

    meta = get_medicine_metadata(conn, produkt_id, atc_map, atc_hierarchy)
    if "nazwa_produktu" not in meta:
        raise HTTPException(status_code=404, detail="Lek nie został znaleziony")

    # Administration routes
    c.execute("SELECT droga_podania_nazwa FROM drogi_podania WHERE produkt_id = ?", (produkt_id,))
    meta["drogi_podania"] = [r[0] for r in c.fetchall()]

    # Manufacturers / Importers
    c.execute("SELECT nazwa_wytworcy_importera, kraj_wytworcy_importera, kraj_eksportu FROM wytworcy WHERE produkt_id = ?", (produkt_id,))
    meta["wytworcy"] = [dict(r) for r in c.fetchall()]

    # Packaging details
    c.execute("SELECT opakowanie_id, kod_gtin, kategoria_dostepnosci, skasowane, numer_eu FROM opakowania WHERE produkt_id = ?", (produkt_id,))
    meta["opakowania"] = [dict(r) for r in c.fetchall()]

    # ChPL Markdown Content
    chpl_text = ""
    try:
        c.execute("SELECT chpl_content FROM fts_dokumenty WHERE produkt_id = ?", (str(produkt_id),))
        row = c.fetchone()
        if row and row["chpl_content"]:
            chpl_text = row["chpl_content"]
    except Exception:
        pass

    # If not in SQLite, fetch from S3
    if not chpl_text:
        try:
            s3_key = f"md/{produkt_id}.md"
            resp = s3_client.get_object(Bucket="plek", Key=s3_key)
            chpl_text = resp["Body"].read().decode("utf-8", errors="replace")
        except Exception as e:
            chpl_text = ""

    meta["chpl_markdown"] = chpl_text

    # Reimbursement data (Refundacja MZ/NFZ)
    meta["refundacja"] = []
    try:
        c.execute("""
            SELECT
                o.opakowanie_id,
                o.kod_gtin,
                r.typ_listy,
                r.nazwa_lek_dawka,
                r.zawartosc_opakowania,
                r.cena_detaliczna,
                r.wysokosc_limitu,
                r.poziom_odplatnosci,
                r.wysokosc_doplaty,
                r.zakres_wskazan,
                r.bezplatny_dziecko_18,
                r.bezplatny_senior_65,
                r.bezplatny_ciaza
            FROM opakowania o
            JOIN refundacja r ON ltrim(o.kod_gtin, '0') = r.kod_gtin_norm
            WHERE o.produkt_id = ?
        """, (produkt_id,))
        meta["refundacja"] = [dict(r) for r in c.fetchall()]
    except Exception as e:
        print(f"Error querying refundacja: {e}")

    meta["is_refundowany"] = len(meta["refundacja"]) > 0

    # Find 3 similar medicines based on vector similarity
    similar_medicines = []
    try:
        c.execute("SELECT embedding FROM vec_dokumenty WHERE produkt_id = ?", (produkt_id,))
        emb_row = c.fetchone()
        if emb_row and emb_row["embedding"]:
            c.execute("""
                SELECT produkt_id, distance
                FROM vec_dokumenty
                WHERE embedding MATCH ? AND k = 10
            """, (emb_row["embedding"],))
            matches = c.fetchall()
            for m in matches:
                other_id = int(m["produkt_id"])
                if other_id == produkt_id or other_id == 0:
                    continue
                s_meta = get_medicine_metadata(conn, other_id, atc_map)
                sim_pct = max(0.0, 1.0 - float(m["distance"])) * 100
                s_meta["similarity_pct"] = round(sim_pct, 1)
                s_meta["distance"] = float(m["distance"])
                similar_medicines.append(s_meta)
                if len(similar_medicines) >= 3:
                    break
    except Exception as e:
        print(f"Error computing similar medicines: {e}")

    meta["podobne_leki"] = similar_medicines

    # Wikidata Drug-Drug Interactions
    meta["interakcje_wikidata"] = get_wikidata_interactions(conn, produkt_id)

    return meta


@app.get("/api/search")
def search(
    q: str = Query(..., description="Query string in natural language"),
    mode: str = Query("rrf", description="Search mode: 'rrf', 'vec', or 'fts'"),
    only_refunded: bool = Query(False, description="Filter only reimbursed medicines (NFZ)"),
    top_k: int = Query(10, ge=1, le=50, description="Number of results"),
    vec_weight: float = Query(1.0, ge=0.0, le=10.0),
    fts_weight: float = Query(1.0, ge=0.0, le=10.0),
    rrf_k: int = Query(60, ge=1, le=200),
    candidate_pool: int = Query(50, ge=10, le=200)
):
    query_clean = q.strip()
    if not query_clean:
        return {"query": q, "results": [], "total": 0}

    conn = state["db_conn"]
    model = state["model"]
    atc_map = state["atc_map"]
    c = conn.cursor()

    effective_pool = candidate_pool * 2 if only_refunded else candidate_pool

    vec_ranks = {}
    vec_distances = {}
    fts_ranks = {}
    fts_scores = {}
    fts_snippets = {}

    # 1. Dense Vector Search
    if mode in ["rrf", "vec"]:
        prefixed_query = f"[query]: {query_clean}"
        query_vec = model.encode(
            prefixed_query,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True
        ).astype(np.float32)

        c.execute("""
            SELECT produkt_id, distance
            FROM vec_dokumenty
            WHERE embedding MATCH ? AND k = ?
        """, (query_vec.tobytes(), effective_pool))

        for rank, row in enumerate(c.fetchall(), 1):
            pid = int(row["produkt_id"])
            vec_ranks[pid] = rank
            vec_distances[pid] = float(row["distance"])

    # 2. Sparse FTS5 Search with Morfeusz Snippet
    if mode in ["rrf", "fts"]:
        # Prepare sanitized FTS query words
        fts_words = [w for w in query_clean.replace("\"", "").replace("'", "").replace("*", "").split() if len(w) > 1]
        fts_query = " OR ".join(fts_words) if fts_words else query_clean

        try:
            c.execute("""
                SELECT
                    CAST(produkt_id AS INTEGER) as produkt_id,
                    rank as fts_score,
                    snippet(fts_dokumenty, -1, '<mark class="hl">', '</mark>', '...', 35) as fts_snippet
                FROM fts_dokumenty
                WHERE fts_dokumenty MATCH ?
                ORDER BY rank ASC
                LIMIT ?
            """, (fts_query, effective_pool))

            for rank, row in enumerate(c.fetchall(), 1):
                pid = int(row["produkt_id"])
                fts_ranks[pid] = rank
                fts_scores[pid] = float(row["fts_score"])
                fts_snippets[pid] = row["fts_snippet"]
        except Exception as e:
            print(f"FTS Query error: {e}")

    # 3. Combine & Rank
    all_candidate_ids = set(vec_ranks.keys()) | set(fts_ranks.keys())
    ranked_items = []

    for pid in all_candidate_ids:
        r_vec = vec_ranks.get(pid)
        r_fts = fts_ranks.get(pid)

        if mode == "rrf":
            score_vec = (vec_weight / (rrf_k + r_vec)) if r_vec else 0.0
            score_fts = (fts_weight / (rrf_k + r_fts)) if r_fts else 0.0
            final_score = score_vec + score_fts
        elif mode == "vec":
            final_score = (1.0 / (rrf_k + r_vec)) if r_vec else 0.0
        else:  # mode == "fts"
            final_score = (1.0 / (rrf_k + r_fts)) if r_fts else 0.0

        ranked_items.append({
            "produkt_id": pid,
            "score": final_score,
            "rank_vec": r_vec,
            "rank_fts": r_fts,
            "vec_distance": vec_distances.get(pid),
            "fts_score": fts_scores.get(pid),
            "snippet": fts_snippets.get(pid, "")
        })

    # Sort descending by score
    ranked_items.sort(key=lambda x: x["score"], reverse=True)

    atc_hierarchy = state.get("atc_hierarchy", {})

    # Enrich with metadata & apply only_refunded filter
    results = []
    for item in ranked_items:
        meta = get_medicine_metadata(conn, item["produkt_id"], atc_map, atc_hierarchy)
        if only_refunded and not meta.get("is_refundowany"):
            continue
        meta.update(item)
        results.append(meta)
        if len(results) >= top_k:
            break

    return {
        "query": query_clean,
        "mode": mode,
        "only_refunded": only_refunded,
        "total_results": len(results),
        "results": results
    }


def normalize_query_str(text: str) -> str:
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


@app.get("/api/suggestions")
def get_suggestions(
    q: str = Query(..., min_length=1, max_length=100, description="Query prefix"),
    limit: int = Query(8, ge=1, le=20, description="Max suggestions to return")
):
    norm = normalize_query_str(q)
    if not norm:
        return {"query": q, "suggestions": []}

    conn = state["db_conn"]
    c = conn.cursor()

    c.execute("""
        SELECT DISTINCT fraza, kategoria, podtytul, payload_id, popularnosc,
               CASE 
                   WHEN fraza_norm = ? THEN 0
                   WHEN fraza_norm LIKE ? THEN 1
                   ELSE 2
               END as match_priority
        FROM podpowiedzi
        WHERE fraza_norm LIKE ? OR fraza_norm LIKE ?
        ORDER BY match_priority ASC, popularnosc DESC, length(fraza) ASC
        LIMIT ?
    """, (norm, f"{norm}%", f"{norm}%", f"% {norm}%", limit))

    rows = c.fetchall()
    suggestions = []
    seen = set()

    icon_map = {
        "lek": "💊",
        "substancja": "🧪",
        "atc": "🏷️",
        "objaw": "🩺"
    }

    category_labels = {
        "lek": "Lek",
        "substancja": "Substancja",
        "atc": "Klasyfikacja ATC",
        "objaw": "Objaw / Wskazanie"
    }

    for r in rows:
        fraza = r["fraza"]
        kat = r["kategoria"]
        if (fraza, kat) in seen:
            continue
        seen.add((fraza, kat))

        suggestions.append({
            "text": fraza,
            "type": kat,
            "type_label": category_labels.get(kat, kat),
            "icon": icon_map.get(kat, "🔍"),
            "subtext": r["podtytul"],
            "payload_id": r["payload_id"]
        })

    return {
        "query": q,
        "suggestions": suggestions
    }


if __name__ == "__main__":
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
