"""
Corre las 5 consultas de data/consultas_prueba.json y arma la tabla de medición de la PE2.
Uso:  python src/correr_pruebas.py
"""
import json                              # para leer el archivo de consultas
from pathlib import Path                 # para ubicar el archivo sin errores de ruta

from motor import procesar_consulta      # importamos la función principal de motor.py

RAIZ = Path(__file__).resolve().parent.parent                      # carpeta raíz del repo
ARCHIVO = RAIZ / "data" / "consultas_prueba.json"                  # archivo con las 5 consultas

# Metas de la PE1 contra las que comparamos
META_LATENCIA_MS = 5000      # historia de usuario: "¿responde en menos de 5 segundos?"
META_PRECISION = 0.95        # KPI técnico: 95% de precisión

consultas = json.loads(ARCHIVO.read_text(encoding="utf-8"))        # lee y convierte el JSON a lista de Python
filas = []                                                         # acá vamos juntando una fila por consulta

for numero, c in enumerate(consultas, start=1):                    # numero = 1, 2, 3, 4, 5
    print(f"\n--- Consulta {numero}: {c['descripcion']} ---")
    r = procesar_consulta(c["entrada"])                            # ejecuta el motor
    print(json.dumps(r, indent=2, ensure_ascii=False))             # muestra el resultado completo (evidencia para el PDF)
    m = r["metricas"]                                              # mediciones de esta consulta
    decision = r.get("decision_final") if r["ok"] else f"ERROR:{r['error']['codigo']}"
    acierto = r["ok"] and decision == c["decision_esperada"]       # ¿coincide con lo que esperábamos?
    filas.append({"n": numero, "desc": c["descripcion"], "ok": r["ok"], "decision": decision,
                  "esperada": c["decision_esperada"], "acierto": acierto, **m})

# ---- Tabla en formato Markdown (se puede copiar al PDF) ----
print("\n\n| # | Consulta | ¿Validó? | Decisión (esperada) | Tokens (entrada / salida) | Costo USD | Latencia ms |")
print("|---|---|---|---|---|---|---|")
for f in filas:
    print(f"| {f['n']} | {f['desc']} | {'Sí' if f['ok'] else 'No'} | {f['decision']} ({f['esperada']}) | "
          f"{f['tokens_entrada']} / {f['tokens_salida']} | {f['costo_usd']:.6f} | {f['latencia_ms']} |")

# ---- Promedios ----
n = len(filas)
validas = [f for f in filas if f["ok"]]          # solo las consultas que respondieron bien
prom_tin = sum(f["tokens_entrada"] for f in filas) / n
prom_tout = sum(f["tokens_salida"] for f in filas) / n
prom_costo = sum(f["costo_usd"] for f in filas) / n
prom_lat = sum(f["latencia_ms"] for f in filas) / n
aciertos = sum(f["acierto"] for f in filas)
precision = aciertos / n

print(f"\nPROMEDIOS: tokens {prom_tin:.0f} / {prom_tout:.0f} | costo USD {prom_costo:.6f} | latencia {prom_lat:.0f} ms")
if not validas:                                  # si todas fallaron, no tiene sentido comparar con las metas
    print("Ninguna consulta se procesó correctamente: no se puede comparar con las metas. Revisá los errores de arriba.")
else:
    print(f"Consultas válidas: {len(validas)}/{n}")
    print(f"Latencia vs meta ({META_LATENCIA_MS} ms): {'CUMPLE' if prom_lat < META_LATENCIA_MS else 'NO CUMPLE'}")
    print(f"Precisión de decisión: {aciertos}/{n} = {precision:.0%} (meta PE1: {META_PRECISION:.0%}) -> "
          f"{'CUMPLE' if precision >= META_PRECISION else 'NO CUMPLE'} (muestra de solo {n} consultas)")
