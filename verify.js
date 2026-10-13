#!/usr/bin/env node
/* Arnés de regresión: valida TODOS los módulos del dashboard contra data.json real.
   Uso: node verify.js <ruta-index.html> <ruta-data.json> */
const fs = require('fs');
const htmlPath = process.argv[2] || '/home/user/scraper/deploy-github/index.html';
const dataPath = process.argv[3] || '/home/user/scraper/deploy-github/data.json';
const html = fs.readFileSync(htmlPath, 'utf8');
const DATA = JSON.parse(fs.readFileSync(dataPath, 'utf8'));

// --- extraer config de la app Vue ---
const i0 = html.indexOf('createApp({');
const i1 = html.indexOf('}).mount(', i0);
if (i0 < 0 || i1 < 0) { console.error('FATAL: no se encuentra createApp/mount'); process.exit(2); }
const cfgSrc = html.slice(i0 + 'createApp('.length, i1 + 1);
global.location = { pathname: '/vuelos-rio-live/' };
const DIA=['dom','lun','mar','mié','jue','vie','sáb'];
const NOMS={JA:'JetSMART',H2:'SKY Airline',LA:'LATAM',LU:'LATAM',G3:'GOL',AD:'Azul',CM:'Copa Airlines',AV:'Avianca',AR:'Aerolíneas Argentinas',AM:'Aeroméxico',EK:'Emirates',TK:'Turkish Airlines',IB:'Iberia',DL:'Delta',UA:'United',AA:'American',AF:'Air France',KL:'KLM',LH:'Lufthansa'};
let cfg;
try { cfg = eval('(' + cfgSrc + ')'); } catch (e) { console.error('FATAL: config no compila:', e.message); process.exit(2); }

// --- emular instancia Vue (data + methods + computed como getters) ---
const vm = Object.assign({}, typeof cfg.data === 'function' ? cfg.data() : cfg.data);
for (const [k, f] of Object.entries(cfg.methods || {})) vm[k] = f.bind(vm);
for (const [k, f] of Object.entries(cfg.computed || {})) Object.defineProperty(vm, k, { get: () => f.call(vm), configurable: true });
vm.DATA = DATA; vm.SUBS = [];

let fails = 0, checks = 0;
function ok(cond, msg, extra) { checks++; if (!cond) { fails++; console.log('  FAIL ' + msg + (extra !== undefined ? ' -> ' + JSON.stringify(extra) : '')); } else console.log('  ok   ' + msg); }
const money = n => typeof n === 'number' && isFinite(n) && n > 30000 && n < 5000000;
const fresh = ts => { if (!ts) return false; const t = Date.parse(String(ts).replace(' ', 'T') + 'Z'); return isFinite(t) && (Date.now() - t) < 24 * 3600e3; };

console.log('== 0. Consolidado ==');
const consol = vm.consol || [];
ok(consol.length >= 3, 'consol tiene >=3 filas', consol.length);
ok(consol.every(r => money(r.pp)), 'todos los pp son montos validos', consol.map(r => r.pp));
ok(consol.every(r => (r.link || '').startsWith('http')), 'todos los links son http');
ok(consol.every((r, i) => i === 0 || consol[i - 1].pp <= r.pp), 'ordenado mas barato primero', consol.map(r => r.pp));
const f = DATA.fuentes || {};
const staleOTA = consol.filter(r => /Falabella|Despegar/.test(r.emp) && !fresh((f[/Falabella/.test(r.emp) ? 'falabella' : 'despegar'] || {}).ts));
ok(staleOTA.length === 0, 'sin OTA con datos de +24h en consolidado', staleOTA.map(r => r.emp));
const staleCochaSSR = consol.filter(r => r.emp === 'Cocha' && r.tipo && /desde publicado/.test(r.tipo) && !fresh((f.cocha || {}).ts));
ok(staleCochaSSR.length === 0, 'Cocha SSR sin dato fresco no entra al consolidado');
consol.forEach(r => console.log('     ' + r.emp + ': $' + Math.round(r.pp).toLocaleString('es-CL') + ' (' + r.ida + ' -> ' + r.vuelta + ')'));


