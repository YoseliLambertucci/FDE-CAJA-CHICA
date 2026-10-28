"""
Motor LLM base - Asistente de caja chica (PE2).

Recibe el texto de un ticket/factura + el área del empleado, se lo pasa a Claude,
valida que la respuesta tenga el formato correcto y devuelve el resultado junto con
lo que costó y lo que tardó.
"""

# ---------------------------------------------------------------------------
# 1) IMPORTS: herramientas que usamos (ya vienen escritas por otras personas)
# ---------------------------------------------------------------------------
import logging                      # para dejar mensajes en la terminal ("qué campo falló")
import os                           # para leer variables de entorno (la clave de la API)
import time                         # para medir cuántos milisegundos tarda la llamada
from datetime import date           # tipo "fecha": Python verifica que la fecha exista de verdad
from enum import Enum               # para definir listas de valores permitidos
from pathlib import Path            # para armar rutas de archivos sin errores
from typing import Optional         # para decir "este campo puede venir vacío (None)"

import anthropic                    # librería oficial para hablar con Claude
import openai                       # librería "estándar" que también hablan Groq y Ollama
from dotenv import load_dotenv      # lee el archivo .env y carga sus valores
from pydantic import (              # Pydantic: valida que los datos tengan la forma correcta
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# 2) CONFIGURACIÓN
# ---------------------------------------------------------------------------
load_dotenv()  # carga el archivo .env (donde está tu clave) a las variables de entorno

# Configuración de mensajes: formato "NIVEL | mensaje". INFO muestra los avisos normales.
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("motor")  # "log" es el objeto con el que escribimos mensajes

# Carpeta raíz del repo: este archivo está en src/, así que subimos un nivel.
RAIZ = Path(__file__).resolve().parent.parent

# PROVEEDOR: quién nos presta el modelo. "anthropic" (Claude), "groq" (gratis en la nube) u "ollama" (en tu PC).
PROVEEDOR = os.getenv("PROVEEDOR", "anthropic").lower()

# Valores por defecto de cada proveedor (se pueden cambiar desde .env).
PROVEEDORES = {
    "anthropic": {"modelo": "claude-haiku-5-5", "base_url": None, "var_clave": "ANTHROPIC_API_KEY"},
    "groq": {"modelo": "openai/gpt-oss-120b", "base_url": "https://api.groq.com/openai/v1", "var_clave": "GROQ_API_KEY"},
    "ollama": {"modelo": "llama3.1:8b", "base_url": "http://localhost:11434/v1", "var_clave": None},  # local: no usa clave
}
if PROVEEDOR not in PROVEEDORES:  # si escribiste mal el nombre, avisamos enseguida
    raise SystemExit(f"PROVEEDOR '{PROVEEDOR}' no válido. Usá: {', '.join(PROVEEDORES)}")
CONFIG = PROVEEDORES[PROVEEDOR]   # configuración del proveedor elegido

# Modelo y precios. Se leen de .env; si no existen, se usan los valores por defecto.
MODELO = os.getenv("MODELO") or CONFIG["modelo"]   # si MODELO está vacío, usa el del proveedor
PRECIO_ENTRADA_USD_MTOK = float(os.getenv("PRECIO_ENTRADA_USD_MTOK", "0.10"))  # USD por millón de tokens
PRECIO_SALIDA_USD_MTOK = float(os.getenv("PRECIO_SALIDA_USD_MTOK", "0.50"))

# Si la confianza del modelo es menor a 90%, se fuerza revisión humana (riesgo 1 de la PE1).
UMBRAL_CONFIANZA = 0.90

# Datos del Decreto 77/26 (art. 1): fondo total y "valor máximo" por pago individual de cada área.
# (Según el art. 87 del Decreto 2980/00, el decreto de caja chica fija "el valor máximo de cada pago individual".)
AREAS = {
    "Contaduría": {"monto": 200_000, "valor_maximo": 150_000},
    "Contrataciones": {"monto": 250_000, "valor_maximo": 200_000},
    "Del. Municipal R. 11": {"monto": 200_000, "valor_maximo": 150_000},
    "Dirección Hospital": {"monto": 300_000, "valor_maximo": 150_000},
    "Dirección de Museos y Escuela Bellas Artes": {"monto": 200_000, "valor_maximo": 150_000},
    "H. Concejo Deliberante": {"monto": 200_000, "valor_maximo": 100_000},
    "Dir. de Cultura": {"monto": 300_000, "valor_maximo": 250_000},
    "Sec. de Deportes": {"monto": 300_000, "valor_maximo": 250_000},
    "Sec. de Desarrollo Social": {"monto": 150_000, "valor_maximo": 150_000},
    "Sec. de Gobierno": {"monto": 300_000, "valor_maximo": 200_000},
    "Sec. de Legal, Tec. y Adm.": {"monto": 250_000, "valor_maximo": 150_000},
    "Sec. de Obras Públicas": {"monto": 150_000, "valor_maximo": 100_000},
    "Sec. de Producción": {"monto": 200_000, "valor_maximo": 150_000},
    "Sec. de Seguridad": {"monto": 250_000, "valor_maximo": 150_000},
    "Sec. Servicios Generales": {"monto": 250_000, "valor_maximo": 200_000},
}


# ---------------------------------------------------------------------------
# 3) CONTRATO DE DATOS (Pydantic): qué entra y qué tiene que salir
# ---------------------------------------------------------------------------
class Categoria(str, Enum):
    """Valores PERMITIDOS para el tipo de gasto (art. 3 del Decreto 77/26). Cualquier otro valor falla."""
    PAPELERIA = "papeleria"
    PASAJES = "pasajes"
    REPUESTOS = "repuestos"
    COMESTIBLES = "comestibles"
    LIMPIEZA = "limpieza"
    ALIMENTOS = "alimentos"
    SERVICIOS_MENORES = "servicios_menores"
    OTRO_GASTO_MENOR = "otro_gasto_menor"
    NO_CORRESPONDE = "no_corresponde"  # gasto que NO se puede pagar con caja chica


class Decision(str, Enum):
    """Las únicas 3 decisiones posibles del sistema."""
    APROBAR = "aprobar"
    REVISION_HUMANA = "revision_humana"  # el sistema se pausa y le pregunta al contador
    RECHAZAR = "rechazar"


class Entrada(BaseModel):
    """Lo que ENTRA al motor (viene de la historia de usuario: el empleado carga un ticket)."""

    # REGLA 1 (patrón): el legajo debe tener forma "EMP-" + 3 dígitos. Ej: EMP-101
    id_empleado: str = Field(pattern=r"^EMP-\d{3}$")

    # Área del empleado. Se valida más abajo contra la lista del decreto.
    area: str

    # REGLA 2 (largo mínimo): el texto del ticket debe tener al menos 10 caracteres (y máximo 3000).
    texto_ticket: str = Field(min_length=10, max_length=3000)

    # REGLA 3 (valor permitido): el área tiene que existir en el Decreto 77/26.
    @field_validator("area")          # "este método valida el campo area"
    @classmethod
    def area_debe_existir(cls, valor: str) -> str:
        if valor not in AREAS:        # si el área no está en la lista del decreto...
            raise ValueError(f"área '{valor}' no figura en el Decreto 77/26")  # ...falla con este mensaje
        return valor                  # si está, la dejamos pasar


class Salida(BaseModel):
    """Lo que tiene que DEVOLVER el modelo. Si no cumple estas reglas, la respuesta se considera inválida."""

    es_comprobante: bool  # True/False: ¿el texto es realmente un ticket o factura?

    # Optional = puede ser None (vacío). Es necesario para el caso "fuera de tema".
    comercio: Optional[str] = Field(default=None, min_length=2)                 # largo mínimo 2
    fecha: Optional[date] = None                                                # fecha real (no acepta 2026-13-45)
    monto_total: Optional[float] = Field(default=None, gt=0, le=10_000_000)     # RANGO: mayor a 0, hasta 10 millones

    categoria: Categoria                                  # Enum: solo valores de la lista
    decision: Decision                                    # Enum: solo aprobar / revision_humana / rechazar
    confianza: float = Field(ge=0, le=1)                  # RANGO: entre 0 y 1
    justificacion: str = Field(min_length=10, max_length=500)  # explicación obligatoria (mín. 10 caracteres)

    # Regla que mira VARIOS campos juntos: NO se puede aprobar un comprobante al que le falten datos.
    # (Si es un ticket borroso, el modelo puede dejar campos en null y pedir "revision_humana".)
    @model_validator(mode="after")
    def no_aprobar_con_datos_faltantes(self):
        if self.es_comprobante and self.decision == Decision.APROBAR:
            faltan = [c for c in ("comercio", "fecha", "monto_total") if getattr(self, c) is None]
            if faltan:
                raise ValueError(f"decision es 'aprobar' pero faltan: {', '.join(faltan)}")
        return self


# ---------------------------------------------------------------------------
# 4) FUNCIONES AUXILIARES (piezas chicas que usa el motor)
# ---------------------------------------------------------------------------
def cargar_plantilla(nombre: str) -> str:
    """Lee un prompt desde la carpeta prompts/. Así el texto del prompt NO está escrito dentro del código."""
    return (RAIZ / "prompts" / nombre).read_text(encoding="utf-8")


def calcular_costo_usd(tokens_entrada: int, tokens_salida: int) -> float:
    """Costo = tokens x precio por millón / 1.000.000 (se calcula por separado entrada y salida)."""
    costo_entrada = tokens_entrada * PRECIO_ENTRADA_USD_MTOK / 1_000_000
    costo_salida = tokens_salida * PRECIO_SALIDA_USD_MTOK / 1_000_000
    return costo_entrada + costo_salida


def limpiar_json(texto: str) -> str:
    """A veces el modelo agrega ```json ... ``` o texto de más. Nos quedamos solo con lo que va de '{' a '}'."""
    inicio = texto.find("{")    # posición de la primera llave
    fin = texto.rfind("}")      # posición de la última llave
    if inicio == -1 or fin == -1:
        return texto            # no hay llaves: devolvemos tal cual y Pydantic dirá que es inválido
    return texto[inicio:fin + 1]


def resumir_errores(error: ValidationError) -> list:
    """Convierte el error de Pydantic en una lista simple: qué campo falló y por qué."""
    resumen = []
    for e in error.errors():                       # Pydantic entrega una lista con cada problema
        campo = ".".join(str(x) for x in e["loc"])  # "loc" es la ubicación del campo (ej: "monto_total")
        resumen.append({"campo": campo or "(respuesta completa)", "problema": e["msg"]})
    return resumen


def armar_metricas(inicio: float, tokens_entrada: int, tokens_salida: int, intentos: int) -> dict:
    """Junta las mediciones de la consulta en un diccionario."""
    return {
        "tokens_entrada": tokens_entrada,
        "tokens_salida": tokens_salida,
        "costo_usd": round(calcular_costo_usd(tokens_entrada, tokens_salida), 6),
        "latencia_ms": round((time.perf_counter() - inicio) * 1000),  # segundos -> milisegundos
        "intentos": intentos,
    }


def error_estructurado(codigo: str, mensaje: str, campos: list, metricas: dict) -> dict:
    """Formato único para TODOS los errores: así quien use el motor siempre sabe qué esperar."""
    return {"ok": False, "error": {"codigo": codigo, "mensaje": mensaje, "campos": campos}, "metricas": metricas}


class ErrorProveedor(Exception):
    """Error de la API ya traducido a un código nuestro (sin importar de qué proveedor venga)."""
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)   # guarda el mensaje en la excepción
        self.codigo = codigo        # guardamos también el código (ej: API_LIMITE_USO)


