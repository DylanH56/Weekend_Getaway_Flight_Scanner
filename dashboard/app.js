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

// Raw Ryanair fare (flight_price_eur). The bus surcharge is now
// informational only -- shown as a separate small line on each card
// but not added to the headline number. Falls back to
// effective_price_eur for compatibility with pre-refactor deals.json.
function dealPrice(d) {
  const p = d.flight_price_eur != null ? d.flight_price_eur : d.effective_price_eur;
  return p == null ? Infinity : p;
}

function sortDeals(deals, mode) {
  const copy = deals.slice();
  if (mode === "price") {
    copy.sort((a, b) => {
      const diff = dealPrice(a) - dealPrice(b);
      return diff !== 0 ? diff : a.outbound_departure.localeCompare(b.outbound_departure);
    });
  } else if (mode === "date") {
    copy.sort((a, b) => {
      const d = a.outbound_departure.localeCompare(b.outbound_departure);
      if (d !== 0) return d;
      if (a.origin !== b.origin) return a.origin === "SNN" ? -1 : 1;
      return (a.destination_city || "").localeCompare(b.destination_city || "");
    });
  } else if (mode === "country") {
    copy.sort((a, b) => {
      const c = (a.destination_country || "").localeCompare(b.destination_country || "");
      return c !== 0 ? c : dealPrice(a) - dealPrice(b);
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
  if (deal.is_lowest_ever) li.classList.add("lowest-ever");
  li.dataset.idx = idx;

  const flightPrice = deal.flight_price_eur != null
    ? deal.flight_price_eur
    : deal.effective_price_eur;
  const hasPrice = flightPrice != null;
  const priceDisplay = hasPrice
    ? `&euro;${flightPrice.toFixed(0)}`
    : `<span class="price-check">check &rarr;</span>`;
  const bus = deal.bus_surcharge_eur || 0;
  // Bus surcharge is now a separate small note, not rolled into the
  // headline price. SNN is still "direct from Shannon" (zero bus).
  const priceNote = hasPrice
    ? (deal.origin === "SNN"
        ? "direct from Shannon"
        : `+&euro;${bus.toFixed(0)} Limerick bus (not incl.)`)
    : (deal.origin === "SNN"
        ? "live price via link"
        : `+&euro;${bus.toFixed(0)} Limerick bus (not incl.)`);

  // Price history annotations from history.py: is_lowest_ever,
  // price_delta_eur, last_seen_eur. Show a small trend chip when
  // any of them is meaningful.
  let trendHtml = "";
  if (deal.is_lowest_ever && deal.lowest_ever_at) {
    trendHtml = `<span class="trend lowest-ever">&#128293; lowest ever</span>`;
  } else if (typeof deal.price_delta_eur === "number" && deal.price_delta_eur !== 0) {
    const delta = deal.price_delta_eur;
    if (delta < 0) {
      trendHtml = `<span class="trend down">&darr; &euro;${Math.abs(delta).toFixed(0)}</span>`;
    } else {
      trendHtml = `<span class="trend up">&uarr; &euro;${delta.toFixed(0)}</span>`;
    }
  }

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
        ${trendHtml}
      </div>
    </div>
    <div class="meta-row">
      <span class="badge ${deal.origin.toLowerCase()}">${ORIGIN_LABEL[deal.origin]}</span>
      ${deal.carrier_code ? `<span class="badge carrier carrier-${deal.carrier_code.toLowerCase()}">${deal.carrier_code}</span>` : ""}
      ${deal.weekend_window_label ? `<span class="badge window">${deal.weekend_window_label}</span>` : ""}
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

  const markerLayer = L.layerGroup().addTo(map);

  // Track which destination the user has clicked on the map. When set,
  // the sidebar is filtered to show only deals for that IATA, across
  // all weekends and both origins. Clicking the "clear" button or the
  // same marker again resets it.
  let selectedDestination = null;

  // Weekend-window filter state. Default: the classic Fri->Sun is
  // enabled and all others are off (matches the pre-multi-window
  // behaviour). User toggles chips to enable longer windows.
  const enabledWindows = new Set(["fri_sun"]);

  function currentFilters() {
    const sliderEl = $("price-max");
    const maxPrice = sliderEl ? parseInt(sliderEl.value, 10) : 150;
    return {
      showSNN: $("filter-snn").checked,
      showDUB: $("filter-dub").checked,
      maxPrice: isFinite(maxPrice) ? maxPrice : 150,
      sortMode: $("sort").value,
      windows: enabledWindows,
    };
  }

  // Apply filters common to both the map and the sidebar.
  function applyFilters(deals, f) {
    return deals.filter((d) => {
      if (d.origin === "SNN" && !f.showSNN) return false;
      if (d.origin === "DUB" && !f.showDUB) return false;
      if (dealPrice(d) > f.maxPrice) return false;
      // Weekend window: default to fri_sun for deals that predate the
      // multi-window field (back-compat with old deals.json).
      const win = d.weekend_window || "fri_sun";
      if (f.windows.size > 0 && !f.windows.has(win)) return false;
      return true;
    });
  }

  // Render the chip row from payload.weekend_windows. Each chip shows
  // the window label + a live count of deals that match (respecting
  // the other filters, but ignoring the chip itself so toggling one on
  // always shows its true capacity).
  function renderWindowChips() {
    const container = $("window-chips");
    if (!container) return;
    const windows = payload.weekend_windows || [{ id: "fri_sun", label: "Fri \u2192 Sun" }];
    container.innerHTML = "";
    windows.forEach((w) => {
      // Count deals that would match if this window were the only
      // window filter active (other filters still apply).
      const f = currentFilters();
      const otherFilters = { ...f, windows: new Set([w.id]) };
      const count = applyFilters(payload.deals, otherFilters).length;

      const chip = document.createElement("div");
      chip.className = "window-chip" + (enabledWindows.has(w.id) ? " active" : "");
      chip.innerHTML = `${w.label} <span class="count">${count}</span>`;
      chip.addEventListener("click", () => {
        if (enabledWindows.has(w.id)) {
          // Don't let the user disable the last remaining window --
          // that'd leave the dashboard empty with no obvious cause.
          if (enabledWindows.size === 1) return;
          enabledWindows.delete(w.id);
        } else {
          enabledWindows.add(w.id);
        }
        render();
      });
      container.appendChild(chip);
    });
  }

  // Group deals by destination IATA and pick the cheapest per destination.
  // Used to render one price-badge marker per city on the map instead of
  // one per weekend. Click a marker and the sidebar filters to the full
  // list of weekends for that destination.
  function groupCheapestByDestination(deals) {
    const byIata = new Map();
    for (const d of deals) {
      const iata = d.destination_iata;
      if (!iata) continue;
      const existing = byIata.get(iata);
      if (!existing || dealPrice(d) < dealPrice(existing)) {
        byIata.set(iata, d);
      }
    }
    return Array.from(byIata.values());
  }

  function updateMeta(visibleCount, totalCount) {
    if (payload.mode === "prospects") {
      metaEl.innerHTML =
        `<b>Prospects mode</b> &middot; ` +
        `<b>${totalCount}</b> route/weekend combos over ${payload.weekends_scanned} weekends &middot; ` +
        `no live prices or time filter &mdash; click through to check`;
    } else {
      metaEl.innerHTML =
        `Last scanned <b>${generated}</b> &middot; ` +
        `<b>${visibleCount}</b> / ${totalCount} deals shown &middot; ` +
        `Dublin bus fare &euro;${payload.bus_return_cost_eur} <span class="muted">(not in price)</span>`;
    }
  }

  function renderMapMarkers(filteredDeals) {
    markerLayer.clearLayers();
    const bounds = [];
    const cheapestPerDest = groupCheapestByDestination(filteredDeals);

    cheapestPerDest.forEach((deal) => {
      const hasCoords =
        typeof deal.destination_lat === "number" &&
        typeof deal.destination_lon === "number" &&
        isFinite(deal.destination_lat) &&
        isFinite(deal.destination_lon);
      if (!hasCoords) return;

      const price = dealPrice(deal);
      const selected = selectedDestination === deal.destination_iata;
      const originClass = deal.origin.toLowerCase();
      const html = `<div class="price-badge ${originClass}${selected ? " selected" : ""}" data-iata="${deal.destination_iata}">&euro;${price.toFixed(0)}</div>`;
      const icon = L.divIcon({
        className: "",  // we style the inner div directly
        html,
        iconSize: null,
        iconAnchor: [20, 12],
      });

      const marker = L.marker(
        [deal.destination_lat, deal.destination_lon],
        { icon, riseOnHover: true, title: deal.destination_city || deal.destination_iata }
      );

      marker.on("click", () => {
        if (selectedDestination === deal.destination_iata) {
          selectedDestination = null;
        } else {
          selectedDestination = deal.destination_iata;
          map.panTo([deal.destination_lat, deal.destination_lon], { animate: true });
        }
        render();
      });

      markerLayer.addLayer(marker);
      bounds.push([deal.destination_lat, deal.destination_lon]);
    });

    bounds.push([52.7, -8.9]);  // Ireland anchor
    if (bounds.length > 1 && selectedDestination === null) {
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 6 });
    }
  }

  function renderSidebar(filteredDeals, sortMode) {
    listEl.innerHTML = "";

    let list = filteredDeals;
    if (selectedDestination) {
      list = list.filter((d) => d.destination_iata === selectedDestination);
    }
    const sorted = sortDeals(list, sortMode);

    const titleEl = $("deals-title");
    if (selectedDestination) {
      const sample = sorted[0];
      const city = sample ? (sample.destination_city || selectedDestination) : selectedDestination;
      titleEl.textContent = `${city} (${selectedDestination})`;
      $("clear-destination").style.display = "block";
    } else {
      titleEl.textContent = `Deals`;
      $("clear-destination").style.display = "none";
    }

    if (sorted.length === 0) {
      listEl.innerHTML = '<p class="empty">No deals match the current filters.</p>';
      return;
    }

    sorted.forEach((deal, idx) => {
      const li = renderDealCard(deal, idx);
      listEl.appendChild(li);
      li.addEventListener("click", (e) => {
        if (e.target.tagName === "A") return;
        // Pan the map to this card's destination if we have coords.
        const hasCoords =
          typeof deal.destination_lat === "number" &&
          typeof deal.destination_lon === "number" &&
          isFinite(deal.destination_lat) &&
          isFinite(deal.destination_lon);
        if (hasCoords) {
          map.panTo([deal.destination_lat, deal.destination_lon], { animate: true });
        }
        li.classList.add("highlighted");
        setTimeout(() => li.classList.remove("highlighted"), 1500);
      });
    });
  }

  function render() {
    const f = currentFilters();
    const filtered = applyFilters(payload.deals, f);
    updateMeta(filtered.length, payload.deals.length);
    renderWindowChips();
    renderMapMarkers(filtered);
    renderSidebar(filtered, f.sortMode);
  }

  // Slider label live-updates as you drag; map+sidebar re-render on every change.
  const priceSlider = $("price-max");
  const priceLabel = $("price-max-label");
  if (priceSlider && priceLabel) {
    priceSlider.addEventListener("input", () => {
      priceLabel.innerHTML = `&euro;${priceSlider.value}`;
      render();
    });
  }

  // In prospects mode, default to sorting by date since prices are unknown.
  if (payload.mode === "prospects") {
    const sel = $("sort");
    if (sel && sel.value === "price") sel.value = "date";
    // Max slider to 150 isn't meaningful with null prices; open it fully.
    if (priceSlider) {
      priceSlider.value = "150";
      priceLabel.innerHTML = "&euro;150";
    }
  }

  $("filter-snn").addEventListener("change", render);
  $("filter-dub").addEventListener("change", render);
  $("sort").addEventListener("change", render);
  $("clear-destination").addEventListener("click", () => {
    selectedDestination = null;
    render();
  });

  render();
}

main();
