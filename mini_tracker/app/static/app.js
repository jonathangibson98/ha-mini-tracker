/* Mini Tracker UI. Plain JS, no build step. Uses relative URLs so it works behind HA ingress. */
(() => {
'use strict';

const MODE = document.querySelector('meta[name=mt-mode]').content; // "admin" | "view"
const Q = new URLSearchParams(location.search);
const MI = 1609.344;

const S = { cfg: { units: 'mi', theme: 'auto', tiles: 'osm', tile_url: '' }, units: 'mi', night: false, dark: false,
            devices: [], places: [], sel: null, ha: true, version: '' };
let SETTINGS = null;   // full settings (admin only)

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) {
    o.method = o.method || 'POST';
    o.headers = { 'Content-Type': 'application/json' };
    o.body = JSON.stringify(o.json);
    delete o.json;
  }
  const r = await fetch('api/' + path, o);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).error || m; } catch (e) { /* ignore */ }
    throw new Error(m);
  }
  return r.json();
}

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

function fmtDist(m) {
  if (S.units === 'km') return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toFixed(m < 10000 ? 2 : 1) + ' km';
  return m < 160 ? Math.round(m * 3.28084) + ' ft' : (m / MI).toFixed(m < 16093 ? 2 : 1) + ' mi';
}
function fmtSpeed(mph) { return S.units === 'km' ? Math.round(mph * 1.609344) + ' km/h' : Math.round(mph) + ' mph'; }
function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return d + 'd ' + h + 'h';
  if (h) return h + 'h ' + m + 'm';
  if (m) return m + 'm';
  return s + 's';
}
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const fmtDT = (ts) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
function ago(ts) {
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 90) return 'just now';
  return fmtDur(s) + ' ago';
}
const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return Math.floor(x / 1000); };
const dateStr = (d) => { const x = new Date(d); return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0') + '-' + String(x.getDate()).padStart(2, '0'); };
const devById = (id) => S.devices.find((d) => d.id === id);
const devName = (id) => (devById(id) || (SETTINGS && SETTINGS.devices[id]) || { name: id }).name;

const encPath = (p) => p.split('/').map(encodeURIComponent).join('/');
// Icon "specs" look like Home Assistant's: mdi:home, local:garage, <set>:name, pic:<file>, ha:<entity>#<ver>, or an emoji.
function icoURL(spec, color) {
  const m = /^([a-z0-9_-]+):(.+)$/i.exec(spec);
  if (!m) return null;
  const pre = m[1].toLowerCase();
  const mdi = pre === 'mdi' || pre === 'hass';
  return `api/ico/${encodeURIComponent(pre)}/${encPath(m[2])}` + (mdi ? `?c=${color || 'ffffff'}` : '');
}
function iconSrc(spec) {
  if (spec.startsWith('pic:')) return ['photo', 'api/picture/' + encodeURIComponent(spec.slice(4))];
  if (spec.startsWith('ha:')) {
    const [eid, v] = spec.slice(3).split('#');
    return ['photo', 'api/hapic/' + encodeURIComponent(eid) + (v ? '?v=' + v : '')];
  }
  const u = icoURL(spec, 'ffffff');
  if (!u) return null;
  return [/^(mdi|hass):/i.test(spec) ? 'mdi' : 'svg', u];
}
function avatarHTML(o, cls = '') {
  const spec = (o.icon || '').trim();
  const c = esc(o.color || '#03a9f4');
  const init = (o.name || '?').trim().split(/\s+/).map((w) => Array.from(w)[0]).join('').slice(0, 2).toUpperCase();
  const src = spec && iconSrc(spec);
  if (src) return `<div class="av ${src[0]} ${cls}" style="--c:${c}"><img src="${esc(src[1])}" data-fb="${esc(init)}" alt=""></div>`;
  if (spec && !/^[a-z0-9_-]+:/i.test(spec)) return `<div class="av emo ${cls}" style="--c:${c}">${esc(Array.from(spec).slice(0, 2).join(''))}</div>`;
  return `<div class="av ${cls}" style="--c:${c}">${esc(init)}</div>`;
}
// If an icon cannot be loaded (unknown set, deleted file, no picture), fall back to the initials.
document.addEventListener('error', (e) => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && t.dataset && t.dataset.fb !== undefined) {
    const p = t.parentNode;
    if (p) { p.classList.remove('mdi', 'svg', 'photo'); p.textContent = t.dataset.fb; }
  }
}, true);

// ------------------------------------------------------------------ map
const map = L.map('map', { zoomControl: false, preferCanvas: true }).setView([39.8, -98.6], 4);
L.control.zoom({ position: 'bottomright' }).addTo(map);
const LY = { places: L.layerGroup().addTo(map), trails: L.layerGroup().addTo(map), pb: L.layerGroup().addTo(map),
             tmp: L.layerGroup().addTo(map), stops: L.layerGroup().addTo(map), heat: null };
const markers = {};
let tileLayer = null, tileKey = '';

function applyTheme() {
  const t = Q.get('theme') || S.cfg.theme;
  S.dark = t === 'dark' || (t === 'auto' && S.night) || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.body.classList.toggle('dark', S.dark);
  const c = S.cfg;
  $('#map').classList.toggle('dark-tiles', S.dark && c.tiles === 'osm');
  const key = [c.tiles, c.tiles === 'carto' ? S.dark : '', c.tile_url].join('|');
  if (key === tileKey) return;
  tileKey = key;
  if (tileLayer) map.removeLayer(tileLayer);
  let url, opt = { maxZoom: 19 };
  if (c.tiles === 'carto') {
    url = `https://{s}.basemaps.cartocdn.com/${S.dark ? 'dark_all' : 'rastertiles/voyager'}/{z}/{x}/{y}{r}.png`;
    opt.subdomains = 'abcd';
    opt.attribution = '&copy; OpenStreetMap contributors &copy; CARTO';
  } else if (c.tiles === 'custom' && c.tile_url) {
    url = c.tile_url; opt.attribution = 'Custom tiles';
  } else {
    url = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    opt.attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  }
  tileLayer = L.tileLayer(url, opt).addTo(map);
}

let placesKey = '';
function renderPlaces(places) {
  const key = JSON.stringify(places);
  if (key === placesKey) return;
  placesKey = key;
  LY.places.clearLayers();
  for (const p of places) {
    L.circle([p.lat, p.lon], { radius: p.radius, color: p.color, weight: 1, dashArray: '4 4', fillOpacity: 0.12, interactive: false }).addTo(LY.places);
    L.marker([p.lat, p.lon], { interactive: false, keyboard: false, icon: L.divIcon({ className: '', iconSize: [0, 0],
      html: `<div class="pl">${avatarHTML(p)}<div class="lb">${esc(p.name)}</div></div>` }) }).addTo(LY.places);
  }
}

