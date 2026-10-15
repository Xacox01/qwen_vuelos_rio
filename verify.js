#!/usr/bin/env node
/* Arnés de regresión v2 — valida el nuevo dashboard OTA-plano contra data.json real.
   Uso: node verify.js <ruta-index.html> <ruta-data.json> */
const fs = require('fs');
const htmlPath = process.argv[2] || 'index.html';
const dataPath = process.argv[3] || 'data.json';
const html = fs.readFileSync(htmlPath, 'utf8');
const DATA = JSON.parse(fs.readFileSync(dataPath, 'utf8'));

const i0 = html.indexOf('createApp({');
const i1 = html.indexOf('}).mount(', i0);
if (i0 < 0 || i1 < 0) { console.error('FATAL: no se encuentra createApp/mount'); process.exit(2); }
const cfgSrc = html.slice(i0 + 'createApp('.length, i1 + 1);
global.location = { pathname: '/vuelos-rio-live/' };
// constantes globales del script del sitio (necesarias en el scope del eval)
const DIA = ['dom','lun','mar','mié','jue','vie','sáb'];
const MES = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
const MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'];
const SKL = (from,to,ida,vta) => `https://skiplagged.com/flights/${from}/${to}/${ida}${vta?'/'+vta:''}?sort=cost`;
let cfg;
try { cfg = eval('(' + cfgSrc + ')'); } catch (e) { console.error('FATAL: config no compila:', e.message); process.exit(2); }

const vm = Object.assign({}, typeof cfg.data === 'function' ? cfg.data() : cfg.data);
for (const [k, f] of Object.entries(cfg.methods || {})) vm[k] = f.bind(vm);
for (const [k, f] of Object.entries(cfg.computed || {})) Object.defineProperty(vm, k, { get: () => f.call(vm), configurable: true });
vm.D = DATA;

let fails = 0, checks = 0;
function ok(cond, msg, extra) { checks++; if (!cond) { fails++; console.log('  FAIL ' + msg + (extra !== undefined ? ' -> ' + JSON.stringify(extra) : '')); } else console.log('  ok   ' + msg); }
const money = n => typeof n === 'number' && isFinite(n) && n > 30000 && n < 5000000;
const fresh = ts => { if (!ts) return false; const t = Date.parse(String(ts).replace(' ', 'T') + 'Z'); return isFinite(t) && (Date.now() - t) < 26 * 3600e3; };

console.log('== 1. tiles (precios por fuente) ==');
const tiles = vm.tiles;
ok(Array.isArray(tiles) && tiles.length >= 5, 'tiles >= 5 fuentes', tiles.length);
ok(tiles.every(t => money(t.clp)), 'todos los clp validos', tiles.map(t => t.clp));
ok(tiles.every((t, i) => i === 0 || tiles[i - 1].clp <= t.clp), 'ordenado barato primero');
ok(tiles.every(t => (t.url || '').startsWith('http')), 'links http');
ok(tiles.every(t => typeof t.fresco === 'boolean'), 'flag fresco presente');
ok(vm.minimo === tiles[0].clp, 'minimo == tile mas barato');
const F = DATA.fuentes || {};
const stale = tiles.filter(t => (t.key === 'despegar' || t.key === 'falabella') && !fresh((F[t.key] || {}).ts));
ok(stale.length === 0, 'sin OTA con datos +24h', stale.map(t => t.key));
tiles.forEach(t => console.log('     ' + t.nombre + ': $' + Math.round(t.clp).toLocaleString('es-CL') + ' (' + t.ts + ')'));

console.log('== 2. ventanasTop + buscador ==');
const vw = vm.ventanasTop;
ok(Array.isArray(vw) && vw.length >= 1, 'ventanasTop >= 1', vw.length);
ok(vw.every(w => /^\d{4}-\d{2}-\d{2}$/.test(w.ida) && /^\d{4}-\d{2}-\d{2}$/.test(w.vuelta)), 'fechas ISO');
ok(vw.every(w => w.ida < w.vuelta), 'ida < vuelta');
ok(vw.every(w => money(w.clp)), 'precios de ventana validos');
ok((vm.sklLink(vw[0].ida, vw[0].vuelta) || '').includes('skiplagged.com/flights/SCL/GIG'), 'sklLink formato');

console.log('== 3. filas de vuelos ==');
const filas = vm.filas;
ok(Array.isArray(filas) && filas.length >= 10, 'filas >= 10', filas.length);
ok(filas.every(r => money(r.clp)), 'precios de filas validos');
ok(filas.every((r, i) => i === 0 || filas[i - 1].clp <= r.clp), 'filas ordenadas');
ok(filas.every(r => (r.link || '').startsWith('http')), 'links de fila http');
ok(filas.every(r => r.ida < r.vuelta), 'fechas coherentes en filas');
ok(new Set(filas.map(r => r.cod)).size >= 2, '>= 2 aerolineas distintas');
const fd = vm.filasFiltradas;
vm.filterMax = 350000;
ok(vm.filasFiltradas.every(r => r.clp <= 350000), 'filtro 350K funciona');
vm.filterMax = 0; vm.onlyDirect = true;
ok(vm.filasFiltradas.every(r => r.directo), 'filtro directos funciona');
vm.onlyDirect = false;

