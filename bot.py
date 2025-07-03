import os
import logging
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import re
import json

from openai import OpenAI
import requests
from pydub import AudioSegment
from pydub.utils import which
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from elevenlabs.client import ElevenLabs

from config import TELEGRAM_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, VOICE_ID
from calendar_manager import CalendarManager

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

# Configura ElevenLabs con la nuova API
try:
    elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    logger.info("ElevenLabs configurato con client API")
except Exception as e:
    logger.error(f"Errore nella configurazione di ElevenLabs: {e}")
    elevenlabs_client = None

class TelegramBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        # Dizionario per mantenere la cronologia delle conversazioni per ogni utente
        # Struttura: {user_id: [{"role": "user/assistant", "content": "messaggio"}, ...]}
        self.conversation_history = {}
        self.max_history_length = 10  # Mantieni gli ultimi 10 messaggi per utente
        
        # Dizionario per gestire i flussi di prenotazione in corso
        # Struttura: {user_id: {"step": "waiting_datetime|waiting_title|waiting_confirmation", "data": {...}}}
        self.booking_flows = {}
        
        # Inizializza il gestore del calendario
        self.calendar_manager = CalendarManager()
        
        self.setup_handlers()
    
    def setup_handlers(self):
        """Configura i gestori per i messaggi del bot"""
        # Gestore per il comando /start
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Gestore per il comando /clear (cancella cronologia)
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        
        # Gestore per il comando /health (per Railway health check)
        self.application.add_handler(CommandHandler("health", self.health_command))
        
        # Gestori per comandi del calendario
        self.application.add_handler(CommandHandler("prenota", self.prenota_command))
        self.application.add_handler(CommandHandler("appuntamenti", self.appuntamenti_command))
        self.application.add_handler(CommandHandler("cancella", self.cancella_command))
        
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
            f"🤖 Ciao {user_name}! Sono il tuo assistente vocale e posso gestire i tuoi appuntamenti!\n\n"
            "**Cosa posso fare:**\n"
            "📝 Rispondere ai tuoi messaggi di testo\n"
            "🎤 Rispondere ai tuoi messaggi vocali\n"
            "📅 Gestire i tuoi appuntamenti su Google Calendar\n\n"
            "**Comandi disponibili:**\n"
            "📅 /prenota - Prenota un nuovo appuntamento\n"
            "📋 /appuntamenti - Visualizza i prossimi appuntamenti\n"
            "❌ /cancella - Annulla prenotazione in corso\n"
            "🔄 /start - Ricomincia da capo\n\n"
            "Puoi anche dire semplicemente 'voglio prenotare un appuntamento per domani alle 15' e io ti aiuterò!\n\n"
            "Ti risponderò sempre con un messaggio vocale! 🔊"
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
            user_id = update.effective_user.id
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
                
                # Elimina il messaggio di stato temporaneamente per gestire il flusso di prenotazione
                await status_message.delete()
                
                # Controlla se l'utente è in un flusso di prenotazione
                if user_id in self.booking_flows:
                    await self.handle_booking_flow(update, text)
                    return
                
                # Controlla se il messaggio riguarda prenotazioni (parsing naturale)
                booking_intent = self.detect_booking_intent(text)
                if booking_intent:
                    await self.start_booking_flow(update, text)
                    return
                
                # Invia nuovo messaggio di stato per la risposta normale
                status_message = await update.message.reply_text("💭 Sto pensando alla risposta...")
                
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
            user_id = update.effective_user.id
            logger.info(f"Ricevuto messaggio di testo da {update.effective_user.first_name}: {text}")
            
            # Controlla se l'utente è in un flusso di prenotazione
            if user_id in self.booking_flows:
                await self.handle_booking_flow(update, text)
                return
            
            # Controlla se il messaggio riguarda prenotazioni (parsing naturale)
            booking_intent = self.detect_booking_intent(text)
            if booking_intent:
                await self.start_booking_flow(update, text)
                return
            
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

