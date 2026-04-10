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
  if (deal.photo_url) {
    li.classList.add("has-photo");
    // Thumbnail rendered as a CSS background image. We set it inline
    // so each card can have its own photo without bloating the
    // stylesheet; the dark overlay keeps text readable.
    li.style.backgroundImage = `url("${deal.photo_url}")`;
  }
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
      ${deal.weather_emoji ? `<span class="weather-pill" title="${deal.weather_text || ""}">${deal.weather_emoji} ${deal.weather_high_c != null ? Math.round(deal.weather_high_c) + "&deg;" : ""}${deal.weather_low_c != null ? " / " + Math.round(deal.weather_low_c) + "&deg;" : ""}</span>` : ""}
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
                ? `&euro;${Math.round(cheapest)}`
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
  $("sort").addEventListener("change", render);
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
    //   weather = bonus if weather code is clear-ish (emoji starts
    //             with a sun or partly cloudy symbol)
    //   cheap-bias = divide by a random factor so the same set of
    //                "best" deals doesn't come up every click
    const scored = candidates.map((d) => {
      const price = dealPrice(d);
      let score = price;
      const emoji = d.weather_emoji || "";
      // Sun / clear / partly-cloudy -> score bonus.
      if (emoji.includes("\u2600") || emoji.includes("\u26C5")) score -= 15;
      // Overcast / fog -> small penalty.
      if (emoji.includes("\u2601") || emoji.includes("\U0001F32B")) score += 5;
      // Any kind of rain -> heavier penalty.
      if (emoji.includes("\U0001F327") || emoji.includes("\U0001F326")) score += 15;
      // Thunder -> big penalty.
      if (emoji.includes("\u26C8")) score += 25;
      // Tiny random jitter so repeated clicks surface variety.
      score += Math.random() * 20;
      return { deal: d, score };
    });
    scored.sort((a, b) => a.score - b.score);
    const picks = scored.slice(0, 3).map((s) => s.deal);

    // Clear any prior destination filter and re-render with just
    // these three highlighted at the top.
    selectedDestination = null;
    render();
    // Highlight the three picks after render has populated the list.
    setTimeout(() => {
      const cards = listEl.querySelectorAll(".deal");
      const pickKeys = new Set(
        picks.map(
          (p) =>
            `${p.carrier_code}|${p.origin}|${p.destination_iata}|${p.outbound_departure}`
        )
      );
      cards.forEach((card, i) => {
        const d = picks[0] && candidates[i];
        // Best-effort: highlight any card whose deal matches a pick.
      });
      // Scroll first pick into view and highlight it directly.
      const firstPick = picks[0];
      const firstCard = Array.from(cards).find((card) => {
        const title = card.querySelector(".city")?.textContent || "";
        return title.includes(firstPick.destination_city || firstPick.destination_iata);
      });
      if (firstCard) {
        firstCard.scrollIntoView({ behavior: "smooth", block: "center" });
        picks.forEach((p) => {
          const matchCard = Array.from(cards).find((card) => {
            const title = card.querySelector(".city")?.textContent || "";
            return title.includes(p.destination_city || p.destination_iata);
          });
          if (matchCard) {
            matchCard.classList.add("highlighted");
            setTimeout(() => matchCard.classList.remove("highlighted"), 3500);
          }
        });
      }
    }, 50);
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
