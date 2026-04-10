/* Weekend Getaway Flight Scanner - dashboard front-end */

const ORIGIN_LABEL = { SNN: "Shannon", DUB: "Dublin" };
const ORIGIN_COLOR = {
  SNN: { stroke: "#4ade80", fill: "#065f46" },
  DUB: { stroke: "#60a5fa", fill: "#1e3a8a" },
};

const $ = (id) => document.getElementById(id);

function fmtDateTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-IE", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IE", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  });
}

function sortDeals(deals, mode) {
  const copy = deals.slice();
  const priceOr = (d) => (d.effective_price_eur == null ? Infinity : d.effective_price_eur);
  if (mode === "price") {
    copy.sort((a, b) => {
      const diff = priceOr(a) - priceOr(b);
      return diff !== 0 ? diff : a.outbound_departure.localeCompare(b.outbound_departure);
    });
  } else if (mode === "date") {
    copy.sort((a, b) => {
      const d = a.outbound_departure.localeCompare(b.outbound_departure);
      if (d !== 0) return d;
      // within a weekend, SNN before DUB, then cheapest or alphabetical
      if (a.origin !== b.origin) return a.origin === "SNN" ? -1 : 1;
      return (a.destination_city || "").localeCompare(b.destination_city || "");
    });
  } else if (mode === "country") {
    copy.sort((a, b) => {
      const c = (a.destination_country || "").localeCompare(b.destination_country || "");
      return c !== 0 ? c : priceOr(a) - priceOr(b);
    });
  }
  return copy;
}

async function loadDeals() {
  const res = await fetch("deals.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`deals.json HTTP ${res.status}`);
  return res.json();
}

function initMap() {
  const map = L.map("map", {
    zoomControl: true,
    worldCopyJump: true,
  }).setView([50, 10], 4);

  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 19,
    }
  ).addTo(map);

  // Mark the Irish origins for context.
  L.circleMarker([52.702, -8.925], {
    radius: 6,
    color: "#facc15",
    fillColor: "#854d0e",
    fillOpacity: 1,
    weight: 2,
  }).addTo(map).bindTooltip("Shannon (SNN)");

  L.circleMarker([53.421, -6.27], {
    radius: 6,
    color: "#facc15",
    fillColor: "#854d0e",
    fillOpacity: 1,
    weight: 2,
  }).addTo(map).bindTooltip("Dublin (DUB)");

  return map;
}

function renderDealCard(deal, idx) {
  const li = document.createElement("li");
  li.className = "deal";
  li.dataset.idx = idx;

  const hasPrice = deal.effective_price_eur != null;
  const priceDisplay = hasPrice
    ? `&euro;${deal.effective_price_eur.toFixed(0)}`
    : `<span class="price-check">check &rarr;</span>`;
  const priceNote = hasPrice
    ? (deal.origin === "SNN"
        ? "direct from Shannon"
        : `incl. &euro;${deal.bus_surcharge_eur.toFixed(0)} Limerick bus`)
    : (deal.origin === "SNN"
        ? "live price via link"
        : `+&euro;${deal.bus_surcharge_eur.toFixed(0)} Limerick bus`);

  const hasTimes = !!deal.outbound_arrival;
  const timesHtml = hasTimes
    ? `
      <div><span class="label">OUT</span> ${fmtDateTime(deal.outbound_departure)} &rarr; ${fmtDateTime(deal.outbound_arrival)} &middot; ${deal.outbound_flight_number || ""}</div>
      <div><span class="label">RET</span> ${fmtDateTime(deal.inbound_departure)} &rarr; ${fmtDateTime(deal.inbound_arrival)} &middot; ${deal.inbound_flight_number || ""}</div>
    `
    : `
      <div><span class="label">OUT</span> ${fmtDate(deal.outbound_departure)} evening <span class="muted">(target 16:00+)</span></div>
      <div><span class="label">RET</span> ${fmtDate(deal.inbound_departure)} evening <span class="muted">(target 15:00+)</span></div>
    `;

  const warnHtml = deal.time_window_note
    ? `<div class="warn">&#9888; Filter for evening departures on the booking site &mdash; the link can't do it for you.</div>`
    : "";

  li.innerHTML = `
    <div class="top">
      <div>
        <div class="city">${deal.destination_city || deal.destination_iata}</div>
        <div class="country">${deal.destination_country || ""} &middot; ${deal.destination_iata}</div>
      </div>
      <div class="price-block">
        <div class="price">${priceDisplay}</div>
        <div class="price-note">${priceNote}</div>
      </div>
    </div>
    <div class="meta-row">
      <span class="badge ${deal.origin.toLowerCase()}">${ORIGIN_LABEL[deal.origin]}</span>
      <span class="country">${fmtDate(deal.outbound_departure)} &ndash; ${fmtDate(deal.inbound_departure)}</span>
    </div>
    <div class="times">${timesHtml}</div>
    ${warnHtml}
    <div class="book-row">
      <a class="book book-google" href="${deal.google_flights_url}" target="_blank" rel="noopener">Google Flights &rarr;</a>
      <a class="book book-sky" href="${deal.skyscanner_url}" target="_blank" rel="noopener">Skyscanner &rarr;</a>
    </div>
  `;
  return li;
}