let accCircles = {};
function renderDevices(devs) {
  const filt = (Q.get('dev') || '').split(',').filter(Boolean);
  if (filt.length) devs = devs.filter((d) => filt.includes(d.id));
  S.devices = devs;
  const seen = new Set();
  for (const d of devs) {
    if (d.lat == null) continue;
    seen.add(d.id);
    const stale = Date.now() / 1000 - d.ts > 6 * 3600;
    const key = JSON.stringify([d.icon, d.name, d.color, stale]);
    let m = markers[d.id];
    if (!m) {
      m = markers[d.id] = { mk: L.marker([d.lat, d.lon], { zIndexOffset: 1000 }).addTo(map), key: '' };
      m.mk.on('click', () => selectDevice(d.id, true));
    }
    m.mk.setLatLng([d.lat, d.lon]);
    if (m.key !== key) {
      m.key = key;
      m.mk.setIcon(L.divIcon({ className: '', iconSize: [44, 66], iconAnchor: [22, 22],
        html: `<div class="mk ${stale ? 'stale' : ''}">${avatarHTML(d)}<div class="lb">${esc(d.name)}</div></div>` }));
    }
    if (d.acc && d.acc > 30) {
      if (!accCircles[d.id]) accCircles[d.id] = L.circle([d.lat, d.lon], { radius: d.acc, color: d.color, weight: 1, fillOpacity: 0.08, interactive: false }).addTo(map);
      accCircles[d.id].setLatLng([d.lat, d.lon]).setRadius(d.acc);
    } else if (accCircles[d.id]) { map.removeLayer(accCircles[d.id]); delete accCircles[d.id]; }
  }
  for (const id of Object.keys(markers)) {
    if (!seen.has(id)) {
      map.removeLayer(markers[id].mk); delete markers[id];
      if (accCircles[id]) { map.removeLayer(accCircles[id]); delete accCircles[id]; }
    }
  }
  renderChips();
  if (Q.get('follow') && markers[Q.get('follow')]) map.panTo(markers[Q.get('follow')].mk.getLatLng());
}

function renderChips() {
  const el = $('#chips');
  if (Q.get('chips') === '0') { el.hidden = true; return; }
  el.innerHTML = S.devices.map((d) => `<div class="chip ${S.sel === d.id ? 'sel' : ''}" data-id="${esc(d.id)}">${avatarHTML(d)}
    <div class="t"><span class="n">${esc(d.name)}</span><span class="s">${d.lat == null ? 'no location yet' :
      esc((d.place || 'Away') + ' · ' + ago(d.ts) + (d.batt != null ? ' · ' + Math.round(d.batt) + '%' : ''))}</span></div></div>`).join('');
  $$('.chip', el).forEach((c) => c.addEventListener('click', () => selectDevice(c.dataset.id, true)));
}

function selectDevice(id, fly) {
  S.sel = id;
  const d = devById(id);
  if (fly && d && d.lat != null) map.flyTo([d.lat, d.lon], Math.max(map.getZoom(), 15), { duration: 0.6 });
  renderChips();
  if (TAB === 'live') renderLive();
}

let firstFit = true;
async function refresh() {
  try {
    const d = await api('state');
    S.cfg = d.settings; S.units = d.settings.units; S.night = d.night; S.ha = d.ha; S.version = d.version;
    $('#banner').hidden = d.ha || MODE === 'view';
    $('#banner').textContent = 'Not connected to Home Assistant. Retrying…';
    applyTheme();
    renderPlaces(d.places);
    S.places = d.places;
    renderDevices(d.devices);
    if (firstFit) {
      firstFit = false;
      const pts = S.devices.filter((x) => x.lat != null).map((x) => [x.lat, x.lon]);
      if (pts.length === 1) map.setView(pts[0], 15);
      else if (pts.length > 1) map.fitBounds(pts, { padding: [70, 70], maxZoom: 16 });
      else if (d.places.length) map.fitBounds(d.places.map((p) => [p.lat, p.lon]), { padding: [70, 70], maxZoom: 15 });
      if (MODE === 'view' && Q.get('trail')) loadViewTrails();
    }
    if (TAB === 'live') renderLive();
  } catch (e) {
    console.warn('[mini_tracker] refresh failed:', e);
    $('#banner').hidden = false; $('#banner').textContent = 'Connection lost. Retrying…';
  }
}

// ------------------------------------------------------------------ read-only embed trails
async function loadViewTrails() {
  const hours = Math.max(1, Math.min(parseFloat(Q.get('trail')) || 24, 24 * 30));
  const ids = S.devices.map((d) => d.id);
  if (!ids.length) return;
  const end = Math.floor(Date.now() / 1000);
  const h = await api(`history?dev=${encodeURIComponent(ids.join(','))}&start=${end - hours * 3600}&end=${end}`);
  LY.trails.clearLayers();
  for (const id of ids) {
    const pts = (h.devices[id] || { points: [] }).points.map((p) => [p[1], p[2]]);
    if (pts.length > 1) L.polyline(pts, { color: devById(id).color, weight: 4, opacity: 0.8 }).addTo(LY.trails);
  }
}

// ------------------------------------------------------------------ panel / tabs (admin)
let TAB = 'live';
const TABS = [['live', 'Live'], ['history', 'History'], ['timeline', 'Timeline'], ['places', 'Places'], ['settings', 'Settings']];
const panel = () => $('#panel');

function setupSheet() {
  if (MODE !== 'admin') return;
  $('#sheet').hidden = false;
  $('#tabs').innerHTML = TABS.map(([k, l]) => `<button data-t="${k}">${l}</button>`).join('');
  $$('#tabs button').forEach((b) => b.addEventListener('click', () => { $('#sheet').classList.remove('min'); showTab(b.dataset.t); }));
  $('#grab').addEventListener('click', () => $('#sheet').classList.toggle('min'));
  showTab('live');
}

