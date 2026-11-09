/* Mapio — a touch-first browser map for in-car screens.
 * Free services: OSM / CARTO / Esri tiles, Nominatim search, OSRM routing. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem('mapio_' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem('mapio_' + k, JSON.stringify(v)); } catch (e) {} }
  };

  // ── Map + layers ────────────────────────────────────────────────
  var LAYERS = {
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 20, subdomains: 'abcd', attribution: '© OpenStreetMap, © CARTO' }),
    light: L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
      maxZoom: 20, subdomains: 'abcd', attribution: '© OpenStreetMap, © CARTO' }),
    osm: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© OpenStreetMap contributors' }),
    sat: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, attribution: 'Imagery © Esri' })
  };

  var view = store.get('view', { c: [41.7151, 44.8271], z: 13 }); // Tbilisi default
  var map = L.map('map', { zoomControl: false, center: view.c, zoom: view.z, tap: true });
  var current = null;

  function setLayer(name) {
    if (!LAYERS[name]) name = 'dark';
    if (current) map.removeLayer(LAYERS[current]);
    LAYERS[name].addTo(map);
    current = name;
    store.set('layer', name);
    document.querySelectorAll('#layer-menu .opt').forEach(function (b) {
      b.classList.toggle('on', b.dataset.layer === name);
    });
  }
  setLayer(store.get('layer', 'dark'));

  map.on('moveend', function () {
    var c = map.getCenter();
    store.set('view', { c: [c.lat, c.lng], z: map.getZoom() });
  });

  $('zoom-in').onclick = function () { map.zoomIn(); };
  $('zoom-out').onclick = function () { map.zoomOut(); };
  $('layers').onclick = function () { $('layer-menu').hidden = !$('layer-menu').hidden; };
  $('layer-menu').onclick = function (e) {
    var l = e.target.dataset && e.target.dataset.layer;
    if (l) { setLayer(l); $('layer-menu').hidden = true; }
  };

  var toastTimer;
  function toast(msg) {
    $('toast').textContent = msg; $('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { $('toast').hidden = true; }, 3500);
  }

  // ── Geolocation / follow ───────────────────────────────────────
  var me = null, accCircle = null, follow = false, lastPos = null, watchId = null;
  var meIcon = L.divIcon({ className: '', html: '<div class="me"></div>', iconSize: [30, 30], iconAnchor: [15, 15] });

  function onPos(p) {
    var ll = [p.coords.latitude, p.coords.longitude];
    lastPos = ll;
    if (!me) {
      me = L.marker(ll, { icon: meIcon, interactive: false, zIndexOffset: 1000 }).addTo(map);
      accCircle = L.circle(ll, { radius: p.coords.accuracy, color: '#3e8bff', weight: 1, fillOpacity: .08 }).addTo(map);
    } else {
      me.setLatLng(ll); accCircle.setLatLng(ll).setRadius(p.coords.accuracy);
    }
    var s = p.coords.speed;
    $('speed-val').textContent = (s == null || isNaN(s)) ? '–' : Math.round(s * 3.6);
    if (follow) map.setView(ll, Math.max(map.getZoom(), 16), { animate: true });
    if (route) updateNext(ll);
  }
  function onErr(e) {
    toast(e.code === 1 ? 'Location permission denied' : 'Location unavailable');
    setFollow(false);
  }
  function startWatch() {
    if (watchId != null || !navigator.geolocation) return;
    watchId = navigator.geolocation.watchPosition(onPos, onErr, { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 });
  }
  function setFollow(on) {
    follow = on;
    $('locate').classList.toggle('on', on);
    if (on) { startWatch(); if (lastPos) map.setView(lastPos, Math.max(map.getZoom(), 16)); }
  }
  $('locate').onclick = function () {
    if (!navigator.geolocation) return toast('Geolocation not supported');
    setFollow(!follow);
  };
  map.on('dragstart', function () { if (follow) setFollow(false); });
  startWatch();

  // ── Search (Nominatim) ─────────────────────────────────────────
  var q = $('q'), results = $('results'), searchTimer, searchSeq = 0;

  function search(text) {
    var seq = ++searchSeq;
    var c = map.getCenter();
    var url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=0&limit=8' +
      '&q=' + encodeURIComponent(text) +
      '&viewbox=' + [c.lng - 1, c.lat + 1, c.lng + 1, c.lat - 1].join(',');
    fetch(url, { headers: { 'Accept-Language': navigator.language || 'en' } })
      .then(function (r) { return r.json(); })
      .then(function (list) {
        if (seq !== searchSeq) return;
        results.innerHTML = '';
        if (!list.length) { results.innerHTML = '<li>No results</li>'; results.hidden = false; return; }
        list.forEach(function (it) {
          var li = document.createElement('li');
          var parts = it.display_name.split(', ');
          li.textContent = it.name || parts[0];
          var sm = document.createElement('small');
          sm.textContent = parts.slice(it.name ? 0 : 1).join(', ');
          li.appendChild(sm);
          li.onclick = function () { pick([+it.lat, +it.lon], li.firstChild.textContent, sm.textContent); };
          results.appendChild(li);
        });
        results.hidden = false;
      })
      .catch(function () { if (seq === searchSeq) toast('Search failed'); });
  }
  q.addEventListener('input', function () {
    clearTimeout(searchTimer);
    var t = q.value.trim();
    if (t.length < 3) { results.hidden = true; return; }
    searchTimer = setTimeout(function () { search(t); }, 450); // Nominatim: ≤1 req/s
  });
  $('search-form').onsubmit = function (e) { e.preventDefault(); var t = q.value.trim(); if (t) search(t); q.blur(); };
  $('clear-q').onclick = function () { q.value = ''; results.hidden = true; };

  // ── Selected place ─────────────────────────────────────────────
  var pinIcon = L.divIcon({ className: '', html: '<div class="pin"></div>', iconSize: [26, 26], iconAnchor: [13, 30] });
  var pin = null, picked = null;

  function pick(ll, name, addr) {
    results.hidden = true; q.blur();
    picked = { ll: ll, name: name };
    if (pin) pin.setLatLng(ll); else pin = L.marker(ll, { icon: pinIcon }).addTo(map);
    setFollow(false);
    map.setView(ll, 16);
    $('pl-name').textContent = name;
    $('pl-addr').textContent = addr || (ll[0].toFixed(5) + ', ' + ll[1].toFixed(5));
    $('place').hidden = false;
  }
  function clearPlace() { $('place').hidden = true; if (pin && !route) { map.removeLayer(pin); pin = null; } }
  $('pl-close').onclick = clearPlace;

  // Long-press (or right click) on the map drops a pin.
  map.on('contextmenu', function (e) {
    var ll = [e.latlng.lat, e.latlng.lng];
    pick(ll, 'Dropped pin', '');
    fetch('https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=' + ll[0] + '&lon=' + ll[1])
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.display_name && picked && picked.ll === ll) $('pl-addr').textContent = d.display_name; })
      .catch(function () {});
  });

  // ── Routing (OSRM) ─────────────────────────────────────────────
  var route = null, routeLine = null;

  function fmtDist(m) { return m >= 1000 ? (m / 1000).toFixed(m >= 10000 ? 0 : 1) + ' km' : Math.round(m / 10) * 10 + ' m'; }
  function fmtTime(s) { var h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? h + ' h ' + m + ' min' : m + ' min'; }
  function stepText(st) {
    var m = st.maneuver, road = st.name || st.ref || '';
    var mod = m.modifier ? m.modifier.replace('slight ', 'slightly ').replace('sharp ', 'sharp ') : '';
    var t;
    switch (m.type) {
      case 'depart': t = 'Head out'; break;
      case 'arrive': return 'Arrive at destination';
      case 'roundabout': case 'rotary': t = 'At the roundabout take exit ' + (m.exit || ''); break;
      case 'merge': t = 'Merge ' + mod; break;
      case 'on ramp': t = 'Take the ramp ' + mod; break;
      case 'off ramp': t = 'Take the exit ' + mod; break;
      case 'fork': t = 'Keep ' + mod; break;
      case 'end of road': t = 'At the end of the road turn ' + mod; break;
      case 'continue': case 'new name': t = mod === 'straight' || !mod ? 'Continue' : 'Continue ' + mod; break;
      default: t = mod === 'straight' ? 'Go straight' : mod === 'uturn' ? 'Make a U-turn' : 'Turn ' + mod;
    }
    return road ? t + ' onto ' + road : t;
  }

  function startRoute() {
    if (!picked) return;
    if (!lastPos) { toast('Waiting for your location…'); setFollow(true); return; }
    var dest = picked;
    var url = 'https://router.project-osrm.org/route/v1/driving/' +
      lastPos[1] + ',' + lastPos[0] + ';' + dest.ll[1] + ',' + dest.ll[0] +
      '?overview=full&geometries=geojson&steps=true';
    toast('Calculating route…');
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (d.code !== 'Ok' || !d.routes.length) throw new Error('no route');
      var r = d.routes[0];
      var steps = [];
      r.legs.forEach(function (leg) { leg.steps.forEach(function (s) { steps.push(s); }); });
      route = { dest: dest, steps: steps };
      if (routeLine) map.removeLayer(routeLine);
      var coords = r.geometry.coordinates.map(function (c) { return [c[1], c[0]]; });
      routeLine = L.layerGroup([
        L.polyline(coords, { color: '#0b3d91', weight: 12, opacity: .9 }),
        L.polyline(coords, { color: '#3e8bff', weight: 7 })
      ]).addTo(map);
      map.fitBounds(L.latLngBounds(coords), { paddingTopLeft: [420, 100], paddingBottomRight: [120, 100] });

      $('rp-dest').textContent = dest.name;
      $('rp-sum').textContent = fmtTime(r.duration) + ' · ' + fmtDist(r.distance);
      var ol = $('rp-steps'); ol.innerHTML = '';
      steps.forEach(function (s) {
        var li = document.createElement('li');
        li.textContent = stepText(s);
        var sp = document.createElement('span'); sp.textContent = s.distance ? fmtDist(s.distance) : '';
        li.appendChild(sp); ol.appendChild(li);
      });
      $('place').hidden = true;
      $('route-panel').hidden = false;
      document.body.classList.add('routing');
      $('toast').hidden = true;
      updateNext(lastPos);
    }).catch(function () { toast('Could not find a route'); });
  }

  // Next maneuver = first upcoming step whose start point is still ahead (closest-ahead heuristic).
  function updateNext(ll) {
    if (!route) return;
    var here = L.latLng(ll), best = 0, bestD = Infinity;
    route.steps.forEach(function (s, i) {
      var p = s.maneuver.location, d = here.distanceTo([p[1], p[0]]);
      if (d < bestD) { bestD = d; best = i; }
    });
    var next = route.steps[Math.min(best + (bestD < 25 ? 1 : 0), route.steps.length - 1)];
    var np = next.maneuver.location;
    var dist = here.distanceTo([np[1], np[0]]);
    $('rp-next').textContent = (next.maneuver.type === 'arrive' && dist < 30) ? 'You have arrived' : 'In ' + fmtDist(dist) + ': ' + stepText(next);
  }

  function endRoute() {
    route = null;
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    if (pin) { map.removeLayer(pin); pin = null; }
    $('route-panel').hidden = true;
    document.body.classList.remove('routing');
  }
  $('pl-go').onclick = startRoute;
  $('rp-close').onclick = endRoute;

  // Keep the map from going stale after the car screen wakes up.
  document.addEventListener('visibilitychange', function () { if (!document.hidden) map.invalidateSize(); });
  window.addEventListener('resize', function () { map.invalidateSize(); });
})();