def crear_cliente():
    """Crea el 'cliente' que habla con el proveedor elegido."""
    if PROVEEDOR == "anthropic":
        return anthropic.Anthropic()                      # toma ANTHROPIC_API_KEY solo desde el entorno
    clave = os.getenv(CONFIG["var_clave"]) if CONFIG["var_clave"] else "ollama"  # Ollama pide una clave de mentira
    return openai.OpenAI(api_key=clave, base_url=os.getenv("BASE_URL", CONFIG["base_url"]))


def llamar_modelo(cliente, sistema: str, usuario: str):
    """Hace UNA llamada real al modelo y devuelve (texto, tokens_entrada, tokens_salida, motivo_de_fin)."""
    try:
        if PROVEEDOR == "anthropic":
            respuesta = cliente.messages.create(
                model=MODELO,                                    # qué modelo usar
                max_tokens=4096,                                 # tope de respuesta (deja margen para "pensar")
                system=sistema,                                  # instrucciones fijas (el prompt de sistema)
                messages=[{"role": "user", "content": usuario}],  # el mensaje de esta consulta
                extra_body={"output_config": {"effort": "low"}},  # esfuerzo bajo = más rápido y barato (Haiku 5.5 no acepta "temperature")
                timeout=30,                                      # si en 30 segundos no responde, se corta
            )
            # La respuesta puede traer varios "bloques" (ej: pensamiento + texto). Juntamos solo los de tipo texto.
            texto = "".join(b.text for b in respuesta.content if b.type == "text")
            # "usage" informa cuántos tokens se usaron: de acá sale el costo REAL.
            return texto, respuesta.usage.input_tokens, respuesta.usage.output_tokens, respuesta.stop_reason
        else:  # groq u ollama: formato "OpenAI"
            extra = {"reasoning_effort": "low"} if "gpt-oss" in MODELO else {}  # gpt-oss "piensa": lo dejamos en esfuerzo bajo
            respuesta = cliente.chat.completions.create(
                model=MODELO,
                messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                response_format={"type": "json_object"},         # pide que la respuesta sea un JSON
                temperature=0,                                   # 0 = respuestas lo más repetibles posible
                max_tokens=4000,                                 # margen: el "pensamiento" también consume tokens
                timeout=60,                                      # un modelo local puede tardar más
                **extra,                                         # agrega reasoning_effort solo si corresponde
            )
            texto = respuesta.choices[0].message.content or ""
            return texto, respuesta.usage.prompt_tokens, respuesta.usage.completion_tokens, respuesta.choices[0].finish_reason

    # Traducimos los errores de ambas librerías a nuestros códigos (los nombres de error son iguales en las dos).
    except (anthropic.AuthenticationError, openai.AuthenticationError):
        raise ErrorProveedor("API_CLAVE_INVALIDA", "La clave de la API es incorrecta o está vencida.")
    except (anthropic.RateLimitError, openai.RateLimitError):
        raise ErrorProveedor("API_LIMITE_USO", "Se alcanzó el límite de uso de la API. Esperá un momento.")
    except (anthropic.APITimeoutError, openai.APITimeoutError):  # (va antes que ConnectionError: es un caso particular)
        raise ErrorProveedor("API_TIMEOUT", "El modelo tardó demasiado en responder.")
    except (anthropic.APIConnectionError, openai.APIConnectionError):
        raise ErrorProveedor("API_SIN_CONEXION", "No hay conexión con la API (¿internet? ¿Ollama encendido?).")
    except (anthropic.NotFoundError, openai.NotFoundError):         # 404: casi siempre es un nombre de modelo que ya no existe
        raise ErrorProveedor("API_MODELO_NO_ENCONTRADO",
                             f"El modelo '{MODELO}' no existe en {PROVEEDOR}. Revisá MODELO en .env o la lista de modelos del proveedor.")
    except (anthropic.APIStatusError, openai.APIStatusError) as e:  # cualquier otro error que devuelva la API
        raise ErrorProveedor("API_ERROR", f"La API devolvió un error (código {e.status_code}): {str(e)[:200]}")