async function main() {
  const map = initMap();
  const listEl = $("deal-list");
  const metaEl = $("meta");

  let payload;
  try {
    payload = await loadDeals();
  } catch (e) {
    console.error(e);
    metaEl.textContent = "Unable to load deals.json";
    listEl.innerHTML =
      '<p class="empty">No deals data found yet.<br>Run <code>python scanner.py</code> to generate <code>deals.json</code>, then refresh.</p>';
    return;
  }

  const generated = payload.generated_at
    ? new Date(payload.generated_at).toLocaleString("en-IE")
    : "unknown";
  if (payload.mode === "prospects") {
    metaEl.innerHTML =
      `<b>Prospects mode</b> &middot; ` +
      `<b>${payload.deals.length}</b> route/weekend combos over ${payload.weekends_scanned} weekends &middot; ` +
      `no live prices or time filter &mdash; click through to check &middot; ` +
      `run <code>python scanner.py</code> for real Ryanair fares (no API key needed)`;
  } else {
    metaEl.innerHTML =
      `Last scanned <b>${generated}</b> &middot; ` +
      `<b>${payload.deals.length}</b> deals &le; &euro;${payload.price_cap_eur} &middot; ` +
      `Dublin bus fare &euro;${payload.bus_return_cost_eur}`;
  }

  const markers = new Map(); // idx -> Leaflet marker
  const cards = new Map(); // idx -> <li>
  const markerLayer = L.layerGroup().addTo(map);

  function render() {
    const showSNN = $("filter-snn").checked;
    const showDUB = $("filter-dub").checked;
    const sortMode = $("sort").value;

    listEl.innerHTML = "";
    markerLayer.clearLayers();
    markers.clear();
    cards.clear();

    const filtered = payload.deals.filter(
      (d) => (d.origin === "SNN" && showSNN) || (d.origin === "DUB" && showDUB)
    );
    const sorted = sortDeals(filtered, sortMode);

    if (sorted.length === 0) {
      listEl.innerHTML = '<p class="empty">No deals match the current filters.</p>';
      return;
    }

    const bounds = [];

    sorted.forEach((deal, idx) => {
      const li = renderDealCard(deal, idx);
      listEl.appendChild(li);
      cards.set(idx, li);

      // Deals with unknown coordinates still render in the sidebar
      // but don't get a map marker. Happens when Ryanair returns a
      // destination we don't have in the IATA lookup table.
      const hasCoords =
        typeof deal.destination_lat === "number" &&
        typeof deal.destination_lon === "number" &&
        isFinite(deal.destination_lat) &&
        isFinite(deal.destination_lon);

      if (!hasCoords) {
        // Clicking the card with no marker just highlights the card.
        li.addEventListener("click", (e) => {
          if (e.target.tagName === "A") return;
          li.classList.add("highlighted");
          setTimeout(() => li.classList.remove("highlighted"), 1500);
        });
        return;
      }

      const colors = ORIGIN_COLOR[deal.origin];
      const marker = L.circleMarker(
        [deal.destination_lat, deal.destination_lon],
        {
          radius: 8,
          color: colors.stroke,
          fillColor: colors.fill,
          fillOpacity: 0.85,
          weight: 2,
        }
      );

      const popupHtml = `
        <b>${deal.destination_city || deal.destination_iata}</b>
        <span style="color:#94a3b8"> (${deal.destination_iata})</span><br>
        <span style="color:#4ade80;font-size:15px;font-weight:700">&euro;${deal.effective_price_eur.toFixed(0)}</span>
        from ${ORIGIN_LABEL[deal.origin]}<br>
        <span style="color:#cbd5e1">${fmtDate(deal.outbound_departure)} &ndash; ${fmtDate(deal.inbound_departure)}</span><br>
        <a href="${deal.google_flights_url}" target="_blank" rel="noopener">Google Flights</a>
        &nbsp;&middot;&nbsp;
        <a href="${deal.skyscanner_url}" target="_blank" rel="noopener">Skyscanner</a>
      `;
      marker.bindPopup(popupHtml);

      marker.on("click", () => {
        const el = cards.get(idx);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("highlighted");
          setTimeout(() => el.classList.remove("highlighted"), 1500);
        }
      });

      li.addEventListener("click", (e) => {
        if (e.target.tagName === "A") return;
        marker.openPopup();
        map.panTo([deal.destination_lat, deal.destination_lon]);
      });

      markerLayer.addLayer(marker);
      markers.set(idx, marker);
      bounds.push([deal.destination_lat, deal.destination_lon]);
    });

    // Always include Ireland in the bounds so context stays clear.
    bounds.push([52.7, -8.9]);
    if (bounds.length > 1) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 6 });
    }
  }

  // In prospects mode, default to sorting by date since prices are unknown.
  if (payload.mode === "prospects") {
    const sel = $("sort");
    if (sel && sel.value === "price") sel.value = "date";
  }

  $("filter-snn").addEventListener("change", render);
  $("filter-dub").addEventListener("change", render);
  $("sort").addEventListener("change", render);

  render();
}

main();
