import os
import logging
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime
import pytz

from openai import OpenAI
import requests
from pydub import AudioSegment
from pydub.utils import which
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import elevenlabs

from config import TELEGRAM_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, VOICE_ID

# Configurazione per Railway
PORT = int(os.environ.get('PORT', 8000))
RAILWAY_ENVIRONMENT = os.environ.get('RAILWAY_ENVIRONMENT', 'development')

# Configura percorso FFmpeg per ambiente di produzione
try:
    import subprocess
    # Su Railway/Linux, FFmpeg dovrebbe essere disponibile nel PATH
    if RAILWAY_ENVIRONMENT == 'production' or os.name != 'nt':
        AudioSegment.converter = "ffmpeg"
        AudioSegment.ffmpeg = "ffmpeg"
        AudioSegment.ffprobe = "ffprobe"
        logging.info("FFmpeg configurato per ambiente di produzione")
    else:
        # Configurazione Windows (development)
        if os.name == 'nt':  # Windows
            try:
                result = subprocess.run(['cmd', '/c', 'where', 'ffmpeg'], 
                                      capture_output=True, text=True, check=True)
                ffmpeg_path = result.stdout.strip().split('\n')[0]
                if ffmpeg_path and os.path.exists(ffmpeg_path):
                    AudioSegment.converter = ffmpeg_path
                    AudioSegment.ffmpeg = ffmpeg_path
                    AudioSegment.ffprobe = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe")
                    logging.info(f"FFmpeg configurato: {ffmpeg_path}")
                else:
                    raise FileNotFoundError("FFmpeg non trovato nel PATH")
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Percorsi comuni di FFmpeg su Windows come fallback
                fallback_paths = [
                    r"C:\ffmpeg\bin\ffmpeg.exe",
                    r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
                    r"C:\Users\{}\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe".format(os.getenv('USERNAME', '')),
                    which("ffmpeg")
                ]
                
                for ffmpeg_path in fallback_paths:
                    if ffmpeg_path and os.path.exists(ffmpeg_path):
                        AudioSegment.converter = ffmpeg_path
                        AudioSegment.ffmpeg = ffmpeg_path
                        AudioSegment.ffprobe = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe")
                        logging.info(f"FFmpeg configurato (fallback): {ffmpeg_path}")
                        break
                else:
                    logging.warning("FFmpeg non trovato! Il bot potrebbe non funzionare con i messaggi vocali.")
        
except Exception as e:
    logging.warning(f"Impossibile configurare FFmpeg: {e}")
    # Usa configurazione predefinita
    AudioSegment.converter = "ffmpeg"
    AudioSegment.ffmpeg = "ffmpeg"
    AudioSegment.ffprobe = "ffprobe"

# Configurazione logging per produzione
log_level = logging.INFO if RAILWAY_ENVIRONMENT == 'production' else logging.DEBUG
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
logger = logging.getLogger(__name__)

# Configura OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Configura ElevenLabs
elevenlabs.set_api_key(ELEVENLABS_API_KEY)

class TelegramBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        # Dizionario per mantenere la cronologia delle conversazioni per ogni utente
        # Struttura: {user_id: [{"role": "user/assistant", "content": "messaggio"}, ...]}
        self.conversation_history = {}
        self.max_history_length = 10  # Mantieni gli ultimi 10 messaggi per utente
        self.setup_handlers()
    
    def setup_handlers(self):
        """Configura i gestori per i messaggi del bot"""
        # Gestore per il comando /start
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Gestore per il comando /clear (cancella cronologia)
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        
        # Gestore per il comando /health (per Railway health check)
        self.application.add_handler(CommandHandler("health", self.health_command))
        
        # Gestore per messaggi vocali
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_message))
        
        # Gestore per messaggi di testo
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        
        # Gestore degli errori
        self.application.add_error_handler(self.error_handler)
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Gestisce gli errori del bot"""
        logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
        
        # Prova a notificare l'utente se possibile
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "🔧 Si è verificato un errore temporaneo. Riprova tra qualche istante."
                )
            except Exception as e:
                logger.error(f"Impossibile inviare messaggio di errore: {e}")
    
    async def health_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Health check endpoint per Railway"""
        await update.message.reply_text("🤖 Bot attivo e funzionante!")
        logger.info("Health check eseguito")
    
    def get_user_history(self, user_id: int) -> list:
        """Ottieni la cronologia delle conversazioni per un utente"""
        return self.conversation_history.get(user_id, [])
    
    def add_to_history(self, user_id: int, role: str, content: str):
        """Aggiungi un messaggio alla cronologia dell'utente"""
        if user_id not in self.conversation_history:
            self.conversation_history[user_id] = []
        
        self.conversation_history[user_id].append({
            "role": role,
            "content": content
        })
        
        # Mantieni solo gli ultimi N messaggi per evitare che la cronologia diventi troppo lunga
        if len(self.conversation_history[user_id]) > self.max_history_length:
            self.conversation_history[user_id] = self.conversation_history[user_id][-self.max_history_length:]
    
    def clear_user_history(self, user_id: int):
        """Cancella la cronologia di un utente"""
        if user_id in self.conversation_history:
            del self.conversation_history[user_id]
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /start"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        # Cancella la cronologia precedente
        self.clear_user_history(user_id)
        
        welcome_message = (
            f"🤖 Ciao {user_name}! Sono il tuo assistente vocale.\n\n"
            "Puoi inviarmi:\n"
            "📝 Messaggi di testo\n"
            "🎤 Messaggi vocali\n\n"
            "Ti risponderò sempre con un messaggio vocale! 🔊\n\n"
            "💭 Mantengo la memoria della nostra conversazione.\n"
            "🔄 Usa /start per ricominciare da capo."
        )
        await update.message.reply_text(welcome_message)
    
    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /clear per cancellare la cronologia"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        # Cancella la cronologia dell'utente
        self.clear_user_history(user_id)
        
        clear_message = (
            f"🧹 Cronologia cancellata, {user_name}!\n\n"
            "La nostra conversazione riparte da zero. 🔄"
        )
        await update.message.reply_text(clear_message)
    
    async def handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce i messaggi vocali"""
        status_message = None
        try:
            logger.info(f"Ricevuto messaggio vocale da {update.effective_user.first_name}")
            
            # Invia messaggio di stato
            status_message = await update.message.reply_text("🎤 Sto elaborando il tuo messaggio vocale...")
            
            # Scarica il file audio con timeout
            voice_file = await update.message.voice.get_file()
            
            with tempfile.TemporaryDirectory() as temp_dir:
                # Percorsi dei file temporanei
                oga_path = os.path.join(temp_dir, "voice.oga")
                
                # Scarica il file vocale
                await voice_file.download_to_drive(oga_path)
                logger.info(f"File audio scaricato: {oga_path}")
                
                # Trascrivi con OpenAI Whisper (supporta direttamente OGG/OGA)
                await status_message.edit_text("📝 Sto trascrivendo il messaggio...")
                text = await self.transcribe_audio(oga_path)
                
                if not text:
                    await status_message.edit_text("❌ Non sono riuscito a trascrivere l'audio. Riprova!")
                    return
                
                logger.info(f"Testo trascritto: {text}")
                
                # Genera risposta e invia audio
                await self.process_and_respond(text, update, status_message)
        
        except Exception as e:
            logger.error(f"Errore nell'elaborazione del messaggio vocale: {e}", exc_info=True)
            try:
                if status_message:
                    await status_message.edit_text("❌ Si è verificato un errore nell'elaborazione del messaggio vocale.")
                else:
                    await update.message.reply_text("❌ Si è verificato un errore nell'elaborazione del messaggio vocale.")
            except Exception as reply_error:
                logger.error(f"Errore nell'invio del messaggio di errore: {reply_error}")
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce i messaggi di testo"""
        try:
            text = update.message.text
            logger.info(f"Ricevuto messaggio di testo da {update.effective_user.first_name}: {text}")
            
            # Invia messaggio di stato
            status_message = await update.message.reply_text("💭 Sto pensando alla risposta...")
            
            # Genera risposta e invia audio
            await self.process_and_respond(text, update, status_message)
        
        except Exception as e:
            logger.error(f"Errore nell'elaborazione del messaggio di testo: {e}")
            await update.message.reply_text("❌ Si è verificato un errore nell'elaborazione del messaggio.")
    
    async def transcribe_audio(self, audio_path: str) -> str:
        """Trascrivi audio usando OpenAI Whisper"""
        try:
            with open(audio_path, "rb") as audio_file:
                transcript = await asyncio.to_thread(
                    openai_client.audio.transcriptions.create,
                    model="whisper-1",
                    file=audio_file
                )
                return transcript.text.strip()
        except Exception as e:
            logger.error(f"Errore nella trascrizione: {e}")
            return ""
    
    async def generate_response(self, text: str, user_id: int) -> str:
        """Genera risposta usando OpenAI GPT-3.5-turbo con cronologia conversazionale"""
        try:
            # Ottieni la cronologia dell'utente
            history = self.get_user_history(user_id)
            
            # Ottieni data e ora attuali in italiano
            timezone_italy = pytz.timezone('Europe/Rome')
            now = datetime.now(timezone_italy)
            
            # Formato data e ora in italiano
            giorni_settimana = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato', 'domenica']
            mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
                   'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
            
            giorno_settimana = giorni_settimana[now.weekday()]
            mese = mesi[now.month - 1]
            
            data_ora_italiana = f"{giorno_settimana} {now.day} {mese} {now.year}, ore {now.hour:02d}:{now.minute:02d}"
            
            # Determina il saluto appropriato in base all'ora
            if 5 <= now.hour < 12:
                periodo_giorno = "mattina"
                saluto_contesto = "È mattina"
            elif 12 <= now.hour < 18:
                periodo_giorno = "pomeriggio" 
                saluto_contesto = "È pomeriggio"
            elif 18 <= now.hour < 22:
                periodo_giorno = "sera"
                saluto_contesto = "È sera"
            else:
                periodo_giorno = "notte"
                saluto_contesto = "È notte"
            
            # Costruisci i messaggi per OpenAI includendo la cronologia
            system_prompt = f"""Sei una persona vera, non un assistente AI. Parla come un amico intimo, in modo spontaneo e naturale. Usa un linguaggio colloquiale, informale. Non dire mai di essere un'IA o un assistente. Rispondi come se fossi una persona reale che sta semplicemente chiacchierando con un amico. Sii empatico, caloroso e autentico. Usa espressioni italiane naturali e spontanee. Ricorda quello che è stato detto nella conversazione precedente e fai riferimento ad esso quando appropriato.

INFORMAZIONI TEMPORALI ATTUALI:
- Data e ora: {data_ora_italiana}
- {saluto_contesto}
- Giorno della settimana: {giorno_settimana}

Usa queste informazioni quando appropriate per dare contesto temporale alle tue risposte. Se ti chiedono che giorno è, che ora è, ecc., rispondi naturalmente basandoti su queste informazioni."""

            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                }
            ]
            
            # Aggiungi la cronologia delle conversazioni
            for msg in history:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            
            # Aggiungi il messaggio corrente dell'utente
            messages.append({
                "role": "user",
                "content": text
            })
            
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=150,
                temperature=0.9
            )
            
            content = response.choices[0].message.content
            response_text = content.strip() if content else "Mi dispiace, non sono riuscito a generare una risposta."
            
            # Aggiungi il messaggio dell'utente e la risposta alla cronologia
            self.add_to_history(user_id, "user", text)
            self.add_to_history(user_id, "assistant", response_text)
            
            return response_text
        
        except Exception as e:
            logger.error(f"Errore nella generazione della risposta: {e}")
            return "Mi dispiace, si è verificato un errore nel generare la risposta."
    
    async def text_to_speech(self, text: str) -> bytes:
        """Converte testo in audio usando ElevenLabs"""
        try:
            audio = await asyncio.to_thread(
                elevenlabs.generate,
                text=text,
                voice=VOICE_ID,
                model="eleven_multilingual_v2"
            )
            
            # Converte l'audio in bytes
            audio_bytes = b''.join(audio)
            return audio_bytes
        
        except Exception as e:
            logger.error(f"Errore nella sintesi vocale: {e}")
            raise
    
    async def process_and_respond(self, text: str, update: Update, status_message):
        """Elabora il testo e invia la risposta vocale"""
        try:
            user_id = update.effective_user.id
            
            # Genera risposta con OpenAI
            await status_message.edit_text("🤖 Sto generando la risposta...")
            response_text = await self.generate_response(text, user_id)
            logger.info(f"Risposta generata: {response_text}")
            
            # Converte la risposta in audio
            await status_message.edit_text("🔊 Sto creando l'audio...")
            audio_data = await self.text_to_speech(response_text)
            
            # Elimina il messaggio di stato
            await status_message.delete()
            
            # Invia il messaggio vocale
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_audio:
                temp_audio.write(audio_data)
                temp_audio_path = temp_audio.name
            
            try:
                with open(temp_audio_path, 'rb') as audio_file:
                    await update.message.reply_voice(audio_file)
                logger.info("Messaggio vocale inviato con successo")
            finally:
                # Pulisce il file temporaneo
                os.unlink(temp_audio_path)
        
        except Exception as e:
            logger.error(f"Errore nell'elaborazione e risposta: {e}")
            await status_message.edit_text("❌ Si è verificato un errore nel generare la risposta vocale.")
    
    def run(self):
        """Avvia il bot"""
        logger.info("🤖 Bot Telegram Vocale avviato!")
        logger.info(f"Ambiente: {RAILWAY_ENVIRONMENT}")
        
        if RAILWAY_ENVIRONMENT == 'production':
            logger.info("Avvio in modalità produzione (Railway)")
        else:
            logger.info("Premi Ctrl+C per fermare il bot")
        
        # Avvia il bot con polling ottimizzato per Railway
        self.application.run_polling(
            poll_interval=1.0,
            timeout=10,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

def main():
    """Funzione principale"""
    try:
        logger.info("Inizializzazione bot...")
        
        # Verifica configurazione
        required_vars = [TELEGRAM_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, VOICE_ID]
        if not all(required_vars):
            logger.error("❌ Configurazione incompleta! Verifica le variabili d'ambiente.")
            raise ValueError("Variabili d'ambiente mancanti")
        
        logger.info("✅ Configurazione verificata")
        
        bot = TelegramBot()
        bot.run()
        
    except KeyboardInterrupt:
        logger.info("Bot fermato dall'utente")
    except Exception as e:
        logger.error(f"Errore critico: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main() 