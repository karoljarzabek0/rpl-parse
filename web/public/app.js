/**
 * Minimal JavaScript Frontend matching reference wyszukiwarka_chpl design.
 * Preserves search state, results, and query history across navigation.
 */

function slugify(productName) {
  return (productName || "")
    .toLowerCase()
    .replace(/[-,\\%+ ]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function getAtcIcon(kod_atc) {
  const icons = {
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

  if (!kod_atc || typeof kod_atc !== "string") {
    return "other.svg";
  }

  const atcLetter = kod_atc.charAt(0).toUpperCase();
  return icons[atcLetter] || "other.svg";
}

function renderResultsList(results) {
  const resultsContainer = document.getElementById("results");
  resultsContainer.innerHTML = "";

  if (results && results.length > 0) {
    results.forEach((item) => {
      const card = document.createElement("div");
      card.className = "med-card";

      const firstAtc = item.atc && item.atc[0] ? item.atc[0] : { code: "", display: "" };
      const iconFile = getAtcIcon(firstAtc.code);
      const atcGroupText = firstAtc.display || "Brak klasyfikacji ATC";

      const commonName = item.nazwa_powszechnie_stosowana || (item.substancje && item.substancje[0] ? item.substancje[0].nazwa : "");
      const mocText = item.moc ? ` (${item.moc})` : "";

      let headlineHtml = "";
      if (item.snippet && item.snippet.trim()) {
        headlineHtml = `<p class="headline">"${item.snippet}"</p>`;
      }

      const refundBadge = item.is_refundowany
        ? '<span class="rf-badge-nfz">Refundowany (NFZ)</span>'
        : "";

      let gifBadge = "";
      if (item.has_gif_warning && item.gif_status) {
        gifBadge = `<span class="badge-gif-tag">Decyzja GIF: ${escapeHtml(item.gif_status)}</span>`;
      }

      card.innerHTML = `
        <div class="ind-med">
          <div class="ind-med-left">
            <a href="/lek/${item.id}">
              <h2>${escapeHtml(item.nazwa_produktu)}${refundBadge}${gifBadge}</h2>
              <p>${escapeHtml(commonName)}${escapeHtml(mocText)} &bull; ${escapeHtml(item.nazwa_postaci_farmaceutycznej || "")}</p>
            </a>
          </div>
          <div class="ind-med-right">
            <a href="/lek/${item.id}">
              <img src="/svg/${iconFile}" alt="${escapeHtml(firstAtc.code)}" title="${escapeHtml(firstAtc.display)}" width="44" height="44">
            </a>
          </div>
        </div>
        <p class="atc-group">${escapeHtml(atcGroupText)}</p>
        ${headlineHtml}
      `;
      resultsContainer.appendChild(card);
    });
  } else {
    resultsContainer.innerHTML = '<p class="empty-msg">Nie znaleziono pasujących leków.</p>';
  }
}

async function performSearch(query, onlyRefunded = false, useCacheOnly = false) {
  const resultsContainer = document.getElementById("results");

  // Save current query and filter state to session
  sessionStorage.setItem("last_search_query", query);
  sessionStorage.setItem("last_search_refunded", onlyRefunded ? "1" : "0");

  const cacheKey = `cached_results_${query}_${onlyRefunded ? "ref" : "all"}`;

  // Check cache first for instant restoration
  const cachedJson = sessionStorage.getItem(cacheKey);
  if (cachedJson) {
    try {
      const cachedResults = JSON.parse(cachedJson);
      renderResultsList(cachedResults);
      if (useCacheOnly) return;
    } catch (e) {}
  } else {
    resultsContainer.innerHTML = '<div class="container"><div class="loader"></div></div>';
  }

  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&only_refunded=${onlyRefunded}&top_k=15`);
    const data = await response.json();

    if (data && data.results) {
      sessionStorage.setItem(cacheKey, JSON.stringify(data.results));
      renderResultsList(data.results);
    } else {
      resultsContainer.innerHTML = '<p class="empty-msg">Nie znaleziono pasujących leków.</p>';
    }
  } catch (error) {
    if (!cachedJson) {
      resultsContainer.innerHTML = '<p class="empty-msg">Wystąpił błąd podczas pobierania danych.</p>';
    }
    console.error(error);
  }
}

function updateURLAndSearch() {
  const searchBox = document.getElementById("searchBox");
  const query = searchBox.value.trim();
  if (!query) return;

  const refundCheck = document.getElementById("refundOnlyCheck");
  const onlyRefunded = refundCheck ? refundCheck.checked : false;

  const newURL = `/?q=${encodeURIComponent(query)}${onlyRefunded ? "&refunded=1" : ""}`;
  window.history.pushState({ query: query, refunded: onlyRefunded }, "", newURL);
  document.title = `Wyniki dla: "${query}"`;
  performSearch(query, onlyRefunded);
}

function restoreState() {
  const params = new URLSearchParams(window.location.search);
  let query = params.get("q");
  let refunded = params.get("refunded") === "1";

  // Fallback to session query if on root /
  if (!query && window.location.pathname === "/") {
    query = sessionStorage.getItem("last_search_query");
    refunded = sessionStorage.getItem("last_search_refunded") === "1";
    if (query) {
      const newURL = `/?q=${encodeURIComponent(query)}${refunded ? "&refunded=1" : ""}`;
      window.history.replaceState({ query: query, refunded: refunded }, "", newURL);
    }
  }

  const refundCheck = document.getElementById("refundOnlyCheck");
  if (refundCheck) {
    refundCheck.checked = refunded;
  }

  if (query) {
    const searchBox = document.getElementById("searchBox");
    if (searchBox) searchBox.value = query;
    document.title = `Wyniki dla: "${query}"`;
    performSearch(query, refunded);
  }
}

function escapeHtml(text) {
  if (!text) return "";
  return text
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function fetchDbStats() {
  const footerEl = document.getElementById("dbStatsText");
  if (!footerEl) return;
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) throw new Error("Stats unavailable");
    const data = await res.json();
    footerEl.innerHTML = `Baza RPL: <b>${data.total_products_xml.toLocaleString()}</b> leków &bull; Teksty ChPL: <b>${data.indexed_fts_morfeusz.toLocaleString()}</b> &bull; Wektory: <b>${data.indexed_vectors_vec0.toLocaleString()}</b>`;
  } catch (e) {
    footerEl.textContent = "Baza danych aktywna";
  }
}

window.addEventListener("DOMContentLoaded", () => {
  restoreState();
  fetchDbStats();
});
window.addEventListener("popstate", restoreState);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    restoreState();
    fetchDbStats();
  }
});
