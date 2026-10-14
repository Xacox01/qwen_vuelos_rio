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
ok(html.includes('class="mark"') && html.includes('livepill'), 'logo + pill EN VIVO presentes');
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

console.log('== P21. Calendario de precios ==');
const calO = vm.calOut, calI = vm.calIn;
ok(Object.keys(calO).length > 10, 'calOut tiene dias', Object.keys(calO).length);
ok(Object.keys(calI).length > 10, 'calIn tiene dias', Object.keys(calI).length);
ok(Object.values(calO).every(x => money(x.min) && typeof x.src === 'string'), 'calOut valores validos');
ok(Object.values(calI).every(x => money(x.min)), 'calIn valores validos');
ok(Object.keys(calO).every(k => /^2027-0[12]-\d\d$/.test(k)), 'calOut fechas ene/feb 2027');
ok(Object.keys(calI).every(k => /^2027-0[12]-\d\d$/.test(k)), 'calIn fechas ene/feb 2027');
const rg = vm.calRange;
ok(money(rg.lo) && money(rg.hi) && rg.lo <= rg.hi, 'calRange coherente', [rg.lo, rg.hi]);
const cc = vm.calCells;
ok(cc.ene.filter(Boolean).length === 31, 'ene tiene 31 dias', cc.ene.filter(Boolean).length);
ok(cc.feb.filter(Boolean).length === 28, 'feb tiene 28 dias (2027 no bisiesto)', cc.feb.filter(Boolean).length);
ok(cc.ene.find(x => x).d === 1 && cc.ene[cc.ene.length - 1].d === 31, 'ene d=1..31 en orden');
ok(cc.feb[0] && cc.feb[0].d === 1 && cc.feb[cc.feb.length - 1].d === 28, 'feb d=1..28 (1-feb-2027 es lunes)');
ok(cc.ene.filter(Boolean).every(x => x.fecha.startsWith('2027-01')), 'ene fechas 2027-01-XX');
ok(cc.feb.filter(Boolean).every(x => x.fecha.startsWith('2027-02')), 'feb fechas 2027-02-XX');
vm.calMode = 'ida';
ok(vm.dayOk('2027-02-20') === false, 'ida post 14-feb deshabilitada');
ok(vm.dayOk('2027-03-01') === false, 'fecha fuera de rango deshabilitada');
const diaBarato = Object.keys(calO).filter(k => k <= '2027-02-14').sort((a, b) => calO[a].min - calO[b].min)[0];
ok(vm.dayOk(diaBarato) === true, 'dia con precio valido habilitado', diaBarato);
vm.calMode = 'vuelta';
ok(vm.dayOk('2027-02-24') === false, 'vuelta post 23-feb deshabilitada');
vm.calMode = 'ida';

console.log('== P21. heat/kfmt/rtLinks ==');
ok(vm.kfmt(313628) === '$314K', 'kfmt miles', vm.kfmt(313628));
ok(vm.kfmt(1743976) === '$1.7M', 'kfmt millones', vm.kfmt(1743976));
const hLo = vm.heat(rg.lo), hHi = vm.heat(rg.hi);
ok(hLo.background && hLo.background.includes('hsla(158'), 'heat(min) = verde', hLo.background);
ok(hHi.background && hHi.background.includes('hsla(0,'), 'heat(max) = rojo', hHi.background);
ok(Object.keys(vm.heat(null)).length === 0, 'heat(sin precio) vacio');
ok((vm.heatTxt(rg.lo).color || '').includes('hsl(158'), 'heatTxt(min) verde');
const rl = vm.rtLinks('2027-01-22', '2027-02-01');
ok(rl.length === 4 && rl.every(x => (x.href || '').startsWith('http')), 'rtLinks 4 destinos http');
ok(rl[0].href.includes('2027-01-22') && rl[0].href.includes('2027-02-01'), 'rtLinks JetSMART con ambas fechas');
ok(rl[1].href.includes('skyairline') && rl[1].href.includes('2027-01-22'), 'rtLinks SKY con fechas');
ok(rl[2].href.includes('cocha.com') && rl[2].href.includes('date2=2027-02-01'), 'rtLinks Cocha con fechas');
ok(rl[3].href.includes('skiplagged.com/flights/SCL/GIG/2027-01-22/2027-02-01'), 'rtLinks Skiplagged con fechas');