console.log('== 0b. Portada: ranking compañías + mezcla + campeón ==');
vm.pax = 9;
const rt = vm.rtPorAerolinea || [];
ok(rt.length >= 3, 'rtPorAerolinea tiene >=3 compañías', rt.length);
ok(rt.every(r => money(r.clp)), 'rtPorAerolinea precios validos', rt.map(r => r.clp));
ok(rt.every((r, i) => i === 0 || rt[i - 1].clp <= r.clp), 'rtPorAerolinea ordenado');
ok(new Set(rt.map(r => r.cod)).size === rt.length, 'sin compañías duplicadas', rt.map(r => r.cod));
ok(rt.every(r => (r.link || '').startsWith('http')), 'rtPorAerolinea links http');
ok(rt.every(r => r.ida < r.vuelta), 'rtPorAerolinea fechas coherentes');
const ja = rt.find(r => r.cod === 'JA');
if (ja && (vm.combosPax || []).length) ok(ja.clp <= vm.combosPax[0].clp + 0.5, 'JetSMART ranking <= combo oficial pax');
const mx = vm.mezclas;
ok(mx !== null && mx !== undefined, 'mezcla entre compañías existe');
if (mx) {
  ok(money(mx.total), 'mezcla total valido', mx.total);
  ok(mx.total === mx.ida.clp + mx.vuelta.clp, 'mezcla total = ida+vuelta');
  ok(mx.ida.cod !== mx.vuelta.cod, 'mezcla usa compañías distintas', [mx.ida.cod, mx.vuelta.cod]);
  ok(mx.noches >= 9, 'mezcla >=9 noches', mx.noches);
  ok(mx.vuelta.fecha <= '2027-02-23', 'mezcla vuelta <= 23-feb');
  ok((mx.ida.link || '').startsWith('http') && (mx.vuelta.link || '').startsWith('http'), 'mezcla links http');
}
const camp = vm.campeon;
ok(camp !== null && camp !== undefined, 'campeon existe');
if (camp && rt.length && mx) {
  ok(camp.total === Math.min(rt[0].clp, mx.total), 'campeon = min(ranking, mezcla)', [camp.total, rt[0].clp, mx.total]);
  ok(camp.botones.length >= 1 && camp.botones.every(b => (b.href || '').startsWith('http')), 'campeon botones validos');
  ok(money(camp.total), 'campeon precio valido');
}

console.log('== 1. Combos JetSMART ==');
for (const pax of [1, 4, 5, 9]) {
  vm.pax = pax;
  const c = vm.combosPax || [];
  ok(c.length > 0, 'pax=' + pax + ': combos no vacio', c.length);
  ok(c.every(x => money(x.clp)), 'pax=' + pax + ': precios validos');
  ok(c.every((x, i) => i === 0 || c[i - 1].clp <= x.clp), 'pax=' + pax + ': ordenado');
  ok(c.every(x => x.noches >= 9), 'pax=' + pax + ': todas >=9 noches');
}
vm.pax = 9;

console.log('== 2/3. Idas y vueltas ==');
ok((vm.fI || []).length > 10, 'fI con filas', (vm.fI || []).length);
ok((vm.fV || []).length > 10, 'fV con filas', (vm.fV || []).length);
ok(vm.fI.every(x => money(x.clp)) && vm.fV.every(x => money(x.clp)), 'precios ida/vuelta validos');

console.log('== 4. SKY ==');
const skyP = (f.sky || {}).pairs || [];
ok(skyP.length > 0, 'sky.pairs no vacio', skyP.length);
ok(skyP.every((x, i) => i === 0 || skyP[i - 1].clp <= x.clp), 'sky pairs ordenado');
ok(skyP.every(x => money(x.clp)), 'sky precios validos');
ok(skyP.every(x => x.ida <= x.vuelta), 'sky fechas coherentes');

console.log('== 5. LATAM ==');
const latC = (f.latam || {}).combos || [];
ok(latC.length > 0, 'latam.combos no vacio', latC.length);
ok(latC.every((x, i) => i === 0 || latC[i - 1].clp <= x.clp), 'latam combos ordenado');
ok((vm.latRows || []).length > 0, 'latRows no vacio');

