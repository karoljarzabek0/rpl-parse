# Dokumentacja REST API (Backend API Reference)

Serwer backendu API (`python/api_server.py`) został zbudowany przy użyciu frameworka **FastAPI**. Standardowo nasłuchuje na porcie `8000` i komunikuje się z serwerem SSR oraz klientami zewnętrznymi za pomocą formatu JSON.

---

## 1. Wykaz Endpointów

| Metoda | Ścieżka | Opis |
| :--- | :--- | :--- |
| `GET` | `/api/stats` | Zwraca aktualne statystyki bazy danych i stan modelu wektorowego. |
| `GET` | `/api/search` | Główne zapytanie hybrydowe (RRF), wektorowe lub pełnotekstowe (FTS5). |
| `GET` | `/api/medicine/{id}` | Pełna karta leku (ChPL Markdown, refundacja, decyzje GIF, interakcje Wikidata, podobne leki). |
| `GET` | `/api/substance/{name}` | Dedykowany profil substancji czynnej (wskazania ICD-11/10, interakcje, lista leków w RPL). |
| `GET` | `/api/suggestions` | Podpowiedzi autouzupełniania dla paska wyszukiwania. |

---

## 2. Szczegółowy Opis Endpointów

### 2.1 `GET /api/stats`
Zwraca liczbę produktów, wektorów i dokumentów FTS zaindeksowanych w bazie.

#### Przykładowa odpowiedź:
```json
{
  "total_products_xml": 20223,
  "indexed_vectors_vec0": 11084,
  "indexed_fts_morfeusz": 11084,
  "model": "OPI-PIB/PolDense-400M",
  "device": "cuda"
}
```

---

### 2.2 `GET /api/search`
Główny endpoint wyszukiwarki wspierający zapytania w języku naturalnym.

#### Parametry zapytania (Query Parameters):
| Parametr | Typ | Domyślnie | Opis |
| :--- | :--- | :--- | :--- |
| `q` | `string` | *wymagany* | Tekst zapytania w języku naturalnym (np. *"ból gardła i gorączka"*). |
| `mode` | `string` | `"rrf"` | Tryb wyszukiwania: `"rrf"` (hybryda), `"vec"` (tylko wektory), `"fts"` (tylko FTS5). |
| `only_refunded`| `boolean`| `false` | Gdy `true`, zwraca wyłącznie leki objęte refundacją NFZ/MZ. |
| `top_k` | `integer`| `10` | Liczba zwracanych wyników (1 - 50). |
| `vec_weight` | `float` | `1.0` | Waga rankingu wektorowego w RRF ($w_{vec}$). |
| `fts_weight` | `float` | `1.0` | Waga rankingu leksykalnego w RRF ($w_{fts}$). |
| `rrf_k` | `integer`| `60` | Stała wygładzająca w RRF. |
| `candidate_pool`|`integer`| `50` | Liczba kandydatów pobieranych z każdej gałęzi przed scaleniem. |

#### Przykładowa odpowiedź:
```json
{
  "query": "nadciśnienie tętnicze",
  "mode": "rrf",
  "only_refunded": true,
  "total_results": 1,
  "results": [
    {
      "id": 100214643,
      "nazwa_produktu": "Presartan",
      "nazwa_powszechnie_stosowana": "Losartanum kalicum",
      "moc": "50 mg",
      "nazwa_postaci_farmaceutycznej": "Tabletki powlekane",
      "podmiot_odpowiedzialny": "Bausch Health Ireland Ltd.",
      "substancje_display": "Losartanum kalicum (50 mg)",
      "atc": [
        {
          "code": "C09CA01",
          "display": "C09CA01 → Leki działające na układ renina–angiotensyna (Układ sercowo-naczyniowy)"
        }
      ],
      "is_refundowany": true,
      "has_gif_warning": true,
      "gif_status": "Wycofanie z obrotu",
      "score": 0.0275,
      "rank_vec": 4,
      "rank_fts": 24,
      "snippet": "...Leczenie pierwotnego <mark class=\"hl\">nadciśnienia</mark> tętniczego..."
    }
  ]
}
```

---

### 2.3 `GET /api/medicine/{produkt_id}`
Zwraca komplet informacji o wybranym preparacie leczniczym.

