#!/usr/bin/env python3
"""Bootstrap local del módulo Playas: escaneo COMPLETO (16 destinos × 3 ventanas
Skiplagged + 16 cotizaciones Cocha) → playas_seed.json. Se inyecta en data.json
antes del push; después CI mantiene la rotación por ciclo."""
import os, json, importlib.util, datetime

os.environ["PLAYAS_FULL"] = "1"
os.environ["PLAYAS_COCHA"] = "16"

spec = importlib.util.spec_from_file_location("scraper", os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper.py"))
m = importlib.util.module_from_spec(spec)
src = open(spec.origin).read().replace('if __name__ == "__main__":\n    main()', "")
exec(compile(src, "scraper.py", "exec"), m.__dict__)

t0 = datetime.datetime.now()
res = m.fetch_playas({}, 0)
out = {"ts": m.now_iso(), "ok": True, **res}
dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playas_seed.json")
with open(dest, "w", encoding="utf8") as fh:
    json.dump(out, fh, ensure_ascii=False)
bajos = [d for d in res["destinos"] if d.get("mejor") and d["mejor"]["clp"] < res["budget"]]
print(f"SEED_PLAYAS_OK en {datetime.datetime.now()-t0} · {len(bajos)}/{len(res['destinos'])} bajo presupuesto")
for d in res["destinos"]:
    mj = d.get("mejor")
    print(f"  {d['flag']} {d['cod']:4} {d['ciudad'][:32]:34} " + (f"${mj['clp']:>9,} · {mj['src']:10} · {mj['aerolinea']} · {mj['escalas']} esc" if mj else "sin resultados"))
