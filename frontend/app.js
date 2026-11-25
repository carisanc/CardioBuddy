(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var VENTANA = 60; // segundos en el gráfico
  var perfil = null, lecturas = [], ws = null, reintento = 1000, alertaId = null;

  // ---------- utilidades
  function guardarId(id) { try { localStorage.setItem('cb_perfil', String(id)); } catch (e) {} }
  function leerId() { try { return localStorage.getItem('cb_perfil'); } catch (e) { return null; } }
  function hora(ts) { return new Date(ts * 1000).toLocaleTimeString('es-EC', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  function api(url, opt) {
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok) throw b; return b;
      });
    });
  }
  var ETQ = { normal: ['ok', 'Normal'], atencion: ['warn', 'Atención'], critica: ['bad', 'Crítica'] };
  function chip(id, estado) {
    var e = $(id), v = ETQ[estado] || ['info', 'Sin datos'];
    e.className = 'chip ' + v[0]; e.textContent = v[1];
  }

  // ---------- navegación
  function show(v) {
    ['welcome', 'form', 'dash'].forEach(function (n) { $('v-' + n).hidden = n !== v; });
    if (v !== 'dash') cerrarWs();
    window.scrollTo(0, 0);
  }
  $('go-form').onclick = function () {
    if (perfil) {
      $('f-name').value = perfil.nombre; $('f-gender').value = perfil.genero;
      $('f-age').value = perfil.edad; $('f-mail').value = perfil.correo; $('f-dev').value = perfil.dispositivo;
    }
    show('form');
  };
  $('go-dash').onclick = function () { abrirPanel(); };
  $('back-welcome').onclick = function () { show('welcome'); };
  $('edit').onclick = $('go-form').onclick;

  // ---------- formulario
  $('child-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var d = {
      nombre: $('f-name').value.trim(), genero: $('f-gender').value,
      edad: $('f-age').value === '' ? NaN : Number($('f-age').value),
      correo: $('f-mail').value.trim(), dispositivo: $('f-dev').value.trim()
    };
    var err = '';
    if (!d.nombre || !d.genero || !d.dispositivo) err = 'Completa el nombre, el género y el código del dispositivo.';
    else if (!(d.edad >= 0 && d.edad <= 17 && Number.isInteger(d.edad))) err = 'La edad debe ser un número entero entre 0 y 17.';
    else if (!/^\S+@\S+\.\S+$/.test(d.correo)) err = 'Escribe un correo válido, por ejemplo padre@correo.com.';
    else if (!$('f-ok').checked) err = 'Marca la casilla de consentimiento para continuar.';
    $('f-err').textContent = err;
    if (err) return;
    $('f-submit').disabled = true;
    api('/api/perfiles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) })
      .then(function (p) { perfil = p; guardarId(p.id); abrirPanel(); })
      .catch(function () { $('f-err').textContent = 'No se pudo guardar el perfil. Revisa los datos e inténtalo de nuevo.'; })
      .then(function () { $('f-submit').disabled = false; });
  });

  // ---------- panel
  function abrirPanel() {
    var id = perfil ? perfil.id : leerId();
    if (!id) { show('form'); return; }
    api('/api/perfiles/' + id + '/resumen?segundos=' + VENTANA).then(function (r) {
      perfil = r.perfil;
      $('d-title').textContent = 'Monitoreo de ' + perfil.nombre;
      $('d-sub').textContent = perfil.genero + ', ' + perfil.edad + (perfil.edad === 1 ? ' año' : ' años') + '. Las alertas llegan a ' + perfil.correo + '.';
      $('lim').textContent = perfil.fc_max; $('o2lim').textContent = perfil.spo2_min;
      $('dev-id').textContent = 'ESP32 · código ' + perfil.dispositivo;
      var y = escalaY(perfil.fc_max); $('thr').setAttribute('y1', y); $('thr').setAttribute('y2', y);
      lecturas = r.lecturas.map(function (l) { return { ts: l.ts, fc: l.fc }; });
      if (r.lecturas.length) mostrarLectura(r.lecturas[r.lecturas.length - 1], true);
      $('events').innerHTML = '';
      r.eventos.slice().reverse().forEach(agregarEvento);
      var pendiente = r.eventos.filter(function (e) { return e.tipo === 'alerta' && !e.reconocido; })[0];
      if (pendiente) mostrarAlerta(pendiente); else $('alert').hidden = true;
      conexion(r.en_linea);
      dibujar();
      show('dash');
      abrirWs();
    }).catch(function () { try { localStorage.removeItem('cb_perfil'); } catch (e) {} perfil = null; show('form'); });
  }

  function escalaY(v) { return (200 - (Math.min(200, Math.max(40, v)) - 40) / 160 * 200).toFixed(1); }
  function dibujar() {
    var ahora = Date.now() / 1000;
    lecturas = lecturas.filter(function (l) { return l.ts >= ahora - VENTANA; });
    // se corta la línea si faltan lecturas por más de 3 s (dispositivo desconectado)
    $('line').setAttribute('d', lecturas.map(function (l, i) {
      var corte = i === 0 || l.ts - lecturas[i - 1].ts > 3;
      return (corte ? 'M' : 'L') + ((l.ts - (ahora - VENTANA)) / VENTANA * 600).toFixed(1) + ' ' + escalaY(l.fc);
    }).join(' '));
    $('empty').hidden = lecturas.length > 0;
  }
  setInterval(function () { if (!$('v-dash').hidden) dibujar(); }, 1000);

  function mostrarLectura(l, historica) {
    $('hr').textContent = Math.round(l.fc);
    $('o2').textContent = l.spo2 == null ? '--' : Math.round(l.spo2) + '%';
    chip('hr-st', l.estado_fc); chip('o2-st', l.estado_spo2);
    if (l.fc > 0) $('heart').style.animationDuration = (60 / l.fc) + 's';
    if (!historica) { lecturas.push({ ts: l.ts, fc: l.fc }); dibujar(); }
  }

  function conexion(enLinea) {
    $('dot').classList.toggle('on', enLinea);
    $('heart').classList.toggle('on', enLinea);
    $('dev-st').textContent = enLinea ? 'Conectado y enviando lecturas' : 'Sin lecturas recientes';
    var b = $('badge');
    b.className = 'chip ' + (enLinea ? 'ok' : 'info');
    b.textContent = enLinea ? 'Datos en vivo' : 'Esperando dispositivo';
  }

  function agregarEvento(e) {
    var li = document.createElement('li');
    if (e.tipo !== 'alerta') li.className = 'info';
    li.textContent = hora(e.ts) + ' – ' + e.mensaje;
    $('events').prepend(li);
  }
  function mostrarAlerta(e) { alertaId = e.id; $('alert-msg').textContent = e.mensaje; $('alert').hidden = false; }
  $('alert-close').onclick = function () {
    $('alert').hidden = true;
    if (alertaId) api('/api/eventos/' + alertaId + '/reconocer', { method: 'POST' }).catch(function () {});
    alertaId = null;
  };

  // ---------- WebSocket en vivo
  function abrirWs() {
    cerrarWs();
    var url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/' + encodeURIComponent(perfil.dispositivo);
    var s = new WebSocket(url); ws = s;
    s.onopen = function () { reintento = 1000; };
    s.onmessage = function (m) {
      var d; try { d = JSON.parse(m.data); } catch (e) { return; }
      if (d.tipo === 'lectura') { mostrarLectura(d); conexion(true); }
      else if (d.tipo === 'estado') conexion(d.en_linea);
      else if (d.tipo === 'alerta') { mostrarAlerta(d); agregarEvento({ tipo: 'alerta', ts: d.ts, mensaje: d.mensaje }); }
    };
    s.onclose = function () {
      if (ws !== s) return; // se cerró a propósito
      conexion(false);
      setTimeout(function () { if (ws === s && !$('v-dash').hidden) abrirWs(); }, reintento);
      reintento = Math.min(reintento * 2, 15000);
    };
  }
  function cerrarWs() { var s = ws; ws = null; if (s) s.close(); }

  // ---------- inicio
  var guardado = leerId();
  if (guardado) {
    api('/api/perfiles/' + guardado).then(function (p) {
      perfil = p; $('go-dash').hidden = false;
      $('go-dash').textContent = 'Seguir con el monitoreo de ' + p.nombre;
    }).catch(function () {});
  }
})();
