# 🚀 Guida Deploy su Railway

## Passaggi per il Deploy

### 1. Preparazione Repository
✅ **Completato** - Il repository è già configurato con tutti i file necessari

### 2. Deploy su Railway

1. **Vai su [Railway.app](https://railway.app)**
2. **Accedi con GitHub**
3. **Clicca "New Project"**
4. **Seleziona "Deploy from GitHub repo"**
5. **Scegli il repository `stedbrown/ste-clone-bot`**

### 3. Configurazione Variabili d'Ambiente

Una volta creato il progetto, vai nella sezione **Variables** e aggiungi:

```
TELEGRAM_TOKEN=il_tuo_token_telegram
OPENAI_API_KEY=la_tua_api_key_openai  
ELEVENLABS_API_KEY=la_tua_api_key_elevenlabs
VOICE_ID=il_tuo_voice_id_elevenlabs
RAILWAY_ENVIRONMENT=production
```

### 4. Deploy Automatico

Railway farà il deploy automaticamente quando:
- Aggiungi le variabili d'ambiente
- Pushes nuovi commit al repository

### 5. Verifica Funzionamento

1. **Controlla i logs** nella dashboard Railway
2. **Testa il bot** su Telegram con `/start`
3. **Verifica health check** con `/health`

## 🔧 Comandi Bot Disponibili

- `/start` - Avvia la conversazione
- `/clear` - Cancella cronologia  
- `/health` - Health check per Railway

## 📊 Monitoraggio

### Logs Railway
```bash
# I logs mostreranno:
✅ Configurazione verificata
🤖 Bot Telegram Vocale avviato!
Ambiente: production
Avvio in modalità produzione (Railway)
```

### Health Check
- URL: La tua app avrà un URL automatico Railway
- Il bot risponderà ai health check automatici
- Comando manuale: `/health` via Telegram

## 🛠️ Troubleshooting

### Bot non risponde
1. Verifica le variabili d'ambiente su Railway
2. Controlla i logs per errori di API keys
3. Assicurati che il token Telegram sia corretto

### Errori Audio  
- Su Railway, FFmpeg è disponibile automaticamente
- I file temporanei vengono gestiti automaticamente

### Problemi di Memory/Performance
- Railway gestisce automaticamente scaling e risorse
- Il bot è ottimizzato per uso efficiente delle risorse

## 🔄 Aggiornamenti

Per aggiornare il bot:
1. Modifica il codice localmente
2. Commit e push su GitHub
3. Railway farà il redeploy automaticamente

```bash
git add .
git commit -m "Update: descrizione modifiche"
git push
``` 