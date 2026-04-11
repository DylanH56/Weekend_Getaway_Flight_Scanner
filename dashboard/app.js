/* Weekend Getaway Flight Scanner - dashboard front-end */

const ORIGIN_LABEL = { SNN: "Shannon", DUB: "Dublin", BHX: "Birmingham" };
const ORIGIN_COLOR = {
  SNN: { stroke: "#4ade80", fill: "#065f46" },  // green
  DUB: { stroke: "#60a5fa", fill: "#1e3a8a" },  // blue
  BHX: { stroke: "#fb923c", fill: "#7c2d12" },  // amber/orange
};

// Country -> region mapping. Used by the region-chip filter so users
// can click "Iberia" to see Spain/Portugal or "Italy" for just Italy.
// Any country not in the map lands in "Other".
const REGION_MAP = {
  "United Kingdom": "UK & Ireland",
  "Ireland": "UK & Ireland",
  "Spain": "Iberia",
  "Portugal": "Iberia",
  "France": "France & Benelux",
  "Belgium": "France & Benelux",
  "Netherlands": "France & Benelux",
  "Luxembourg": "France & Benelux",
  "Germany": "Germany & Central Europe",
  "Austria": "Germany & Central Europe",
  "Switzerland": "Germany & Central Europe",
  "Czechia": "Germany & Central Europe",
  "Czech Republic": "Germany & Central Europe",
  "Slovakia": "Germany & Central Europe",
  "Hungary": "Germany & Central Europe",
  "Poland": "Germany & Central Europe",
  "Italy": "Italy",
  "Malta": "Italy",
  "Denmark": "Scandinavia",
  "Sweden": "Scandinavia",
  "Norway": "Scandinavia",
  "Finland": "Scandinavia",
  "Iceland": "Scandinavia",
  "Greece": "Mediterranean",
  "Croatia": "Mediterranean",
  "Cyprus": "Mediterranean",
  "Slovenia": "Mediterranean",
  "Bosnia and Herzegovina": "Balkans",
  "Serbia": "Balkans",
  "Montenegro": "Balkans",
  "Albania": "Balkans",
  "Macedonia": "Balkans",
  "North Macedonia": "Balkans",
  "Kosovo": "Balkans",
  "Romania": "Balkans",
  "Bulgaria": "Balkans",
  "Turkey": "Balkans",
  "Estonia": "Baltics",
  "Latvia": "Baltics",
  "Lithuania": "Baltics",
  "Morocco": "North Africa",
  "Israel": "Other",
};
function regionOf(country) {
  return REGION_MAP[country] || "Other";
}

// Display order for region chips -- popular regions first so the
// sidebar doesn't jump around as users click through.
const REGION_ORDER = [
  "UK & Ireland",
  "Iberia",
  "Italy",
  "France & Benelux",
  "Germany & Central Europe",
  "Mediterranean",
  "Scandinavia",
  "Balkans",
  "Baltics",
  "North Africa",
  "Other",
];

const $ = (id) => document.getElementById(id);

