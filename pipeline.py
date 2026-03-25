#!/usr/bin/env python3
"""
PsicoFaceless Pipeline — Master Script v2.0
Stack: edge-tts + GPT-4o-mini + FAL.AI SDXL + FFmpeg
Coste objetivo: < $0.05/vídeo | Real: ~$0.005/vídeo
Instalación: pip install pytrends openai edge-tts fal-client
             google-auth google-auth-oauthlib google-api-python-client Pillow
"""

import os, json, asyncio, subprocess, csv, random
from datetime import datetime
from pathlib import Path
import edge_tts
from openai import OpenAI
import fal_client, requests
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# ── CONFIGURACIÓN ────────────────────────────────────────────
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
FAL_KEY            = os.environ["FAL_KEY"]
YOUTUBE_TOKEN_FILE = os.environ.get("YOUTUBE_TOKEN_FILE", "/opt/psicofaceless/yt_token.json")
GUMROAD_LINK       = os.environ.get("GUMROAD_LINK", "https://gumroad.com/TU_ENLACE")
OUTPUT_DIR         = Path("/opt/psicofaceless/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)
os.environ["FAL_KEY"] = FAL_KEY

FALLBACK_TOPICS = [
    "sesgo de confirmación", "efecto halo", "psicología del dinero",
    "lenguaje corporal", "efecto Dunning-Kruger", "sesgo de anclaje",
    "psicología de las decisiones", "memoria y estrés", "disonancia cognitiva",
    "efecto Baader-Meinhof", "principio de reciprocidad", "fatiga de decisión"
]

# ── MÓDULO 1: TRENDING TOPIC ──────────────────────────────────
def get_trending_topic() -> str:
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="es-ES", tz=60, timeout=(10, 30))
        trending = pt.trending_searches(pn="spain")[0].tolist()[:20]

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content":
                f"De estas búsquedas trending en España: {trending}\n"
                "Elige el tema MÁS relacionado con psicología o comportamiento humano. "
                "Si ninguno encaja, responde: 'sesgos cognitivos'. "
                "Responde SOLO con el tema, sin explicación."}],
            max_tokens=20
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"   ⚠️  Trending fallback activado: {e}")
        return random.choice(FALLBACK_TOPICS)

