# 🤖 Bot Telegram Vocale

Un bot Telegram intelligente che riceve messaggi di testo e vocali e risponde sempre con messaggi vocali utilizzando OpenAI GPT e ElevenLabs per la sintesi vocale.

## ✨ Funzionalità

- 📝 Riceve messaggi di testo e vocali
- 🧠 Utilizza OpenAI GPT-3.5-turbo per generare risposte intelligenti
- 🗣️ Converte le risposte in audio con ElevenLabs
- 💭 Mantiene la memoria della conversazione
- 🕐 Conosce data e ora in tempo reale
- 🇮🇹 Risponde in italiano in modo naturale

## 🚀 Deploy su Railway

### 1. Clona il repository
```bash
git clone https://github.com/stedbrown/ste-clone-bot.git
cd ste-clone-bot
```

### 2. Configurazione su Railway

1. Vai su [Railway.app](https://railway.app)
2. Collega il tuo account GitHub
3. Clicca "New Project" → "Deploy from GitHub repo"
4. Seleziona questo repository

### 3. Variabili d'ambiente

Configura le seguenti variabili d'ambiente su Railway:

```
TELEGRAM_TOKEN=il_tuo_token_telegram
OPENAI_API_KEY=la_tua_api_key_openai
ELEVENLABS_API_KEY=la_tua_api_key_elevenlabs
VOICE_ID=il_tuo_voice_id_elevenlabs
RAILWAY_ENVIRONMENT=production
```

### 4. Come ottenere le API Keys

#### Telegram Bot Token
1. Contatta [@BotFather](https://t.me/botfather) su Telegram
2. Usa `/newbot` e segui le istruzioni
3. Salva il token che ti viene fornito

#### OpenAI API Key
1. Vai su [OpenAI Platform](https://platform.openai.com)
2. Crea un account e vai su "API Keys"
3. Genera una nuova API key

#### ElevenLabs API Key e Voice ID
1. Registrati su [ElevenLabs](https://elevenlabs.io)
2. Vai su Profile → API Keys
3. Per il Voice ID, vai su Voices e copia l'ID della voce che preferisci

## 🛠️ Sviluppo Locale

### Prerequisiti
- Python 3.11+
- FFmpeg

### Installazione
```bash
pip install -r requirements.txt
```

### Configurazione
Crea un file `.env` con:
```
TELEGRAM_TOKEN=il_tuo_token
OPENAI_API_KEY=la_tua_key
ELEVENLABS_API_KEY=la_tua_key
VOICE_ID=voice_id
```

### Avvio
```bash
python start.py  # Verifica configurazione e avvia
# oppure
python bot.py    # Avvia direttamente
```

## 📁 Struttura del Progetto

```
├── bot.py              # Codice principale del bot
├── config.py           # Configurazione e variabili d'ambiente
├── start.py            # Script di verifica e avvio
├── requirements.txt    # Dipendenze Python
├── railway.json        # Configurazione Railway
├── Procfile           # Comando di avvio per Railway
├── runtime.txt        # Versione Python per Railway
└── README.md          # Questo file
```

## 🔧 Comandi Bot

- `/start` - Avvia/riavvia la conversazione
- `/clear` - Cancella la cronologia della conversazione
- `/health` - Verifica stato del bot (per Railway health check)

## 📝 Note

- Il bot mantiene la memoria delle ultime 10 interazioni per utente
- Tutti i messaggi audio temporanei vengono eliminati automaticamente
- Il bot è ottimizzato per l'ambiente di produzione Railway

## 🛠️ Funzionalità Tecniche

- **Gestione asincrona**: Utilizzo di asyncio per operazioni non bloccanti
- **File temporanei**: Gestione automatica di file temporanei con cleanup
- **Logging**: Sistema completo di logging per debugging
- **Gestione errori**: Handling robusto degli errori con messaggi informativi
- **Conversione audio**: Supporto automatico per formati audio Telegram

## 🚨 Troubleshooting

### Errori comuni:

**"Variabile d'ambiente non configurata"**
- Verifica che il file `.env` esista e contenga tutte le variabili

**"FFmpeg non trovato"**
- Installa FFmpeg e assicurati che sia nel PATH di sistema

**"Errore API OpenAI/ElevenLabs"**
- Verifica la validità delle API keys
- Controlla i crediti disponibili sui rispettivi servizi

**"Errore nella trascrizione"**
- Verifica che l'audio sia chiaro e comprensibile
- Controlla la connessione internet

## 🤝 Contributi

Sentiti libero di aprire issue o pull request per miglioramenti!

## 📄 Licenza

Questo progetto è fornito "as-is" per scopi educativi e di apprendimento. 