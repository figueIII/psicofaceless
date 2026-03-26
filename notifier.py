# notifier.py — Sistema de alertas por Telegram
import requests, os, logging
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Emojis por categoría para hacer los mensajes más visuales
CATEGORY_EMOJI = {
    "dev":       "💻",
    "seo":       "🔍",
    "marketing": "📣",
    "ia":        "🤖",
    "datos":     "📊",
    "otro":      "📌"
}

BUDGET_EMOJI = {
    "alto>500€":        "💰💰💰",
    "medio 100-500€":   "💰💰",
    "bajo<100€":        "💰",
    "no_especificado":  "❓"
}

def send_telegram_alert(source: str, title: str, url: str, analysis: dict):
    """Envía alerta formateada a Telegram cuando aparece un lead ORO"""
    
    cat_emoji    = CATEGORY_EMOJI.get(analysis.get("categoria", "otro"), "📌")
    budget_emoji = BUDGET_EMOJI.get(analysis.get("presupuesto", "no_especificado"), "❓")
    
    message = f"""
🏆 *LEAD ORO DETECTADO*

{cat_emoji} *Categoría:* {analysis.get('categoria', 'N/A').upper()}
📊 *Score IA:* {analysis.get('score', 0)}/100
{budget_emoji} *Presupuesto:* {analysis.get('presupuesto', 'N/D')}
⚡ *Urgencia:* {analysis.get('urgencia', 'N/D').upper()}

📝 *Post:* {title[:120]}

💡 *Por qué es ORO:*
_{analysis.get('razon', 'Sin razonamiento')}_

🔗 [Ver Lead Completo]({url})

🕐 Fuente: `{source}`
"""
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(api_url, json=payload, timeout=10)
        
        if response.status_code == 200:
            log.info(f"📱 Alerta Telegram enviada: {title[:50]}")
        else:
            log.warning(f"Error Telegram {response.status_code}: {response.text[:100]}")
    
    except requests.exceptions.RequestException as e:
        log.error(f"No se pudo enviar alerta Telegram: {e}")

def send_daily_summary(stats: dict):
    """Resumen diario automatizado (opcional)"""
    message = f"""
📈 *RESUMEN DIARIO LEADBOT*

🔍 Posts analizados: {stats.get('scanned', 0)}
🏆 Leads ORO: {stats.get('gold', 0)}
🥈 Leads Plata: {stats.get('silver', 0)}
🗑️ Descartados: {stats.get('trash', 0)}

💡 Coste IA estimado: ~${stats.get('cost_usd', 0):.4f}
"""
    send_telegram_alert.__wrapped__ if hasattr(send_telegram_alert, '__wrapped__') else None
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(api_url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }, timeout=10)