function showTab(t) {
  TAB = t;
  $$('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.t === t));
  if (t !== 'history') stopPlayback();
  if (t === 'live') renderLive();
  if (t === 'history') renderHistory();
  if (t === 'timeline') renderTimeline();
  if (t === 'places') renderPlacesTab();
  if (t === 'settings') renderSettings();
  panel().scrollTop = 0;
}

// ---- Live
function renderLive() {
  const rows = S.devices.map((d) => `<div class="card"><div class="top">${avatarHTML(d)}<b>${esc(d.name)}</b>
      <button class="b sm sec" data-go="${esc(d.id)}">Show</button></div>
      <div class="kv"><span class="muted">Place</span><span>${esc(d.place || 'Away')}</span></div>
      <div class="kv"><span class="muted">Updated</span><span>${d.ts ? esc(ago(d.ts)) : 'never'}</span></div>
      ${d.batt != null ? `<div class="kv"><span class="muted">Battery</span><span>${Math.round(d.batt)}%</span></div>` : ''}
      ${d.acc != null ? `<div class="kv"><span class="muted">Accuracy</span><span>${esc(fmtDist(d.acc))}</span></div>` : ''}
      ${d.lat != null ? `<div class="kv"><span class="muted">Coordinates</span><a href="https://www.openstreetmap.org/?mlat=${d.lat}&mlon=${d.lon}#map=17/${d.lat}/${d.lon}" target="_blank" rel="noopener">${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}</a></div>` : ''}
    </div>`).join('');
  panel().innerHTML = `<h3>Live</h3>${rows || '<p class="muted">No devices are tracked yet. Open <b>Settings</b> and turn some on.</p>'}
    <p class="muted">Mini Tracker ${esc(S.version)} · updates every 10 s</p>`;
  $$('[data-go]', panel()).forEach((b) => b.addEventListener('click', () => { selectDevice(b.dataset.go, true); if (innerWidth < 900) $('#sheet').classList.add('min'); }));
}

// ---- History
const H = { range: 'today', devs: new Set(), heat: false, points: false, data: null };
let PB = null;

function rangeFor(k) {
  const now = Math.floor(Date.now() / 1000), sod = startOfDay(Date.now());
  if (k === 'today') return [sod, now];
  if (k === 'yesterday') return [sod - 86400, sod - 1];
  if (k === '3d') return [now - 3 * 86400, now];
  if (k === '7d') return [now - 7 * 86400, now];
  if (k === '30d') return [now - 30 * 86400, now];
  const a = $('#hs'), b = $('#he');
  return [a && a.value ? Math.floor(new Date(a.value) / 1000) : sod, b && b.value ? Math.floor(new Date(b.value) / 1000) : now];
}

function renderHistory() {
  if (!H.devs.size) S.devices.forEach((d) => H.devs.add(d.id));
  const rl = [['today', 'Today'], ['yesterday', 'Yesterday'], ['3d', '3 days'], ['7d', '7 days'], ['30d', '30 days'], ['custom', 'Custom']];
  panel().innerHTML = `<h3>Devices</h3><div class="chips2">${S.devices.map((d) => `<button class="pill ${H.devs.has(d.id) ? 'on' : ''}" data-d="${esc(d.id)}">${esc(d.name)}</button>`).join('')}</div>
    <h3>Range</h3><div class="chips2">${rl.map(([k, l]) => `<button class="pill ${H.range === k ? 'on' : ''}" data-r="${k}">${l}</button>`).join('')}</div>
    <div class="row" id="custom" ${H.range === 'custom' ? '' : 'hidden'}><label>From<input type="datetime-local" id="hs"></label><label>To<input type="datetime-local" id="he"></label></div>
    <div class="row inline"><label><input type="checkbox" id="hheat" ${H.heat ? 'checked' : ''}> Heatmap</label><label><input type="checkbox" id="hpts" ${H.points ? 'checked' : ''}> Show points</label></div>
    <div class="row"><button class="b" id="hload">Show history</button><button class="b sec" id="hclear">Clear</button></div>
    <div id="hout"></div><div id="hpb"></div>`;
  $$('[data-d]', panel()).forEach((b) => b.addEventListener('click', () => { const id = b.dataset.d; H.devs.has(id) ? H.devs.delete(id) : H.devs.add(id); b.classList.toggle('on'); }));
  $$('[data-r]', panel()).forEach((b) => b.addEventListener('click', () => { H.range = b.dataset.r; $$('[data-r]', panel()).forEach((x) => x.classList.toggle('on', x === b)); $('#custom').hidden = H.range !== 'custom'; }));
  $('#hheat').addEventListener('change', (e) => { H.heat = e.target.checked; });
  $('#hpts').addEventListener('change', (e) => { H.points = e.target.checked; });
  $('#hload').addEventListener('click', loadHistory);
  $('#hclear').addEventListener('click', () => { stopPlayback(); clearHistoryLayers(); $('#hout').innerHTML = ''; $('#hpb').innerHTML = ''; });
  if (H.data) drawHistoryOut();
}

function clearHistoryLayers() {
  LY.trails.clearLayers(); LY.pb.clearLayers(); LY.stops.clearLayers();
  if (LY.heat) { map.removeLayer(LY.heat); LY.heat = null; }
  H.data = null;
}

async function loadHistory() {
  const ids = [...H.devs];
  if (!ids.length) return toast('Pick at least one device');
  const [start, end] = rangeFor(H.range);
  if (end <= start) return toast('End must be after start');
  $('#hload').disabled = true;
  try {
    stopPlayback(); clearHistoryLayers();
    const extra = H.heat ? '&tol=2&max=20000' : '';
    const r = await api(`history?dev=${encodeURIComponent(ids.join(','))}&start=${start}&end=${end}${extra}`);
    H.data = { devices: r.devices, start, end };
    drawHistory();
    drawHistoryOut();
  } catch (e) { toast('History failed: ' + e.message); }
  $('#hload').disabled = false;
}

function drawHistory() {
  const all = [], heat = [];
  for (const [id, h] of Object.entries(H.data.devices)) {
    const col = (devById(id) || { color: '#03a9f4' }).color;
    const ll = h.points.map((p) => [p[1], p[2]]);
    all.push(...ll);
    if (!ll.length) continue;
    L.polyline(ll, { color: col, weight: 4, opacity: 0.85, lineJoin: 'round' }).addTo(LY.trails);
    L.circleMarker(ll[0], { radius: 6, color: '#fff', weight: 2, fillColor: '#43a047', fillOpacity: 1 }).bindTooltip('Start ' + fmtDT(h.points[0][0])).addTo(LY.trails);
    L.circleMarker(ll[ll.length - 1], { radius: 6, color: '#fff', weight: 2, fillColor: '#db4437', fillOpacity: 1 }).bindTooltip('End ' + fmtDT(h.points[h.points.length - 1][0])).addTo(LY.trails);
    if (H.points) h.points.slice(0, 2500).forEach((p) => L.circleMarker([p[1], p[2]], { radius: 3, color: col, weight: 1, fillOpacity: 0.9 }).bindTooltip(fmtDT(p[0])).addTo(LY.trails));
    if (H.heat) heat.push(...ll);
  }
  if (H.heat && heat.length && L.heatLayer) LY.heat = L.heatLayer(heat, { radius: 14, blur: 18, maxZoom: 17, minOpacity: 0.35 }).addTo(map);
  if (all.length) map.fitBounds(all, { padding: [50, 50], maxZoom: 17 });
}

function drawHistoryOut() {
  const rows = Object.entries(H.data.devices).map(([id, h]) => `<div class="kv"><span>${esc(devName(id))}</span><span>${esc(fmtDist(h.distance_m))} · ${h.count.toLocaleString()} pts</span></div>`).join('');
  const have = Object.values(H.data.devices).some((h) => h.points.length > 1);
  $('#hout').innerHTML = `<h3>Summary</h3>${rows || '<p class="muted">Nothing recorded in that range.</p>'}`;
  if (have) buildPlayback();
}

// playback
function buildPlayback() {
  const t0 = Math.min(...Object.values(H.data.devices).filter((h) => h.points.length).map((h) => h.points[0][0]));
  const t1 = Math.max(...Object.values(H.data.devices).filter((h) => h.points.length).map((h) => h.points[h.points.length - 1][0]));
  PB = { t0, t1, t: t0, playing: false, speed: 2, last: 0, raf: 0, marks: {}, prog: {} };
  $('#hpb').innerHTML = `<h3>Playback</h3><div class="pb"><button class="b sm" id="pbplay">▶</button>
    <input type="range" id="pbs" min="0" max="1000" value="0"><select id="pbsp" style="width:70px"><option>1</option><option selected>2</option><option>4</option><option>8</option></select>x</div>
    <div class="muted" id="pbt" style="margin-top:4px"></div>`;
  $('#pbplay').addEventListener('click', () => (PB.playing ? stopPlayback() : startPlayback()));
  $('#pbs').addEventListener('input', (e) => { PB.t = PB.t0 + (PB.t1 - PB.t0) * (e.target.value / 1000); pbDraw(); });
  $('#pbsp').addEventListener('change', (e) => { PB.speed = +e.target.value; });
  pbDraw();
}

function pbPos(pts, t) {
  if (!pts.length || t < pts[0][0]) return null;
  let lo = 0, hi = pts.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (pts[mid][0] <= t) lo = mid; else hi = mid - 1; }
  const a = pts[lo], b = pts[lo + 1];
  if (!b || b[0] - a[0] > 1800 || t >= b[0] + 0) return { ll: [a[1], a[2]], i: lo };
  const f = (t - a[0]) / (b[0] - a[0]);
  return { ll: [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f], i: lo };
}

