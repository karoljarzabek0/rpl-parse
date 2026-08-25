# Interfejs Użytkownika i Warstwa Frontend (Web UI)

Interfejs aplikacji WWW łączy szybkość renderowania po stronie serwera (**Bun.js SSR**) z natychmiastową reaktywnością po stronie klienta (Vanilla JavaScript, zero zbędnych bibliotek, natywny CSS).

---

## 1. Serwer Aplikacji WWW (`web/server.ts`)

Serwer działa pod kontrolą środowiska **Bun** i odpowiada za:
- **Routing URL**:
  - `/` — strona główna wyszukiwarki (serwuje statyczny szablon `index.html`),
  - `/lek/:id` — pełna karta produktu leczniczego generowana w locie po stronie serwera (SSR),
  - `/api/*` — transparentny proxy do serwera Pythona (`http://127.0.0.1:8000`),
  - `/svg/*`, `/style.css`, `/app.js` — serwowanie zasobów statycznych.
- **Natywny Parser Markdown w Rust**:
  - Wykorzystuje wbudowaną w silnik Bun 1.4+ obsługę szybkiego parsowania Markdown (`Bun.markdown`), co pozwala na renderowanie wielotysięcznych dokumentów ChPL do HTML w czasie poniżej **0.5 ms**.

---

## 2. Funkcjonalności Strony Głównej (`index.html` & `app.js`)

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Rejestr Produktów Leczniczych                        │
│                hybrydowa wyszukiwarka semantyczna (RRF)                │
├────────────────────────────────────────────────────────────────────────┤
│ [ Wpisz objawy, nazwę leku lub problem...                        ] [Szukaj] │
│ ☑ Tylko leki refundowane (NFZ)                                         │
├────────────────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────────────────────┐ │
│ │ 💊 Presartan 50 mg • Tabletki powlekane              [Refundowany] │ │
│ │ Substancja: Losartanum kalicum (50 mg) • ATC: C09CA01              │ │
│ │ ⚠️ Decyzja GIF: Wycofanie z obrotu                                 │ │
│ │ ...Leczenie pierwotnego nadciśnienia tętniczego u dorosłych...     │ │
│ └────────────────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────────┤
│ Baza RPL: 20 223 leków • Teksty ChPL: 11 084 • Wektory: 11 084        │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Pamięć Podręczna i Błyskawiczna Nawigacja Wstecz (0 ms Delay)
- Wszystkie wyszukiwane zapytania oraz ich wyniki są automatycznie buforowane w `sessionStorage` przeglądarki pod kluczami `cached_results_{query}_{mode}`.
- Przejście do szczegółów leku (`/lek/:id`) i powrót przyciskiem wstecz w przeglądarce lub linkiem `← Powrót` **natychmiastowo przywraca** stan widoku, pozycję checkboxa refundacji oraz listę wyników bez ponownego obciążania backendu.

### 2.2 Synchronizacja Adresu URL
- Wprowadzenie zapytania aktualizuje historię przeglądarki (`window.history.pushState`), dzięki czemu każdy stan wyszukiwania (np. `/?q=migrena&refunded=1`) można skopiować i udostępnić jako bezpośredni link.

---

## 3. Karta Szczegółów Leku (`/lek/:id`)

Karta produktu leczniczego składa się z logicznych bloków prezentujących kompletne dane urzędowe, kliniczne i bezpieczeństwa:

### 3.1 Nagłówek i Pigułki Klasyfikacji ATC
- Wyświetla nazwę handlową, postać, dawkę, podmiot odpowiedzialny oraz graficzną ikonę anatomicznej grupy ATC (np. *Układ krążenia*, *Układ nerwowy*, *Leki przeciwinfekcyjne*).

### 3.2 Alert Bezpieczeństwa GIF (Decyzje Nadzorcze)
- Jeśli Główny Inspektor Farmaceutyczny wydał decyzję dotyczącą tego produktu (np. *Wycofanie z obrotu* lub *Wstrzymanie*), na samej górze karty pojawia się informacyjny, spokojny baner ostrzegawczy z numerem decyzji, datą, seriami leku oraz bezpośrednim linkiem do pobrania oficjalnego uzasadnienia PDF.

### 3.3 Tabela Opakowań i Odpłatności NFZ / MZ
- Jeśli lek jest refundowany, generowana jest tabela z cenami urzędowymi, dopłatami pacjenta, zakresem wskazań refundacyjnych oraz specjalnymi uprawnieniami (**65+**, **<18**, **Ciąża**).

### 3.4 Podobne Leki (Podobieństwo Wektorowe)
- Trzy najbliższe preparaty wyznaczone na podstawie odległości wektorowej $L_2$ w przestrzeni `PolDense-400M` z podaniem procentowego wskaźnika zgodności semantycznej.

### 3.5 Interakcje Lekowe Wikidata (Komponent Rozwijany)
- **Komponent `<details class="interactions-accordion">`**:
  - Domyślnie zwinięty, aby nie przytłaczać użytkownika.
  - Wyposażony we flagę `FUNKCJA TESTOWA` oraz klauzulę informacyjną.
  - Prezentuje w siatce (grid) substancje wchodzące w interakcję, ich kody ATC oraz klikalne odnośniki do zarejestrowanych preparatów z bazy RPL.

### 3.6 Pełna Treść ChPL (SmPC Markdown)
- Sformatowana, czytelna treść urzędowej Charakterystyki Produktu Leczniczego (wskazania, przeciwwskazania, dawkowanie, farmakokinetyka, działania niepożądane).
