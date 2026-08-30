/**
 * Bun Web Server for RPL Search & Markdown Rendering.
 * Powered by Bun 1.4.0's native Bun.markdown.html() renderer.
 */

const PORT = process.env.PORT || 3000;
const API_BACKEND = process.env.API_BACKEND || "http://127.0.0.1:8000";

function getAtcIcon(kod_atc: string) {
  const icons: Record<string, string> = {
    A: "stomach.svg",
    B: "blood.svg",
    C: "heart.svg",
    D: "skin.svg",
    G: "gender.svg",
    H: "hormones.svg",
    J: "virus.svg",
    L: "cancer.svg",
    M: "bones.svg",
    N: "brain.svg",
    P: "bug.svg",
    R: "lungs.svg",
    S: "eye.svg",
    V: "other.svg",
  };
  const letter = (kod_atc || "").trim().charAt(0).toUpperCase();
  return icons[letter] || "other.svg";
}

function escapeHtml(str: any) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderGlobalHeader(options: { isHome?: boolean } = {}): string {
  const isHome = options.isHome ?? false;
  return `
  <header class="global-site-header">
    <div class="header-inner">
      <a href="/" onclick="handleBack(event)" class="brand-link" title="Strona główna wyszukiwarki RPL">
        <img src="/svg/chpl.svg" alt="RPL Logo" class="brand-logo" width="24" height="24" />
        <span class="brand-title">Rejestr Produktów Leczniczych</span>
        <span class="brand-badge">RRF Search</span>
      </a>
      <nav class="header-nav">
        ${
          isHome
            ? `<span class="header-nav-status">Wyszukiwarka leków i ChPL</span>`
            : `<a href="/" onclick="handleBack(event)" class="nav-search-btn" title="Powrót do wyników wyszukiwania">
                <span class="nav-icon">🔍</span>
                <span>Wróć do wyszukiwarki</span>
               </a>`
        }
      </nav>
    </div>
  </header>
  `;
}

