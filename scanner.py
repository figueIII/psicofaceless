# scanner.py — Motor principal del sistema
import feedparser, sqlite3, schedule, time, json, logging, os
from datetime import datetime
from dotenv import load_dotenv
import openai
from sources import FEED_SOURCES, HEADERS, HOT_KEYWORDS, TRASH_KEYWORDS

# ─── Configuración inicial ────────────────────────────────────────────────────
load_dotenv()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/root/leadbot/logs/scanner.log"),
        logging.StreamHandler()  # También muestra en pantalla
    ]
)
log = logging.getLogger(__name__)

# ─── Base de datos ────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("/root/leadbot/leads.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id          TEXT PRIMARY KEY,
            source      TEXT,
            title       TEXT,
            url         TEXT,
            score       INTEGER,
            categoria   TEXT,
            presupuesto TEXT,
            razon       TEXT,
            clasificacion TEXT,
            timestamp   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_ids (
            id TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    return conn

# ─── Pre-filtro rápido (sin gastar tokens de IA) ──────────────────────────────
def pre_filter(title: str, summary: str) -> bool:
    """Filtro rápido por palabras clave ANTES de llamar a la IA (gratis)"""
    text = (title + " " + summary).lower()
    
    # Rechazar inmediatamente si tiene palabras de basura
    for word in TRASH_KEYWORDS:
        if word in text:
            return False
    
    # Aceptar si tiene al menos 1 palabra de alta intención
    for word in HOT_KEYWORDS:
        if word in text:
            return True
    
    return False  # Si no tiene señales claras, descartar

# ─── Análisis con IA (el cerebro) ────────────────────────────────────────────
def analyze_with_ai(title: str, summary: str, source: str) -> dict:
    """Envía el post a GPT-4o-mini y obtiene clasificación estructurada"""
    
    prompt = f"""
Eres un asistente de ventas B2B experto en detectar oportunidades de negocio.
Analiza este post de la plataforma "{source}" y clasifícalo.

TÍTULO: {title}
DESCRIPCIÓN: {summary[:800]}

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{{
  "clasificacion": "ORO" | "PLATA" | "BASURA",
  "score": <número del 0 al 100>,
  "categoria": "dev" | "seo" | "marketing" | "ia" | "datos" | "otro",
  "presupuesto": "alto>500€" | "medio 100-500€" | "bajo<100€" | "no_especificado",
  "urgencia": "alta" | "media" | "baja",
  "razon": "<máximo 80 caracteres explicando por qué>"
}}

REGLAS DE CLASIFICACIÓN:
- ORO (score 80-100): Tiene presupuesto claro, busca contratar YA, problema técnico concreto
- PLATA (score 50-79): Intención de contratar probable, presupuesto vago o sin urgencia extrema
- BASURA (score 0-49): Sin presupuesto, solo busca consejo gratis, spam, o irrelevante
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,  # Baja temperatura = respuestas más consistentes
            max_tokens=200
        )
        return json.loads(response.choices[0].message.content)
    
    except json.JSONDecodeError:
        log.warning(f"IA devolvió JSON inválido para: {title[:50]}")
        return {"clasificacion": "BASURA", "score": 0, "razon": "Error de parseo"}
    except Exception as e:
        log.error(f"Error llamando a OpenAI: {e}")
        return {"clasificacion": "BASURA", "score": 0, "razon": f"Error API: {str(e)[:50]}"}

# ─── Escaneo principal ────────────────────────────────────────────────────────
def scan_all_sources():
    from notifier import send_telegram_alert  # Import aquí para evitar circular
    
    conn = init_db()
    total_scanned = 0
    total_gold = 0
    
    log.info(f"🔍 Iniciando escaneo de {len(FEED_SOURCES)} fuentes...")
    
    for source_name, feed_url in FEED_SOURCES.items():
        try:
            # Leer el RSS feed
            feed = feedparser.parse(feed_url, request_headers=HEADERS)
            
            if not feed.entries:
                log.warning(f"Sin entradas en {source_name}")
                continue
            
            for entry in feed.entries[:15]:  # Máximo 15 posts por fuente
                entry_id = getattr(entry, 'id', entry.link)
                
                # ¿Ya lo hemos visto? → Saltar
                already_seen = conn.execute(
                    "SELECT id FROM seen_ids WHERE id=?", (entry_id,)
                ).fetchone()
                
                if already_seen:
                    continue
                
                # Registrar como visto
                conn.execute("INSERT OR IGNORE INTO seen_ids VALUES (?)", (entry_id,))
                total_scanned += 1
                
                title = entry.get('title', '')
                summary = entry.get('summary', entry.get('description', ''))
                url = entry.get('link', '')
                
                # Pre-filtro gratuito antes de gastar tokens de IA
                if not pre_filter(title, summary):
                    log.debug(f"[SKIP] {title[:50]}")
                    conn.commit()
                    continue
                
                # Análisis con IA
                analysis = analyze_with_ai(title, summary, source_name)
                
                # Guardar en base de datos
                conn.execute("""
                    INSERT OR IGNORE INTO leads 
                    (id, source, title, url, score, categoria, presupuesto, razon, clasificacion, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry_id, source_name, title, url,
                    analysis.get("score", 0),
                    analysis.get("categoria", "otro"),
                    analysis.get("presupuesto", "no_especificado"),
                    analysis.get("razon", ""),
                    analysis.get("clasificacion", "BASURA"),
                    datetime.now().isoformat()
                ))
                conn.commit()
                
                # 🏆 Si es ORO → alerta inmediata por Telegram
                if analysis.get("clasificacion") == "ORO":
                    total_gold += 1
                    log.info(f"🏆 LEAD ORO: Score {analysis.get('score')} | {title[:60]}")
                    send_telegram_alert(
                        source=source_name,
                        title=title,
                        url=url,
                        analysis=analysis
                    )
                elif analysis.get("clasificacion") == "PLATA":
                    log.info(f"🥈 PLATA: Score {analysis.get('score')} | {title[:60]}")
        
        except Exception as e:
            log.error(f"Error escaneando {source_name}: {e}")
            continue
    
    conn.close()
    log.info(f"✅ Escaneo completado: {total_scanned} nuevos posts, {total_gold} leads ORO")

# ─── Planificador ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("🚀 LeadBot iniciado. Escaneando cada 20 minutos...")
    
    scan_all_sources()  # Ejecución inmediata al arrancar
    
    schedule.every(20).minutes.do(scan_all_sources)
    
    while True:
        schedule.run_pending()
        time.sleep(60)
