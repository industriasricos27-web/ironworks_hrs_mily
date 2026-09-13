import os
import json
import time
import hmac
import hashlib
import requests

from flask import Flask, request, jsonify

from google import genai
from google.genai import types

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


app = Flask(__name__)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("No se encontró GEMINI_API_KEY.")

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# CONFIGURACIÓN GOOGLE DRIVE / DOCS (FIJA Y DEFINITIVA)
# ============================================================
GOOGLE_FOLDER_ID = "13DTk5zWfh31fb0gt6otHhLKau72tubzT"
ruta_json = r"C:\Users\UseR\Downloads\credentials.json"

if not os.path.exists(ruta_json):
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
else:
    with open(ruta_json, "r", encoding="utf-8") as f:
        GOOGLE_SERVICE_ACCOUNT_JSON = f.read()

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]


def crear_servicios_google():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        print("Aviso: No se encontraron credenciales de Google Service Account.")
        return None, None
    try:
        datos_credenciales = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        credenciales = service_account.Credentials.from_service_account_info(
            datos_credenciales,
            scopes=GOOGLE_SCOPES
        )

        drive = build(
            "drive",
            "v3",
            credentials=credenciales,
            cache_discovery=False
        )

        docs = build(
            "docs",
            "v1",
            credentials=credenciales,
            cache_discovery=False
        )

        return drive, docs
    except Exception as e:
        print(f"Error cargando servicios de Google: {e}")
        return None, None


drive_service, docs_service = crear_servicios_google()


# ============================================================
# CONFIGURACIÓN WHATSAPP META
# ============================================================

META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "ironworks_mily_2026")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID")
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v18.0")
META_APP_SECRET = os.environ.get("META_APP_SECRET")


# ============================================================
# IDENTIFICACIÓN DEL EQUIPO INTERNO
# ============================================================

NUMEROS_INTERNOS = [
    "573239603437",  # Alexa
    "573224579894",  # Andrés
]


# ============================================================
# CONFIGURACIÓN DEL CATÁLOGO Y MEMORIA
# ============================================================

CACHE_CATALOGO_SEGUNDOS = 300

catalogo_cache = {
    "timestamp": 0,
    "contenido": ""
}

conversaciones = {}
mensajes_procesados = set()


# ============================================================
# PROMPT BASE DE MILY (INCLUYENDO PINTEREST Y MODELO DE NEGOCIO)
# ============================================================

INSTRUCCIONES_MILY = """
PROMPT DE SISTEMA — MILY 2.0
ASESORA COMERCIAL Y ASISTENTE INTERNA DE IRONWORKS HRs

IDENTIDAD
Eres Mily, la Asesora Comercial Virtual de IRONWORKS HRs —
Especialistas en heavy ironwork, fine furniture y minimalist crafts para small spaces.
Lema comercial: "tú imaginas, nosotros creamos".

============================================================
1. DOBLE ROL
============================================================

MODO CLIENTE EXTERNO:
- Trato respetuoso, profesional y cercano.
- Utiliza "Sí señor" o "Sí señora" cuando corresponda.
- No uses expresiones informales.
- Tu objetivo es orientar, diagnosticar necesidades y conducir la conversación hacia cotización o visita técnica.

MODO EQUIPO INTERNO:
- El equipo autorizado incluye a Alexa, Andrés y taller.
- Habla de forma directa, clara y técnica.

============================================================
2. MODELO DE NEGOCIO Y PINTEREST (VITRINA PRINCIPAL)
============================================================
- NO manejamos stock ni inventario fijo. Trabajamos 100% sobre pedido y personalizados.
- Si el cliente te envía una foto de referencia (o diseño), elógiala y confírmale con entusiasmo que en Ironworks HRs podemos fabricarla a medida o adaptarla a sus espacios.
- Cuando el cliente quiera ver más inspiración, trabajos o catálogos visuales, compárteles con orgullo nuestro perfil oficial de Pinterest: https://co.pinterest.com/industriasricos/

============================================================
3. FUENTE DE INFORMACIÓN Y CATÁLOGO
============================================================
El contenido del catálogo en Google Drive es la FUENTE DE VERDAD. No inventes precios oficiales ni materiales si no están allí; en tal caso, indícale al cliente que pasa a revisión técnica.
"""


# ============================================================
# FUNCIONES DE GOOGLE DRIVE Y DOCS
# ============================================================

def extraer_texto_google_doc(documento):
    texto_total = []
    body = documento.get("body", {})
    contenido = body.get("content", [])

    for elemento in contenido:
        parrafo = elemento.get("paragraph")
        if not parrafo:
            continue

        elementos_parrafo = parrafo.get("elements", [])
        for elemento_parrafo in elementos_parrafo:
            text_run = elemento_parrafo.get("textRun")
            if text_run:
                texto_total.append(text_run.get("content", ""))

    return "".join(texto_total).strip()


def obtener_documentos_de_carpeta():
    if not drive_service:
        return []

    documentos = []
    page_token = None
    consulta = (
        f"'{GOOGLE_FOLDER_ID}' in parents "
        "and trashed = false "
        "and mimeType = 'application/vnd.google-apps.document'"
    )

    try:
        while True:
            respuesta = drive_service.files().list(
                q=consulta,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=100,
                pageToken=page_token
            ).execute()

            archivos = respuesta.get("files", [])
            documentos.extend(archivos)

            page_token = respuesta.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        print(f"Error listando archivos de Google Drive: {e}")

    return documentos