console.log('== 8. Cocha ==');
const co = f.cocha || {};
for (const v of co.ventanas || []) {
  ok(money(v.min_clp), 'cocha ventana ' + v.ida + ' min valido', v.min_clp);
  ok(v.resultados.every((x, i) => i === 0 || v.resultados[i - 1].clp <= x.clp), 'cocha ventana ' + v.ida + ' resultados ordenados');
  ok(v.min_clp === v.resultados[0].clp, 'cocha ventana ' + v.ida + ' min_clp == resultados[0]');
  ok((v.link || '').includes('cocha.com'), 'cocha link valido');
}
const br = co.brasil || [];
ok(br.length > 0, 'cocha.brasil no vacio', br.length);
ok(br.every((x, i) => i === 0 || br[i - 1].min_clp <= x.min_clp), 'brasil ordenado por precio');
const rioMin = Math.min(...(co.ventanas || [{ min_clp: Infinity }]).map(v => v.min_clp));
ok(br.every(b => Math.abs(b.delta_pct - Math.round((b.min_clp / rioMin - 1) * 1000) / 10) < 0.15), 'brasil delta_pct correcto', br.map(b => [b.cod, b.delta_pct]));
ok(br.every(b => (b.link || '').includes('cocha.com')), 'brasil links validos');

console.log('== Skiplagged (si existe) ==');
const sk = f.skiplagged || {};
if (sk.ok) {
  ok((sk.ventanas || []).length > 0, 'skiplagged ventanas no vacias');
  for (const v of sk.ventanas || []) {
    ok(money(v.min_clp), 'skiplagged ventana ' + v.ida + ' min valido', v.min_clp);
    ok(v.resultados.every((x, i) => i === 0 || v.resultados[i - 1].clp <= x.clp), 'skiplagged resultados ordenados');
    ok((v.link || '').includes('skiplagged.com'), 'skiplagged link valido');
  }
} else console.log('  -- fuente skiplagged aun no publicada (ok si es el primer ciclo)');

console.log('== 9/10. Alertas ==');
ok(Array.isArray(DATA.alerts), 'alerts es array');
ok(typeof DATA.minimos === 'object' && DATA.minimos !== null, 'minimos presente');
ok(typeof vm.maskMail('abc@x.com') === 'string' && vm.maskMail('abc@x.com').indexOf('\u2022') >= 0, 'maskMail funciona');
ok(vm.subUrl('ALTA').includes('issues/new'), 'subUrl funciona');


console.log('== Estructura de documento ==');
ok(html.trimStart().startsWith('<!DOCTYPE html>'), 'documento empieza con <!DOCTYPE html>');
for (const tag of ['<html', '<head>', '</head>', '<style>', '</style>', '<body>', '</body>', '</html>', '<div id="app">', 'unpkg.com/vue@3']) {
  ok(html.includes(tag), 'contiene ' + tag);
}
ok(html.indexOf('</style>') < html.indexOf('<div id="app">'), 'el CSS está antes del #app');
ok(html.indexOf('<style>') < html.indexOf('</style>'), 'bloque <style> cerrado correctamente');
ok(html.includes('aurora3') && html.includes('grain'), 'capas de fondo aurora presentes');
ok((html.match(/<style>/g) || []).length === 1, 'un solo bloque <style>');
ok((html.match(/<div id="app">/g) || []).length === 1, 'un solo #app');

console.log('== Estructura HTML ==');
const secs = [...html.matchAll(/cur===(\d+)\}/g)].map(m => +m[1]);
const tabsLine = [...html.matchAll(/TABS\s*[:=]\s*\[([^\]]*)\]/g)];
const nTabs = tabsLine.length ? (tabsLine[0][1].match(/'/g) || []).length / 2 : -1;
ok(new Set(secs).size === secs.length, 'sin secciones duplicadas', secs);
ok(nTabs === secs.length, 'TABS(' + nTabs + ') == secciones(' + secs.length + ')');
for (let i = 0; i < secs.length; i++) ok(secs.includes(i), 'seccion ' + i + ' existe');

console.log('== fmt/dw ==');
ok(vm.fmt(1234567).includes('1.234.567'), 'fmt es-CL', vm.fmt(1234567));
ok(typeof vm.dw('2027-01-22') === 'string' && vm.dw('2027-01-22').length > 1, 'dw funciona');

console.log('\n' + (fails === 0 ? 'TODO OK' : 'HAY FALLOS') + ': ' + (checks - fails) + '/' + checks + ' checks pasaron');
process.exit(fails === 0 ? 0 : 1);
