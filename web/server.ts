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
      <a href="/" onclick="handleBack(event)" class="brand-link" title="Strona główna powlekane.pl">
        <img src="/svg/chpl.svg" alt="powlekane.pl Logo" class="brand-logo" width="24" height="24" />
        <span class="brand-title">powlekane<span class="brand-tld">.pl</span></span>
        <span class="brand-badge">Wyszukiwarka</span>
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
          `<tr><td><a href="/substancja/${encodeURIComponent(s.nazwa)}" class="substance-table-link" title="Zobacz szczegółowy profil substancji ${escapeHtml(s.nazwa)}">🔬 <b>${escapeHtml(s.nazwa)}</b> <span class="substance-link-arrow">↗</span></a></td><td>${escapeHtml(s.ilosc)} ${escapeHtml(s.jednostka)}</td></tr>`
      )
      .join("");
  } else if (med.nazwa_powszechnie_stosowana) {
    substancesRows = `<tr><td colspan="2"><a href="/substancja/${encodeURIComponent(med.nazwa_powszechnie_stosowana)}" class="substance-table-link" title="Zobacz profil substancji ${escapeHtml(med.nazwa_powszechnie_stosowana)}">🔬 <b>${escapeHtml(med.nazwa_powszechnie_stosowana)}</b> <span class="substance-link-arrow">↗</span></a></td></tr>`;
  } else {
    substancesRows = `<tr><td colspan="2">Brak danych</td></tr>`;
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
  <title>${escapeHtml(med.nazwa_produktu)} — powlekane.pl</title>
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
                            ? `<a href="${escapeHtml(c.icd11_url || ('https://icd.who.int/browse/2026-01/mms/en#' + (c.icd11_foundation_id || '')))}" target="_blank" rel="noopener noreferrer" class="icd-badge icd11-badge" title="ICD-11 (MMS: ${escapeHtml(c.icd11_mms || 'brak')}, Foundation ID: ${escapeHtml(c.icd11_foundation_id || 'brak')})">
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

function renderSubstancePage(sub: any): string {
  const atcList = sub.kody_atc || [];
  const primaryAtc = atcList[0] ? atcList[0].code : "";
  const iconSvg = getAtcIcon(primaryAtc);

  const conditions = sub.zastosowanie?.conditions || [];
  const interactions = sub.interakcje?.interactions || [];
  const products = sub.produkty || [];

  return `<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="icon" type="image/svg+xml" href="/svg/chpl.svg" />
  <title>${escapeHtml(sub.nazwa_substancji)} — Substancja Czynna — powlekane.pl</title>
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

    function filterProducts(filterType) {
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      const activeBtn = document.getElementById('tab-' + filterType);
      if (activeBtn) activeBtn.classList.add('active');

      const query = (document.getElementById('productSearchInput')?.value || '').toLowerCase().trim();
      const rows = document.querySelectorAll('.product-row-item');
      let visibleCount = 0;

      rows.forEach(row => {
        const isSingle = row.getAttribute('data-single') === 'true';
        const name = (row.getAttribute('data-name') || '').toLowerCase();
        const holder = (row.getAttribute('data-holder') || '').toLowerCase();

        let matchesTab = true;
        if (filterType === 'single') matchesTab = isSingle;
        if (filterType === 'combo') matchesTab = !isSingle;

        let matchesSearch = true;
        if (query) {
          matchesSearch = name.includes(query) || holder.includes(query);
        }

        if (matchesTab && matchesSearch) {
          row.style.display = '';
          visibleCount++;
        } else {
          row.style.display = 'none';
        }
      });

      const countEl = document.getElementById('visibleProductsCount');
      if (countEl) countEl.innerText = visibleCount.toString();
    }
  </script>
</head>
<body>
  ${renderGlobalHeader({ isHome: false })}

  <div class="single-medicine-main substance-detail-main">
    <p class="back-link"><a href="/" onclick="handleBack(event)">&larr; Powrót do wyszukiwarki</a></p>

    <!-- Substance Header -->
    <div class="single-med-header substance-header-card">
      <div class="header-left">
        <div class="substance-type-badge">
          <span class="substance-badge-icon">🧪</span>
          <span>Substancja Czynna (Rejestr RPL / Ph. Eur.)</span>
        </div>
        <h1 class="substance-title-main">${escapeHtml(sub.nazwa_substancji)}</h1>
        <p class="med-subtitle">
          ${sub.wikidata?.substance_name ? `Nazwa międzynarodowa (INN / PL): <b>${escapeHtml(sub.wikidata.substance_name)}</b>` : "Nazwa farmakopealna w rejestrze leków"}
        </p>
        
        <div class="substance-badges-bar">
          ${sub.wikidata?.wikidata_id ? `
            <a href="${escapeHtml(sub.wikidata.wikidata_url)}" target="_blank" rel="noopener noreferrer" class="icd-badge qid-badge" title="Profil substancji w grafie wiedzy Wikidata">
              <span class="icd-type">Wikidata</span>
              <span class="icd-code">${escapeHtml(sub.wikidata.wikidata_id)}</span>
              <span class="icd-ext-icon">↗</span>
            </a>
          ` : ""}
          
          ${atcList.map((a: any) => `
            <span class="atc-code-pill" title="${escapeHtml(a.subgroup || a.group || a.code)}">
              ATC: <b>${escapeHtml(a.code)}</b> ${a.subgroup ? `(${escapeHtml(a.subgroup)})` : ""}
            </span>
          `).join("")}
        </div>

        <div class="substance-stats-grid">
          <div class="stat-box">
            <span class="stat-number">${sub.liczba_produktow}</span>
            <span class="stat-label">Leków w Polsce</span>
          </div>
          <div class="stat-box">
            <span class="stat-number">${sub.liczba_jednoskladnikowych}</span>
            <span class="stat-label">Jednoskładnikowych</span>
          </div>
          <div class="stat-box">
            <span class="stat-number">${sub.liczba_wieloskladnikowych}</span>
            <span class="stat-label">Leków złożonych</span>
          </div>
          <div class="stat-box">
            <span class="stat-number">${conditions.length}</span>
            <span class="stat-label">Wskazań medycznych</span>
          </div>
          <div class="stat-box">
            <span class="stat-number">${interactions.length}</span>
            <span class="stat-label">Wykrytych interakcji</span>
          </div>
        </div>
      </div>
      <div class="header-right">
        <img src="/svg/${iconSvg}" alt="${escapeHtml(primaryAtc)}" width="64" height="64">
      </div>
    </div>

    <!-- Wskazania Medyczne (ICD-11 & ICD-10) -->
    <div class="interactions-wrapper">
      <div class="substance-section-card">
        <div class="section-card-header">
          <div class="summary-left">
            <span class="badge-atc">Zastosowanie Medyczne</span>
            <h2 class="substance-section-title">Wskazania terapeutyczne (ICD-11 & ICD-10)</h2>
          </div>
          <div class="summary-right">
            <span class="interactions-count-badge">${conditions.length} jednostek chorobowych</span>
          </div>
        </div>

        <div class="section-card-body">
          <div class="disclaimer-callout">
            <b>ℹ️ Informacja medyczna:</b> Jednostki chorobowe i kody klasyfikacji medycznych (ICD-11 MMS, Foundation ID, ICD-10) są przypisane na podstawie ontologii <b>Wikidata</b> (Wikiprojekt Lekoznawstwo) oraz oficjalnego słownika <b>WHO / CeZ</b>. Dokładne wskazania dla konkretnego preparatu handlowego znajdują się w jego Charakterystyce Produktu Leczniczego (ChPL).
          </div>

          ${conditions.length > 0 ? `
            <div class="uses-tags-cloud">
              ${conditions.map((c: any) => `
                <div class="use-tag-item">
                  <div class="use-tag-main">
                    <span class="use-tag-icon">🩺</span>
                    <span class="use-tag-name">${escapeHtml(c.name)}</span>
                  </div>
                  <div class="use-tag-codes">
                    ${c.icd11_mms || c.icd11_foundation_id ? `
                      <a href="${escapeHtml(c.icd11_url || ('https://icd.who.int/browse/2026-01/mms/en#' + (c.icd11_foundation_id || '')))}" target="_blank" rel="noopener noreferrer" class="icd-badge icd11-badge" title="ICD-11 (MMS: ${escapeHtml(c.icd11_mms || 'brak')}, Foundation ID: ${escapeHtml(c.icd11_foundation_id || 'brak')})">
                        <span class="icd-type">ICD-11</span>
                        <span class="icd-code">${escapeHtml(c.icd11_mms || c.icd11_foundation_id)}</span>
                        <span class="icd-ext-icon">↗</span>
                      </a>
                    ` : ""}
                    ${c.icd10_codes ? `
                      <span class="icd-badge icd10-badge" title="ICD-10">
                        <span class="icd-type">ICD-10</span>
                        <span class="icd-code">${escapeHtml(c.icd10_codes)}</span>
                      </span>
                    ` : ""}
                    <a href="https://www.wikidata.org/wiki/${escapeHtml(c.wikidata_id)}" target="_blank" rel="noopener noreferrer" class="icd-badge qid-badge" title="Encja Wikidata: ${escapeHtml(c.wikidata_name || c.name)}">
                      <span class="icd-type">WD</span>
                      <span class="icd-code">${escapeHtml(c.wikidata_id)}</span>
                    </a>
                  </div>
                </div>
              `).join("")}
            </div>
          ` : `
            <div class="no-interactions-msg">
              <p>ℹ️ Brak zarejestrowanych wskazań w otwartym grafie wiedzy Wikidata dla tej substancji.</p>
            </div>
          `}
        </div>
      </div>
    </div>

    <!-- Interakcje Farmakologiczne -->
    <div class="interactions-wrapper">
      <div class="substance-section-card">
        <div class="section-card-header">
          <div class="summary-left">
            <span class="badge-experimental">Interakcje Lekowe</span>
            <h2 class="substance-section-title">Interakcje z innymi substancjami czynnymi</h2>
          </div>
          <div class="summary-right">
            <span class="interactions-count-badge">${interactions.length} wykrytych interakcji</span>
          </div>
        </div>

        <div class="section-card-body">
          <div class="disclaimer-callout">
            <b>ℹ️ Ostrzeżenie o interakcjach:</b> Poniższy wykaz interakcji ma charakter poglądowo-badawczy (baza Wikidata). Przed zastosowaniem leku należy zawsze skonsultować się z lekarzem lub farmaceutą i sprawdzić punkt 4.5 oficjalnego ChPL.
          </div>

          ${interactions.length > 0 ? `
            <div class="interactions-grid">
              ${interactions.map((item: any) => {
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
                      <span class="inter-substance-name">
                        <a href="/substancja/${encodeURIComponent(item.substance_name)}" class="inter-substance-link" title="Karta substancji ${escapeHtml(item.substance_name)}">
                          ${escapeHtml(item.substance_name)} ↗
                        </a>
                      </span>
                      ${atcBadges}
                    </div>
                    ${sampleDrugsHtml}
                  </div>
                `;
              }).join("")}
            </div>
          ` : `
            <div class="no-interactions-msg">
              <p>ℹ️ Brak zarejestrowanych interakcji w bazie Wikidata dla tej substancji.</p>
            </div>
          `}
        </div>
      </div>
    </div>

    <!-- Leki w Polsce zawierające tę substancję -->
    <div class="substance-products-section">
      <div class="section-card-header">
        <div class="summary-left">
          <span class="badge-atc">Rejestr Leków RPL</span>
          <h2 class="substance-section-title">Produkty lecznicze w Polsce (${sub.liczba_produktow})</h2>
        </div>
        <div class="summary-right">
          <span class="interactions-count-badge">Widoczne: <b id="visibleProductsCount">${products.length}</b> / ${products.length}</span>
        </div>
      </div>

      ${products.length > 0 ? `
        <!-- Filter Controls & Search -->
        <div class="products-filter-bar">
          <div class="tabs-group">
            <button type="button" id="tab-all" class="tab-btn active" onclick="filterProducts('all')">Wszystkie (${sub.liczba_produktow})</button>
            <button type="button" id="tab-single" class="tab-btn" onclick="filterProducts('single')">Jednoskładnikowe (${sub.liczba_jednoskladnikowych})</button>
            <button type="button" id="tab-combo" class="tab-btn" onclick="filterProducts('combo')">Leki złożone (${sub.liczba_wieloskladnikowych})</button>
          </div>
          <div class="filter-search-box">
            <input type="text" id="productSearchInput" placeholder="🔍 Szukaj leku lub podmiotu..." oninput="filterProducts(document.querySelector('.tab-btn.active').id.replace('tab-', ''))" />
          </div>
        </div>

        <!-- Products Grid / Table -->
        <div class="substance-products-table-wrap">
          <table class="substance-products-table">
            <thead>
              <tr>
                <th>Nazwa handlowa i postać</th>
                <th>Moc / Dawka</th>
                <th>Podmiot odpowiedzialny</th>
                <th>Kategoria</th>
                <th>Skład preparatu</th>
                <th>Kod ATC</th>
              </tr>
            </thead>
            <tbody>
              ${products.map((p: any) => `
                <tr class="product-row-item" data-single="${p.czy_jednoskladnikowy}" data-name="${escapeHtml(p.nazwa_produktu)}" data-holder="${escapeHtml(p.podmiot_odpowiedzialny || '')}">
                  <td>
                    <a href="/lek/${p.id}" class="product-name-link" title="Przejdź do karty leku ${escapeHtml(p.nazwa_produktu)}">
                      <b>${escapeHtml(p.nazwa_produktu)}</b> ↗
                    </a>
                    <div class="product-form-text">${escapeHtml(p.nazwa_postaci_farmaceutycznej || "")}</div>
                  </td>
                  <td><span class="product-moc-pill">${escapeHtml(p.moc || "—")}</span></td>
                  <td class="product-holder-text">${escapeHtml(p.podmiot_odpowiedzialny || "—")}</td>
                  <td>
                    <span class="avail-badge ${p.kategoria_dostepnosci === 'OTC' ? 'avail-otc' : 'avail-rp'}">
                      ${escapeHtml(p.kategoria_dostepnosci || "—")}
                    </span>
                  </td>
                  <td>
                    ${p.czy_jednoskladnikowy
                      ? `<span class="composition-badge comp-single">Jednoskładnikowy</span>`
                      : `<div class="composition-badge comp-combo" title="${escapeHtml(p.wszystkie_substancje || '')}">Lek złożony (${p.liczba_substancji} skł.)</div>`
                    }
                  </td>
                  <td><code>${escapeHtml(p.kod_atc || "—")}</code></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : `
        <div class="no-interactions-msg" style="padding: 1.5rem; text-align: center;">
          <p>ℹ️ <b>Brak zarejestrowanych preparatów handlowych w Rejestrze RPL</b> dla tej substancji.</p>
          <p class="no-inter-sub" style="margin-top: 0.35rem;">Substancja występuje w bazach międzynarodowych (Wikidata) lub jako związek wchodzący w interakcje farmakologiczne.</p>
        </div>
      `}
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
  <title>Nie znaleziono leku — powlekane.pl</title>
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
    <p style="color: var(--text-gray); margin: 1rem 0 2rem;">Podany identyfikator produktu leczniczego nie istnieje w bazie leków.</p>
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
  <title>Błąd serwera — powlekane.pl</title>
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

    // 2. Active Substance Detail Route: /substancja/:name
    if (url.pathname.startsWith("/substancja/")) {
      const rawName = url.pathname.slice(12);
      const substanceName = decodeURIComponent(rawName);
      try {
        const res = await fetch(`${API_BACKEND}/api/substance/${encodeURIComponent(substanceName)}`);
        if (!res.ok) {
          return new Response(`<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="icon" type="image/svg+xml" href="/svg/chpl.svg" />
  <title>Nie znaleziono substancji — powlekane.pl</title>
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
    <h1>Substancja nie została odnaleziona</h1>
    <p style="color: var(--text-gray); margin: 1rem 0 2rem;">Substancja czynna <b>"${escapeHtml(substanceName)}"</b> nie występuje w polskim rejestrze leków RPL.</p>
    <p><a href="/" onclick="handleBack(event)" class="nav-search-btn" style="display: inline-flex;">← Wróć do wyszukiwarki</a></p>
  </div>
</body>
</html>`, {
            status: 404,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          });
        }
        const subData = await res.json();
        const pageHtml = renderSubstancePage(subData);
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
  <title>Błąd serwera — powlekane.pl</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  ${renderGlobalHeader({ isHome: false })}
  <div class="single-medicine-main" style="text-align: center; padding: 2.5rem 1rem;">
    <h1>Wystąpił błąd serwera</h1>
    <p style="color: #dc2626; margin: 1rem 0 2rem;">${escapeHtml(err.message)}</p>
    <p><a href="/" class="nav-search-btn" style="display: inline-flex;">← Wróć do wyszukiwarki</a></p>
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

console.log(`🌐 powlekane.pl Web App running on http://0.0.0.0:${server.port} (Local: http://localhost:${server.port})`);
console.log(`🔗 Connected to API Backend: ${API_BACKEND}`);
console.log(`⚡ Markdown engine: Bun ${Bun.version} native Rust parser`);
