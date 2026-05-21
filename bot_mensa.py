"""
Bot Telegram multi-utente per le storie di una mensa Instagram.

Funziona così:
- Gira sempre, in background, sul PC.
- Gli utenti scrivono /start al bot per iscriversi, /stop per disiscriversi.
- Ogni INTERVALLO_MIN minuti (solo nelle finestre orarie configurate) controlla
  le storie nuove e le manda a tutti gli iscritti.
- Ogni notte all'ora configurata cancella i file media piu' vecchi di
  ETA_MAX_ORE ore, mantenendo l'archivio per evitare ri-download.

Requisiti:
  pip install "python-telegram-bot[job-queue]" requests
  gallery-dl installato e nel PATH
"""

import asyncio
import json
import os
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============= CONFIGURAZIONE =============
# Carica variabili da .env se python-dotenv è installato
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "INCOLLA_TELEGRAM_TOKEN")

# Cartella di base: stessa in cui sta bot_mensa.py.
# Cosi' funziona qualunque sia la working directory da cui viene lanciato.
BASE_DIR = Path(__file__).resolve().parent

USERNAME       = "cx_food"
DOWNLOAD_DIR   = os.environ.get("DOWNLOAD_DIR",  str(BASE_DIR / "downloads"))
COOKIES_FILE   = os.environ.get("COOKIES_FILE",  str(BASE_DIR / "cookies.txt"))
ARCHIVE_FILE   = os.environ.get("ARCHIVE_FILE",  str(BASE_DIR / "downloads" / "storie_archive.txt"))
ISCRITTI_FILE  = os.environ.get("ISCRITTI_FILE", str(BASE_DIR / "iscritti.json"))
LOG_FILE       = os.environ.get("LOG_FILE",      str(BASE_DIR / "storie_log.txt"))

# Quanto spesso controllare le storie (in minuti)
INTERVALLO_MIN = 30

# Finestre orarie in cui controllare (fuori da queste non scarica nulla).
# Imposta a None per controllare sempre.
FINESTRE = [
    (time(9, 0),  time(13, 0)),   # pranzo
    (time(16, 0), time(19, 30)),  # cena
]

# Pulizia automatica: cancella file media piu' vecchi di X ore
ETA_MAX_ORE  = 24
ORA_PULIZIA  = time(22, 0)   # ogni sera alle 22:00
# ==========================================


MEDIA_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".gif"}


# ---------- Log ----------
def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- Gestione iscritti ----------
def carica_iscritti() -> set[int]:
    p = Path(ISCRITTI_FILE)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        log(f"Errore caricamento iscritti: {e}")
        return set()


