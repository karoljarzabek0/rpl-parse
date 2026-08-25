# Architektura Systemu i Silnik Wyszukiwania Hybrydowego

Platforma **RPL Search** została zaprojektowana z myślą o maksymalnej wydajności, precyzji semantycznej oraz prostocie utrzymania operacyjnego (ang. *operational simplicity*). Zamiast złożonych klastrów rozproszonych (Elasticsearch, OpenSearch, Qdrant), całość architektury opiera się na wydajnym silniku **SQLite** rozszerzonym o moduły wektorowe (`sqlite-vec`) oraz pełnotekstowe (`FTS5`), zasilanym przez model głęboki **`OPI-PIB/PolDense-400M`**.

---

## 1. Architektura Hybrydowa (RRF — Reciprocal Rank Fusion)

Tradycyjne wyszukiwarki leksykalne świetnie radzą sobie z dokładnymi nazwami handlowymi i substancjami chemicznymi (np. *"Amoksiklav"*, *"Ibuprofenum"*), ale zawodzą przy opisowych zapytaniach w języku naturalnym (np. *"pieczenie w klatce piersiowej po posiłku"*). Z kolei modele wektorowe doskonale rozumieją kontekst kliniczny i objawy, ale mogą mieć niższą precyzję przy unikalnych kodach i specyficznych nazwach własnych.

Aby połączyć zalety obu podejść, system implementuje algorytm **Reciprocal Rank Fusion (RRF)**:

$$RRF\_score(d) = \sum_{m \in \{vec, fts\}} w_m \cdot \frac{1}{k + rank_m(d)}$$

Gdzie:
- $rank_{vec}(d)$ — pozycja dokumentu w rankingu wektorowym (według odległości cosinusowej w `sqlite-vec`),
- $rank_{fts}(d)$ — pozycja dokumentu w rankingu leksykalnym (według punktacji BM25 w tabeli `fts_dokumenty`),
- $k = 60$ — stała wygładzająca (zapobiega dominacji pierwszych miejsc w pojedynczym kanale),
- $w_{vec} = 1.0, w_{fts} = 1.0$ — konfigurowalne wagi obu składowych.

```
                    ┌──────────────────────────────────────────────┐
                    │               Zapytanie (Query)              │
                    └──────────────────────┬───────────────────────┘
                                           │
                    ┌──────────────────────┴───────────────────────┐
                    ▼                                              ▼
    ┌───────────────────────────────┐              ┌───────────────────────────────┐
    │     Lematyzacja Morfeusz      │              │  PolDense-400M (Embeddings)   │
    │   "bóle głowy" -> "ból głowa" │              │   "[query]: bóle głowy..."    │
    └───────────────┬───────────────┘              └───────────────┬───────────────┘
                    ▼                                              ▼
    ┌───────────────────────────────┐              ┌───────────────────────────────┐
    │     SQLite FTS5 (BM25)        │              │     sqlite-vec (K-NN Cosine)  │
    │       Top 50 Kandydatów       │              │       Top 50 Kandydatów       │
    └───────────────┬───────────────┘              └───────────────┬───────────────┘
                    │                                              │
                    └──────────────────────┬───────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │        Reciprocal Rank Fusion (RRF)          │
                    │        RRF(d) = w1/(60+r1) + w2/(60+r2)      │
                    └──────────────────────┬───────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │    Top K Wyników (Wzbogaconych o Relacje)    │
                    │      (ChPL + Refundacja + GIF + Wikidata)    │
                    └──────────────────────────────────────────────┘
```

---

## 2. Warstwa Wyszukiwania Wektorowego (Dense Retrieval)

### 2.1 Model: `OPI-PIB/PolDense-400M`
- **Architektura**: ModernBERT zoptymalizowany dla języka polskiego przez Ośrodek Przetwarzania Informacji (OPI-PIB).
- **Wymiarowość wektora**: $D = 1024$ (`float32` / `bfloat16`).
- **Maksymalne okno kontekstu**: **4 096 tokenów**.
- **Prefiks zapytań**: Zgodnie ze specyfikacją retrieval modelu, każde zapytanie wyszukiwania jest poprzedzane prefiksem `[query]: `.
- **Akceleracja sprzętowa**: Wykorzystuje Flash Attention / SDPA (Scaled Dot-Product Attention) w PyTorch na GPU NVIDIA z obsługą `bfloat16`.

