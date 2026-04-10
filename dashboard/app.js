/* Weekend Getaway Flight Scanner - dashboard front-end */

const ORIGIN_LABEL = { SNN: "Shannon", DUB: "Dublin", BHX: "Birmingham" };
const ORIGIN_COLOR = {
  SNN: { stroke: "#4ade80", fill: "#065f46" },  // green
  DUB: { stroke: "#60a5fa", fill: "#1e3a8a" },  // blue
  BHX: { stroke: "#fb923c", fill: "#7c2d12" },  // amber/orange
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

// ---------- Currency conversion ----------
// Rates fetched once a day from Frankfurter (ECB, no key) and
// cached in localStorage. EUR is the base; everything else is a
// multiplier applied at render time.
const CURRENCY_STORAGE_KEY = "wgfs_fx_rates_v1";
const CURRENCY_SYMBOLS = { EUR: "\u20ac", GBP: "\u00a3", USD: "$" };
let fxRates = { EUR: 1.0, GBP: 0.85, USD: 1.08 };  // sane fallback
let activeCurrency = "EUR";

async function loadExchangeRates() {
  const cached = localStorage.getItem(CURRENCY_STORAGE_KEY);
  const today = new Date().toISOString().slice(0, 10);
  if (cached) {
    try {
      const parsed = JSON.parse(cached);
      if (parsed && parsed.date === today && parsed.rates) {
        fxRates = { EUR: 1.0, ...parsed.rates };
        return;
      }
    } catch {}
  }
  try {
    const res = await fetch("https://api.frankfurter.app/latest?from=EUR&to=GBP,USD");
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.rates) {
      fxRates = { EUR: 1.0, ...data.rates };
      localStorage.setItem(
        CURRENCY_STORAGE_KEY,
        JSON.stringify({ date: today, rates: data.rates })
      );
    }
  } catch {
    /* stay with the hardcoded fallback */
  }
}

function formatPrice(eur) {
  if (eur == null || !isFinite(eur)) return "";
  const rate = fxRates[activeCurrency] || 1;
  const sym = CURRENCY_SYMBOLS[activeCurrency] || "\u20ac";
  return `${sym}${(eur * rate).toFixed(0)}`;
}

// ---------- Carbon footprint ----------
// Great-circle distance (haversine) in km, then multiply by a
// standard short-haul economy-class factor to get an approximate
// per-passenger CO2 figure. Based on UK DEFRA 2023 factors: short
// haul ~0.156 kg/km, of which ~0.133 is the flight itself. This is
// a rough-cut estimate, not a certified measurement -- good enough
// to put a ballpark number on each card.
const KG_CO2_PER_KM = 0.156;

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const R = 6371;  // km
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

const ORIGIN_COORDS = {
  SNN: [52.702, -8.925],
  DUB: [53.421, -6.27],
  BHX: [52.4539, -1.748],
};

function estimateCO2Kg(deal) {
  const from = ORIGIN_COORDS[deal.origin];
  const to =
    typeof deal.destination_lat === "number" &&
    typeof deal.destination_lon === "number"
      ? [deal.destination_lat, deal.destination_lon]
      : null;
  if (!from || !to) return null;
  const oneWay = haversineKm(from[0], from[1], to[0], to[1]);
  // Round trip, *2 for return journey. CO2 per passenger on a
  // full economy short-haul seat.
  return Math.round(oneWay * 2 * KG_CO2_PER_KM);
}

// ---------- iCal export ----------
function fmtIcalDate(iso) {
  // "2026-05-01T19:25:00" -> "20260501T192500"
  if (!iso) return "";
  return iso.replace(/[-:]/g, "").slice(0, 15);
}

function icsFor(deal) {
  const uid = `${deal.carrier_code || "??"}-${deal.origin}-${deal.destination_iata}-${(deal.outbound_departure || "").slice(0, 10)}@weekend-getaway-scanner`;
  const outDep = fmtIcalDate(deal.outbound_departure);
  const outArr = fmtIcalDate(deal.outbound_arrival) || outDep;
  const inDep = fmtIcalDate(deal.inbound_departure);
  const inArr = fmtIcalDate(deal.inbound_arrival) || inDep;
  const city = deal.destination_city || deal.destination_iata;
  const now = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15) + "Z";
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Weekend Getaway Flight Scanner//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${uid}-out`,
    `DTSTAMP:${now}`,
    `DTSTART:${outDep}`,
    `DTEND:${outArr}`,
    `SUMMARY:Flight ${deal.origin}\u2192${deal.destination_iata} \u2014 ${city}`,
    `DESCRIPTION:${deal.carrier_code || ""} ${deal.outbound_flight_number || ""} \u20ac${(deal.flight_price_eur || 0).toFixed(0)} round trip`,
    "STATUS:TENTATIVE",
    "END:VEVENT",
    "BEGIN:VEVENT",
    `UID:${uid}-ret`,
    `DTSTAMP:${now}`,
    `DTSTART:${inDep}`,
    `DTEND:${inArr}`,
    `SUMMARY:Flight ${deal.destination_iata}\u2192${deal.origin} (return)`,
    `DESCRIPTION:${deal.carrier_code || ""} ${deal.inbound_flight_number || ""}`,
    "STATUS:TENTATIVE",
    "END:VEVENT",
    "END:VCALENDAR",
  ];
  return lines.join("\r\n");
}

