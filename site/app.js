const eventsEl = document.getElementById("events");
const emptyStateEl = document.getElementById("empty-state");
const venueFilterEl = document.getElementById("venue-filter");
const townFilterEl = document.getElementById("town-filter");
const lastUpdatedEl = document.getElementById("last-updated");

let allEvents = [];

const SOUTH_SHORE_CENTER = [42.05, -70.85];
const MASSACHUSETTS_BOUNDS = L.latLngBounds([41.1, -73.6], [42.9, -69.8]);

const map = L.map("map", {
  maxBounds: MASSACHUSETTS_BOUNDS,
  maxBoundsViscosity: 1.0, // hard stop at the edge instead of rubber-banding past it
}).setView(SOUTH_SHORE_CENTER, 10);

// Can't zoom out past seeing all of Massachusetts.
map.setMinZoom(map.getBoundsZoom(MASSACHUSETTS_BOUNDS));

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  maxZoom: 18,
}).addTo(map);
const markerLayer = L.layerGroup().addTo(map);

function todayIso() {
  // Shows are all Eastern Time (South Shore MA), so "today" must always be
  // the Eastern calendar date — not UTC (flips a day early every evening)
  // and not the viewer's own local date either (wrong for anyone browsing
  // from outside Eastern time, e.g. it'd still be "yesterday" for a Pacific
  // visitor well into an Eastern evening). A show stays listed under today
  // all day until the Eastern date itself rolls over, regardless of what
  // time the show was.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function formatLastUpdated(iso) {
  if (!iso) return "";
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
  return `Last updated ${formatted} ET`;
}