// Toast helper: fade in, hold for ~1.8s, fade out. Used for "Copied
// link to clipboard" and any future transient confirmations.
let toastTimeout = null;
function showToast(message) {
  const el = $("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => el.classList.remove("show"), 1800);
}

// Compute flight duration from ISO timestamps. Returns "2h 15m" or
// null if inputs are missing. The scanner already emits these in
// local-airport time so the subtraction lines up cleanly.
function flightDurationLabel(depIso, arrIso) {
  if (!depIso || !arrIso) return null;
  const dep = new Date(depIso);
  const arr = new Date(arrIso);
  if (isNaN(dep) || isNaN(arr)) return null;
  let mins = Math.round((arr - dep) / 60000);
  if (mins < 0) return null;  // crossed midnight weirdness -- skip
  // Clamp obviously-wrong values (over 24h) to avoid nonsense output
  // if the scanner ever emits bogus timestamps.
  if (mins > 24 * 60) return null;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

// Build a Booking.com search URL pre-filled with destination city
// and the weekend's check-in / check-out dates (derived from the
// flight outbound/inbound departure dates). Booking.com is happy to
// receive a free-text city name in the `ss` param.
function bookingUrl(deal) {
  const city = deal.destination_city || deal.destination_iata;
  const checkin = (deal.outbound_departure || "").slice(0, 10);
  const checkout = (deal.inbound_departure || "").slice(0, 10);
  if (!city || !checkin || !checkout) return null;
  const params = new URLSearchParams({
    ss: city,
    checkin: checkin,
    checkout: checkout,
    group_adults: "2",
    no_rooms: "1",
    group_children: "0",
  });
  return `https://www.booking.com/searchresults.html?${params.toString()}`;
}

// Airbnb search URL for the same window. Airbnb uses different query
// param names than Booking.com but the principle is the same.
function airbnbUrl(deal) {
  const city = deal.destination_city || deal.destination_iata;
  const country = deal.destination_country || "";
  const checkin = (deal.outbound_departure || "").slice(0, 10);
  const checkout = (deal.inbound_departure || "").slice(0, 10);
  if (!city || !checkin || !checkout) return null;
  const query = country ? `${city}, ${country}` : city;
  const params = new URLSearchParams({
    query,
    checkin,
    checkout,
    adults: "2",
  });
  return `https://www.airbnb.com/s/${encodeURIComponent(query)}/homes?${params.toString()}`;
}

// Google search URL for "things to do in <city> this weekend". Dead
// simple, opens in a new tab, gives the user a jumping-off point.
function activitiesUrl(deal) {
  const city = deal.destination_city || deal.destination_iata;
  if (!city) return null;
  return `https://www.google.com/search?q=${encodeURIComponent("things to do in " + city + " this weekend")}`;
}

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

// Price-history data loaded in main(). Keyed by dealKey() (which
// matches history.py's route_key format exactly). Empty {} until
// loadHistory() resolves. Module-level so renderDealCard() can
// read it without being threaded through every call site.
let historyByKey = {};

// Comparison tray: Set of dealKey strings the user has checked for
// side-by-side comparison. Capped at COMPARE_MAX_ITEMS deals to
// keep the modal usable. Persists across filter changes so you can
// check deals in different views.
const COMPARE_MAX_ITEMS = 3;
let comparedKeys = new Set();

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

// Load the scanner's price-history file if present. Returns an empty
// object on any failure (file not found, JSON parse error, network
// blip) so sparkline rendering degrades gracefully -- a card with no
// history just shows no sparkline, nothing else breaks.
async function loadHistory() {
  try {
    const res = await fetch("history.json", { cache: "no-store" });
    if (!res.ok) return {};
    const data = await res.json();
    return (data && typeof data === "object") ? data : {};
  } catch (e) {
    return {};
  }
}

// Render a compact inline SVG sparkline for a list of price
// observations. Returns "" if there are fewer than 2 points (nothing
// to draw). Widely supported across browsers; no external lib.
//
//   observations: array of [iso_ts, price] pairs (history.json format)
//   width/height: SVG viewBox dimensions in pixels
//
// The path is a polyline normalized against the min/max range so
// the sparkline is always fully visible regardless of actual prices.
// Last point gets a filled circle so "current" stands out from the
// trend line.
function sparklineSvg(observations, width = 80, height = 20) {
  if (!Array.isArray(observations) || observations.length < 2) return "";
  const prices = observations
    .map((o) => (Array.isArray(o) && typeof o[1] === "number" ? o[1] : null))
    .filter((p) => p != null);
  if (prices.length < 2) return "";

  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;  // guard against flat lines -> div by zero
  const pad = 2;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  // Build polyline points: x spaced evenly, y inverted (SVG origin
  // is top-left) and normalized to [pad, height-pad].
  const pts = prices.map((p, i) => {
    const x = pad + (i * innerW) / (prices.length - 1);
    const y = pad + innerH - ((p - min) / range) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const polyline = pts.join(" ");

  // Pick a color based on trend: green if last < first (price dropped),
  // red if up, yellow if flat.
  const first = prices[0];
  const last = prices[prices.length - 1];
  let stroke = "#94a3b8";  // neutral grey
  if (last < first - 0.5) stroke = "#4ade80";       // down trend
  else if (last > first + 0.5) stroke = "#f87171";  // up trend
  else stroke = "#facc15";                           // flat-ish

  // Last-point dot coordinates.
  const lastX = pad + innerW;
  const lastY = pad + innerH - ((last - min) / range) * innerH;

  const title = `${prices.length} obs, min €${min.toFixed(0)}, max €${max.toFixed(0)}`;
  return `
    <svg class="sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"
         xmlns="http://www.w3.org/2000/svg" aria-label="${title}">
      <title>${title}</title>
      <polyline points="${polyline}" fill="none" stroke="${stroke}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" />
      <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="1.8" fill="${stroke}" />
    </svg>
  `.trim();
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
// to their rendered cards after re-render, for share-link deep
// linking, and for looking up the route's price history. Matches
// history.py's route_key() format EXACTLY: carrier|origin|dest|
// window|outbound_date (date only, not full ISO timestamp) so
// history.json can be consulted with a direct dict lookup.
function dealKey(d) {
  const outDate = (d.outbound_departure || "").slice(0, 10);
  return `${d.carrier_code || "FR"}|${d.origin}|${d.destination_iata}|${d.weekend_window || "fri_sun"}|${outDate}`;
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

  // Flight duration (outbound leg). Pure client-side -- the scanner
  // already emits ISO timestamps so we just subtract them here.
  const outDuration = flightDurationLabel(deal.outbound_departure, deal.outbound_arrival);

  // Hotel + activity links pre-filled with the weekend dates.
  const hotelHref = bookingUrl(deal);
  const stayHref = airbnbUrl(deal);
  const activitiesHref = activitiesUrl(deal);

  // Price history sparkline. If this route has at least 2 prior
  // observations in historyByKey, render a tiny 80x20 SVG. Otherwise
  // sparklineHtml is an empty string and nothing shows.
  const thisDealKey = dealKey(deal);
  const obs = historyByKey[thisDealKey] || [];
  const sparklineHtml = sparklineSvg(obs);

  // Compare checkbox state (rendered checked if this deal is in
  // the comparedKeys set).
  const isCompared = comparedKeys.has(thisDealKey);

  li.innerHTML = `
    <div class="top">
      <div class="top-left">
        <div class="city">${deal.destination_city || deal.destination_iata}</div>
        <div class="country">${deal.destination_country || ""} &middot; ${deal.destination_iata}</div>
        ${sparklineHtml ? `<div class="sparkline-wrap" title="60-day price history">${sparklineHtml}</div>` : ""}
      </div>
      <div class="price-block">
        <label class="compare-check" title="Add to compare (up to ${COMPARE_MAX_ITEMS})">
          <input type="checkbox" data-action="compare-toggle" ${isCompared ? "checked" : ""}>
        </label>
        <div class="price">${priceDisplay}</div>
        <div class="price-note">${priceNote}</div>
        ${trendHtml}
      </div>
    </div>
    <div class="meta-row">
      <span class="badge ${(deal.origin || "").toLowerCase()}">${ORIGIN_LABEL[deal.origin] || deal.origin || "?"}</span>
      ${deal.carrier_code ? `<span class="badge carrier carrier-${deal.carrier_code.toLowerCase()}">${deal.carrier_code}</span>` : ""}
      ${deal.weekend_window_label ? `<span class="badge window">${deal.weekend_window_label}</span>` : ""}
      ${deal.weather_emoji ? `<span class="weather-pill" title="${deal.weather_text || ""}">${deal.weather_emoji} ${deal.weather_high_c != null ? Math.round(deal.weather_high_c) + "&deg;" : ""}${deal.weather_low_c != null ? " / " + Math.round(deal.weather_low_c) + "&deg;" : ""}</span>` : ""}
      <span class="country">${fmtDate(deal.outbound_departure)} &ndash; ${fmtDate(deal.inbound_departure)}</span>
    </div>
    <div class="times">${timesHtml}</div>
    ${warnHtml}
    <div class="extras-row">
      ${outDuration ? `<span class="extra duration" title="Outbound flight duration">&#x2708; ${outDuration}</span>` : ""}
      ${co2Kg != null ? `<span class="extra" title="Approx per-passenger CO2 for the round trip">&#x1F33F; ~${co2Kg} kg CO&#8322;</span>` : ""}
      <a class="extra ical" href="#" data-action="ical">&#x1F4C5; Calendar</a>
      ${hotelHref ? `<a class="extra hotels" href="${hotelHref}" target="_blank" rel="noopener">&#x1F3E8; Hotels</a>` : ""}
      ${stayHref ? `<a class="extra hotels" href="${stayHref}" target="_blank" rel="noopener">&#x1F3E0; Airbnb</a>` : ""}
      ${activitiesHref ? `<a class="extra activities" href="${activitiesHref}" target="_blank" rel="noopener">&#x1F3DB; Things to do</a>` : ""}
      <a class="extra share" href="#" data-action="share">&#x1F517; Share</a>
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
  // Share click handler: builds a deep-link URL and copies it.
  const shareLink = li.querySelector('a[data-action="share"]');
  if (shareLink) {
    shareLink.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const url = new URL(location.href);
      url.searchParams.set("deal", dealKey(deal));
      const href = url.toString();
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(href);
          showToast("Link copied to clipboard");
        } else {
          // Fallback for older browsers / non-HTTPS contexts: show
          // the URL in a prompt so the user can copy it manually.
          window.prompt("Copy this link:", href);
        }
      } catch (err) {
        console.warn("Share copy failed:", err);
        window.prompt("Copy this link:", href);
      }
    });
  }
  // Prevent the card's outer click handler (which pans the map) from
  // firing when the user clicks a hotels/airbnb/activities link.
  li.querySelectorAll(".extras-row a").forEach((a) => {
    a.addEventListener("click", (e) => e.stopPropagation());
  });

  // Compare checkbox: toggle this deal in/out of the compare tray.
  const cmp = li.querySelector('input[data-action="compare-toggle"]');
  if (cmp) {
    cmp.addEventListener("click", (e) => {
      e.stopPropagation();
    });
    cmp.addEventListener("change", (e) => {
      e.stopPropagation();
      const k = thisDealKey;
      if (cmp.checked) {
        if (comparedKeys.size >= COMPARE_MAX_ITEMS) {
          cmp.checked = false;
          showToast(`Max ${COMPARE_MAX_ITEMS} deals in compare`);
          return;
        }
        comparedKeys.add(k);
      } else {
        comparedKeys.delete(k);
      }
      // Update the compare tray button visibility / count without
      // a full re-render. Re-render would scroll the user back to
      // the top of the list which is annoying when checking boxes.
      updateCompareTrayButton();
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
    // Load deals and history in parallel. history.json is optional --
    // if it doesn't exist yet (e.g. first scan after deploy) the
    // sparklines simply don't render, nothing breaks. historyByKey
    // is the module-level global read by renderDealCard.
    const [dealsResult, historyResult] = await Promise.all([
      loadDeals(),
      loadHistory(),
    ]);
    payload = dealsResult;
    historyByKey = historyResult;
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

  // Active region filter -- null means "show all regions". Set by
  // clicking a chip in #region-chips.
  let activeRegion = null;

  // Search query from the #search-box input. Normalized to lowercase
  // once at read-time; applied against city, country, and IATA.
  function currentSearchQuery() {
    const el = $("search-box");
    return el ? (el.value || "").trim().toLowerCase() : "";
  }

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
      region: activeRegion,
      search: currentSearchQuery(),
    };
  }

  // Show/hide + update count on the "Compare N" button in the
  // sidebar header. Called after every checkbox toggle so the user
  // sees the count change without a full re-render.
  function updateCompareTrayButton() {
    const btn = $("open-compare");
    const count = $("compare-count");
    if (!btn) return;
    if (comparedKeys.size === 0) {
      btn.style.display = "none";
    } else {
      btn.style.display = "block";
      if (count) count.textContent = String(comparedKeys.size);
    }
  }

  // Render one side-by-side compare card. Uses the same deal fields
  // as the main sidebar card but laid out as a structured grid of
  // rows for easy visual comparison across multiple picks.
  function renderCompareCard(deal) {
    const price = dealPrice(deal);
    const co2Kg = estimateCO2Kg(deal);
    const outDuration = flightDurationLabel(deal.outbound_departure, deal.outbound_arrival);
    const inDuration = flightDurationLabel(deal.inbound_departure, deal.inbound_arrival);
    const k = dealKey(deal);

    const weatherCell = deal.weather_emoji
      ? `${deal.weather_emoji} ${deal.weather_high_c != null ? Math.round(deal.weather_high_c) + "&deg;" : ""}${deal.weather_low_c != null ? " / " + Math.round(deal.weather_low_c) + "&deg;" : ""}`
      : "&mdash;";

    const hasPhoto = !!deal.photo_url;
    const bgStyle = hasPhoto ? `style="background-image: url('${deal.photo_url}');"` : "";

    const card = document.createElement("div");
    card.className = "compare-card" + (hasPhoto ? " has-photo" : "");
    if (hasPhoto) card.style.backgroundImage = `url('${deal.photo_url}')`;
    card.innerHTML = `
      <div class="cc-city">${deal.destination_city || deal.destination_iata}</div>
      <div class="cc-row"><span class="cc-label">From</span><span class="cc-value">${ORIGIN_LABEL[deal.origin] || deal.origin}</span></div>
      <div class="cc-price">${formatPrice(price)}</div>
      <div class="cc-row"><span class="cc-label">Carrier</span><span class="cc-value">${deal.carrier_name || deal.carrier_code || "?"}</span></div>
      <div class="cc-row"><span class="cc-label">Window</span><span class="cc-value">${deal.weekend_window_label || "Fri \u2192 Sun"}</span></div>
      <div class="cc-row"><span class="cc-label">Dates</span><span class="cc-value">${fmtDate(deal.outbound_departure)} &ndash; ${fmtDate(deal.inbound_departure)}</span></div>
      <div class="cc-row"><span class="cc-label">Outbound</span><span class="cc-value">${outDuration || "?"}</span></div>
      <div class="cc-row"><span class="cc-label">Return</span><span class="cc-value">${inDuration || "?"}</span></div>
      <div class="cc-row"><span class="cc-label">Weather</span><span class="cc-value">${weatherCell}</span></div>
      <div class="cc-row"><span class="cc-label">CO&#8322;</span><span class="cc-value">${co2Kg != null ? "~" + co2Kg + " kg" : "&mdash;"}</span></div>
      <div class="cc-row"><span class="cc-label">Lowest ever</span><span class="cc-value">${deal.lowest_ever_eur != null ? formatPrice(deal.lowest_ever_eur) : "&mdash;"}</span></div>
      <button class="cc-remove" data-key="${k}">Remove</button>
    `;
    return card;
  }

  // Open the modal and populate it with every currently-compared deal.
  function openCompareModal() {
    const modal = $("compare-modal");
    const grid = $("compare-grid");
    if (!modal || !grid) return;

    grid.innerHTML = "";

    if (comparedKeys.size === 0) {
      grid.innerHTML = '<div class="compare-empty">No deals selected. Check the boxes on deal cards to add them here.</div>';
    } else {
      // Look up the actual deal objects from payload.deals by key.
      const compareList = [];
      for (const d of payload.deals) {
        if (comparedKeys.has(dealKey(d))) {
          compareList.push(d);
        }
      }
      compareList.forEach((deal) => {
        const card = renderCompareCard(deal);
        grid.appendChild(card);
      });
      // Wire up remove buttons.
      grid.querySelectorAll(".cc-remove").forEach((btn) => {
        btn.addEventListener("click", () => {
          const k = btn.dataset.key;
          comparedKeys.delete(k);
          if (comparedKeys.size === 0) {
            closeCompareModal();
          } else {
            openCompareModal();  // re-render
          }
          updateCompareTrayButton();
          // Uncheck the corresponding card in the sidebar if it's visible.
          const card = Array.from(listEl.querySelectorAll(".deal")).find(
            (c) => c.dataset.dealKey === k
          );
          if (card) {
            const cb = card.querySelector('input[data-action="compare-toggle"]');
            if (cb) cb.checked = false;
          }
        });
      });
    }

    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("compare-open");
  }

  function closeCompareModal() {
    const modal = $("compare-modal");
    if (!modal) return;
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("compare-open");
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
      // Region filter: null = all regions.
      if (f.region && regionOf(d.destination_country) !== f.region) return false;
      // Text search: match against city, country, or IATA (all lower-cased).
      if (f.search) {
        const hay = (
          (d.destination_city || "") + " " +
          (d.destination_country || "") + " " +
          (d.destination_iata || "")
        ).toLowerCase();
        if (!hay.includes(f.search)) return false;
      }
      return true;
    });
  }

  // Compute the set of regions present in the current payload +
  // count deals per region, respecting all OTHER filters (origin,
  // price, windows, search) but ignoring the region filter itself
  // so toggling a chip always reflects its true deal count.
  function computeRegionCounts() {
    const f = currentFilters();
    const noRegionFilter = { ...f, region: null };
    const candidates = applyFilters(payload.deals, noRegionFilter);
    const counts = {};
    for (const d of candidates) {
      const r = regionOf(d.destination_country);
      counts[r] = (counts[r] || 0) + 1;
    }
    return counts;
  }

  // Render region chips in REGION_ORDER, skipping regions that have
  // zero deals under the current non-region filters.
  function renderRegionChips() {
    const container = $("region-chips");
    if (!container) return;
    const counts = computeRegionCounts();
    container.innerHTML = "";
    // "All" chip first -- clicking it clears the region filter.
    const allChip = document.createElement("div");
    const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);
    allChip.className = "region-chip" + (activeRegion === null ? " active" : "");
    allChip.innerHTML = `All <span class="count">${totalCount}</span>`;
    allChip.addEventListener("click", () => {
      activeRegion = null;
      render();
      syncUrlState();
    });
    container.appendChild(allChip);
    // Then one chip per populated region, in display order.
    for (const region of REGION_ORDER) {
      const n = counts[region] || 0;
      if (n === 0) continue;
      const chip = document.createElement("div");
      chip.className = "region-chip" + (activeRegion === region ? " active" : "");
      chip.innerHTML = `${region} <span class="count">${n}</span>`;
      chip.addEventListener("click", () => {
        // Click the active chip again to deselect; otherwise activate it.
        activeRegion = activeRegion === region ? null : region;
        render();
        syncUrlState();
      });
      container.appendChild(chip);
    }
  }

  // --- URL state persistence -----------------------------------------
  // Reflects filter state in the URL querystring so bookmarks and
  // "share this view" links work. Writes via history.replaceState
  // so we don't pollute the browser history on every filter change.
  function syncUrlState() {
    const f = currentFilters();
    const params = new URLSearchParams();

    // Origin checkboxes: only serialise if NOT all three are on.
    const origins = [];
    if (f.showSNN) origins.push("SNN");
    if (f.showDUB) origins.push("DUB");
    if (f.showBHX) origins.push("BHX");
    if (origins.length < 3) params.set("origin", origins.join(","));

    // Price cap: only serialise if not at the max slider value.
    const slider = $("price-max");
    const defaultMax = slider ? parseInt(slider.max, 10) : 150;
    if (f.maxPrice !== defaultMax) params.set("max", String(f.maxPrice));

    // Weekend windows: serialise if not just the default fri_sun.
    const wins = Array.from(f.windows);
    if (wins.length !== 1 || wins[0] !== "fri_sun") {
      params.set("win", wins.join(","));
    }

    // Region: only if set.
    if (f.region) params.set("region", f.region);

    // Search query: only if non-empty.
    if (f.search) params.set("q", f.search);

    // Selected destination pin.
    if (selectedDestination) params.set("dest", selectedDestination);

    // Sort mode: only if not default.
    if (f.sortMode && f.sortMode !== "price") params.set("sort", f.sortMode);

    // Currency: only if not EUR.
    if (activeCurrency && activeCurrency !== "EUR") params.set("cur", activeCurrency);

    const qs = params.toString();
    const newUrl = qs
      ? `${location.pathname}?${qs}${location.hash}`
      : `${location.pathname}${location.hash}`;
    try {
      history.replaceState(null, "", newUrl);
    } catch (e) {
      // Some browsers block replaceState in certain contexts (e.g.
      // file:// URLs). Non-fatal -- filters still work.
    }
  }

  // Read URL params once at page load and apply them to the initial
  // filter state. Called BEFORE the first render() so the dashboard
  // comes up with the bookmarked view already applied.
  function applyUrlState() {
    const params = new URLSearchParams(location.search);

    const originParam = params.get("origin");
    if (originParam) {
      const set = new Set(originParam.split(",").map((s) => s.trim().toUpperCase()));
      const snn = $("filter-snn"); if (snn) snn.checked = set.has("SNN");
      const dub = $("filter-dub"); if (dub) dub.checked = set.has("DUB");
      const bhx = $("filter-bhx"); if (bhx) bhx.checked = set.has("BHX");
    }

    const maxParam = parseInt(params.get("max") || "", 10);
    if (isFinite(maxParam) && maxParam > 0) {
      const slider = $("price-max");
      const label = $("price-max-label");
      if (slider) slider.value = String(maxParam);
      if (label) label.innerHTML = `&euro;${maxParam}`;
    }

    const winParam = params.get("win");
    if (winParam) {
      enabledWindows.clear();
      winParam.split(",").map((s) => s.trim()).filter(Boolean).forEach((w) => enabledWindows.add(w));
      // Guard against empty set -- fall back to default.
      if (enabledWindows.size === 0) enabledWindows.add("fri_sun");
    }

    const regionParam = params.get("region");
    if (regionParam && REGION_ORDER.includes(regionParam)) {
      activeRegion = regionParam;
    }

    const qParam = params.get("q");
    if (qParam) {
      const box = $("search-box");
      if (box) box.value = qParam;
    }

    const destParam = params.get("dest");
    if (destParam) {
      selectedDestination = destParam;
    }

    const sortParam = params.get("sort");
    if (sortParam) {
      const sel = $("sort");
      if (sel) sel.value = sortParam;
    }

    const curParam = params.get("cur");
    if (curParam && ["EUR", "GBP", "USD"].includes(curParam)) {
      activeCurrency = curParam;
      const sel = $("currency");
      if (sel) sel.value = curParam;
    }
  }
  // --- end URL state persistence -----------------------------------

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

    // Default sidebar title when no destination is pinned. Matches
    // the HTML so the title reads "Trip Ideas" at rest and changes
    // to "<City> (IATA)" when the user pins a destination via the
    // map marker. Single source of truth -- any future rename only
    // has to touch this constant.
    const DEFAULT_TITLE = "Trip Ideas";
    const titleEl = $("deals-title");
    if (selectedDestination) {
      const sample = sorted[0];
      const city = sample ? (sample.destination_city || selectedDestination) : selectedDestination;
      titleEl.textContent = `${city} (${selectedDestination})`;
      $("clear-destination").style.display = "block";
    } else {
      titleEl.textContent = DEFAULT_TITLE;
      $("clear-destination").style.display = "none";
    }

    if (sorted.length === 0) {
      // Compute a helpful empty-state: how many deals exist in total
      // vs. how many filters are currently narrowing them down. If
      // the user hasn't changed any filters there's nothing to reset,
      // so just show the basic message.
      const f = currentFilters();
      const origins = [f.showSNN, f.showDUB, f.showBHX].filter(Boolean).length;
      const hiddenCount = payload.deals.length;
      const hints = [];
      if (f.maxPrice < 150) hints.push(`raising the price slider (currently &euro;${f.maxPrice})`);
      if (origins < 3) hints.push("enabling more origin airports");
      if (f.region) hints.push(`clearing the <b>${f.region}</b> region filter`);
      if (f.search) hints.push(`clearing the search for <b>"${f.search.replace(/</g, "&lt;")}"</b>`);
      if (f.windows.size === 1 && !f.windows.has("fri_sun")) hints.push("enabling more weekend windows");
      if (selectedDestination) hints.push("unpinning the selected destination");

      const showReset = hints.length > 0;
      const hintHtml = showReset
        ? `Try ${hints.slice(0, 2).join(" or ")}.`
        : "The scanner hasn&rsquo;t found any matching deals yet.";

      listEl.innerHTML = `
        <div class="empty-state">
          <span class="empty-icon">&#x1F50D;</span>
          <div class="empty-title">No deals match your filters</div>
          <div class="empty-hint">${hintHtml}${hiddenCount > 0 ? ` (${hiddenCount} total deals in payload)` : ""}</div>
          ${showReset ? '<button class="reset-filters-btn" id="reset-filters">Reset all filters</button>' : ""}
        </div>
      `;
      const resetBtn = $("reset-filters");
      if (resetBtn) {
        resetBtn.addEventListener("click", () => {
          resetFiltersToDefaults();
          render();
          syncUrlState();
        });
      }
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

  function resetFiltersToDefaults() {
    // Restore every filter element to its default state. Called by
    // the empty-state "Reset all filters" button.
    const snn = $("filter-snn"); if (snn) snn.checked = true;
    const dub = $("filter-dub"); if (dub) dub.checked = true;
    const bhx = $("filter-bhx"); if (bhx) bhx.checked = true;
    const slider = $("price-max");
    const label = $("price-max-label");
    if (slider) slider.value = "150";
    if (label) label.innerHTML = "&euro;150";
    const searchBox = $("search-box");
    if (searchBox) searchBox.value = "";
    enabledWindows.clear();
    enabledWindows.add("fri_sun");
    activeRegion = null;
    selectedDestination = null;
  }

  function render() {
    const f = currentFilters();
    const filtered = applyFilters(payload.deals, f);
    updateMeta(filtered.length, payload.deals.length);
    renderWindowChips();
    renderRegionChips();
    renderMapMarkers(filtered);
    renderSidebar(filtered, f.sortMode);
    updateCompareTrayButton();
    syncUrlState();
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

  // Compare tray + modal wiring.
  const openCompareBtn = $("open-compare");
  if (openCompareBtn) {
    openCompareBtn.addEventListener("click", openCompareModal);
  }
  const closeCompareBtn = $("close-compare");
  if (closeCompareBtn) {
    closeCompareBtn.addEventListener("click", closeCompareModal);
  }
  // Click outside modal content to close.
  const compareModal = $("compare-modal");
  if (compareModal) {
    compareModal.addEventListener("click", (e) => {
      if (e.target === compareModal) closeCompareModal();
    });
  }
  // Escape key closes the modal if it's open.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && compareModal && compareModal.style.display !== "none") {
      closeCompareModal();
    }
  });

  // Search box: debounced so rapid typing doesn't cause 20 re-renders
  // per second on large payloads. 150ms feels responsive.
  const searchBox = $("search-box");
  if (searchBox) {
    let searchTimer = null;
    searchBox.addEventListener("input", () => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        render();
      }, 150);
    });
    // Escape key clears the box.
    searchBox.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && searchBox.value) {
        searchBox.value = "";
        render();
      }
    });
  }
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
    console.log("[recommend] clicked");
    const f = currentFilters();
    const candidates = applyFilters(payload.deals, f);
    console.log(`[recommend] ${candidates.length} candidates match filters`);
    if (candidates.length === 0) {
      showToast("No deals match filters -- try clearing some");
      return;
    }

    // Score each candidate: lower is better.
    //   base       = flight price
    //   weather    = bonus/penalty based on weather emoji
    //   diversity  = bonus for destinations we haven't picked yet
    //                (stops "3 different Barcelona weekends")
    //   jitter     = random component so repeated clicks vary
    const pickedIatas = new Set();
    const scored = candidates.map((d) => {
      const price = dealPrice(d);
      let score = price;
      const emoji = d.weather_emoji || "";
      if (emoji.includes("\u2600") || emoji.includes("\u26C5") || emoji.includes("\uD83C\uDF24")) score -= 15;
      if (emoji.includes("\u2601") || emoji.includes("\uD83C\uDF2B")) score += 5;
      if (emoji.includes("\uD83C\uDF27") || emoji.includes("\uD83C\uDF26")) score += 15;
      if (emoji.includes("\uD83C\uDF28")) score += 10;
      if (emoji.includes("\u26C8")) score += 25;
      score += Math.random() * 30;  // slightly wider jitter for more variety
      return { deal: d, score };
    });
    scored.sort((a, b) => a.score - b.score);

    // Diversity filter: walk the sorted list and only keep one pick
    // per destination IATA. Otherwise the top 3 can be 3 different
    // Barcelona weekends, which defeats the "surprise me" vibe.
    const picks = [];
    for (const s of scored) {
      if (picks.length >= 3) break;
      const iata = s.deal.destination_iata;
      if (pickedIatas.has(iata)) continue;
      pickedIatas.add(iata);
      picks.push(s.deal);
    }
    console.log(`[recommend] picks:`, picks.map((p) => `${p.destination_city} (€${dealPrice(p)})`));

    if (picks.length === 0) return;

    // Clear any filters that would hide the picks from the user:
    // the destination pin and the text search. Keep origin / price /
    // window filters so the user's explicit choices still apply.
    selectedDestination = null;
    const searchBox = $("search-box");
    if (searchBox && searchBox.value) {
      searchBox.value = "";
    }
    render();

    // Toast with the three pick names so the user gets immediate
    // visible feedback regardless of whether they notice the
    // highlight/scroll animation.
    const pickNames = picks.map((p) => p.destination_city || p.destination_iata);
    showToast(`Suggested: ${pickNames.join(", ")}`);

    // After render has populated the list, match each pick to its
    // card by stable deal key and:
    //   * scroll the first pick into view (works even if already
    //     at top -- window.scrollIntoView is a no-op in that case)
    //   * add .highlighted-strong to all three for a brighter glow
    //   * pan the map to fit all three picks
    setTimeout(() => {
      const pickKeys = new Set(picks.map(dealKey));
      const cards = Array.from(listEl.querySelectorAll(".deal"));
      const matchedCards = cards.filter((c) => pickKeys.has(c.dataset.dealKey));
      console.log(`[recommend] matched ${matchedCards.length} of ${picks.length} cards in DOM`);

      if (matchedCards.length === 0) {
        console.warn("[recommend] no matched cards -- picks may not be in visible list. picks:", picks, "pickKeys:", Array.from(pickKeys));
        // Defensive fallback: pan the map to the first pick's coords
        // so at least SOMETHING visible happens.
        const firstCoords = picks.find(
          (p) => p.destination_lat != null && p.destination_lon != null
        );
        if (firstCoords && typeof map !== "undefined" && map) {
          map.panTo([firstCoords.destination_lat, firstCoords.destination_lon], { animate: true });
        }
        return;
      }

      // Scroll the first matched card into view with a delay so the
      // highlight animation is visible as it enters the viewport.
      matchedCards[0].scrollIntoView({ behavior: "smooth", block: "center" });
      matchedCards.forEach((card, i) => {
        // Stagger the highlight classes slightly so the user's eye
        // tracks them one-by-one instead of all flashing simultaneously.
        setTimeout(() => {
          card.classList.add("highlighted", "highlighted-strong");
          setTimeout(() => {
            card.classList.remove("highlighted", "highlighted-strong");
          }, 4000);
        }, i * 250);
      });

      // Pan the map to fit all three picks.
      const coords = picks
        .filter((p) => p.destination_lat != null && p.destination_lon != null)
        .map((p) => [p.destination_lat, p.destination_lon]);
      if (coords.length > 0 && typeof map !== "undefined" && map) {
        try {
          const bounds = L.latLngBounds(coords);
          map.fitBounds(bounds.pad(0.4), { maxZoom: 6, animate: true });
        } catch (e) {
          // Leaflet not ready or bounds invalid -- the highlight +
          // scroll + toast already happened, so we've delivered
          // some visible response.
        }
      }
    }, 80);
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
        // Refresh history too, so sparklines stay in sync with the
        // new scan data. Fails silently if history.json is absent.
        historyByKey = await loadHistory();
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

  // Apply URL-state BEFORE the first render so the dashboard comes up
  // with the bookmarked filters already in place. Also handles the
  // ?deal=... deep-link case below.
  applyUrlState();

  render();

  // Deep-link: ?deal=FR|DUB|BCN|fri_sun|2026-05-08T18:00:00 scrolls
  // to and highlights the matching card on initial load. Done after
  // render() so the cards exist in the DOM.
  const dealLinkParam = new URLSearchParams(location.search).get("deal");
  if (dealLinkParam) {
    setTimeout(() => {
      const cards = Array.from(listEl.querySelectorAll(".deal"));
      const match = cards.find((c) => c.dataset.dealKey === dealLinkParam);
      if (match) {
        match.scrollIntoView({ behavior: "smooth", block: "center" });
        match.classList.add("highlighted");
        setTimeout(() => match.classList.remove("highlighted"), 4000);
        showToast("Deal from shared link");
      } else {
        console.warn("Shared deal link not found:", dealLinkParam);
      }
    }, 100);
  }
}