console.log('== 4. calendarios ==');
const cals = vm.calendarios;
ok(Array.isArray(cals) && cals.length === 4, '4 calendarios (2 meses x 2 sentidos)', cals.length);
ok(cals.every(c => c.cells.length >= 28 && c.cells.length <= 42), 'grids de dias completos', cals.map(c => c.cells.length));
const conPrecio = cals.flatMap(c => c.cells).filter(c => c && c.clp);
ok(conPrecio.length >= 30, '>= 30 dias con precio', conPrecio.length);
ok(conPrecio.every(c => money(c.clp)), 'precios de calendario validos');
ok(cals.every(c => c.cells.some(c2 => c2 && c2.cheap)), 'cada calendario tiene dia barato marcado');

console.log('== 5. destinos y ofertas ==');
const ds = vm.destinos;
ok(Array.isArray(ds) && ds.length >= 12, 'destinos >= 12', ds.length);
ok(ds.every(d => money(d.clp)), 'precios destinos validos');
ok(ds.every(d => (d.link || '').startsWith('http')), 'links destinos http');
ok(ds.every((d, i) => i === 0 || ds[i - 1].clp <= d.clp), 'destinos ordenados');
ok(ds.every(d => typeof d.enPresupuesto === 'boolean'), 'flag presupuesto');
ok(vm.destinosEnPresupuesto >= 6, '>= 6 destinos dentro de presupuesto', vm.destinosEnPresupuesto);
ok(vm.ofertas.length >= 5 && vm.ofertas.every(o => o.cod !== 'GIG'), 'ofertas excluyen Rio (card grande)');
ok(vm.ofertaRio && money(vm.ofertaRio.clp), 'ofertaRio presente');
ds.forEach(d => console.log('     ' + d.cod + ' ' + d.ciudad + ': $' + Math.round(d.clp).toLocaleString('es-CL') + (d.enPresupuesto ? '' : ' (sobre presupuesto)')));

console.log('== 6. cupones ==');
const cp = vm.cupones;
ok(Array.isArray(cp) && cp.length >= 10, 'cupones >= 10', cp.length);
ok(cp.every(c => c.id && c.merchant && c.title && (c.url || '').startsWith('http')), 'cupones con campos minimos');
ok(cp.some(c => c.code && /^[A-Za-z0-9\-_.]{3,30}$/.test(c.code)), 'hay al menos 1 codigo real', cp.filter(c => c.code).map(c => c.code));
ok(cp.some(c => c.verified), 'hay cupones verificados');
ok(vm.cuponesVisibles.length === cp.length, 'filtro "todas" muestra todo');
if (vm.merchants.length) { vm.cpF = vm.merchants[0]; ok(vm.cuponesVisibles.every(c => c.merchant === vm.merchants[0]), 'filtro por merchant funciona'); vm.cpF = 'todas'; }
console.log('     merchants: ' + vm.merchants.join(', '));
console.log('     codigos: ' + cp.filter(c => c.code).map(c => c.merchant + ':' + c.code).join(', '));

console.log('== 7. alertas + estado ==');
ok(Array.isArray(vm.alertas), 'alertas es array');
const est = vm.estado;
ok(Array.isArray(est) && est.length >= 6, 'estado >= 6 fuentes', est.length);
ok(est.every(s => typeof s.ok === 'boolean' && s.det), 'estado con det y ok');

console.log('== 8. utilidades y HTML ==');
ok(vm.fmt(337754) === '337.754', 'fmt separador miles CLP', vm.fmt(337754));
ok(vm.fmtK(337754) === '338', 'fmtK redondea a miles', vm.fmtK(337754));
ok(vm.dshort('2027-01-22').length > 5, 'dshort legible', vm.dshort('2027-01-22'));
ok(vm.ago(DATA.updated).includes('hace') || vm.ago(DATA.updated) === 'ahora', 'ago funciona', vm.ago(DATA.updated));
['assets/dest/GIG.jpg', 'assets/dest/FLN.jpg', 'assets/dest/CUN.jpg'].forEach(p => {
  const found = fs.existsSync(require('path').join(require('path').dirname(htmlPath), p));
  ok(found, 'asset existe: ' + p);
});
ok(html.includes('id="cupones"'), 'seccion cupones en HTML');
ok(html.includes('subUrl'), 'suscripcion email presente');
ok(!/linear-gradient\((?![0-9]+deg,rgba\(18,33,64)/.test(html.slice(0, html.indexOf('</style>'))), 'sin gradientes decorativos (diseno plano OTA)');
ok((html.match(/#0062e3/g) || []).length >= 3, 'color de marca azul presente');
ok(!html.includes('CyberMonday'), 'sin cupones caducados hardcodeados');

console.log('\n' + (checks - fails) + '/' + checks + ' checks OK' + (fails ? ' — ' + fails + ' FALLOS' : ''));
process.exit(fails ? 1 : 0);