function renderMedicinePage(med: any): string {
  const firstAtc = med.atc && med.atc[0] ? med.atc[0] : { code: "", group: "", subgroup: "", display: "" };
  const iconSvg = getAtcIcon(firstAtc.code);

  // Render markdown with Bun 1.4.0 native markdown renderer!
  let chplHtml = "";
  if (med.chpl_markdown && med.chpl_markdown.trim()) {
    try {
      chplHtml = Bun.markdown.html(med.chpl_markdown, {
        tables: true,
        headings: { ids: true },
        autolinks: true,
      });
    } catch (e: any) {
      chplHtml = `<p>Błąd parsowania Markdown: ${escapeHtml(e.message)}</p>`;
    }
  } else {
    chplHtml = `<p class="text-muted">Brak dołączonego pliku Charakterystyki Produktu Leczniczego (ChPL).</p>`;
  }

  // Substances table rows
  let substancesRows = "";
  if (med.substancje && med.substancje.length > 0) {
    substancesRows = med.substancje
      .map(
        (s: any) =>
          `<tr><td>${escapeHtml(s.nazwa)}</td><td>${escapeHtml(s.ilosc)} ${escapeHtml(s.jednostka)}</td></tr>`
      )
      .join("");
  } else {
    substancesRows = `<tr><td colspan="2">${escapeHtml(med.nazwa_powszechnie_stosowana || "Brak danych")}</td></tr>`;
  }

  // Packaging table rows
  let pkgRows = "";
  if (med.opakowania && med.opakowania.length > 0) {
    pkgRows = med.opakowania
      .map(
        (p: any) =>
          `<tr><td>${escapeHtml(p.opakowanie_id)}</td><td>${escapeHtml(p.kod_gtin || "—")}</td><td>${escapeHtml(p.kategoria_dostepnosci)}</td></tr>`
      )
      .join("");
  }

  // Routes
  const routesStr = (med.drogi_podania || []).join(", ") || "Brak danych";

  return `<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="icon" type="image/svg+xml" href="/svg/chpl.svg" />
  <title>${escapeHtml(med.nazwa_produktu)} — RPL</title>
  <link rel="stylesheet" href="/style.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:ital,wght@0,100..900;1,100..900&display=swap" rel="stylesheet">
  <script>
    function handleBack(e) {
      if (e) e.preventDefault();
      if (window.history.length > 1) {
        window.history.back();
      } else {
        const q = sessionStorage.getItem("last_search_query");
        window.location.href = q ? "/?q=" + encodeURIComponent(q) : "/";
      }
    }
  </script>
</head>
<body>
  ${renderGlobalHeader({ isHome: false })}

  <div class="single-medicine-main">
    <p class="back-link"><a href="/" onclick="handleBack(event)">&larr; Powrót do wyszukiwarki</a></p>

    <div class="single-med-header">
      <div class="header-left">
        <h1>${escapeHtml(med.nazwa_produktu)}</h1>
        <p class="med-subtitle">${escapeHtml(med.nazwa_powszechnie_stosowana || "")} ${med.moc ? `(${escapeHtml(med.moc)})` : ""}</p>
        <p class="atc-code-tag">Kod ATC: <b>${escapeHtml(firstAtc.code || "Brak")}</b></p>
        
        <div class="doc-buttons">
          ${med.charakterystyka ? `<a href="${escapeHtml(med.charakterystyka)}" target="_blank" rel="noopener noreferrer" class="btn-doc btn-chpl">📄 Pobierz ChPL (Rejestr RPL)</a>` : ""}
          ${med.ulotka ? `<a href="${escapeHtml(med.ulotka)}" target="_blank" rel="noopener noreferrer" class="btn-doc btn-leaflet">📋 Pobierz Ulotkę (Rejestr RPL)</a>` : ""}
          ${med.etykieto_ulotka ? `<a href="${escapeHtml(med.etykieto_ulotka)}" target="_blank" rel="noopener noreferrer" class="btn-doc btn-leaflet">🏷️ Etykieto-ulotka (Rejestr RPL)</a>` : ""}
        </div>
      </div>
      <div class="header-right">
        <img src="/svg/${iconSvg}" alt="${escapeHtml(firstAtc.code)}" width="56" height="56">
      </div>
    </div>

    ${
      med.decyzje_gif && med.decyzje_gif.length > 0
        ? `
    <div class="gif-alert-box">
      <div class="gif-alert-header">
        <span class="gif-alert-icon">ℹ️</span>
        <b>Komunikat Głównego Inspektora Farmaceutycznego (GIF / RDG)</b>
      </div>
      <div class="gif-alert-body">
        <p class="gif-alert-desc">Dla tego produktu leczniczego zarejestrowano decyzje w Rejestrze Decyzji GIF:</p>
        <ul class="gif-decision-list">
          ${med.decyzje_gif
            .map(
              (d: any) => `
            <li class="gif-decision-item">
              <div class="gif-item-top">
                <span class="gif-badge ${
                  d.rodzaj_decyzji === "Wycofanie z obrotu"
                    ? "badge-wycofanie"
                    : d.rodzaj_decyzji === "Wstrzymanie w obrocie"
                    ? "badge-wstrzymanie"
                    : d.rodzaj_decyzji === "Zakaz wprowadzania"
                    ? "badge-zakaz"
                    : "badge-inna"
                }">${escapeHtml(d.rodzaj_decyzji)}</span>
                <span class="gif-meta">Decyzja nr <b>${escapeHtml(d.numer_decyzji)}</b> z dnia ${escapeHtml(d.data_decyzji || "b.d.")}</span>
              </div>
              ${
                d.numer_serii
                  ? `<div class="gif-series">Dotyczy serii: <code>${escapeHtml(d.numer_serii)}</code> ${d.data_waznosci ? `(data ważności: ${escapeHtml(d.data_waznosci)})` : ""}</div>`
                  : `<div class="gif-series">Dotyczy wszystkich serii / wprowadzenia do obrotu</div>`
              }
              ${
                d.link_decyzja
                  ? `<div class="gif-link-wrap"><a href="${escapeHtml(d.link_decyzja)}" target="_blank" rel="noopener noreferrer" class="gif-pdf-link">📄 Zobacz treść decyzji GIF (PDF) &rarr;</a></div>`
                  : ""
              }
            </li>
          `
            )
            .join("")}
        </ul>
      </div>
    </div>`
        : ""
    }

    <div class="info-table">
      <div class="info-group atc-info-group">
        <h3>Klasyfikacja ATC (Drzewo hierarchiczne)</h3>
        ${
          firstAtc.levels && firstAtc.levels.length > 0
            ? `<div class="atc-hierarchy-tree">
                ${firstAtc.levels
                  .map((lvl: any, idx: number) => {
                    const isLast = idx === firstAtc.levels.length - 1;
                    return `
                      <div class="atc-tree-node ${isLast ? 'atc-tree-leaf' : ''}">
                        <div class="atc-node-left">
                          <span class="atc-level-tag">Poziom ${lvl.level}</span>
                          <code class="atc-code-tag">${escapeHtml(lvl.code)}</code>
                        </div>
                        <div class="atc-node-right">
                          <div class="atc-node-name">${escapeHtml(lvl.name)}</div>
                          <div class="atc-node-desc">${escapeHtml(lvl.level_name)}</div>
                        </div>
                      </div>
                    `;
                  })
                  .join("")}
              </div>`
            : `<table>
                <tr>
                  <td>Grupa główna:</td>
                  <td>${escapeHtml(firstAtc.group || "Brak")}</td>
                </tr>
                <tr>
                  <td>Podgrupa:</td>
                  <td>${escapeHtml(firstAtc.subgroup || "Brak")}</td>
                </tr>
              </table>`
        }
      </div>

      <div class="info-group">
        <h3>Postać i podanie</h3>
        <table>
          <tr>
            <td>Postać:</td>
            <td>${escapeHtml(med.nazwa_postaci_farmaceutycznej || "—")}</td>
          </tr>
          <tr>
            <td>Droga podania:</td>
            <td>${escapeHtml(routesStr)}</td>
          </tr>
        </table>
      </div>

      <div class="info-group">
        <h3>Podmiot odpowiedzialny</h3>
        <table>
          <tr>
            <td>Podmiot:</td>
            <td>${escapeHtml(med.podmiot_odpowiedzialny || "—")}</td>
          </tr>
          <tr>
            <td>Nr pozwolenia:</td>
            <td>${escapeHtml(med.numer_pozwolenia || "—")} (${escapeHtml(med.waznosc_pozwolenia || "—")})</td>
          </tr>
        </table>
      </div>

      <div class="info-group">
        <h3>Skład i substancje czynne</h3>
        <table>
          <thead>
            <tr>
              <th>Substancja</th>
              <th>Ilość</th>
            </tr>
          </thead>
          <tbody>
            ${substancesRows}
          </tbody>
        </table>
      </div>

      ${
        med.refundacja && med.refundacja.length > 0
          ? `<div class="info-group">
        <h3>Dostępne opakowania i refundacja (NFZ / MZ)</h3>
        <table class="refund-table">
          <thead>
            <tr>
              <th>Opakowanie</th>
              <th>Cena</th>
              <th>Odpłatność</th>
              <th>Dopłata</th>
              <th>0 zł</th>
              <th>Wskazania</th>
            </tr>
          </thead>
          <tbody>
            ${med.refundacja
              .map((r: any) => {
                let badges = [];
                if (r.bezplatny_senior_65) badges.push('<span class="rf-tag rf-senior" title="Bezpłatny dla seniorów 65+">65+</span>');
                if (r.bezplatny_dziecko_18) badges.push('<span class="rf-tag rf-child" title="Bezpłatny dla dzieci i młodzieży do 18 r.ż.">&lt;18</span>');
                if (r.bezplatny_ciaza) badges.push('<span class="rf-tag rf-preg" title="Bezpłatny dla kobiet w ciąży">Ciąża</span>');
                const badgesHtml = badges.length > 0 ? badges.join(" ") : "—";
                const cena = r.cena_detaliczna ? `${escapeHtml(r.cena_detaliczna)} zł` : "—";
                const doplata = r.wysokosc_doplaty ? `${escapeHtml(r.wysokosc_doplaty)} zł` : "—";
                return `
                  <tr>
                    <td><b>${escapeHtml(r.zawartosc_opakowania || r.nazwa_lek_dawka || "Opakowanie")}</b><br><span style="font-size:0.7rem; color:var(--text-gray)">GTIN: ${escapeHtml(r.kod_gtin)}</span></td>
                    <td>${cena}</td>
                    <td><span class="rf-odp">${escapeHtml(r.poziom_odplatnosci)}</span></td>
                    <td><b style="color:var(--primary)">${doplata}</b></td>
                    <td>${badgesHtml}</td>
                    <td style="font-size:0.73rem; color:#444; max-width:180px;">${escapeHtml(r.zakres_wskazan || "Wszystkie zarejestrowane wskazania")}</td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>`
          : `<div class="info-group">
        <h3>Opakowania i refundacja</h3>
        ${
          pkgRows
            ? `<table>
          <thead>
            <tr>
              <th>ID Opakowania</th>
              <th>Kod GTIN</th>
              <th>Dostępność</th>
            </tr>
          </thead>
          <tbody>
            ${pkgRows}
          </tbody>
        </table>`
            : '<p style="color:var(--text-gray); font-size:0.8rem;">Brak danych o opakowaniach.</p>'
        }
        <p style="margin-top:0.5rem; font-size:0.78rem; color:var(--text-gray); font-style:italic;">ℹ️ Produkt nie znajduje się w aktualnym wykazie leków refundowanych (odpłatność 100%).</p>
      </div>`
      }

      ${
        med.podobne_leki && med.podobne_leki.length > 0
          ? `<div class="info-group">
        <h3>Podobne leki (podobieństwo wektorowe)</h3>
        <div class="similar-list">
          ${med.podobne_leki
            .map((sim: any) => {
              const sAtc = sim.atc && sim.atc[0] ? sim.atc[0] : { code: "", display: "" };
              const sIcon = getAtcIcon(sAtc.code);
              const sCommon = sim.nazwa_powszechnie_stosowana || (sim.substancje && sim.substancje[0] ? sim.substancje[0].nazwa : "");
              const sMoc = sim.moc ? ` (${sim.moc})` : "";
              return `
                <div class="similar-item">
                  <div class="similar-left">
                    <a href="/lek/${sim.id}">
                      <div class="similar-title">${escapeHtml(sim.nazwa_produktu)}</div>
                      <div class="similar-sub">${escapeHtml(sCommon)}${escapeHtml(sMoc)} &bull; ${escapeHtml(sim.nazwa_postaci_farmaceutycznej || "")}</div>
                    </a>
                  </div>
                  <div class="similar-right">
                    <span class="sim-badge">${sim.similarity_pct}%</span>
                    <a href="/lek/${sim.id}">
                      <img src="/svg/${sIcon}" alt="${escapeHtml(sAtc.code)}" title="${escapeHtml(sAtc.display)}" width="32" height="32">
                    </a>
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>`
          : ""
      }
    </div>

    <!-- Zastosowanie Medyczne (Wikidata) - Komponent Rozwijany -->
    <div class="interactions-wrapper" style="margin-bottom: 0.75rem;">
      <details class="interactions-accordion uses-accordion">
        <summary class="interactions-summary">
          <div class="summary-left">
            <span class="badge-experimental">Funkcja testowa</span>
            <span class="summary-title">Zastosowanie lecznicze i wskazania (Wikidata)</span>
          </div>
          <div class="summary-right">
            ${
              med.zastosowanie_wikidata && med.zastosowanie_wikidata.conditions_count > 0
                ? `<span class="interactions-count-badge uses-count-badge">${med.zastosowanie_wikidata.conditions_count} leczonych stanów</span>`
                : `<span class="interactions-count-badge badge-empty">0 wskazań w bazie</span>`
            }
            <span class="chevron-icon">▾</span>
          </div>
        </summary>

        <div class="interactions-body">
          <div class="disclaimer-callout">
            <b>ℹ️ Zastrzeżenie prawne i medyczne:</b> Informacje o zastosowaniu leczniczym są mapowane z otwartej bazy wiedzy <b>Wikidata</b> (właściwość <i>leczony stan medyczny / P2175</i>). Moduł ten ma charakter wyłącznie poglądowo-edukacyjny i <u>nie zastępuje</u> oficjalnych wskazań terapeutycznych zawartych w punkcie <b>4.1 ChPL</b> poniżej.
          </div>

          ${
            med.zastosowanie_wikidata && med.zastosowanie_wikidata.conditions_count > 0
              ? `
              <div class="interactions-source-info">
                Substancje leku (Wikidata): ${med.zastosowanie_wikidata.source_substances
                  .map((s: any) => `<b>${escapeHtml(s.name)}</b> (ATC: <code>${escapeHtml(s.atc_code)}</code>)`)
                  .join(", ")}
              </div>
              <div class="uses-tags-cloud">
                ${med.zastosowanie_wikidata.conditions
                  .map((c: any) => `
                    <div class="use-tag-item">
                      <div class="use-tag-main">
                        <span class="use-tag-icon">🩺</span>
                        <span class="use-tag-name">${escapeHtml(c.name)}</span>
                        ${c.substance ? `<span class="use-tag-sub">(${escapeHtml(c.substance)})</span>` : ""}
                      </div>
                      <div class="use-tag-codes">
                        ${
                          c.icd11_mms || c.icd11_foundation_id
                            ? `<a href="${escapeHtml(c.icd11_url || ('https://icd.who.int/browse/2026-01/mms/pl#' + (c.icd11_foundation_id || '')))}" target="_blank" rel="noopener noreferrer" class="icd-badge icd11-badge" title="ICD-11 (MMS: ${escapeHtml(c.icd11_mms || 'brak')}, Foundation ID: ${escapeHtml(c.icd11_foundation_id || 'brak')})">
                                <span class="icd-type">ICD-11</span>
                                <span class="icd-code">${escapeHtml(c.icd11_mms || c.icd11_foundation_id)}</span>
                                <span class="icd-ext-icon">↗</span>
                              </a>`
                            : ""
                        }
                        ${
                          c.icd10_codes
                            ? `<span class="icd-badge icd10-badge" title="ICD-10">
                                <span class="icd-type">ICD-10</span>
                                <span class="icd-code">${escapeHtml(c.icd10_codes)}</span>
                              </span>`
                            : ""
                        }
                        <a href="https://www.wikidata.org/wiki/${escapeHtml(c.wikidata_id)}" target="_blank" rel="noopener noreferrer" class="icd-badge qid-badge" title="Encja Wikidata: ${escapeHtml(c.wikidata_name || c.name)}">
                          <span class="icd-type">WD</span>
                          <span class="icd-code">${escapeHtml(c.wikidata_id)}</span>
                        </a>
                      </div>
                    </div>
                  `)
                  .join("")}
              </div>`
              : `
              <div class="no-interactions-msg">
                <p>ℹ️ <b>Brak zarejestrowanych wskazań w bazie Wikidata</b> dla kodów ATC tego produktu (${escapeHtml(firstAtc.code || "brak")}).</p>
                <p class="no-inter-sub">Pełne i wiążące wskazania terapeutyczne znajdują się w sekcji <b>4.1 ChPL (Wskazania do stosowania)</b> poniżej.</p>
              </div>`
          }
        </div>
      </details>
    </div>

    <!-- Interakcje Lekowe (Wikidata) - Komponent Rozwijany -->
    <div class="interactions-wrapper">
      <details class="interactions-accordion">
        <summary class="interactions-summary">
          <div class="summary-left">
            <span class="badge-experimental">Funkcja testowa</span>
            <span class="summary-title">Interakcje z innymi substancjami (Wikidata)</span>
          </div>
          <div class="summary-right">
            ${
              med.interakcje_wikidata && med.interakcje_wikidata.interactions_count > 0
                ? `<span class="interactions-count-badge">${med.interakcje_wikidata.interactions_count} wykrytych interakcji</span>`
                : `<span class="interactions-count-badge badge-empty">0 interakcji w bazie</span>`
            }
            <span class="chevron-icon">▾</span>
          </div>
        </summary>

        <div class="interactions-body">
          <div class="disclaimer-callout">
            <b>ℹ️ Zastrzeżenie prawne i medyczne:</b> Informacje o interakcjach są mapowane automatycznie z otwartej bazy wiedzy <b>Wikidata</b> (Wikiprojekt Lekoznawstwo). Moduł ten ma charakter wyłącznie poglądowo-badawczy i <u>nie może</u> być traktowany jako definitywne ani wyczerpujące źródło wiedzy medycznej. Zawsze zapoznaj się z oficjalną Charakterystyką Produktu Leczniczego (ChPL) i skonsultuj z lekarzem lub farmaceutą.
          </div>

          ${
            med.interakcje_wikidata && med.interakcje_wikidata.interactions_count > 0
              ? `
              <div class="interactions-source-info">
                Substancja czynna leku (Wikidata): ${med.interakcje_wikidata.source_substances
                  .map((s: any) => `<b>${escapeHtml(s.name)}</b> (ATC: <code>${escapeHtml(s.atc_code)}</code>)`)
                  .join(", ")}
              </div>
              <div class="interactions-grid">
                ${med.interakcje_wikidata.interactions
                  .map((item: any) => {
                    const atcBadges = item.atc_codes && item.atc_codes.length > 0
                      ? item.atc_codes.map((c: string) => `<span class="atc-code-pill">${escapeHtml(c)}</span>`).join(" ")
                      : "";
                    const sampleDrugsHtml = item.sample_drugs && item.sample_drugs.length > 0
                      ? `<div class="sample-drugs-wrap">
                          <span class="sample-drugs-label">Przykłady w rejestrze RPL:</span>
                          <div class="sample-drugs-list">
                            ${item.sample_drugs
                              .map((sd: any) => `<a href="/lek/${sd.id}" class="sample-drug-pill" title="${escapeHtml(sd.nazwa_produktu)}">${escapeHtml(sd.nazwa_produktu)}</a>`)
                              .join("")}
                          </div>
                        </div>`
                      : "";
                    return `
                      <div class="interaction-card">
                        <div class="interaction-card-header">
                          <span class="inter-substance-name">${escapeHtml(item.substance_name)}</span>
                          ${atcBadges}
                        </div>
                        ${sampleDrugsHtml}
                      </div>
                    `;
                  })
                  .join("")}
              </div>`
              : `
              <div class="no-interactions-msg">
                <p>ℹ️ <b>Brak zarejestrowanych interakcji w bazie Wikidata</b> dla kodów ATC tego produktu (${escapeHtml(firstAtc.code || "brak")}).</p>
                <p class="no-inter-sub">Brak wpisu w bazie Wikidata nie oznacza braku interakcji farmakologicznych. Szczegółowe i wiążące informacje o interakcjach z innymi lekami znajdują się w sekcji <b>4.5 ChPL</b> poniżej.</p>
              </div>`
          }
        </div>
      </details>
    </div>

    <div class="chpl-section">
      <h2>Charakterystyka Produktu Leczniczego (ChPL)</h2>
      <div class="chpl-content markdown-body">
        ${chplHtml}
      </div>
    </div>

    <p class="back-link-bottom"><a href="/" onclick="handleBack(event)">&larr; Powrót na stronę główną</a></p>

    <footer class="app-footer">
      <p id="dbStatsText">Baza danych: wczytywanie...</p>
    </footer>
  </div>

  <script>
    fetch('/api/stats')
      .then(r => r.json())
      .then(d => {
        const el = document.getElementById('dbStatsText');
        if (el) {
          el.innerHTML = 'Baza RPL: <b>' + d.total_products_xml.toLocaleString() + '</b> leków &bull; Teksty ChPL: <b>' + d.indexed_fts_morfeusz.toLocaleString() + '</b> &bull; Wektory: <b>' + d.indexed_vectors_vec0.toLocaleString() + '</b>';
        }
      })
      .catch(() => {});
  </script>
</body>
</html>`;
}

const server = Bun.serve({
  port: PORT,
  hostname: "0.0.0.0",
  async fetch(req) {
    const url = new URL(req.url);

    // 1. Medicine Detail Route: /lek/:id
    const lekMatch = url.pathname.match(/^\/lek\/(\d+)$/);
    if (lekMatch) {
      const produktId = lekMatch[1];
      try {
        const res = await fetch(`${API_BACKEND}/api/medicine/${produktId}`);
        if (!res.ok) {
          return new Response(`<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="icon" type="image/svg+xml" href="/svg/chpl.svg" />
  <title>Nie znaleziono leku — RPL</title>
  <link rel="stylesheet" href="/style.css" />
  <script>
    function handleBack(e) {
      if (e) e.preventDefault();
      const q = sessionStorage.getItem("last_search_query");
      window.location.href = q ? "/?q=" + encodeURIComponent(q) : "/";
    }
  </script>
</head>
<body>
  ${renderGlobalHeader({ isHome: false })}
  <div class="single-medicine-main" style="text-align: center; padding: 2.5rem 1rem;">
    <h1>Lek #${escapeHtml(produktId)} nie został znaleziony</h1>
    <p style="color: var(--text-gray); margin: 1rem 0 2rem;">Podany identyfikator produktu leczniczego nie istnieje w bazie RPL.</p>
    <p><a href="/" onclick="handleBack(event)" class="nav-search-btn" style="display: inline-flex;">← Wróć do wyszukiwarki</a></p>
  </div>
</body>
</html>`, {
            status: 404,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          });
        }
        const med = await res.json();
        const pageHtml = renderMedicinePage(med);
        return new Response(pageHtml, {
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      } catch (err: any) {
        return new Response(`<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="icon" type="image/svg+xml" href="/svg/chpl.svg" />
  <title>Błąd serwera — RPL</title>
  <link rel="stylesheet" href="/style.css" />
  <script>
    function handleBack(e) {
      if (e) e.preventDefault();
      const q = sessionStorage.getItem("last_search_query");
      window.location.href = q ? "/?q=" + encodeURIComponent(q) : "/";
    }
  </script>
</head>
<body>
  ${renderGlobalHeader({ isHome: false })}
  <div class="single-medicine-main" style="text-align: center; padding: 2.5rem 1rem;">
    <h1>Wystąpił błąd serwera</h1>
    <p style="color: #dc2626; margin: 1rem 0 2rem;">${escapeHtml(err.message)}</p>
    <p><a href="/" onclick="handleBack(event)" class="nav-search-btn" style="display: inline-flex;">← Wróć do wyszukiwarki</a></p>
  </div>
</body>
</html>`, {
          status: 500,
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      }
    }

    // 2. Proxy API requests
    if (url.pathname.startsWith("/api/")) {
      try {
        const targetUrl = `${API_BACKEND}${url.pathname}${url.search}`;
        const response = await fetch(targetUrl, {
          method: req.method,
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
          },
          body: req.method !== "GET" && req.method !== "HEAD" ? req.body : undefined,
        });

        const data = await response.arrayBuffer();
        const headers = new Headers();
        headers.set("Content-Type", response.headers.get("Content-Type") || "application/json; charset=utf-8");
        headers.set("Access-Control-Allow-Origin", "*");
        headers.set("Cache-Control", "no-cache, no-store, must-revalidate");

        return new Response(data, {
          status: response.status,
          headers,
        });
      } catch (err: any) {
        return new Response(
          JSON.stringify({
            error: "Backend API is currently offline or loading model",
            details: err?.message,
          }),
          {
            status: 503,
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "Access-Control-Allow-Origin": "*",
            },
          }
        );
      }
    }

    // 3. Static File Serving with Proper MIME types
    const MIME_TYPES: Record<string, string> = {
      ".html": "text/html; charset=utf-8",
      ".css": "text/css; charset=utf-8",
      ".js": "application/javascript; charset=utf-8",
      ".json": "application/json; charset=utf-8",
      ".svg": "image/svg+xml",
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".ico": "image/x-icon",
      ".woff2": "font/woff2",
      ".woff": "font/woff",
    };

    let filePath = url.pathname === "/" ? "/index.html" : url.pathname;
    const file = Bun.file(`${import.meta.dir}/public${filePath}`);

    if (await file.exists()) {
      const extMatch = filePath.match(/\.[a-z0-9]+$/i);
      const ext = extMatch ? extMatch[0].toLowerCase() : "";
      const contentType = MIME_TYPES[ext] || file.type || "application/octet-stream";

      return new Response(file, {
        headers: {
          "Content-Type": contentType,
          "Cache-Control": ext === ".html" ? "no-cache" : "public, max-age=86400",
          "Access-Control-Allow-Origin": "*",
        },
      });
    }

    return new Response("Not Found", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  },
});

console.log(`🌐 RPL Web App running on http://0.0.0.0:${server.port} (Local: http://localhost:${server.port})`);
console.log(`🔗 Connected to API Backend: ${API_BACKEND}`);
console.log(`⚡ Markdown engine: Bun ${Bun.version} native Rust parser`);