function downloadIcs(deal) {
  const content = icsFor(deal);
  const blob = new Blob([content], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const fileName = `${deal.origin}-${deal.destination_iata}-${(deal.outbound_departure || "").slice(0, 10)}.ics`;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sortDeals(deals, mode) {
  const copy = deals.slice();
  if (mode === "price") {
    copy.sort((a, b) => {
      const diff = dealPrice(a) - dealPrice(b);
      return diff !== 0 ? diff : a.outbound_departure.localeCompare(b.outbound_departure);
    });
  } else if (mode === "date") {
    // Secondary sort: SNN -> DUB -> BHX -> anything else.
    const ORIGIN_RANK = { SNN: 0, DUB: 1, BHX: 2 };
    copy.sort((a, b) => {
      const d = a.outbound_departure.localeCompare(b.outbound_departure);
      if (d !== 0) return d;
      const ra = ORIGIN_RANK[a.origin] ?? 99;
      const rb = ORIGIN_RANK[b.origin] ?? 99;
      if (ra !== rb) return ra - rb;
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

  // Mark the origin airports for context. Yellow rings so they're
  // distinct from the destination price badges.
  const ORIGIN_PINS = [
    { coords: [52.702, -8.925], label: "Shannon (SNN)" },
    { coords: [53.421, -6.27],  label: "Dublin (DUB)" },
    { coords: [52.4539, -1.748], label: "Birmingham (BHX)" },
  ];
  ORIGIN_PINS.forEach((pin) => {
    L.circleMarker(pin.coords, {
      radius: 6,
      color: "#facc15",
      fillColor: "#854d0e",
      fillOpacity: 1,
      weight: 2,
    }).addTo(map).bindTooltip(pin.label);
  });

  return map;
}

// Stable identity for a deal -- used to match recommendations back
// to their rendered cards even after re-render. Matches the dedupe
// key the scanner uses server-side.
function dealKey(d) {
  return `${d.carrier_code || "FR"}|${d.origin}|${d.destination_iata}|${d.weekend_window || "fri_sun"}|${d.outbound_departure}`;
}

function renderDealCard(deal, idx) {
  const li = document.createElement("li");
  li.className = "deal";
  if (deal.is_lowest_ever) li.classList.add("lowest-ever");
  if (deal.photo_url) {
    li.classList.add("has-photo");
    // Thumbnail rendered as a CSS background image. We set it inline
    // so each card can have its own photo without bloating the
    // stylesheet; the dark overlay keeps text readable.
    li.style.backgroundImage = `url("${deal.photo_url}")`;
  }
  li.dataset.idx = idx;
  li.dataset.dealKey = dealKey(deal);

  const flightPrice = deal.flight_price_eur != null
    ? deal.flight_price_eur
    : deal.effective_price_eur;
  const hasPrice = flightPrice != null;
  const priceDisplay = hasPrice
    ? formatPrice(flightPrice)
    : `<span class="price-check">check &rarr;</span>`;
  const co2Kg = estimateCO2Kg(deal);
  const bus = deal.bus_surcharge_eur || 0;
  // Bus surcharge is a separate note, not rolled into the headline
  // price. Only DUB deals carry a non-zero bus_surcharge (Limerick
  // <-> Dublin bus). SNN and BHX show "direct from <origin>".
  const originName = ORIGIN_LABEL[deal.origin] || deal.origin;
  const priceNote = hasPrice
    ? (bus > 0
        ? `+&euro;${bus.toFixed(0)} Limerick bus (not incl.)`
        : `direct from ${originName}`)
    : (bus > 0
        ? `+&euro;${bus.toFixed(0)} Limerick bus (not incl.)`
        : "live price via link");

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
      ${deal.weather_emoji ? `<span class="weather-pill" title="${deal.weather_text || ""}">${deal.weather_emoji} ${deal.weather_high_c != null ? Math.round(deal.weather_high_c) + "&deg;" : ""}${deal.weather_low_c != null ? " / " + Math.round(deal.weather_low_c) + "&deg;" : ""}</span>` : ""}
      <span class="country">${fmtDate(deal.outbound_departure)} &ndash; ${fmtDate(deal.inbound_departure)}</span>
    </div>
    <div class="times">${timesHtml}</div>
    ${warnHtml}
    <div class="extras-row">
      ${co2Kg != null ? `<span class="extra" title="Approx per-passenger CO2 for the round trip">&#x1F33F; ~${co2Kg} kg CO&#8322;</span>` : ""}
      <a class="extra ical" href="#" data-action="ical">&#x1F4C5; Add to calendar</a>
    </div>
    <div class="book-row">
      <a class="book book-google" href="${deal.google_flights_url}" target="_blank" rel="noopener">Google Flights &rarr;</a>
      <a class="book book-sky" href="${deal.skyscanner_url}" target="_blank" rel="noopener">Skyscanner &rarr;</a>
    </div>
  `;
  // Attach iCal click handler after innerHTML is set
  const icalLink = li.querySelector('a[data-action="ical"]');
  if (icalLink) {
    icalLink.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();  // don't trigger the card's own click handler
      downloadIcs(deal);
    });
  }
  return li;
}

async function main() {
  // Kick off FX rate loading immediately so currency toggles feel
  // snappy. Completes in the background; fallback rates are used
  // until it resolves.
  loadExchangeRates();

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

  // Marker cluster group: collapses nearby price badges into a
  // single "N deals here" cluster at low zoom so the map doesn't
  // drown in overlapping badges (London has 4 airports worth of
  // clutter alone). At city zoom everything expands back to
  // individual badges. Uses the plugin loaded via CDN in index.html;
  // falls back to a plain layerGroup if the plugin isn't available
  // (e.g. offline first load before the SW cached it).
  const markerLayer =
    typeof L.markerClusterGroup === "function"
      ? L.markerClusterGroup({
          maxClusterRadius: 45,
          disableClusteringAtZoom: 7,
          showCoverageOnHover: false,
          spiderfyOnMaxZoom: true,
          iconCreateFunction: (cluster) => {
            const count = cluster.getChildCount();
            // Pick the cheapest deal in this cluster so the cluster
            // label shows the best available price, not the count.
            let cheapest = Infinity;
            cluster.getAllChildMarkers().forEach((m) => {
              const p = m.options._dealPrice;
              if (typeof p === "number" && p < cheapest) cheapest = p;
            });
            const label =
              isFinite(cheapest) && cheapest < Infinity
                ? formatPrice(cheapest)
                : `${count}`;
            return L.divIcon({
              html: `<div class="price-badge cluster">${label} <span class="count">&middot;${count}</span></div>`,
              className: "",
              iconSize: null,
              iconAnchor: [30, 14],
            });
          },
        })
      : L.layerGroup();
  markerLayer.addTo(map);

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
    const bhxEl = $("filter-bhx");
    return {
      showSNN: $("filter-snn").checked,
      showDUB: $("filter-dub").checked,
      showBHX: bhxEl ? bhxEl.checked : true,
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
      if (d.origin === "BHX" && !f.showBHX) return false;
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
      const html = `<div class="price-badge ${originClass}${selected ? " selected" : ""}" data-iata="${deal.destination_iata}">${formatPrice(price)}</div>`;
      const icon = L.divIcon({
        className: "",  // we style the inner div directly
        html,
        iconSize: null,
        iconAnchor: [20, 12],
      });

      const marker = L.marker(
        [deal.destination_lat, deal.destination_lon],
        {
          icon,
          riseOnHover: true,
          title: deal.destination_city || deal.destination_iata,
          _dealPrice: price,  // consumed by the cluster icon factory
        }
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
  const bhxCheckbox = $("filter-bhx");
  if (bhxCheckbox) {
    bhxCheckbox.addEventListener("change", render);
  }
  $("sort").addEventListener("change", render);
  const currencyEl = $("currency");
  if (currencyEl) {
    currencyEl.addEventListener("change", () => {
      activeCurrency = currencyEl.value;
      render();
    });
  }
  $("clear-destination").addEventListener("click", () => {
    selectedDestination = null;
    render();
  });

  // Recommend-me button: picks 3 deals weighted towards
  // "cheap + sunny", scrolls the sidebar to the first, highlights
  // all three, and pans the map to fit them. Uses whatever deals
  // pass the current filters so origin / slider / window settings
  // still apply.
  $("recommend-me").addEventListener("click", () => {
    const f = currentFilters();
    const candidates = applyFilters(payload.deals, f);
    if (candidates.length === 0) return;

    // Score each candidate: lower is better.
    //   base    = flight price
    //   weather = bonus if weather emoji is a sun / partly cloudy,
    //             penalty for rain / thunder / fog
    //   jitter  = small random component so the same "best" set
    //             doesn't come up every click
    //
    // NOTE on emoji literals: Python's \U0001F327 escape does NOT
    // work in JS strings -- backslash-capital-U isn't a recognized
    // escape, so "\U0001F327".includes check is searching for the
    // literal text "U0001F327" and never matches. We use the actual
    // emoji characters directly instead, which matches the exact
    // Unicode code points that enrichments.py WMO_CODE_MAP emits.
    const scored = candidates.map((d) => {
      const price = dealPrice(d);
      let score = price;
      const emoji = d.weather_emoji || "";
      // Sun / clear / partly-cloudy -> score bonus.
      // U+2600 (sun), U+26C5 (sun behind cloud), U+1F324 (sun behind small cloud)
      if (emoji.includes("\u2600") || emoji.includes("\u26C5") || emoji.includes("\uD83C\uDF24")) {
        score -= 15;
      }
      // Overcast / fog -> small penalty.
      // U+2601 (cloud), U+1F32B (fog)
      if (emoji.includes("\u2601") || emoji.includes("\uD83C\uDF2B")) {
        score += 5;
      }
      // Any kind of rain / showers -> heavier penalty.
      // U+1F327 (cloud with rain), U+1F326 (sun behind rain cloud)
      if (emoji.includes("\uD83C\uDF27") || emoji.includes("\uD83C\uDF26")) {
        score += 15;
      }
      // Snow -> moderate penalty (depends on your tastes; ski trip bonus?).
      // U+1F328 (cloud with snow)
      if (emoji.includes("\uD83C\uDF28")) {
        score += 10;
      }
      // Thunder -> big penalty.
      // U+26C8 (cloud with lightning and rain)
      if (emoji.includes("\u26C8")) {
        score += 25;
      }
      // Tiny random jitter so repeated clicks surface variety.
      score += Math.random() * 20;
      return { deal: d, score };
    });
    scored.sort((a, b) => a.score - b.score);
    const picks = scored.slice(0, 3).map((s) => s.deal);

    // Clear any prior destination pin so the picks are visible in
    // the full filtered list, then re-render.
    selectedDestination = null;
    render();

    // After render has populated the list, match each pick to its
    // card by stable deal key (not by city-name text match, which
    // was fragile and why the old button appeared broken) and:
    //   * add .highlighted to the card (green glow for 3.5s)
    //   * scroll the first pick into view
    //   * pan the map to fit all three picks
    setTimeout(() => {
      const pickKeys = new Set(picks.map(dealKey));
      const cards = Array.from(listEl.querySelectorAll(".deal"));
      const matchedCards = cards.filter((c) => pickKeys.has(c.dataset.dealKey));

      if (matchedCards.length === 0) {
        // Picks didn't end up in the visible list (shouldn't happen
        // given they come from applyFilters, but defensive).
        console.warn("Recommend: no matched cards for picks", picks);
        return;
      }

      matchedCards[0].scrollIntoView({ behavior: "smooth", block: "center" });
      matchedCards.forEach((card) => {
        card.classList.add("highlighted");
        setTimeout(() => card.classList.remove("highlighted"), 3500);
      });

      // Pan the map to fit the three picks if all have coordinates.
      const coords = picks
        .filter((p) => p.destination_lat != null && p.destination_lon != null)
        .map((p) => [p.destination_lat, p.destination_lon]);
      if (coords.length > 0 && typeof map !== "undefined" && map) {
        try {
          const bounds = L.latLngBounds(coords);
          map.fitBounds(bounds.pad(0.3), { maxZoom: 6, animate: true });
        } catch (e) {
          // Leaflet not ready or bounds invalid -- silently ignore,
          // the highlight + scroll already happened.
        }
      }
    }, 60);
  });

  // Mobile sidebar toggle: on narrow screens the sidebar slides in
  // from the right when the hamburger is tapped. Toggling the class
  // on <body> is enough; the CSS media query handles the rest.
  const toggle = $("sidebar-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      document.body.classList.toggle("sidebar-open");
    });
  }

  // Auto-refresh: every 10 minutes, re-fetch deals.json and re-render
  // if the generated_at timestamp changed. Only runs while the tab
  // is visible so background tabs don't pile up network requests.
  async function pollForUpdates() {
    if (document.hidden) return;
    try {
      const res = await fetch("deals.json", { cache: "no-store" });
      if (!res.ok) return;
      const fresh = await res.json();
      if (
        fresh.generated_at &&
        fresh.generated_at !== payload.generated_at
      ) {
        payload = fresh;
        render();
      }
    } catch (e) {
      // Network failure -- ignore, the next tick will try again.
    }
  }
  setInterval(pollForUpdates, 10 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollForUpdates();
  });

  render();
}

// Register the service worker so the dashboard is installable as a
// PWA and works offline after the first load. Only in secure contexts
// (https or localhost) and only if the browser supports it.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch((err) => {
      console.warn("Service worker registration failed:", err);
    });
  });
}

main();
