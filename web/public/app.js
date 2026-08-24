/**
 * Minimal JavaScript Frontend matching reference wyszukiwarka_chpl design.
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

async function performSearch(query) {
  const resultsContainer = document.getElementById("results");
  resultsContainer.innerHTML = '<div class="container"><div class="loader"></div></div>';

  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&top_k=15`);
    const data = await response.json();

    resultsContainer.innerHTML = "";
    if (data && data.results && data.results.length > 0) {
      data.results.forEach((item) => {
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
          ? '<span style="background:#dcfce7; color:#15803d; font-weight:700; font-size:0.68rem; padding:0.15rem 0.4rem; border-radius:4px; margin-left:0.4rem; border:1px solid #bbf7d0;">Refundowany (NFZ)</span>'
          : '';

        card.innerHTML = `
          <div class="ind-med">
            <div class="ind-med-left">
              <a href="/lek/${item.id}">
                <h2>${escapeHtml(item.nazwa_produktu)}${refundBadge}</h2>
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
  } catch (error) {
    resultsContainer.innerHTML = '<p class="empty-msg">Wystąpił błąd podczas pobierania danych.</p>';
    console.error(error);
  }
}

function updateURLAndSearch() {
  const searchBox = document.getElementById("searchBox");
  const query = searchBox.value.trim();
  if (!query) return;

  const newURL = `${window.location.pathname}?q=${encodeURIComponent(query)}`;
  window.history.pushState({ path: newURL }, "", newURL);
  document.title = `Wyniki dla: "${query}"`;
  performSearch(query);
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

window.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(window.location.search);
  const query = params.get("q");
  if (query) {
    document.getElementById("searchBox").value = query;
    performSearch(query);
  }
});
