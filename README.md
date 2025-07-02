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
- Build ottimizzato per Railway deployment

## 🛠️ Funzionalità Tecniche

- **Trascrizione audio**: OpenAI Whisper per convertire messaggi vocali in testo
- **AI Conversazionale**: GPT-3.5-turbo per risposte intelligenti e contestuali
- **Sintesi vocale**: ElevenLabs per audio di alta qualità
- **Gestione memoria**: Mantiene cronologia conversazione per continuità
- **Data/ora italiana**: Risponde con contesto temporale appropriato
- **Gestione errori**: Sistema robusto di error handling e retry
- **Scalabilità**: Ottimizzato per deployment cloud su Railway

## 🔍 Troubleshooting

Se riscontri problemi:

1. **Verifica le API Keys** - Assicurati che siano tutte configurate correttamente
2. **Controlla i logs** - Usa la dashboard Railway per vedere errori specifici
3. **Testa il token Telegram** - Verifica che il bot sia attivo su @BotFather
4. **Verifica il Voice ID** - Controlla che l'ID voce ElevenLabs sia corretto

Per problemi specifici:
- Verifica che l'audio sia chiaro e comprensibile
- Controlla la connessione internet
  
## 🤝 Contributi

Contributi benvenuti! Apri una issue o pull request.

## 📄 Licenza

Questo progetto è fornito "as-is" per scopi educativi e di apprendimento. 