function pbDraw() {
  if (!PB || !H.data) return;
  for (const [id, h] of Object.entries(H.data.devices)) {
    const col = (devById(id) || { color: '#03a9f4' }).color;
    const pos = pbPos(h.points, PB.t);
    if (!pos) continue;
    if (!PB.marks[id]) PB.marks[id] = L.circleMarker(pos.ll, { radius: 9, color: '#fff', weight: 3, fillColor: col, fillOpacity: 1 }).addTo(LY.pb);
    PB.marks[id].setLatLng(pos.ll).bindTooltip(esc(devName(id)), { permanent: false });
    const line = h.points.slice(0, pos.i + 1).map((p) => [p[1], p[2]]).concat([pos.ll]);
    if (!PB.prog[id]) PB.prog[id] = L.polyline(line, { color: col, weight: 7, opacity: 0.95 }).addTo(LY.pb);
    else PB.prog[id].setLatLngs(line);
  }
  $('#pbs') && ($('#pbs').value = Math.round(((PB.t - PB.t0) / Math.max(1, PB.t1 - PB.t0)) * 1000));
  $('#pbt') && ($('#pbt').textContent = fmtDT(PB.t));
}

function startPlayback() {
  if (!PB) return;
  if (PB.t >= PB.t1) PB.t = PB.t0;
  PB.playing = true; PB.last = performance.now();
  $('#pbplay') && ($('#pbplay').textContent = '⏸');
  const step = (now) => {
    if (!PB || !PB.playing) return;
    const dt = (now - PB.last) / 1000; PB.last = now;
    PB.t += ((PB.t1 - PB.t0) / 60) * PB.speed * dt;   // whole range plays in 60 s at 1x
    if (PB.t >= PB.t1) { PB.t = PB.t1; pbDraw(); stopPlayback(); return; }
    pbDraw();
    PB.raf = requestAnimationFrame(step);
  };
  PB.raf = requestAnimationFrame(step);
}
function stopPlayback() {
  if (!PB) return;
  PB.playing = false; cancelAnimationFrame(PB.raf);
  $('#pbplay') && ($('#pbplay').textContent = '▶');
}