def cargar_catalogo_desde_google():
    if not drive_service or not docs_service:
        return "Servicio de Google Drive no disponible de forma local."

    documentos = obtener_documentos_de_carpeta()
    if not documentos:
        return "No hay Google Docs disponibles en la carpeta del catálogo."

    partes = []
    for archivo in documentos:
        try:
            documento = docs_service.documents().get(documentId=archivo["id"]).execute()
            contenido = extraer_texto_google_doc(documento)
            if contenido:
                partes.append(f"\n===== DOCUMENTO: {archivo['name']} =====\n")
                partes.append(contenido)
        except HttpError as error:
            print(f"ERROR leyendo {archivo.get('name')}: {error}")

    resultado = "\n".join(partes).strip()
    return resultado if resultado else "No fue posible obtener contenido del catálogo."


def obtener_catalogo():
    ahora = time.time()
    tiempo_cache = ahora - catalogo_cache["timestamp"]

    if catalogo_cache["contenido"] and tiempo_cache < CACHE_CATALOGO_SEGUNDOS:
        return catalogo_cache["contenido"]

    print("Actualizando catálogo desde Google Drive...")
    nuevo_catalogo = cargar_catalogo_desde_google()
    catalogo_cache["contenido"] = nuevo_catalogo
    catalogo_cache["timestamp"] = ahora

    return nuevo_catalogo


# ============================================================
# LÓGICA DE GEMINI Y MODOS
# ============================================================

def determinar_modo(numero):
    if numero in NUMEROS_INTERNOS:
        return "INTERNO"
    return "CLIENTE"


def obtener_respuesta_mily(numero, mensaje):
    if numero not in conversaciones:
        conversaciones[numero] = {
            "modo": determinar_modo(numero),
            "historial": []
        }

    conversacion = conversaciones[numero]
    modo = conversacion["modo"]
    historial = conversacion["historial"]

    historial.append(
        types.Content(
            role="user",
            parts=[types.Part(text=mensaje)]
        )
    )

    historial_reciente = historial[-20:]
    catalogo = obtener_catalogo()

    contexto_catalogo = f"""
============================================================
CATÁLOGO VIGENTE — FUENTE DE VERDAD
============================================================

{catalogo}

============================================================
FIN DEL CATÁLOGO
============================================================
"""

    contexto_modo = f"""
MODO ACTUAL DEL INTERLOCUTOR: {modo}
"""

    system_instruction = (
        INSTRUCCIONES_MILY
        + contexto_modo
        + contexto_catalogo
    )

    respuesta = client.models.generate_content(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
        ),
        contents=historial_reciente,
    )

    texto = (
        respuesta.text
        or "Sí señor/señora, permítame un momento mientras reviso la información en nuestro taller."
    )

    historial.append(
        types.Content(
            role="model",
            parts=[types.Part(text=texto)]
        )
    )

    conversacion["historial"] = historial[-20:]
    return texto


# ============================================================
# AUXILIARES Y ENVÍO DE WHATSAPP
# ============================================================

def validar_firma_meta():
    if not META_APP_SECRET:
        return True

    firma = request.headers.get("X-Hub-Signature-256", "")
    if not firma.startswith("sha256="):
        return False

    cuerpo = request.get_data()
    digest = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        cuerpo,
        hashlib.sha256
    ).hexdigest()

    firma_esperada = "sha256=" + digest
    return hmac.compare_digest(firma, firma_esperada)


def enviar_whatsapp(numero, texto):
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID or not META_GRAPH_VERSION:
        print("Aviso: Faltan variables de Meta configuradas. No se envió el mensaje por WhatsApp API.")
        return None

    url = (
        f"https://graph.facebook.com/"
        f"{META_GRAPH_VERSION}/"
        f"{META_PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {
            "body": texto
        }
    }

    respuesta = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    print("WhatsApp API:", respuesta.status_code, respuesta.text)
    respuesta.raise_for_status()
    return respuesta.json()


# ============================================================
# RUTAS DEL WEBHOOK Y APLICACIÓN
# ============================================================

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        return str(challenge), 200

    return "Token incorrecto", 403


@app.route("/webhook", methods=["POST"])
def recibir_mensaje():
    if not validar_firma_meta():
        return jsonify({"status": "firma_invalida"}), 403

    datos = request.get_json(silent=True) or {}

    try:
        entry = datos.get("entry", [])
        for elemento in entry:
            cambios = elemento.get("changes", [])
            for cambio in cambios:
                valor = cambio.get("value", {})
                mensajes = valor.get("messages", [])

                for mensaje in mensajes:
                    mensaje_id = mensaje.get("id")
                    if mensaje_id:
                        if mensaje_id in mensajes_procesados:
                            continue
                        mensajes_procesados.add(mensaje_id)

                    if mensaje.get("type") != "text":
                        continue

                    numero = mensaje.get("from")
                    texto_cliente = mensaje.get("text", {}).get("body", "").strip()

                    if not numero or not texto_cliente:
                        continue

                    print(f"Mensaje recibido de {numero}: {texto_cliente}")

                    respuesta = obtener_respuesta_mily(
                        numero,
                        texto_cliente
                    )

                    print(f"Mily responde a {numero}: {respuesta}")

                    enviar_whatsapp(
                        numero,
                        respuesta
                    )

        return jsonify({"status": "recibido"}), 200

    except Exception as error:
        print("ERROR:", error)
        return jsonify({"status": "error"}), 200


@app.route("/", methods=["GET"])
def inicio():
    return jsonify({
        "status": "online",
        "assistant": "Mily 2.0",
        "company": "IronWorks HRs",
        "pinterest": "https://co.pinterest.com/industriasricos/"
    })


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