# ---------------------------------------------------------------------------
# 5) FUNCIÓN PRINCIPAL: procesa una consulta de punta a punta
# ---------------------------------------------------------------------------
def procesar_consulta(datos: dict) -> dict:
    """
    Pasos: validar entrada -> armar prompt -> llamar al modelo -> validar salida
    (con 1 reintento) -> aplicar reglas de negocio -> devolver resultado + métricas.
    """
    inicio = time.perf_counter()              # arranca el cronómetro
    tokens_entrada = tokens_salida = 0        # acumuladores de tokens (suman los dos intentos si hay reintento)

    # --- Paso 1: validar la ENTRADA con el contrato -------------------------
    try:
        entrada = Entrada(**datos)            # si algo no cumple las reglas, Pydantic lanza ValidationError
    except ValidationError as e:
        campos = resumir_errores(e)
        log.warning("Entrada inválida. Campos con problema: %s", campos)
        return error_estructurado("ENTRADA_INVALIDA", "La consulta no cumple el contrato de entrada.",
                                  campos, armar_metricas(inicio, 0, 0, 0))

    # --- Paso 2: verificar que exista la clave de la API ---------------------
    if CONFIG["var_clave"] and not os.getenv(CONFIG["var_clave"]):   # Ollama no necesita clave
        return error_estructurado("CLAVE_FALTANTE", f"Falta {CONFIG['var_clave']} en el archivo .env.",
                                  [], armar_metricas(inicio, 0, 0, 0))

    # --- Paso 3: armar los prompts a partir de las plantillas ----------------
    tope = AREAS[entrada.area]["valor_maximo"]   # tope por pago de esta área (viene del decreto)
    sistema = cargar_plantilla("sistema.txt")    # instrucciones fijas
    usuario_base = cargar_plantilla("usuario.txt").format(  # completa los {huecos} de la plantilla
        area=entrada.area, tope=f"{tope:,}".replace(",", "."), texto_ticket=entrada.texto_ticket
    )

    cliente = crear_cliente()                    # crea el cliente del proveedor elegido
    mensaje_usuario = usuario_base               # el 1er intento usa el mensaje original
    salida = None                                # acá guardaremos la respuesta ya validada
    campos_fallidos = []                         # acá guardamos qué campos fallaron

    # --- Paso 4: llamar al modelo y validar (máximo 2 intentos = 1 reintento) -
    for intento in (1, 2):
        try:
            texto, t_in, t_out, motivo = llamar_modelo(cliente, sistema, mensaje_usuario)
        # Errores de la API: ya vienen traducidos a un código claro por llamar_modelo().
        except ErrorProveedor as e:
            return error_estructurado(e.codigo, str(e), [], armar_metricas(inicio, tokens_entrada, tokens_salida, intento))

        tokens_entrada += t_in                 # sumamos los tokens de este intento
        tokens_salida += t_out

        if motivo == "refusal":                # el modelo se negó a responder: reintentar no sirve
            return error_estructurado("MODELO_RECHAZO", "El modelo se negó a procesar esta consulta.",
                                      [], armar_metricas(inicio, tokens_entrada, tokens_salida, intento))

        # Validar la SALIDA contra el contrato.
        try:
            salida = Salida.model_validate_json(limpiar_json(texto))  # convierte el texto JSON y chequea todas las reglas
            break                                                      # si llegó acá, es válida: salimos del ciclo
        except ValidationError as e:
            campos_fallidos = resumir_errores(e)
            log.warning("Intento %d: la respuesta NO validó. Campos con problema: %s", intento, campos_fallidos)
            # Para el reintento le contamos al modelo exactamente qué estuvo mal.
            detalle = "; ".join(f"{c['campo']}: {c['problema']}" for c in campos_fallidos)
            mensaje_usuario = (usuario_base + "\n\nTu respuesta anterior no fue válida. Problemas: "
                               + detalle + ". Respondé SOLO con el JSON corregido.")

    # Si después de los 2 intentos no hay salida válida, devolvemos un error estructurado.
    if salida is None:
        return error_estructurado("SALIDA_INVALIDA", "El modelo no devolvió una respuesta válida tras 2 intentos.",
                                  campos_fallidos, armar_metricas(inicio, tokens_entrada, tokens_salida, 2))

    # --- Paso 5: reglas de negocio (las decide Python, NO el modelo) ---------
    decision_final = salida.decision
    motivos = []                                                   # lista de razones de la decisión final
    excede_tope = salida.monto_total is not None and salida.monto_total > tope  # comparación exacta con el decreto
    if excede_tope:
        motivos.append(f"El monto ${salida.monto_total:,.2f} supera el valor máximo por pago (${tope:,}) del área.")
    faltan = [c for c in ("comercio", "fecha", "monto_total") if salida.es_comprobante and getattr(salida, c) is None]
    if faltan:                                                     # ticket ilegible o incompleto
        motivos.append(f"Datos ilegibles o ausentes en el comprobante: {', '.join(faltan)}.")
    if salida.es_comprobante and salida.confianza < UMBRAL_CONFIANZA:
        motivos.append(f"Confianza {salida.confianza:.0%} menor al umbral de {UMBRAL_CONFIANZA:.0%}.")
    # Si el modelo quería aprobar pero hay motivos de duda, se pausa y decide un humano (human-in-the-loop).
    if decision_final == Decision.APROBAR and motivos:
        decision_final = Decision.REVISION_HUMANA

    return {
        "ok": True,
        "salida": salida.model_dump(mode="json"),   # la salida validada, como diccionario simple
        "decision_final": decision_final.value,     # lo que realmente hace el sistema
        "excede_tope": excede_tope,
        "tope_area": tope,
        "motivos_revision": motivos,
        "metricas": armar_metricas(inicio, tokens_entrada, tokens_salida, intento),
    }


# ---------------------------------------------------------------------------
# 6) DEMO: se ejecuta solo cuando corrés "python src/motor.py"
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json  # para imprimir los resultados prolijos

    print("=== EJECUCIÓN EXITOSA ===")
    ok = procesar_consulta({
        "id_empleado": "EMP-101",
        "area": "Sec. Servicios Generales",
        "texto_ticket": "LIBRERÍA EL PAPEL S.A. Factura B 0003-00012345 Fecha 14/10/2026 "
                        "2 resmas A4 $9.800 + 1 caja biromes $8.700. TOTAL $18.500,00",
    })
    print(json.dumps(ok, indent=2, ensure_ascii=False))

    print("\n=== EJECUCIÓN CON ERROR (entrada inválida: área inexistente y legajo mal escrito) ===")
    error = procesar_consulta({
        "id_empleado": "101",
        "area": "Área Inventada",
        "texto_ticket": "Ticket de prueba por $5.000",
    })
    print(json.dumps(error, indent=2, ensure_ascii=False))