console.log('== P21. ticker + selInfo + brasilBars ==');
const tk = vm.ticker;
ok(Array.isArray(tk) && tk.length >= 3, 'ticker >= 3 fuentes', tk.length);
ok(tk.every(x => typeof x.k === 'string' && money(x.v)), 'ticker valores validos');
vm.selDate = '';
ok(vm.selInfo === null, 'selInfo null sin fecha elegida');
vm.selDate = diaBarato; vm.calMode = 'ida';
const si = vm.selInfo;
ok(si !== null, 'selInfo existe con fecha', diaBarato);
ok(Array.isArray(si.opts) && si.opts.every(o => money(o.clp) && (o.link || '').startsWith('http') && typeof o.det === 'string'), 'selInfo opts validas', si.opts.length);
ok(si.sugg.length > 0, 'selInfo sugg no vacio para dia mas barato', si.sugg.length);
ok(si.sugg.every(s => s.noches >= 9 && s.vuelta <= '2027-02-23' && s.ida === diaBarato && money(s.min)), 'sugg cumplen >=9 noches y vuelta<=23-feb');
ok(si.sugg.every((s, i) => i === 0 || si.sugg[i - 1].min <= s.min), 'sugg ordenadas por precio');
vm.calMode = 'vuelta';
const diaV = Object.keys(calI).filter(k => k <= '2027-02-23').sort((a, b) => calI[a].min - calI[b].min)[0];
vm.selDate = diaV;
const sv = vm.selInfo;
ok(sv.sugg.every(s => s.vuelta === diaV && s.noches >= 9 && s.ida < diaV), 'sugg modo vuelta coherentes', diaV);
vm.calMode = 'ida'; vm.selDate = '';
const bb = vm.brasilBars;
if (bb.length) {
  ok(bb.every(b => b.w >= 6 && b.w <= 100), 'barras ancho 6-100%', bb.map(b => b.w));
  ok(Math.max(...bb.map(b => b.w)) === 100, 'destino mas caro = 100%');
  ok(bb.every(b => typeof b.good === 'boolean' && b.good === ((b.delta_pct ?? 99) <= 20)), 'flag good = delta<=20%');
  ok(bb.every(b => (b.link || '').startsWith('http')), 'barras con link');
} else console.log('  -- brasilBars vacio (cocha.brasil pendiente esta pasada)');

console.log('== P21. Marcadores de plantilla ==');
for (const m of ['class="ticker"', 'class="cals"', 'class="podium"', 'class="rlist"', 'class="deals"', 'class="tl"', 'class="code"', 'dayMin(', 'dayOk(', 'rtLinks(', 'brasilBars', 'selInfo', 'class="hero"', 'class="kpi"']) {
  ok(html.includes(m), 'plantilla contiene ' + m);
}
ok((html.match(/details class="acc"/g) || []).length >= 8, '>=8 acordeones de detalle', (html.match(/details class="acc"/g) || []).length);
ok(html.includes("calMode==='ida'") && html.includes("calMode==='vuelta'"), 'selector ida/vuelta presente');