// Register the service worker so the dashboard is installable as a
// PWA and works offline after the first load. Only in secure contexts
// (https or localhost) and only if the browser supports it.
//
// Auto-reload flow: when a new SW takes control (because we pushed a
// new dashboard version), reload the page once so the user sees the
// new HTML/CSS/JS immediately instead of having to manually hard-
// refresh. The `refreshing` guard prevents a reload loop if the event
// fires twice in quick succession.
if ("serviceWorker" in navigator) {
  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing) return;
    refreshing = true;
    // Small delay so any pending SW activate() work finishes first.
    setTimeout(() => window.location.reload(), 50);
  });

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("sw.js")
      .then((registration) => {
        // If a waiting worker exists (new SW already installed but
        // stuck in "waiting" state), tell it to skip waiting so the
        // controllerchange handler above fires and we reload.
        if (registration.waiting) {
          registration.waiting.postMessage({ type: "SKIP_WAITING" });
        }
        // If an update is found mid-session, nudge the new worker
        // to activate immediately once it finishes installing.
        registration.addEventListener("updatefound", () => {
          const newWorker = registration.installing;
          if (!newWorker) return;
          newWorker.addEventListener("statechange", () => {
            if (
              newWorker.state === "installed" &&
              navigator.serviceWorker.controller
            ) {
              // A new version is ready. Ask it to skip waiting.
              newWorker.postMessage({ type: "SKIP_WAITING" });
            }
          });
        });
      })
      .catch((err) => {
        console.warn("Service worker registration failed:", err);
      });
  });
}

main();