### 2.2 Strategia Chunkingu i Mean Poolingu dla dokumentów ChPL
Charakterystyki Produktów Leczniczych (ChPL) to obszerne dokumenty medyczne (często liczące 10 000 – 40 000 słów). W celu zachowania pełnej reprezentacji semantycznej:
1. Dokumenty są dzielone na nakładające się okna kontekstowe: **4 096 tokenów** z nakładaniem **256 tokenów**.
2. Dla każdego fragmentu generowany jest 1024-wymiarowy wektor embeddingu.
3. Wektor całego dokumentu $V_{doc}$ powstaje poprzez **uśrednienie wektorów wszystkich jego fragmentów (Mean Pooling)**:
   $$V_{doc} = \frac{1}{N} \sum_{i=1}^{N} V_{chunk_i}$$
4. Wektor jest normalizowany do normy $L_2$ ($||V_{doc}||_2 = 1$), co pozwala na wyznaczanie podobieństwa cosinusowego poprzez prosty iloczyn skalarny w `sqlite-vec`.

---

## 3. Warstwa Wyszukiwania Leksykalnego (FTS5 + Morfeusz 2)

Język polski charakteryzuje się bogatą i złożoną fleksją. Tradycyjne stemmery (np. Porter / Snowball) nie radzą sobie z nieregularną odmianą terminologii farmaceutycznej i medycznej (np. *lek*, *leku*, *lekiem*, *lekarstwa*; *gorączka*, *gorączce*, *przeciwgorączkowy*).

W projekcie zastosowano **Morfeusz 2** — profesjonalny analizator morfologiczny i lematyzator dla języka polskiego:
1. **Indeksowanie**: Podczas budowy indeksu FTS5, pełne teksty ChPL, nazwy handlowe, substancje czynne i opisy ATC są poddawane lematyzacji słownikowej do postaci bazowej (lematu).
2. **Wyszukiwanie**: Słowa wpisywane przez użytkownika w pasku wyszukiwania są w locie sprowadzane do lematów przed przekazaniem do zapytania `MATCH` tabeli wirtualnej `fts_dokumenty`.
3. **Generowanie Snippetów**: SQLite FTS5 generuje podświetlane wycinki zdań (`<mark class="hl">`), w których znaleziono słowa kluczowe.

---

## 4. Stos Technologiczny

| Warstwa | Technologia | Zastosowanie |
| :--- | :--- | :--- |
| **Baza danych** | SQLite 3.45+ | Pojedyncza, zoptymalizowana baza relacyjna, FTS5 i wektorowa |
| **Wektory w SQLite** | `sqlite-vec` (v0.1.6) | Wirtualna tabela `vec0`, indeksy wektorowe SIMD / AVX-512 |
| **Model Językowy** | `OPI-PIB/PolDense-400M` | Gęste embeddingi semantyczne 1024-d |
| **Backend API** | FastAPI / Python 3.12 (uv) | Serwer API, obsługa PyTorch/CUDA, rurociągi importujące |
| **Lematyzacja** | Morfeusz 2 (`morfeusz2`) | Fleksyjna analiza morfologiczna języka polskiego |
| **Frontend SSR** | Bun.js (TypeScript) | Szybki render server-side (0-1ms), obsługa routingu `/lek/:id` |
| **Silnik Markdown** | Bun 1.4 Native Rust parser | Błyskawiczny rendering HTML z Markdown dokumentów ChPL |
| **Object Storage** | S3 (OVH Cloud WAW) | Składowanie surowych plików ChPL oraz archiwów `.db.zst` |
| **Kompresja** | Zstandard (`zstd`) | Kompresja zrzutów bazy danych ze współczynnikiem redukcji >74% |