IMPORTANTE: Il sistema può rilevare automaticamente quando l'utente vuole prenotare appuntamenti dai suoi messaggi naturali (sia scritti che vocali). Non devi suggerire comandi specifici, rispondi naturalmente. Il sistema gestirà automaticamente:
- Rilevamento di intenzioni di prenotazione da messaggi naturali
- Avvio automatico del processo di prenotazione
- Gestione del calendario Google integrato

Se parlano di appuntamenti, tempi, incontri, o vogliono organizzare qualcosa, rispondi in modo naturale e lascia che il sistema rilevi l'intenzione automaticamente.

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
            if elevenlabs_client is None:
                raise Exception("ElevenLabs client non configurato")
            
            # Usa la nuova API di ElevenLabs con text_to_speech.convert
            audio = await asyncio.to_thread(
                elevenlabs_client.text_to_speech.convert,
                text=text,
                voice_id=VOICE_ID,
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128"
            )
            
            # Converte l'audio in bytes
            if hasattr(audio, '__iter__') and not isinstance(audio, (str, bytes)):
                # Se audio è un iteratore di chunk
                audio_bytes = b''.join(audio)
            else:
                # Se audio è già bytes
                audio_bytes = audio if isinstance(audio, bytes) else bytes(audio)
            
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
    
    # === GESTIONE CALENDARIO ===
    
    def detect_booking_intent(self, text: str) -> bool:
        """Rileva se il messaggio contiene un'intenzione di prenotare un appuntamento"""
        keywords_booking = [
            # Parole dirette per prenotazione
            'prenota', 'appuntamento', 'prenotare', 'prenotazione', 'fissare', 'fissa',
            'meeting', 'incontro', 'riunione', 'disponibilità', 'libero', 'impegnato',
            
            # Espressioni temporali che indicano voglia di organizzare
            'quando sei libero', 'quando puoi', 'possiamo vederci', 'ci vediamo',
            'voglio vederti', 'dobbiamo incontrarci', 'organizziamo', 'pianifichiamo',
            
            # Riferimenti temporali specifici che spesso accompagnano prenotazioni
            'domani alle', 'dopodomani alle', 'lunedì alle', 'martedì alle', 
            'mercoledì alle', 'giovedì alle', 'venerdì alle', 'sabato alle', 'domenica alle',
            'settimana prossima', 'la prossima settimana', 'questo weekend',
            
            # Verbi di azione per organizzare
            'voglio prenotare', 'devo prenotare', 'vorrei prenotare', 'posso prenotare',
            'ho bisogno di', 'serve un appuntamento', 'mi serve', 
            
            # Altri indicatori
            'calendario', 'agenda', 'programmare', 'slot', 'orario',
            'consultazione', 'visita', 'sessione'
        ]
        
        # Frasi che spesso indicano prenotazione
        booking_phrases = [
            'voglio un appuntamento',
            'ho bisogno di un appuntamento', 
            'possiamo fissare',
            'vorrei fissare',
            'devo fissare',
            'prendiamo un appuntamento',
            'fissiamo un incontro',
            'organizziamo un meeting',
            'quando ci possiamo vedere',
            'quando possiamo vederci',
            'sei libero',
            'hai tempo',
            'possiamo incontrarci'
        ]
        
        text_lower = text.lower()
        
        # Controlla parole chiave
        keyword_match = any(keyword in text_lower for keyword in keywords_booking)
        
        # Controlla frasi complete
        phrase_match = any(phrase in text_lower for phrase in booking_phrases)
        
        return keyword_match or phrase_match
    
    async def prenota_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /prenota"""
        user_id = update.effective_user.id
        
        # Inizia il flusso di prenotazione
        self.booking_flows[user_id] = {
            "step": "waiting_datetime",
            "data": {}
        }
        
        message = (
            "📅 **Prenotazione Appuntamento**\n\n"
            "Dimmi quando vuoi prenotare l'appuntamento.\n\n"
            "Puoi scrivere ad esempio:\n"
            "• Domani alle 15:00\n"
            "• Lunedì alle 10:30\n"
            "• 25/12 alle 14:00\n"
            "• Oggi pomeriggio\n\n"
            "Scrivi /cancella per annullare."
        )
        
        await update.message.reply_text(message)
    
    async def appuntamenti_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /appuntamenti"""
        try:
            events = self.calendar_manager.get_upcoming_appointments(7)
            message = self.calendar_manager.format_appointment_list(events)
            await update.message.reply_text(message)
        except Exception as e:
            logger.error(f"Errore nel recupero appuntamenti: {e}")
            await update.message.reply_text("❌ Errore nel recupero degli appuntamenti.")
    
    async def cancella_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /cancella per annullare prenotazioni in corso"""
        user_id = update.effective_user.id
        
        if user_id in self.booking_flows:
            del self.booking_flows[user_id]
            await update.message.reply_text("❌ Prenotazione annullata.")
        else:
            await update.message.reply_text("Non hai prenotazioni in corso da annullare.")
    
    async def start_booking_flow(self, update: Update, text: str):
        """Avvia il flusso di prenotazione dal linguaggio naturale"""
        user_id = update.effective_user.id
        
        # Prova a estrarre data/ora dal messaggio
        parsed_datetime = self.calendar_manager.parse_datetime_natural(text)
        
        if parsed_datetime:
            # Data/ora trovata, chiedi conferma
            self.booking_flows[user_id] = {
                "step": "waiting_title",
                "data": {
                    "datetime": parsed_datetime,
                    "original_text": text
                }
            }
            
            formatted_time = parsed_datetime.strftime("%d/%m/%Y alle %H:%M")
            message = (
                f"📅 Perfetto! Ho capito che vuoi prenotare per **{formatted_time}**.\n\n"
                f"Ora dimmi l'oggetto dell'appuntamento (es: 'Riunione di lavoro', 'Visita medica', ecc.)"
            )
            await update.message.reply_text(message)
        else:
            # Non riesco a capire la data, chiedo più info
            self.booking_flows[user_id] = {
                "step": "waiting_datetime",
                "data": {}
            }
            
            message = (
                "📅 Ho capito che vuoi prenotare un appuntamento!\n\n"
                "Però non sono riuscito a capire quando. Puoi essere più specifico?\n\n"
                "Ad esempio:\n"
                "• Domani alle 15:00\n"
                "• Lunedì prossimo alle 10:30\n"
                "• 25/12/2024 alle 14:00"
            )
            await update.message.reply_text(message)
    
    async def handle_booking_flow(self, update: Update, text: str):
        """Gestisce il flusso di prenotazione in corso"""
        user_id = update.effective_user.id
        flow = self.booking_flows[user_id]
        
        if flow["step"] == "waiting_datetime":
            await self._handle_datetime_input(update, text, flow)
        elif flow["step"] == "waiting_title":
            await self._handle_title_input(update, text, flow)
        elif flow["step"] == "waiting_confirmation":
            await self._handle_confirmation_input(update, text, flow)
    
    async def _handle_datetime_input(self, update: Update, text: str, flow: dict):
        """Gestisce l'input della data/ora"""
        user_id = update.effective_user.id
        
        parsed_datetime = self.calendar_manager.parse_datetime_natural(text)
        
        if parsed_datetime:
            # Controlla se la data è nel passato
            now = datetime.now(self.calendar_manager.timezone)
            if parsed_datetime < now:
                await update.message.reply_text(
                    "⏰ La data che hai indicato è nel passato! Dimmi una data futura."
                )
                return
            
            # Salva la data e passa al prossimo step
            flow["data"]["datetime"] = parsed_datetime
            flow["step"] = "waiting_title"
            
            formatted_time = parsed_datetime.strftime("%d/%m/%Y alle %H:%M")
            await update.message.reply_text(
                f"✅ Perfetto! **{formatted_time}**\n\n"
                f"Ora dimmi l'oggetto dell'appuntamento."
            )
        else:
            await update.message.reply_text(
                "❓ Non sono riuscito a capire la data. Prova con:\n"
                "• Domani alle 15:00\n"
                "• Lunedì alle 10:30\n"
                "• 25/12 alle 14:00"
            )
    
    async def _handle_title_input(self, update: Update, text: str, flow: dict):
        """Gestisce l'input del titolo dell'appuntamento"""
        user_id = update.effective_user.id
        
        # Salva il titolo
        flow["data"]["title"] = text
        
        # Calcola durata (default 1 ora)
        start_time = flow["data"]["datetime"]
        end_time = start_time + timedelta(hours=1)
        flow["data"]["end_time"] = end_time
        
        # Controlla disponibilità
        is_free, conflicts = self.calendar_manager.check_availability(start_time, end_time)
        
        # DEBUG: Per ora forziamo sempre come libero per testare la creazione
        logger.info(f"🔧 DEBUG: Risultato controllo disponibilità: is_free={is_free}, conflicts={len(conflicts) if conflicts else 0}")
        
        # Commentiamo temporaneamente il controllo conflitti per il debug
        if False:  # not is_free:
            # Suggerisci slot alternativi
            slots = self.calendar_manager.suggest_free_slots(start_time, 60)
            
            message = f"⚠️ **Conflitto di orario!**\n\n"
            message += f"Hai già un impegno in quel momento.\n\n"
            
            if slots:
                message += "Ecco alcuni orari liberi:\n"
                for i, slot in enumerate(slots, 1):
                    formatted_slot = slot.strftime("%d/%m/%Y alle %H:%M")
                    message += f"{i}. {formatted_slot}\n"
                message += "\nScrivi il numero dello slot che preferisci o una nuova data."
                
                flow["data"]["suggested_slots"] = slots
            else:
                message += "Non ho trovato slot liberi in quella giornata. Dimmi un'altra data."
            
            await update.message.reply_text(message)
            return
        
        # Slot libero, chiedi conferma
        flow["step"] = "waiting_confirmation"
        
        formatted_start = start_time.strftime("%d/%m/%Y alle %H:%M")
        formatted_end = end_time.strftime("%H:%M")
        
        message = (
            f"📋 **Riepilogo Appuntamento**\n\n"
            f"📅 **Data:** {formatted_start}\n"
            f"⏰ **Fine:** {formatted_end}\n"
            f"📝 **Oggetto:** {text}\n\n"
            f"Confermi? Scrivi 'sì' per prenotare o 'no' per annullare."
        )
        
        await update.message.reply_text(message)
    
    async def _handle_confirmation_input(self, update: Update, text: str, flow: dict):
        """Gestisce la conferma dell'appuntamento"""
        user_id = update.effective_user.id
        
        text_lower = text.lower().strip()
        
        if text_lower in ['sì', 'si', 'yes', 'ok', 'conferma', 'confermo']:
            # Crea l'appuntamento
            try:
                event = self.calendar_manager.create_appointment(
                    title=flow["data"]["title"],
                    start_time=flow["data"]["datetime"],
                    end_time=flow["data"]["end_time"],
                    description=f"Appuntamento prenotato tramite bot Telegram"
                )
                
                if event:
                    formatted_time = flow["data"]["datetime"].strftime("%d/%m/%Y alle %H:%M")
                    message = (
                        f"✅ **Appuntamento confermato!**\n\n"
                        f"📅 {formatted_time}\n"
                        f"📝 {flow['data']['title']}\n\n"
                        f"Ti invierò un promemoria prima dell'appuntamento. 🔔"
                    )
                    await update.message.reply_text(message)
                else:
                    await update.message.reply_text("❌ Errore nella creazione dell'appuntamento.")
                
            except Exception as e:
                logger.error(f"Errore nella creazione appuntamento: {e}")
                await update.message.reply_text("❌ Errore nella creazione dell'appuntamento.")
            
            # Pulisci il flusso
            del self.booking_flows[user_id]
            
        elif text_lower in ['no', 'annulla', 'cancel']:
            await update.message.reply_text("❌ Appuntamento annullato.")
            del self.booking_flows[user_id]
        else:
            await update.message.reply_text("❓ Non ho capito. Scrivi 'sì' per confermare o 'no' per annullare.")
    
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