// ---- Timeline
const TLs = { dev: null, date: dateStr(Date.now()) };
function renderTimeline() {
  if (!TLs.dev || !devById(TLs.dev)) TLs.dev = S.sel || (S.devices[0] && S.devices[0].id);
  panel().innerHTML = `<h3>Day timeline</h3><div class="row"><label>Device<select id="tdev">${S.devices.map((d) => `<option value="${esc(d.id)}" ${d.id === TLs.dev ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select></label>
    <label>Day<input type="date" id="tdate" value="${TLs.date}"></label></div>
    <div class="row"><button class="b sec sm" id="tprev">◀ Prev</button><button class="b sec sm" id="tnext">Next ▶</button><button class="b" id="tload">Show</button></div><div id="tout"></div>`;
  $('#tdev').addEventListener('change', (e) => { TLs.dev = e.target.value; });
  $('#tdate').addEventListener('change', (e) => { TLs.date = e.target.value; });
  const shift = (n) => { const d = new Date(TLs.date + 'T12:00:00'); d.setDate(d.getDate() + n); TLs.date = dateStr(d); $('#tdate').value = TLs.date; loadTimeline(); };
  $('#tprev').addEventListener('click', () => shift(-1));
  $('#tnext').addEventListener('click', () => shift(1));
  $('#tload').addEventListener('click', loadTimeline);
  if (TLs.dev) loadTimeline();
}

async function loadTimeline() {
  if (!TLs.dev) return;
  const d0 = new Date(TLs.date + 'T00:00:00'), start = Math.floor(d0 / 1000), end = start + 86400 - 1;
  try {
    const tl = await api(`timeline?dev=${encodeURIComponent(TLs.dev)}&start=${start}&end=${end}`);
    LY.stops.clearLayers(); LY.tmp.clearLayers();
    const items = [...tl.stays.map((s) => ({ k: 'stay', t: s.start, s })), ...tl.trips.map((t) => ({ k: 'trip', t: t.start, t2: t }))].sort((a, b) => a.t - b.t);
    const col = (devById(TLs.dev) || { color: '#03a9f4' }).color;
    tl.stays.forEach((s, i) => L.marker([s.lat, s.lon], { icon: L.divIcon({ className: '', iconSize: [24, 24], iconAnchor: [12, 12],
      html: `<div class="av" style="--c:${esc(col)};width:24px;height:24px;font-size:11px">${i + 1}</div>` }) }).bindTooltip(esc(s.place || 'Stop')).addTo(LY.stops));
    let n = 0;
    const rows = items.map((it) => {
      if (it.k === 'stay') {
        n++; const s = it.s;
        return `<div class="tl" data-stay="${s.lat},${s.lon}"><div class="dot"></div><div class="main"><b>${n}. ${esc(s.place || 'Unknown stop')}</b>
          <div class="time">${fmtTime(s.start)} – ${fmtTime(s.end)} · ${fmtDur(s.duration)}</div>
          ${s.place ? '' : `<button class="b sm sec" data-save="${s.lat},${s.lon}">Save as place</button>`}</div></div>`;
      }
      const t = it.t2;
      return `<div class="tl trip" data-trip="${t.start},${t.end}"><div class="dot"></div><div class="main"><b>${esc(t.from || 'Start')} → ${esc(t.to || 'End')}</b>
        <div class="time">${fmtTime(t.start)} – ${fmtTime(t.end)} · ${esc(fmtDist(t.distance_m))} · ${fmtDur(t.duration)} · avg ${esc(fmtSpeed(t.avg_mph))}</div></div></div>`;
    }).join('');
    const sm = tl.summary;
    $('#tout').innerHTML = `<h3>Summary</h3><div class="kv"><span>Distance</span><span>${esc(fmtDist(sm.distance_m))}</span></div>
      <div class="kv"><span>Time moving</span><span>${fmtDur(sm.moving_s)}</span></div><div class="kv"><span>Stops</span><span>${sm.stops}</span></div>
      <div class="kv"><span>Points</span><span>${sm.points.toLocaleString()}</span></div><h3>Timeline</h3>${rows || '<p class="muted">No stops or trips found for this day.</p>'}`;
    $$('[data-stay]', panel()).forEach((el) => el.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const [la, lo] = el.dataset.stay.split(',').map(Number); map.flyTo([la, lo], 17, { duration: 0.5 });
      if (innerWidth < 900) $('#sheet').classList.add('min');
    }));
    $$('[data-trip]', panel()).forEach((el) => el.addEventListener('click', async () => {
      const [a, b] = el.dataset.trip.split(',').map(Number);
      const h = await api(`history?dev=${encodeURIComponent(TLs.dev)}&start=${a}&end=${b}&tol=2`);
      const ll = ((h.devices[TLs.dev] || {}).points || []).map((p) => [p[1], p[2]]);
      LY.tmp.clearLayers();
      if (ll.length > 1) { const pl = L.polyline(ll, { color: '#ff9800', weight: 6, opacity: 0.95 }).addTo(LY.tmp); map.fitBounds(pl.getBounds(), { padding: [60, 60] }); }
      if (innerWidth < 900) $('#sheet').classList.add('min');
    }));
    $$('[data-save]', panel()).forEach((el) => el.addEventListener('click', (e) => {
      e.stopPropagation();
      const [la, lo] = el.dataset.save.split(',').map(Number);
      showTab('places'); openPlaceForm({ lat: la, lon: lo, radius: 100 });
    }));
    const b = [...tl.stays.map((s) => [s.lat, s.lon])];
    if (b.length) map.fitBounds(b, { padding: [60, 60], maxZoom: 16 });
  } catch (e) { toast('Timeline failed: ' + e.message); }
}

// ---- Places
let addingPlace = false;
function renderPlacesTab() {
  const custom = S.places.filter((p) => p.source === 'custom');
  const zones = S.places.filter((p) => p.source !== 'custom');
  panel().innerHTML = `<div class="row"><button class="b" id="padd">+ Add place</button></div><div id="pform"></div>
    <h3>My places</h3>${custom.map((p) => `<div class="card"><div class="top">${avatarHTML(p)}<b>${esc(p.name)}</b>
       <button class="b sm sec" data-pe="${esc(p.id)}">Edit</button><button class="b sm danger" data-pd="${esc(p.id)}">✕</button></div>
       <div class="muted">${esc(fmtDist(p.radius))} radius</div></div>`).join('') || '<p class="muted">None yet. Tap “Add place”, then tap the map.</p>'}
    <h3>Home Assistant zones</h3>${zones.map((p) => `<div class="kv"><span>${esc(p.name)}</span><span class="muted">${esc(fmtDist(p.radius))}</span></div>`).join('') || '<p class="muted">No zones found.</p>'}
    <h3>Time at places</h3><div class="row"><label>Device<select id="psd">${S.devices.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select></label>
    <label>Period<select id="psdays"><option value="7">7 days</option><option value="30" selected>30 days</option><option value="90">90 days</option></select></label></div>
    <div class="row"><button class="b sec" id="psgo">Calculate</button></div><div id="psout"></div>`;
  $('#padd').addEventListener('click', () => { addingPlace = true; $('#sheet').classList.add('min'); toast('Tap the map where the place is'); });
  $$('[data-pe]', panel()).forEach((b) => b.addEventListener('click', () => openPlaceForm(S.places.find((p) => p.id === b.dataset.pe))));
  $$('[data-pd]', panel()).forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Delete this place?')) return;
    await api('places/' + encodeURIComponent(b.dataset.pd), { method: 'DELETE' }); await refresh(); renderPlacesTab();
  }));
  $('#psgo').addEventListener('click', async () => {
    const dev = $('#psd').value; if (!dev) return;
    const r = await api(`places/stats?dev=${encodeURIComponent(dev)}&days=${$('#psdays').value}`);
    const max = Math.max(1, ...r.map((x) => x.seconds));
    $('#psout').innerHTML = r.map((x) => `<div class="kv"><span>${esc(x.name)}</span><span>${fmtDur(x.seconds)} · ${x.visits} visit${x.visits === 1 ? '' : 's'}</span></div>
      <div class="bar"><i style="width:${(x.seconds / max) * 100}%"></i></div>`).join('') || '<p class="muted">No visits to saved places in that period.</p>';
  });
}

map.on('click', (e) => {
  if (MODE !== 'admin' || !addingPlace) return;
  addingPlace = false;
  $('#sheet').classList.remove('min');
  showTab('places');
  openPlaceForm({ lat: e.latlng.lat, lon: e.latlng.lng, radius: 100 });
});

function openPlaceForm(p) {
  const el = $('#pform'); if (!el) return;
  const isNew = !p.id;
  el.innerHTML = `<div class="card"><h3 style="margin-top:0">${isNew ? 'New place' : 'Edit place'}</h3>
    <div class="row"><label>Name<input type="text" id="fn" value="${esc(p.name || '')}" maxlength="60"></label></div>
    <div class="row"><label>Radius: <span id="frv">${esc(fmtDist(p.radius || 100))}</span><input type="range" id="fr" min="25" max="1000" step="5" value="${p.radius || 100}"></label></div>
    <div class="row"><label>Icon (emoji, mdi:name, local:name)<input type="text" id="fi" value="${esc(p.icon || '')}"></label>
      <button class="b sm sec" id="fpick">Pick</button>
      <label style="flex:0 0 70px">Color<input type="color" id="fc" value="${esc(p.color || '#ff9800')}"></label></div>
    <div class="row"><button class="b" id="fs">Save</button><button class="b sec" id="fx">Cancel</button></div></div>`;
  $('#fpick').addEventListener('click', () => openPicker((spec) => { $('#fi').value = spec; }));
  const draw = () => {
    LY.tmp.clearLayers();
    L.circle([p.lat, p.lon], { radius: +$('#fr').value, color: $('#fc').value, weight: 2, fillOpacity: 0.2 }).addTo(LY.tmp);
  };
  draw(); map.flyTo([p.lat, p.lon], Math.max(map.getZoom(), 16), { duration: 0.4 });
  $('#fr').addEventListener('input', () => { $('#frv').textContent = fmtDist(+$('#fr').value); draw(); });
  $('#fc').addEventListener('input', draw);
  $('#fx').addEventListener('click', () => { LY.tmp.clearLayers(); el.innerHTML = ''; });
  $('#fs').addEventListener('click', async () => {
    const name = $('#fn').value.trim(); if (!name) return toast('Give it a name');
    try {
      await api('places', { json: { id: p.id, name, lat: p.lat, lon: p.lon, radius: +$('#fr').value, icon: $('#fi').value.trim(), color: $('#fc').value } });
      LY.tmp.clearLayers(); await refresh(); renderPlacesTab(); toast('Place saved');
    } catch (e) { toast(e.message); }
  });
}

let ICONS = null;

// ---- Settings
async function renderSettings() {
  panel().innerHTML = '<p class="muted">Loading…</p>';
  let st, cands, stats, icons;
  try {
    [st, cands, stats, icons] = await Promise.all([api('settings'), api('candidates'), api('stats'), api('icons')]);
  } catch (e) { panel().innerHTML = `<p class="muted">Could not load settings: ${esc(e.message)}</p>`; return; }
  SETTINGS = st; ICONS = icons;
  const devs = Object.entries(st.devices);
  const host = location.hostname;
  const pubUrl = `http://${host}:${stats.public_port}/`;
  const ingUrl = `http://${host}:${stats.public_port}/api/ingest`;
  const opt = (v, cur, label) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${label}</option>`;
  panel().innerHTML = `
  <details open><summary>Tracked devices</summary><div>
    <p class="muted">${cands.ha ? 'Pick which Home Assistant people / trackers to record.' : 'Home Assistant is not connected yet.'}</p>
    ${cands.candidates.map((c) => `<div class="row inline"><label><input type="checkbox" data-track="${esc(c.id)}" data-fname="${esc(c.name)}" ${c.tracked ? 'checked' : ''}> ${esc(c.name)} <span class="muted">${esc(c.id)}</span></label></div>`).join('') || '<p class="muted">No people or device trackers with GPS found.</p>'}
    <h3>Appearance</h3>
    ${devs.map(([id, d]) => { const lv = devById(id) || {}; return `<div class="card" data-dev="${esc(id)}"><div class="top">${avatarHTML({ ...d, icon: lv.icon != null ? lv.icon : d.icon })}<b>${esc(d.name)}</b>
        ${id.startsWith('ext.') ? '<span class="muted">external</span>' : ''}<button class="b sm danger" data-del="${esc(id)}">Delete</button></div>
      <div class="row"><label>Name<input type="text" data-f="name" value="${esc(d.name)}"></label><label style="flex:0 0 60px">Color<input type="color" data-f="color" value="${esc(d.color)}"></label></div>
      <div class="row"><label>Icon (blank = use Home Assistant’s)<input type="text" data-f="icon" value="${esc(d.icon || '')}" placeholder="Auto: ${esc(iconSourceText(lv))}"></label>
        <button class="b sm sec" data-pick="${esc(id)}">Pick</button>
        <span class="b sec sm" style="text-align:center;position:relative">Upload picture<input type="file" accept="image/*" data-pic="${esc(id)}" style="position:absolute;inset:0;opacity:0"></span></div>
      <div class="muted">Showing: ${esc(iconSourceText(lv))}</div>
      <div class="row inline"><label><input type="checkbox" data-f="enabled" ${d.enabled ? 'checked' : ''}> Recording</label></div></div>`; }).join('')}
    <div class="row"><label>Add external device (for imports / other apps)<input type="text" id="newext" placeholder="e.g. Old Pixel"></label><button class="b sec" id="addext">Add</button></div>
  </div></details>

  <details><summary>Display</summary><div>
    <div class="row"><label>Units<select id="s_units">${opt('mi', st.units, 'Miles')}${opt('km', st.units, 'Kilometers')}</select></label>
      <label>Theme<select id="s_theme">${opt('auto', st.theme, 'Dark after sunset')}${opt('system', st.theme, 'Follow device')}${opt('light', st.theme, 'Always light')}${opt('dark', st.theme, 'Always dark')}</select></label></div>
    <div class="row"><label>Map tiles<select id="s_tiles">${opt('osm', st.tiles, 'OpenStreetMap (inverted at night)')}${opt('carto', st.tiles, 'CARTO (light / dark)')}${opt('custom', st.tiles, 'Custom URL')}</select></label></div>
    <div class="row" id="s_turl_row" ${st.tiles === 'custom' ? '' : 'hidden'}><label>Tile URL<input type="text" id="s_turl" value="${esc(st.tile_url)}" placeholder="https://…/{z}/{x}/{y}.png"></label></div>
    <div class="row"><label>Custom icons folder (inside your HA config)<input type="text" id="s_icons" value="${esc(st.icons_folder)}"></label></div>
    <p class="muted">${icons.exists ? `Found ${icons.icons.length} SVG icon${icons.icons.length === 1 ? '' : 's'} in /config/${esc(icons.folder)} (use <code>local:name</code> or <code>custom:name</code>).` : `Folder /config/${esc(icons.folder)} does not exist.`}</p>
    <p class="muted">Icon sets available: ${icons.sets.map((x) => `<code>${esc(x.prefix)}:</code> ${x.count.toLocaleString()}`).join(' · ')}. Persons and zones automatically use the picture or icon Home Assistant uses for them.</p>
    <div class="row inline"><label><input type="checkbox" id="s_zones" ${st.use_ha_zones ? 'checked' : ''}> Show Home Assistant zones as places</label></div>
    <div class="row"><button class="b" id="s_save_display">Save</button></div>
  </div></details>

  <details><summary>Recording &amp; stops</summary><div>
    <div class="row"><label>Record when moved at least (m)<input type="number" id="r_dist" value="${st.record.min_distance_m}"></label>
      <label>Heartbeat while still (min)<input type="number" id="r_hb" value="${st.record.heartbeat_min}"></label></div>
    <div class="row"><label>Ignore fixes worse than (m)<input type="number" id="r_acc" value="${st.record.max_accuracy_m}"></label>
      <label>Keep history for (days, 0 = forever)<input type="number" id="r_ret" value="${st.record.retention_days}"></label></div>
    <div class="row"><label>A stop is at least (min)<input type="number" id="v_min" value="${st.visits.min_stay_min}"></label>
      <label>Stop radius (m)<input type="number" id="v_rad" value="${st.visits.radius_m}"></label></div>
    <div class="row"><button class="b" id="s_save_rec">Save</button></div>
  </div></details>

  <details><summary>Data: import, backfill, export</summary><div>
    <div class="kv"><span>Stored points</span><span>${stats.points.toLocaleString()}</span></div>
    <div class="kv"><span>Database size</span><span>${(stats.db_bytes / 1048576).toFixed(1)} MB</span></div>
    <h3>Import history file</h3>
    <p class="muted">Google Takeout (Records.json / Timeline.json), GPX, GeoJSON, OwnTracks .rec, CSV, or any JSON list with lat/lon/time.</p>
    <div class="row"><label>Import into<select id="i_dev"><option value="">New device…</option>${devs.map(([id, d]) => `<option value="${esc(id)}">${esc(d.name)}</option>`).join('')}</select></label>
      <label id="i_new_l">New device name<input type="text" id="i_new" placeholder="e.g. Jon old phone"></label></div>
    <div class="row inline"><label><input type="checkbox" id="i_thin" checked> Thin out redundant points</label></div>
    <div class="row"><input type="file" id="i_file"><button class="b" id="i_go">Import</button></div><div id="i_out" class="muted"></div>
    <h3>Backfill from Home Assistant history</h3>
    <p class="muted">Pulls past locations from the HA recorder for your tracked devices (HA keeps about 10 days by default).</p>
    <div class="row"><label>Days<input type="number" id="b_days" value="10" min="1" max="90"></label><button class="b sec" id="b_go">Backfill</button></div><div id="b_out" class="muted"></div>
    <h3>Export</h3>
    <div class="row"><label>Device<select id="e_dev">${devs.map(([id, d]) => `<option value="${esc(id)}">${esc(d.name)}</option>`).join('')}</select></label>
      <label>Format<select id="e_fmt"><option value="gpx">GPX</option><option value="csv">CSV</option><option value="json">JSON</option></select></label></div>
    <div class="row"><a class="b sec" id="e_go" href="#">Download</a></div>
    <h3>Delete data</h3>
    <div class="row"><label>Device<select id="p_dev">${devs.map(([id, d]) => `<option value="${esc(id)}">${esc(d.name)}</option>`).join('')}</select></label>
      <label>Older than (days)<input type="number" id="p_days" value="365"></label></div>
    <div class="row"><button class="b sec" id="p_old">Delete older</button><button class="b danger" id="p_all">Delete ALL for device</button></div>
  </div></details>

  <details><summary>Read-only map, ingest &amp; MQTT</summary><div>
    <h3>Read-only map (for dashboards)</h3>
    <div class="row inline"><label><input type="checkbox" id="pub_on" ${st.public.enabled ? 'checked' : ''}> Enabled</label></div>
    <div class="kv"><span>URL</span><code class="copy">${esc(pubUrl)}</code></div>
    <p class="muted">${stats.public_running ? 'Running.' : 'Not running.'} No settings button, no editing. Options: <code>?dev=person.jon,person.mj</code> <code>&amp;trail=24</code> <code>&amp;theme=dark</code> <code>&amp;chips=0</code> <code>&amp;follow=person.jon</code></p>
    <p class="muted">Dashboard card: <code>type: iframe</code> + <code>url: ${esc(pubUrl)}?trail=12</code>. If your dashboard is https, the iframe needs https too.</p>
    <h3>Ingest API (other apps)</h3>
    <div class="row inline"><label><input type="checkbox" id="ing_on" ${st.ingest.enabled ? 'checked' : ''}> Enabled</label></div>
    <div class="kv"><span>Endpoint</span><code class="copy">${esc(ingUrl)}</code></div>
    <div class="kv"><span>Token</span><code class="copy">${esc(st.ingest.token)}</code></div>
    <p class="muted">Send as <code>Authorization: Bearer TOKEN</code> or <code>?token=TOKEN</code>. Works with OwnTracks (HTTP mode), Overland, or plain JSON <code>{"device":"name","lat":1,"lon":2}</code>. Needs the read-only map above to be enabled.</p>
    <div class="row"><button class="b sec sm" id="ing_regen">New token</button></div>
    <h3>Publish to Home Assistant (MQTT)</h3>
    <div class="row inline"><label><input type="checkbox" id="mq_on" ${st.mqtt.enabled ? 'checked' : ''}> Publish “place” and “distance today” sensors</label></div>
    <p class="muted">Status: ${stats.mqtt.connected ? 'connected' : esc(stats.mqtt.error || (st.mqtt.enabled ? 'connecting…' : 'off'))}. Uses the Mosquitto app automatically. Leave the broker fields empty unless you use an external broker.</p>
    <div class="row"><label>Broker host<input type="text" id="mq_host" value="${esc(st.mqtt.host)}"></label><label style="flex:0 0 90px">Port<input type="number" id="mq_port" value="${st.mqtt.port}"></label></div>
    <div class="row"><label>Username<input type="text" id="mq_user" value="${esc(st.mqtt.username)}"></label><label>Password<input type="password" id="mq_pass" value="${esc(st.mqtt.password)}"></label></div>
    <div class="row"><button class="b" id="s_save_int">Save</button></div>
  </div></details>`;
  bindSettings(st, stats);
}

let saveTimer = null;
function bindSettings(st, stats) {
  const patchDev = (id, vals) => api('settings', { json: { devices: { [id]: vals } } }).then((s) => { SETTINGS = s; refresh(); });
  $$('[data-track]', panel()).forEach((c) => c.addEventListener('change', async () => {
    await patchDev(c.dataset.track, c.checked ? { enabled: true, name: SETTINGS.devices[c.dataset.track] ? undefined : c.dataset.fname } : { enabled: false });
    toast(c.checked ? 'Tracking on' : 'Tracking off'); renderSettings();
  }));
  $$('[data-dev]', panel()).forEach((card) => {
    const id = card.dataset.dev;
    $$('[data-f]', card).forEach((inp) => inp.addEventListener('change', () => {
      const f = inp.dataset.f; const v = inp.type === 'checkbox' ? inp.checked : inp.value;
      patchDev(id, { [f]: v }).then(() => toast('Saved'));
    }));
  });
  $$('[data-pick]', panel()).forEach((b) => b.addEventListener('click', () => openPicker(async (spec) => {
    await patchDev(b.dataset.pick, { icon: spec }); toast(spec ? 'Icon saved' : 'Using Home Assistant’s icon'); renderSettings();
  })));
  $$('[data-pic]', panel()).forEach((inp) => inp.addEventListener('change', async () => {
    const f = inp.files[0]; if (!f) return;
    try {
      const blob = await resizeImage(f, 128);
      const fd = new FormData(); fd.append('file', blob, 'p.png');
      const r = await api('picture', { method: 'POST', body: fd });
      await patchDev(inp.dataset.pic, { icon: 'pic:' + r.file }); toast('Picture saved'); renderSettings();
    } catch (e) { toast('Upload failed: ' + e.message); }
  }));
  $$('[data-del]', panel()).forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.del;
    if (!confirm(`Remove ${devName(id)} from the tracker?`)) return;
    const purge = confirm('Also delete its recorded history?\n\nOK = delete history too, Cancel = keep history.');
    await api(`devices/${encodeURIComponent(id)}?purge=${purge ? 1 : 0}`, { method: 'DELETE' });
    await refresh(); renderSettings();
  }));
  $('#addext').addEventListener('click', async () => {
    const n = $('#newext').value.trim(); if (!n) return;
    await api('devices', { json: { name: n } }); await refresh(); renderSettings();
  });

  $('#s_tiles').addEventListener('change', (e) => { $('#s_turl_row').hidden = e.target.value !== 'custom'; });
  $('#s_save_display').addEventListener('click', async () => {
    await api('settings', { json: { units: $('#s_units').value, theme: $('#s_theme').value, tiles: $('#s_tiles').value,
      tile_url: $('#s_turl').value, icons_folder: $('#s_icons').value, use_ha_zones: $('#s_zones').checked } });
    ICONS = null; await refresh(); toast('Saved'); renderSettings();
  });
  $('#s_save_rec').addEventListener('click', async () => {
    await api('settings', { json: { record: { min_distance_m: +$('#r_dist').value, heartbeat_min: +$('#r_hb').value,
      max_accuracy_m: +$('#r_acc').value, retention_days: +$('#r_ret').value }, visits: { min_stay_min: +$('#v_min').value, radius_m: +$('#v_rad').value } } });
    toast('Saved');
  });

  // import
  $('#i_dev').addEventListener('change', (e) => { $('#i_new_l').hidden = !!e.target.value; });
  $('#i_go').addEventListener('click', async () => {
    const f = $('#i_file').files[0]; if (!f) return toast('Choose a file first');
    const fd = new FormData();
    fd.append('device', $('#i_dev').value); fd.append('new_name', $('#i_new').value);
    fd.append('thin', $('#i_thin').checked ? '1' : '0'); fd.append('file', f, f.name);
    $('#i_go').disabled = true; $('#i_out').textContent = 'Uploading…';
    try {
      const r = await api('import', { method: 'POST', body: fd });
      await pollJob(r.job, $('#i_out'));
      await refresh();
    } catch (e) { $('#i_out').textContent = 'Failed: ' + e.message; }
    $('#i_go').disabled = false;
  });
  $('#b_go').addEventListener('click', async () => {
    const ents = Object.entries(SETTINGS.devices).filter(([id, d]) => d.enabled && !id.startsWith('ext.')).map(([id]) => id);
    $('#b_out').textContent = 'Starting…';
    try { const r = await api('backfill', { json: { entities: ents, days: +$('#b_days').value } }); await pollJob(r.job, $('#b_out')); }
    catch (e) { $('#b_out').textContent = 'Failed: ' + e.message; }
  });
  const exp = () => { $('#e_go').href = `api/export?dev=${encodeURIComponent($('#e_dev').value)}&fmt=${$('#e_fmt').value}`; };
  $('#e_dev').addEventListener('change', exp); $('#e_fmt').addEventListener('change', exp); exp();
  $('#p_old').addEventListener('click', async () => {
    if (!confirm('Delete this device’s points older than that many days?')) return;
    const r = await api('purge', { json: { dev: $('#p_dev').value, older_than_days: +$('#p_days').value } }); toast(`Deleted ${r.removed.toLocaleString()} points`);
  });
  $('#p_all').addEventListener('click', async () => {
    if (!confirm('Delete ALL recorded history for this device? This cannot be undone.')) return;
    const r = await api('purge', { json: { dev: $('#p_dev').value, all: true } }); toast(`Deleted ${r.removed.toLocaleString()} points`); renderSettings();
  });

  $$('.copy', panel()).forEach((c) => c.addEventListener('click', () => {
    try { navigator.clipboard.writeText(c.textContent); toast('Copied'); } catch (e) { /* ignore */ }
  }));
  $('#ing_regen').addEventListener('click', async () => {
    if (!confirm('Create a new token? Apps using the old one will stop working.')) return;
    await api('ingest/token', { method: 'POST' }); renderSettings();
  });
  $('#s_save_int').addEventListener('click', async () => {
    await api('settings', { json: { public: { enabled: $('#pub_on').checked }, ingest: { enabled: $('#ing_on').checked },
      mqtt: { enabled: $('#mq_on').checked, host: $('#mq_host').value, port: +$('#mq_port').value, username: $('#mq_user').value, password: $('#mq_pass').value } } });
    toast('Saved'); setTimeout(renderSettings, 1500);
  });
}

async function pollJob(id, out) {
  for (;;) {
    const j = await api('jobs/' + id);
    out.textContent = j.message + (j.status === 'running' ? ` (${Math.round(j.progress * 100)}%)` : '');
    if (j.status !== 'running') return j;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function resizeImage(file, size) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = c.height = size;
      const s = Math.min(img.width, img.height), sx = (img.width - s) / 2, sy = (img.height - s) / 2;
      c.getContext('2d').drawImage(img, sx, sy, s, s, 0, 0, size, size);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error('could not read image'))), 'image/png');
    };
    img.onerror = () => reject(new Error('not a valid image'));
    img.src = URL.createObjectURL(file);
  });
}

// ---- Icon picker
function iconSourceText(d) {
  switch (d && d.icon_source) {
    case 'custom': return 'your own icon';
    case 'ha-picture': return 'the picture Home Assistant uses';
    case 'ha-icon': return 'Home Assistant icon ' + (d.icon || '');
    default: return 'initials (Home Assistant has no picture or icon for it)';
  }
}

function closePicker() { const o = $('#picker'); if (o) o.remove(); }
function openPicker(onPick) {
  closePicker();
  const ov = document.createElement('div');
  ov.id = 'picker';
  ov.innerHTML = `<div class="pk"><div class="pkh"><b>Choose an icon</b><button class="b sm sec" id="pkx">Close</button></div>
    <div class="row"><label>Search<input type="text" id="pkq" placeholder="home, garage, car…" autocomplete="off"></label>
    <label style="flex:0 0 140px">Set<select id="pkset"><option value="">All sets</option></select></label></div>
    <div class="row"><button class="b sm sec" id="pkauto">Use Home Assistant’s (auto)</button></div>
    <div id="pkgrid" class="pkgrid"></div><div class="muted" id="pkmsg"></div></div>`;
  document.body.appendChild(ov);
  const color = S.dark ? 'ffffff' : '212121';
  const pick = (spec) => { closePicker(); onPick(spec); };
  $('#pkx').addEventListener('click', closePicker);
  ov.addEventListener('click', (e) => { if (e.target === ov) closePicker(); });
  $('#pkauto').addEventListener('click', () => pick(''));
  api('icons').then((i) => {
    $('#pkset').innerHTML += i.sets.map((x) => `<option value="${esc(x.prefix)}">${esc(x.prefix)} (${x.count.toLocaleString()})</option>`).join('');
  }).catch(() => {});
  let timer = null, seq = 0;
  const run = async () => {
    const my = ++seq;
    const q = $('#pkq').value.trim(), set = $('#pkset').value;
    try {
      const r = await api(`icons/search?q=${encodeURIComponent(q)}&set=${encodeURIComponent(set)}&limit=120`);
      if (my !== seq) return;
      $('#pkgrid').innerHTML = r.results.map((spec) => `<button class="pki" data-spec="${esc(spec)}" title="${esc(spec)}"><img loading="lazy" src="${esc(icoURL(spec, color))}" alt=""><span>${esc(spec.split(':').slice(1).join(':'))}</span></button>`).join('');
      $('#pkmsg').textContent = r.results.length ? (r.results.length >= 120 ? 'Showing the first 120. Type to narrow it down.' : '') : 'No icons match.';
      $$('.pki', $('#pkgrid')).forEach((b) => b.addEventListener('click', () => pick(b.dataset.spec)));
    } catch (e) { $('#pkmsg').textContent = 'Search failed: ' + e.message; }
  };
  $('#pkq').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 200); });
  $('#pkset').addEventListener('change', run);
  run();
  $('#pkq').focus();
}

// ------------------------------------------------------------------ boot
setupSheet();
refresh();
setInterval(refresh, 10000);
if (MODE === 'view' && Q.get('trail')) setInterval(loadViewTrails, 60000);
window.__mt = { S, map, refresh };   // handy for debugging in the browser console
})();