# ── MÓDULO 2: GENERACIÓN DE SCRIPT ───────────────────────────
def generate_script(topic: str) -> dict:
    system = """Eres un guionista experto en psicología para YouTube Shorts en español.
Genera scripts de 60 segundos (≈150 palabras) con:
- Hook en los primeros 3 segundos (dato sorprendente o pregunta)
- UN concepto psicológico concreto + ejemplo cotidiano
- CTA final: "Suscríbete para más psicología diaria 🧠"

Responde ÚNICAMENTE en JSON válido con esta estructura exacta:
{
  "title": "string — título SEO máx 60 chars, con emoji",
  "script_text": "string — texto completo para narrar",
  "description": "string — descripción YouTube máx 150 chars",
  "hashtags": ["lista", "de", "10", "hashtags", "sin", "almohadilla"],
  "thumbnail_prompt": "string — prompt en inglés para Stable Diffusion"
}"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Genera el script sobre: {topic}"}
        ],
        max_tokens=900,
        temperature=0.85,
        response_format={"type": "json_object"}
    )
    return json.loads(resp.choices[0].message.content)

# ── MÓDULO 3: TEXT-TO-SPEECH (GRATIS) ────────────────────────
async def generate_tts(text: str, output_path: Path) -> Path:
    """
    Voces disponibles en español:
      es-MX-JorgeNeural    → hombre mexicano (LATAM, alta retención)
      es-ES-AlvaroNeural   → hombre castellano
      es-ES-ElviraNeural   → mujer castellana
    """
    communicate = edge_tts.Communicate(
        text=text,
        voice="es-MX-JorgeNeural",
        rate="+8%",      # ligeramente más rápido para Shorts
        volume="+15%",
        pitch="+0Hz"
    )
    await communicate.save(str(output_path))
    return output_path

# ── MÓDULO 4: GENERACIÓN DE IMAGEN (SDXL via fal.ai) ─────────
def generate_thumbnail(prompt: str, output_path: Path) -> Path:
    enhanced = (
        f"{prompt}, dark cinematic background, minimalist design, "
        "dramatic moody lighting, high contrast, 8K quality, "
        "psychology YouTube thumbnail style, no text, no watermarks, "
        "photorealistic, award-winning photography"
    )
    result = fal_client.subscribe(
        "fal-ai/fast-sdxl",
        arguments={
            "prompt": enhanced,
            "negative_prompt": "text, watermark, blurry, low quality, cartoon, anime, logo",
            "image_size": "landscape_16_9",   # 1344×768
            "num_inference_steps": 28,
            "guidance_scale": 7.5,
            "num_images": 1,
            "enable_safety_checker": False
        }
    )
    img_url = result["images"][0]["url"]
    with open(output_path, "wb") as f:
        f.write(requests.get(img_url, timeout=30).content)
    return output_path

# ── MÓDULO 5: ENSAMBLAJE CON FFMPEG ──────────────────────────
def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(audio_path)],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])

def build_srt(script_text: str, duration: float, srt_path: Path):
    """Genera archivo .srt sincronizado con el audio."""
    words = script_text.split()
    words_per_chunk = max(6, int(len(words) / (duration / 3.0)))
    chunks = [words[i:i+words_per_chunk]
              for i in range(0, len(words), words_per_chunk)]

    def to_srt_time(s):
        h, m = int(s//3600), int((s%3600)//60)
        sec, ms = int(s%60), int((s%1)*1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks, 1):
            start = (i-1) * (duration / len(chunks))
            end   = i * (duration / len(chunks))
            f.write(f"{i}\n{to_srt_time(start)} --> {to_srt_time(end)}\n")
            f.write(" ".join(chunk) + "\n\n")

def assemble_video(bg_image: Path, audio: Path, script: str,
                   title: str, output: Path) -> Path:
    """
    Pipeline FFmpeg completo:
    1. Imagen de fondo → escalar a 1080×1920 (vertical Shorts)
    2. Overlay semitransparente negro (legibilidad)
    3. Título animado en los primeros 3 segundos
    4. Subtítulos via .srt con estilo custom
    5. Merge con audio
    """
    W, H = 1080, 1920
    duration = get_audio_duration(audio)
    srt_path = output.parent / f"{output.stem}.srt"
    build_srt(script, duration, srt_path)

    # Escapar caracteres especiales para filtro drawtext de FFmpeg
    title_esc = title.replace("'", "\u2019").replace(":", " ").replace(",", " ")
    srt_esc   = str(srt_path).replace("\\", "/").replace(":", "\\:")
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    filter_complex = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1[bg];"
        # Overlay oscuro para legibilidad
        f"[bg]drawbox=x=0:y=0:w={W}:h={H}:color=black@0.50:t=fill[dark];"
        # Título (aparece 0-3.5s con fade-out)
        f"[dark]drawtext="
        f"fontfile='{font_path}':"
        f"text='{title_esc}':"
        f"fontsize=54:fontcolor=white:x=(w-text_w)/2:y=h*0.10:"
        f"shadowcolor=black:shadowx=4:shadowy=4:"
        f"enable='between(t,0,3.5)'[titled];"
        # Barra de progreso inferior
        f"[titled]drawbox="
        f"x=0:y={H-8}:w=iw*t/{duration}:h=8:"
        f"color=0xFF4757@0.9:t=fill[progress];"
        # Subtítulos desde archivo .srt
        f"[progress]subtitles='{srt_esc}':"
        f"force_style='FontName=DejaVu Sans Bold,FontSize=46,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        f"BorderStyle=3,Outline=4,Shadow=3,Alignment=2,MarginV=140'[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(bg_image),
        "-i", str(audio),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",  # Optimizado para streaming
        str(output)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output

# ── MÓDULO 6: UPLOAD YOUTUBE ──────────────────────────────────
def upload_youtube(video: Path, title: str, description: str,
                   hashtags: list) -> str:
    creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN_FILE)
    yt    = build("youtube", "v3", credentials=creds)

    full_desc = (
        f"{description}\n\n"
        f"🧠 Psicología práctica para tu vida diaria\n"
        f"🔔 Suscríbete para el sesgo cognitivo del día\n"
        f"📚 Ebook GRATIS: {GUMROAD_LINK}\n\n"
        + " ".join(f"#{t}" for t in hashtags)
    )
    body = {
        "snippet": {
            "title": title, "description": full_desc,
            "tags": hashtags + ["psicologia","shorts","viral","mente"],
            "categoryId": "27",
            "defaultLanguage": "es", "defaultAudioLanguage": "es"
        },
        "status": {
            "privacyStatus": "public",   # ← cambiar a "private" para tests
            "selfDeclaredMadeForKids": False
        }
    }
    media   = MediaFileUpload(str(video), mimetype="video/mp4",
                              resumable=True, chunksize=256*1024)
    request = yt.videos().insert(part="snippet,status",
                                 body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    vid_id = response["id"]
    print(f"   ✅ https://youtube.com/shorts/{vid_id}")
    return vid_id

# ── MÓDULO 7: LOG CSV ─────────────────────────────────────────
def log_run(topic, title, vid_id, cost):
    log = OUTPUT_DIR / "production_log.csv"
    exists = log.exists()
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts","topic","title","yt_id","cost"])
        if not exists: w.writeheader()
        w.writerow({"ts": datetime.now().isoformat(), "topic": topic,
                    "title": title, "yt_id": vid_id, "cost": f"{cost:.4f}"})

# ── PIPELINE MAESTRO ──────────────────────────────────────────
async def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*50}\n🚀 PsicoFaceless Pipeline — {ts}\n{'='*50}")

    print("📊 [1/6] Trending topic...")
    topic = get_trending_topic()
    print(f"   → {topic}")

    print("✍️  [2/6] Generando script (GPT-4o-mini)...")
    data = generate_script(topic)
    print(f"   → {data['title']}")

    print("🎙️  [3/6] Generando audio (edge-tts, gratis)...")
    audio = OUTPUT_DIR / f"audio_{ts}.mp3"
    await generate_tts(data["script_text"], audio)

    print("🎨 [4/6] Generando imagen (SDXL, $0.003)...")
    image = OUTPUT_DIR / f"thumb_{ts}.jpg"
    generate_thumbnail(data["thumbnail_prompt"], image)

    print("🎬 [5/6] Ensamblando vídeo (FFmpeg, gratis)...")
    video = OUTPUT_DIR / f"video_{ts}.mp4"
    assemble_video(image, audio, data["script_text"], data["title"], video)
    size_mb = video.stat().st_size / 1024 / 1024
    print(f"   → Vídeo listo: {size_mb:.1f} MB")

    print("📤 [6/6] Subiendo a YouTube...")
    vid_id = upload_youtube(video, data["title"],
                            data["description"], data["hashtags"])

    cost = 0.002 + 0.003  # GPT-4o-mini + SDXL
    log_run(topic, data["title"], vid_id, cost)

    # Limpiar temporales
    audio.unlink(missing_ok=True)
    image.unlink(missing_ok=True)

    print(f"\n💰 Coste total: ${cost:.4f} | Vídeo: {vid_id}\n")

if __name__ == "__main__":
    asyncio.run(main())