function formatTime(hhmm) {
  if (!hhmm) return hhmm;
  const [hourStr, minute] = hhmm.split(":");
  const hour = Number(hourStr);
  const period = hour < 12 ? "AM" : "PM";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${hour12}:${minute} ${period}`;
}

function formatTimeRange(start, end) {
  if (!start) return null;
  if (!end) return formatTime(start);

  const startFormatted = formatTime(start);
  const endFormatted = formatTime(end);
  const startPeriod = startFormatted.split(" ")[1];
  const endPeriod = endFormatted.split(" ")[1];

  // Drop the redundant AM/PM off the start when both ends match (e.g.
  // "6:30-9:00 PM" instead of "6:30 PM-9:00 PM").
  if (startPeriod === endPeriod) {
    return `${startFormatted.split(" ")[0]}–${endFormatted}`;
  }
  return `${startFormatted}–${endFormatted}`;
}

function formatDateHeading(iso) {
  if (!iso) return "Date unknown";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function populateFilter(selectEl, values) {
  const unique = [...new Set(values)].sort();
  for (const value of unique) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    selectEl.appendChild(option);
  }
}

function groupByDate(events) {
  const groups = new Map();
  for (const event of events) {
    const key = event.date || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (!a) return 1;
    if (!b) return -1;
    return a.localeCompare(b);
  });
}

function hashString(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 31 + str.charCodeAt(i)) | 0;
  }
  return hash;
}

function buildEventCard(event, index) {
  const card = document.createElement("a");
  card.className = "event-card";
  card.href = event.link || event.post_url;
  card.target = "_blank";
  card.rel = "noopener noreferrer";

  // A small per-show tilt (stable across re-renders, since it's derived from
  // the event's own id) so cards read as hand-pinned rather than a grid, plus
  // a short entrance stagger down the page.
  const tilt = ((hashString(event.id) % 30) / 10 - 1.5).toFixed(2);
  card.style.setProperty("--tilt", `${tilt}deg`);
  card.style.setProperty("--delay", `${Math.min(index * 0.04, 0.4)}s`);

  const textCol = document.createElement("div");
  textCol.className = "event-text";

  const bandLine = document.createElement("div");
  bandLine.className = "event-band";
  bandLine.textContent = event.band || "Untitled show";

  const metaLine = document.createElement("div");
  metaLine.className = "event-meta";
  const metaParts = [];
  const venueText = [event.venue, event.town].filter(Boolean).join(", ");
  if (venueText) metaParts.push(venueText);
  if (event.time) metaParts.push(formatTimeRange(event.time, event.time_end));
  metaLine.textContent = metaParts.join(" · ") || "Details in original post";

  textCol.appendChild(bandLine);
  textCol.appendChild(metaLine);

  if (event.notes) {
    const notesLine = document.createElement("div");
    notesLine.className = "event-notes";
    notesLine.textContent = event.notes;
    textCol.appendChild(notesLine);
  }

  if (event.photo) {
    const photoWrap = document.createElement("div");
    photoWrap.className = "event-photo";
    // Independent tilt from the card itself, opposite direction, so it reads
    // as a separate photo tucked onto the stub rather than part of the tilt.
    photoWrap.style.setProperty("--photo-tilt", `${-tilt * 1.4}deg`);
    const img = document.createElement("img");
    img.src = event.photo;
    img.alt = "";
    img.loading = "lazy";
    img.onerror = () => photoWrap.remove();
    photoWrap.appendChild(img);
    card.appendChild(photoWrap);
  }

  card.appendChild(textCol);
  return card;
}

function buildPopupContent(venue, town, events) {
  const container = document.createElement("div");
  container.className = "map-popup";

  const venueLine = document.createElement("div");
  venueLine.className = "popup-venue";
  venueLine.textContent = [venue, town].filter(Boolean).join(", ");
  container.appendChild(venueLine);

  for (const event of events) {
    const showLine = document.createElement("div");
    showLine.className = "popup-show";

    const link = document.createElement("a");
    link.href = event.link || event.post_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const parts = [event.band || "Untitled show"];
    if (event.time) parts.push(formatTimeRange(event.time, event.time_end));
    link.textContent = `${formatDateHeading(event.date)}: ${parts.join(", ")}`;

    showLine.appendChild(link);
    container.appendChild(showLine);
  }

  return container;
}

function updateMap(events) {
  markerLayer.clearLayers();

  const byLocation = new Map();
  for (const event of events) {
    if (event.lat == null || event.lon == null) continue;
    const key = `${event.lat},${event.lon}`;
    if (!byLocation.has(key)) byLocation.set(key, []);
    byLocation.get(key).push(event);
  }

  for (const [key, group] of byLocation) {
    const [lat, lon] = key.split(",").map(Number);
    const isApproximate = group[0].geocode_precision === "town";
    const marker = L.marker([lat, lon], { opacity: isApproximate ? 0.55 : 1 });
    marker.bindPopup(buildPopupContent(group[0].venue, group[0].town, group));
    marker.addTo(markerLayer);
  }
}

function render() {
  const selectedVenue = venueFilterEl.value;
  const selectedTown = townFilterEl.value;
  const today = todayIso();

  const upcoming = allEvents.filter((e) => {
    if (e.date && e.date < today) return false;
    if (selectedVenue && e.venue !== selectedVenue) return false;
    if (selectedTown && e.town !== selectedTown) return false;
    return true;
  });

  updateMap(upcoming);
  eventsEl.innerHTML = "";

  if (upcoming.length === 0) {
    emptyStateEl.hidden = false;
    return;
  }
  emptyStateEl.hidden = true;

  let cardIndex = 0;
  for (const [date, events] of groupByDate(upcoming)) {
    const group = document.createElement("div");
    group.className = "date-group";

    const heading = document.createElement("h2");
    heading.className = "date-heading";
    heading.textContent = formatDateHeading(date);
    group.appendChild(heading);

    for (const event of events) {
      group.appendChild(buildEventCard(event, cardIndex++));
    }
    eventsEl.appendChild(group);
  }
}

// events.json changes every few hours (new scrape data) — cache-bust with a
// timestamp and force no-cache, since a stale cached copy would silently
// show old data indefinitely (this bit us during development: the browser
// kept serving an old app.js/events.json from disk cache across reloads).
fetch(`events.json?t=${Date.now()}`, { cache: "no-store" })
  .then((res) => res.json())
  .then((data) => {
    allEvents = data.events;
    lastUpdatedEl.textContent = formatLastUpdated(data.last_checked_at);
    populateFilter(venueFilterEl, allEvents.map((e) => e.venue).filter(Boolean));
    populateFilter(townFilterEl, allEvents.map((e) => e.town).filter(Boolean));
    render();
  })
  .catch((err) => {
    emptyStateEl.hidden = false;
    emptyStateEl.textContent = "Couldn't load event data.";
    console.error(err);
  });

// The two filters can select a combination with no matches (a venue that
// isn't actually in the chosen town), so picking one resets the other to
// "All" rather than letting them conflict.
venueFilterEl.addEventListener("change", () => {
  if (venueFilterEl.value) townFilterEl.value = "";
  render();
});
townFilterEl.addEventListener("change", () => {
  if (townFilterEl.value) venueFilterEl.value = "";
  render();
});