#### Przykładowa odpowiedź:
```json
{
  "id": 100002148,
  "nazwa_produktu": "Acard",
  "nazwa_powszechnie_stosowana": "Acidum acetylsalicylicum",
  "moc": "75 mg",
  "nazwa_postaci_farmaceutycznej": "Tabletki dojelitowe",
  "podmiot_odpowiedzialny": "Zakłady Farmaceutyczne POLPHARMA S.A.",
  "substancje": [
    { "nazwa": "Acidum acetylsalicylicum", "ilosc": "75", "jednostka": "mg" }
  ],
  "atc": [
    { "code": "B01AC06", "subgroup": "Leki przeciwzakrzepowe", "group": "Krew i układ krwiotwórczy" }
  ],
  "opakowania": [
    { "opakowanie_id": "01", "wielkosc": "60", "kod_gtin": "05909990861118", "kategoria_dostepnosci": "OTC" }
  ],
  "refundacja": [],
  "is_refundowany": false,
  "decyzje_gif": [],
  "has_gif_warning": false,
  "podobne_leki": [
    {
      "id": 100054381,
      "nazwa_produktu": "Polopiryna Max",
      "moc": "500 mg",
      "similarity_pct": 89.4
    }
  ],
  "interakcje_wikidata": {
    "source_substances": [
      { "wikidata_id": "Q18216", "name": "kwas acetylosalicylowy", "atc_code": "B01AC06" }
    ],
    "interactions_count": 59,
    "interactions": [
      {
        "wikidata_id": "Q423758",
        "substance_name": "(RS)-citalopram",
        "atc_codes": ["N06AB04"],
        "sample_drugs": [
          { "id": 100008544, "nazwa_produktu": "Cipramil", "moc": "20 mg" }
        ]
      }
    ]
  },
  "chpl_markdown": "# 1. NAZWA PRODUKTU LECZNICZEGO\nAcard, 75 mg, tabletki dojelitowe..."
}
```

---

### 2.4 `GET /api/substance/{substance_name}`
Zwraca komplet informacji o wybranej substancji czynnej, jej wskazaniach medycznych (ICD-11 / ICD-10), interakcjach z innymi lekami oraz wszystkich zarejestrowanych w Polsce lekach zawierających tę substancję.

#### Elastyczne Rozpoznawanie Wejścia (*Flexible Multi-Source Resolution*):
Endpoint automatycznie rozpoznaje i rozwiązuje:
1. **Nazwy farmakopealne / łacińskie z RPL** (np. `Paracetamolum`, `Methadoni hydrochloridum`, `Amlodipini besilas`).
2. **Nazwy międzynarodowe i etykiety Wikidata** (np. `paracetamol`, `(RS)-metadon`, `rac-warfaryna`, `droperydol`).
3. **Identyfikatory QID z Wikidata** (np. `Q57055`, `Q179996`).
4. **Nazwy z prefiksami stereoizomerów** (automatyczne oczyszczanie prefiksów `(RS)-`, `(R)-`, `(S)-`, `rac-`, `dl-`).
5. **Substancje wyłącznie międzynarodowe** (gdy substancja nie ma jeszcze leku w Polsce, zwraca jej pełny profil Wikidata bez błędu 404).

#### Przykładowa odpowiedź:
```json
{
  "nazwa_substancji": "(RS)-metadon",
  "canonical_rpl_name": "Methadoni hydrochloridum",
  "wikidata": {
    "wikidata_id": "Q179996",
    "substance_name": "(RS)-metadon",
    "wikidata_url": "https://www.wikidata.org/wiki/Q179996"
  },
  "kody_atc": [
    {
      "code": "N07BC02",
      "subgroup": "Leki stosowane w uzależnieniu od opioidów",
      "group": "Układ nerwowy"
    }
  ],
  "liczba_produktow": 6,
  "liczba_jednoskladnikowych": 6,
  "liczba_wieloskladnikowych": 0,
  "produkty": [
    {
      "id": 100018988,
      "nazwa_produktu": "Methadone Hydrochloride Molteni",
      "moc": "1 mg/ml",
      "nazwa_postaci_farmaceutycznej": "Syrop",
      "podmiot_odpowiedzialny": "L. Molteni & C. dei F.lli Alitti Societa di Esercizio S.p.A.",
      "kategoria_dostepnosci": "Rpw",
      "czy_jednoskladnikowy": true,
      "kod_atc": "N07BC02"
    }
  ],
  "zastosowanie": {
    "conditions_count": 1,
    "conditions": [
      {
        "name": "Uzależnienie od opioidów",
        "wikidata_name": "opioid use disorder",
        "wikidata_id": "Q2891965",
        "icd11_mms": "6C40",
        "icd11_foundation_id": "1802951759",
        "icd11_url": "https://icd.who.int/browse/2026-01/mms/en#1802951759",
        "icd10_codes": "F11"
      }
    ]
  },
  "interakcje": {
    "interactions_count": 110,
    "interactions": [
      {
        "wikidata_id": "Q411347",
        "substance_name": "(RS)-amlodypina",
        "atc_codes": ["C08CA01"],
        "sample_drugs": [
          { "id": 100000301, "nazwa_produktu": "Amlopin", "moc": "5 mg" }
        ]
      }
    ]
  }
}
```
