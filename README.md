# bot_mensa

Bot Telegram per inviare automaticamente le storie Instagram di `@cx_food` agli iscritti.

## Requisiti
- Python 3.8+
- `gallery-dl` installato e disponibile in PATH

## Installazione
```bash
python -m venv venv
# Windows PowerShell
.\\venv\\Scripts\\Activate.ps1
# o cmd
.\\venv\\Scripts\\activate.bat

pip install -r requirements.txt
```

## Configurazione
- Copia `.env.example` in `.env` e imposta `TELEGRAM_TOKEN` (non committare `.env`).
- Nel `.env` puoi sovrascrivere anche i percorsi (`DOWNLOAD_DIR`, `COOKIES_FILE`, `ARCHIVE_FILE`, `ISCRITTI_FILE`, `LOG_FILE`); se non li imposti vengono usati i default in `bot_mensa.py`.
- Assicurati che `gallery-dl` sia installato e che `COOKIES_FILE` sia valido (vedi sezioni sotto).

## Esecuzione
```bash
python bot_mensa.py
```

## Come funziona `gallery-dl`
`gallery-dl` è un downloader da riga di comando per gallerie e contenuti social (Instagram compreso). Il bot non lo importa come libreria: lo lancia come sottoprocesso ad ogni controllo (vedi `scarica_storie()` in `bot_mensa.py`) con un comando equivalente a:

```bash
gallery-dl \
  -d ./downloads \
  --cookies ./cookies.txt \
  --download-archive ./downloads/storie_archive.txt \
  --sleep-request 3 \
  https://www.instagram.com/stories/cx_food/
```

I percorsi mostrati sono i default (relativi alla cartella del progetto) e sono sovrascrivibili dal `.env`.

Cosa fa ogni flag:
- `-d` → cartella di destinazione (`DOWNLOAD_DIR`). I file finiscono in sottocartelle tipo `instagram/cx_food/`.
- `--cookies` → file dei cookie di una sessione Instagram autenticata. Le storie **non** sono visibili senza login, quindi questo file è obbligatorio.
- `--download-archive` → file di testo in cui `gallery-dl` annota gli ID dei post già scaricati, così alla volta dopo li salta. È ciò che evita di rinviare due volte la stessa storia.
- `--sleep-request 3` → attende 3 secondi tra le richieste HTTP, per non farsi rate-limitare da Instagram.

Il bot determina quali file sono nuovi confrontando lo snapshot della cartella prima e dopo l'esecuzione di `gallery-dl`: i file comparsi vengono inviati agli iscritti.

Installazione rapida:
```bash
pip install gallery-dl
gallery-dl --version    # verifica che sia nel PATH
```

## Come ottenere il file `cookies.txt`
Instagram richiede di essere loggati per vedere le storie. `gallery-dl` legge i cookie di sessione da un file in **formato Netscape** (`cookies.txt`). Procedura consigliata:

1. **Crea un account Instagram dedicato** al bot (consigliato: evita di usare il tuo account personale — se Instagram rileva attività anomala può bloccarlo temporaneamente).
2. **Apri il browser e fai login** su `https://www.instagram.com` con quell'account. Verifica di riuscire a vedere le storie di `@cx_food` manualmente.
3. **Installa un'estensione** che esporta i cookie nel formato Netscape, per esempio:
   - Chrome/Edge: "Get cookies.txt LOCALLY"
   - Firefox: "cookies.txt"
4. **Esporta i cookie del dominio `instagram.com`** e salva il file come `cookies.txt` nel percorso indicato da `COOKIES_FILE` (default: `cookies.txt` nella cartella del progetto).
5. **Test manuale** prima di lanciare il bot:
   ```bash
   gallery-dl --cookies ./cookies.txt https://www.instagram.com/stories/cx_food/
   ```
   Se scarica almeno un file, i cookie funzionano. Se vedi errori `401`/`login required`, i cookie sono scaduti o non validi.

### Manutenzione dei cookie
- I cookie di Instagram **scadono** (tipicamente dopo qualche settimana, o subito se Instagram rileva un IP nuovo / un logout). Quando il bot smette di scaricare nuove storie, la prima cosa da controllare è il log: se compaiono errori HTTP 401 o messaggi di login richiesto, rigenera `cookies.txt` ripetendo i passi 2-4.
- **Non fare logout dal browser** con cui hai esportato i cookie: in molti casi Instagram invalida la sessione anche lato server.
- **Non committare mai `cookies.txt`** (è già in `.gitignore`): contiene credenziali di sessione equivalenti a una password.

## Note sulla sicurezza
- Non committare mai token o `cookies.txt`.
- Se hai committato il token accidentalmente, rigenera il token con BotFather e rimuovilo dalla storia Git.

## Licensing
Distribuito con licenza [MIT](LICENSE) — uso libero anche commerciale, basta mantenere il copyright.