def salva_iscritti(iscritti: set[int]):
    Path(ISCRITTI_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(ISCRITTI_FILE).write_text(
        json.dumps(sorted(iscritti)), encoding="utf-8"
    )


# ---------- Comandi del bot ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    iscritti = carica_iscritti()

    if chat_id in iscritti:
        await update.message.reply_text(
            "Sei gia' iscritto. Riceverai il menu della mensa appena viene pubblicato.\n"
            "Usa /stop per disiscriverti."
        )
        return

    iscritti.add(chat_id)
    salva_iscritti(iscritti)
    log(f"Nuovo iscritto: {chat_id}")

    await update.message.reply_text(
        f"Iscritto! Riceverai automaticamente le storie di @{USERNAME} "
        "(menu pranzo e cena) appena vengono pubblicate.\n\n"
        "Comandi:\n"
        "/stop - disiscriviti\n"
        "/status - info"
    )

    # Invio "arretrato": tutte le storie ancora su disco delle ultime ETA_MAX_ORE
    disponibili = media_recenti()
    if not disponibili:
        return

    await update.message.reply_text(
        f"Intanto ecco le {len(disponibili)} storie gia' pubblicate..."
    )
    for f in disponibili:
        ok = await invia_a_utente(context, chat_id, f)
        if not ok:
            # ha bloccato il bot subito? rimuovilo e basta
            iscritti = carica_iscritti()
            iscritti.discard(chat_id)
            salva_iscritti(iscritti)
            return
        # piu' lento di invia_a_tutti perche' qui mandiamo molti messaggi
        # allo stesso utente: Telegram limita a ~1 msg/sec per chat
        await asyncio.sleep(1.0)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    iscritti = carica_iscritti()
    if chat_id in iscritti:
        iscritti.discard(chat_id)
        salva_iscritti(iscritti)
        log(f"Disiscritto: {chat_id}")
        await update.message.reply_text("Disiscritto. Non riceverai piu' storie.")
    else:
        await update.message.reply_text("Non eri iscritto. Usa /start per iscriverti.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iscritti = carica_iscritti()
    in_lista = update.effective_chat.id in iscritti
    await update.message.reply_text(
        f"Account monitorato: @{USERNAME}\n"
        f"Iscritti totali: {len(iscritti)}\n"
        f"Sei iscritto: {'si' if in_lista else 'no'}"
    )


# ---------- Scarica e invia storie ----------
def in_finestra() -> bool:
    if not FINESTRE:
        return True
    ora = datetime.now().time()
    return any(inizio <= ora <= fine for inizio, fine in FINESTRE)


def snapshot_files(directory: str) -> set[str]:
    p = Path(directory)
    if not p.exists():
        return set()
    return {str(f) for f in p.rglob("*") if f.is_file()}


def media_recenti() -> list[str]:
    """File media presenti su disco non piu' vecchi di ETA_MAX_ORE, in ordine cronologico."""
    base = Path(DOWNLOAD_DIR)
    if not base.exists():
        return []
    soglia_ts = (datetime.now() - timedelta(hours=ETA_MAX_ORE)).timestamp()
    files = []
    for f in base.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in MEDIA_EXT:
            continue
        try:
            mt = f.stat().st_mtime
            if mt >= soglia_ts:
                files.append((mt, str(f)))
        except OSError:
            pass
    files.sort()  # piu' vecchio prima, cosi' arrivano nell'ordine in cui sono stati postati
    return [path for _, path in files]


async def scarica_storie() -> list[str]:
    """Esegue gallery-dl e restituisce la lista dei file media nuovi."""
    Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    prima = snapshot_files(DOWNLOAD_DIR)

    cmd = [
        "gallery-dl",
        "-d", DOWNLOAD_DIR,
        "--cookies", COOKIES_FILE,
        "--download-archive", ARCHIVE_FILE,
        "--sleep-request", "3",
        f"https://www.instagram.com/stories/{USERNAME}/",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if stdout.strip():
            log("gallery-dl: " + stdout.decode(errors="replace").strip())
        if stderr.strip():
            log("gallery-dl (err): " + stderr.decode(errors="replace").strip())
    except asyncio.TimeoutError:
        log("gallery-dl timeout (5 min).")
        return []
    except FileNotFoundError:
        log("gallery-dl non trovato nel PATH.")
        return []

    dopo = snapshot_files(DOWNLOAD_DIR)
    nuovi = sorted(
        f for f in (dopo - prima)
        if Path(f).suffix.lower() in MEDIA_EXT
    )
    return nuovi


async def invia_a_utente(context: ContextTypes.DEFAULT_TYPE, chat_id: int, filepath: str) -> bool:
    """Manda un file a un singolo chat_id. Ritorna False se l'utente non e' raggiungibile."""
    ext = Path(filepath).suffix.lower()
    # Uso la data del file invece di datetime.now(): se sto inviando storie
    # gia' scaricate ore fa, dire "ora attuale" sarebbe fuorviante.
    try:
        ts = datetime.fromtimestamp(Path(filepath).stat().st_mtime)
        caption = f"Storia @{USERNAME} - {ts:%H:%M del %d/%m}"
    except OSError:
        caption = f"Storia @{USERNAME}"

    try:
        with open(filepath, "rb") as f:
            if ext in {".jpg", ".jpeg", ".png", ".webp"}:
                await context.bot.send_photo(chat_id, photo=f, caption=caption)
            elif ext in {".mp4", ".mov"}:
                await context.bot.send_video(chat_id, video=f, caption=caption)
            else:
                await context.bot.send_document(chat_id, document=f, caption=caption)
        return True
    except Exception as e:
        msg = str(e).lower()
        log(f"Errore invio a {chat_id}: {e}")
        # solo errori "definitivi" segnalano un utente da rimuovere
        if "blocked" in msg or "chat not found" in msg or "deactivated" in msg:
            return False
        return True


async def invia_a_tutti(context: ContextTypes.DEFAULT_TYPE, filepath: str):
    """Manda un file a tutti gli iscritti. Rimuove chi ha bloccato il bot."""
    iscritti = carica_iscritti()
    if not iscritti:
        return

    da_rimuovere = set()
    for chat_id in iscritti:
        ok = await invia_a_utente(context, chat_id, filepath)
        if not ok:
            da_rimuovere.add(chat_id)
        await asyncio.sleep(0.05)

    if da_rimuovere:
        iscritti -= da_rimuovere
        salva_iscritti(iscritti)
        log(f"Rimossi {len(da_rimuovere)} iscritti non raggiungibili.")


async def job_check_storie(context: ContextTypes.DEFAULT_TYPE):
    if not in_finestra():
        return
    log("Controllo storie...")
    nuovi = await scarica_storie()
    if not nuovi:
        log("Nessuna nuova storia.")
        return

    iscritti = carica_iscritti()
    log(f"Trovate {len(nuovi)} storie. Invio a {len(iscritti)} iscritti.")
    for f in nuovi:
        await invia_a_tutti(context, f)


# ---------- Pulizia file vecchi ----------
async def job_pulizia(context: ContextTypes.DEFAULT_TYPE):
    """Cancella file media piu' vecchi di ETA_MAX_ORE dalla cartella download."""
    base = Path(DOWNLOAD_DIR)
    if not base.exists():
        return

    soglia = datetime.now() - timedelta(hours=ETA_MAX_ORE)
    soglia_ts = soglia.timestamp()

    eliminati = 0
    errori = 0
    byte_liberati = 0

    for f in base.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in MEDIA_EXT:
            continue
        try:
            stat = f.stat()
            if stat.st_mtime < soglia_ts:
                byte_liberati += stat.st_size
                f.unlink()
                eliminati += 1
        except Exception as e:
            errori += 1
            log(f"Errore eliminazione {f}: {e}")

    # rimuovi cartelle vuote (dalle piu' profonde alle meno profonde)
    cartelle_rimosse = 0
    tutte_dir = sorted(
        (d for d in base.rglob("*") if d.is_dir()),
        key=lambda x: len(x.parts),
        reverse=True,
    )
    for d in tutte_dir:
        try:
            d.rmdir()  # fallisce se non vuota, ed e' ok
            cartelle_rimosse += 1
        except OSError:
            pass

    mb = byte_liberati / (1024 * 1024)
    log(
        f"Pulizia: eliminati {eliminati} file ({mb:.1f} MB), "
        f"{cartelle_rimosse} cartelle vuote rimosse, errori: {errori}"
    )


# ---------- Main ----------
def main():
    if TELEGRAM_TOKEN.startswith("INCOLLA"):
        print("ERRORE: imposta TELEGRAM_TOKEN nel file.")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))

    # Controllo storie: primo dopo 10s, poi ogni INTERVALLO_MIN minuti
    app.job_queue.run_repeating(
        job_check_storie,
        interval=INTERVALLO_MIN * 60,
        first=10,
    )

    # Pulizia file vecchi: una volta al giorno a ORA_PULIZIA
    app.job_queue.run_daily(job_pulizia, time=ORA_PULIZIA)

    log(
        f"Bot avviato. Controllo ogni {INTERVALLO_MIN} min nelle finestre, "
        f"pulizia file >{ETA_MAX_ORE}h ogni giorno alle {ORA_PULIZIA:%H:%M}."
    )
    app.run_polling()


if __name__ == "__main__":
    main()