console.log('== P22. Playas LatAm ==');
for (const m2 of ['class="ruler"', 'class="deal"', 'class="dflag"', 'class="dmeter"', 'rbud', 'playasRows', 'playasAxis', 'Pases de abordar']) {
  ok(html.includes(m2), 'plantilla contiene ' + m2);
}
const pl = f.playas || {};
if (pl.ok && (pl.destinos || []).length) {
  const budget = pl.budget || 500000;
  const rows = vm.playasRows;
  ok(rows.length >= 10, 'playasRows >= 10 destinos con precio', rows.length);
  ok(rows.every((r, i) => i === 0 || rows[i - 1].mejor.clp <= r.mejor.clp), 'playas ordenado por precio');
  ok(vm.playasOk.every(d => d.mejor.clp < budget), 'playasOk todos bajo presupuesto');
  ok(vm.playasNo.every(d => d.mejor.clp >= budget), 'playasNo todos sobre presupuesto');
  ok(vm.playasOk.length + vm.playasNo.length === rows.length, 'Ok + No == total filas');
  ok(rows.every(d => money(d.mejor.clp)), 'playas precios validos');
  ok(rows.every(d => (d.mejor.link || '').startsWith('http')), 'playas links http');
  ok(rows.every(d => d.mejor.noches >= 9), 'playas >= 9 noches', rows.filter(d => d.mejor.noches < 9).map(d => d.cod));
  ok(rows.every(d => d.mejor.vuelta <= '2027-02-23'), 'playas vuelta <= 23-feb');
  let wbad = [];
  for (const d of rows) for (const w of d.windows || []) {
    if (!((w.min_clp > 30000 && isFinite(w.min_clp)) && w.ida < w.vuelta && (w.link || '').includes('skiplagged.com'))) wbad.push(d.cod + ':' + w.ida);
  }
  ok(wbad.length === 0, 'ventanas skiplagged validas (precio/fechas/link)', wbad);
  const ax = vm.playasAxis;
  ok(ax && ax.lo < ax.hi && ax.budPct > 0 && ax.budPct < 100, 'eje de ruler coherente', ax && [Math.round(ax.lo), Math.round(ax.hi), Math.round(ax.budPct)]);
  ok(ax && ax.pos(Math.round(ax.lo)) >= 1 && ax.pos(Math.round(ax.hi)) <= 99, 'posiciones de ruler dentro del rango');
  const coP = rows.filter(d => d.cocha && d.cocha.min_clp);
  ok(coP.every(d => money(d.cocha.min_clp) && (d.cocha.link || '').includes('cocha.com')), 'cocha playas precios/links validos');
  console.log('     bajo presupuesto: ' + (vm.playasOk.map(d => d.cod + ' $' + Math.round(d.mejor.clp).toLocaleString('es-CL')).join(' · ') || 'ninguno'));
} else {
  console.log('  -- playas aun sin datos en data.json (primer ciclo) — solo checks de plantilla');
  ok((vm.playasRows || []).length === 0, 'playasRows vacio sin datos');
  ok(vm.playasAxis === null, 'playasAxis null sin datos');
}

console.log('== P23. Tema claro + merchandising ==');
ok(html.includes('--bg:#f5f7fc'), 'fondo claro definido');
ok(!html.includes('#070b14'), 'sin restos del fondo oscuro');
for (const m3 of ['class="dsave"', 'class="chips"', 'class="dpkg"', 'alojaB(', 'alojaA(', 'gigRef', 'ofertas()', 'ahorroVsRio', 'mejorPlaya', 'copyCode(', 'hace(', 'ciudadCorta(']) {
  ok(html.includes(m3), 'plantilla contiene ' + m3);
}
ok((vm.heatTxt(400000).color || '').includes('72%,28%'), 'heatTxt oscuro legible en claro', vm.heatTxt(400000));
if ((pl.ok) && (pl.destinos || []).length) {
  ok(vm.gigRef > 0, 'gigRef con precio de Rio', vm.gigRef);
  const of = vm.ofertas;
  ok(Array.isArray(of) && of.length >= 1 && of.every(d => typeof d.ahorroVsRio === 'number'), 'ofertas con ahorroVsRio calculado', of.length);
  ok(vm.mejorPlaya && vm.mejorPlaya.cod !== 'GIG', 'mejorPlaya existe y no es Rio', vm.mejorPlaya && vm.mejorPlaya.cod);
  const dTest = vm.playasOk[0];
  if (dTest) {
    ok(vm.alojaB(dTest).includes('booking.com') && vm.alojaB(dTest).includes(dTest.mejor.ida), 'alojaB Booking con fechas', vm.alojaB(dTest).slice(0, 90));
    ok(vm.alojaA(dTest).includes('airbnb.com') && vm.alojaA(dTest).includes(dTest.mejor.vuelta), 'alojaA Airbnb con fechas');
    ok(!vm.alojaB(dTest).includes('·'), 'cityClean quita decoraciones del nombre');
  }
}

console.log('\n' + (fails === 0 ? 'TODO OK' : 'HAY FALLOS') + ': ' + (checks - fails) + '/' + checks + ' checks pasaron');
process.exit(fails === 0 ? 0 : 1);
