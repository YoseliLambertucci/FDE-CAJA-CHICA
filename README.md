# fde-caja-chica — Asistente de rendición de caja chica

Motor LLM (PE2): recibe el texto de un ticket/factura y el área del empleado, consulta a Claude y devuelve una decisión
(`aprobar` / `revision_humana` / `rechazar`) validada con Pydantic, junto con tokens, costo en USD y latencia en ms.

Normativa usada: Decreto municipal 77/26 (cajas chicas 2026, General Juan Madariaga) y Decreto provincial 2980/00 (arts. 87–88).

## Estructura

```
fde-caja-chica/
├── README.md
├── requirements.txt
├── .env.example          # nombres de variables (sin valores)
├── .gitignore            # incluye .env
├── prompts/
│   ├── sistema.txt       # prompt de sistema (plantilla separada del código)
│   └── usuario.txt       # mensaje por consulta, con {huecos}
├── data/consultas_prueba.json   # 5 consultas de prueba
└── src/
    ├── motor.py          # contrato Pydantic + llamada al modelo + medición + errores
    └── correr_pruebas.py # corre las 5 consultas y arma la tabla con promedios
```

## Cómo correrlo desde un clon limpio

```bash
git clone <URL-del-repo> && cd fde-caja-chica
python -m venv .venv
source .venv/bin/activate          # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # En Windows: copy .env.example .env
# Abrí .env, elegí PROVEEDOR (anthropic | groq | ollama) y pegá la clave que corresponda
python src/motor.py                # demo: una ejecución exitosa y una con error
python src/correr_pruebas.py       # las 5 consultas + tabla de medición
```

## Proveedores del modelo

| PROVEEDOR | Clave | Modelo por defecto | Para qué |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` | Entrega final (de pago) |
| `groq` | `GROQ_API_KEY` (gratis en console.groq.com) | `openai/gpt-oss-120b` | Pruebas en la nube |
| `ollama` | ninguna | `llama3.1:8b` | Pruebas locales (`ollama pull llama3.1:8b`) |

Groq y Ollama se usan con la librería `openai` (API compatible). Cambiar de proveedor no requiere tocar el código, solo `.env`.
Los costos de las pruebas con Groq/Ollama no representan el costo real del modelo final.

## Qué hace cuando falla la validación

Si la respuesta del modelo no cumple el contrato `Salida`, registra **qué campo falló**, reintenta una vez
informándole al modelo el problema y, si vuelve a fallar, devuelve un error estructurado
(`{"ok": false, "error": {"codigo": "SALIDA_INVALIDA", "campos": [...]}}`). Los errores de la API
(clave inválida, límite de uso, sin conexión, tiempo agotado) también se devuelven con código propio.

## Decisiones de diseño

- El modelo **extrae y clasifica**; la comparación contra el tope por pago y el umbral de confianza (90 %) las hace Python, porque son reglas exactas.
- El texto del ticket se trata como dato no confiable (defensa básica contra instrucciones escondidas en el ticket).
- Proveedor intercambiable desde `.env` (extra opcional de la PE2).
- Modelo final: `claude-haiku-5-5` (USD 0,10 / 0,50 por millón de tokens de entrada / salida para prompts de hasta 100 mil tokens).
