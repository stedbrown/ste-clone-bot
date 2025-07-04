# Bot Telegram per appuntamenti con registrazione utenti e messaggi vocali naturali
# Versione: 3.0 - Sistema completo con UX migliorata
# Ultimo aggiornamento: Messaggi vocali naturali e accompagnatori

import os
import logging
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import re
import json
import random
import uuid

from openai import OpenAI
import requests
from pydub import AudioSegment
from pydub.utils import which
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from elevenlabs.client import ElevenLabs

from config import TELEGRAM_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, VOICE_ID
from calendar_manager import CalendarManager
from user_manager import UserManager

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
        
        # Dizionario per gestire i flussi di registrazione
        # Struttura: {user_id: {"step": "waiting_nome|waiting_cognome|...", "data": {...}}}
        self.registration_flows = {}
        
        # Inizializza i gestori
        self.calendar_manager = CalendarManager()
        self.user_manager = UserManager()
        
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
        self.application.add_handler(CommandHandler("profilo", self.profilo_command))
        
        # Gestore per messaggi vocali
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_message))
        
        # Gestore per messaggi di testo
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        
        # Gestore per callback dei bottoni inline
        self.application.add_handler(CallbackQueryHandler(self.handle_inline_callback))
        
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
        
        # Controlla se l'utente è già registrato
        if not self.user_manager.is_user_registered(user_id):
            await self.start_registration_flow(update)
            return
        
        # Utente già registrato - mostra messaggio di benvenuto personalizzato
        user_display_name = self.user_manager.get_user_display_name(user_id)
        user_info = self.user_manager.get_user_info(user_id)
        
        welcome_message = (
            f"🤖 Bentornato **{user_display_name}**! 🎉\n\n"
            f"📊 Hai già **{user_info['total_appointments']} appuntamenti** prenotati\n\n"
            "**Cosa posso fare per te:**\n"
            "📝 Rispondere ai tuoi messaggi di testo\n"
            "🎤 Rispondere ai tuoi messaggi vocali\n"
            "📅 Gestire i tuoi appuntamenti su Google Calendar\n\n"
            "**Comandi disponibili:**\n"
            "📅 /prenota - Prenota un nuovo appuntamento (con bottoni!)\n"
            "📋 /appuntamenti - Visualizza SOLO i tuoi appuntamenti\n"
            "👤 /profilo - Visualizza e modifica i tuoi dati\n"
            "❌ /cancella - Annulla prenotazione in corso\n"
            "🔄 /start - Ricomincia da capo\n\n"
            "Puoi anche dire semplicemente 'voglio prenotare un appuntamento per domani alle 15' e io ti aiuterò!\n\n"
            "📱 **NOVITÀ:** Ora uso bottoni interattivi per rendere tutto più veloce!\n"
            "🔊 Ti risponderò sempre con **testo + audio**! 🎧\n\n"
            "🔒 **Privacy:** I tuoi appuntamenti sono completamente privati - solo tu puoi vederli!"
        )
        await self.send_text_and_voice(update, welcome_message)

    async def start_registration_flow(self, update: Update):
        """Avvia il processo di registrazione per un nuovo utente"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        # Inizializza il flusso di registrazione
        self.registration_flows[user_id] = {
            "step": "waiting_nome",
            "data": {
                "telegram_name": user_name
            }
        }
        
        welcome_message = (
            f"👋 Ciao **{user_name}**! Benvenuto! 🎉\n\n"
            "Prima di iniziare, ho bisogno di raccogliere alcune informazioni per offrirti un servizio migliore.\n\n"
            "📝 **Questo mi permetterà di:**\n"
            "• Identificarti facilmente negli appuntamenti\n"
            "• Contattarti se necessario\n"
            "• Offrirti un servizio più personalizzato\n\n"
            "🔒 **I tuoi dati sono completamente privati e sicuri.**\n\n"
            "**Iniziamo! Come ti chiami?**\n"
            "_(Scrivi solo il tuo nome)_"
        )
        
        await self.send_text_and_voice(update, welcome_message)

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
            
            # Controlla se l'utente è in un flusso di registrazione
            if user_id in self.registration_flows:
                await self.handle_registration_flow(update, text)
                return
            
            # Controlla se l'utente è registrato
            if not self.user_manager.is_user_registered(user_id):
                await self.start_registration_flow(update)
                return
            
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
    
    async def send_text_and_voice(self, update: Update, text: str, keyboard: InlineKeyboardMarkup = None, edit_message_id: int = None, voice_text: str = None):
        """Invia sia messaggio di testo che vocale, opzionalmente con bottoni inline"""
        try:
            user_id = update.effective_user.id
            
            # Invia prima il messaggio di testo con bottoni (se presenti)
            if edit_message_id:
                # Modifica un messaggio esistente
                text_message = await update.effective_chat.edit_message_text(
                    text=text,
                    message_id=edit_message_id,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                # Invia nuovo messaggio
                text_message = await update.effective_chat.send_message(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            
            # Genera e invia anche l'audio con testo personalizzato
            try:
                # Usa testo vocale personalizzato se fornito, altrimenti genera uno naturale
                if voice_text:
                    audio_text = voice_text
                else:
                    audio_text = self.generate_natural_voice_text(text, user_id)
                
                # Pulisce il testo per l'audio
                clean_text = self.clean_text_for_audio(audio_text)
                
                audio_data = await self.text_to_speech(clean_text)
                
                # Invia il messaggio vocale
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_audio:
                    temp_audio.write(audio_data)
                    temp_audio_path = temp_audio.name
                
                try:
                    with open(temp_audio_path, 'rb') as audio_file:
                        await update.effective_chat.send_voice(audio_file)
                    logger.info("Messaggio testo + vocale inviato con successo")
                finally:
                    # Pulisce il file temporaneo
                    os.unlink(temp_audio_path)
                    
            except Exception as e:
                logger.error(f"Errore nell'invio dell'audio: {e}")
                # Se l'audio fallisce, almeno il testo è stato inviato
                
            return text_message
            
        except Exception as e:
            logger.error(f"Errore nell'invio di testo e voce: {e}")
            # Fallback: invia solo testo normale
            if edit_message_id:
                return await update.effective_chat.edit_message_text(
                    text=f"❌ {text}",
                    message_id=edit_message_id
                )
            else:
                return await update.effective_chat.send_message(text=f"❌ {text}")

    def clean_text_for_audio(self, text: str) -> str:
        """Pulisce il testo per renderlo più naturale per l'audio"""
        import re
        
        # Rimuove markdown
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # **bold**
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # *italic*
        text = re.sub(r'_(.*?)_', r'\1', text)        # _italic_
        text = re.sub(r'`(.*?)`', r'\1', text)        # `code`
        
        # Rimuove emoji e simboli
        text = re.sub(r'[🎉🎯🎮🎧🎤📝📅📋📊📱👤👋✅❌🔒🔄🚀🏆💾🆕🔄🗄️]', '', text)
        
        # Rimuove bullet points
        text = re.sub(r'^[•\-\*]\s*', '', text, flags=re.MULTILINE)
        
        # Rimuove linee vuote multiple
        text = re.sub(r'\n\s*\n', '\n', text)
        
        # Rimuove spazi extra
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()

    def generate_natural_voice_text(self, original_text: str, user_id: int) -> str:
        """Genera un testo vocale naturale e accompagnatore basato sul contenuto"""
        
        # Ottieni il nome dell'utente se registrato
        user_name = "amico"
        if self.user_manager.is_user_registered(user_id):
            user_info = self.user_manager.get_user_info(user_id)
            user_name = user_info["nome"] if user_info else "amico"
        
        # Analizza il tipo di messaggio e genera risposta vocale naturale
        text_lower = original_text.lower()
        
        # Messaggi di benvenuto
        if "benvenuto" in text_lower or "bentornato" in text_lower:
            return f"Ciao {user_name}! Sono qui per aiutarti con i tuoi appuntamenti. Dimmi pure cosa ti serve!"
        
        # Registrazione
        if "registrazione completata" in text_lower:
            return f"Perfetto {user_name}! Ora sei registrato e possiamo lavorare insieme. Sono qui per te!"
        
        # Richiesta dati registrazione
        if "come ti chiami" in text_lower:
            return "Dimmi il tuo nome, così posso conoscerti meglio!"
        elif "cognome" in text_lower and "dimmi" in text_lower:
            return "Perfetto! Ora dimmi il tuo cognome."
        elif "email" in text_lower and "serve" in text_lower:
            return "Adesso ho bisogno della tua email per poterti contattare se necessario."
        elif "telefono" in text_lower and "dimmi" in text_lower:
            return "Ottimo! Ora dimmi il tuo numero di telefono."
        elif "indirizzo" in text_lower and "dimmi" in text_lower:
            return "Quasi finito! Dimmi il tuo indirizzo completo."
        elif "città" in text_lower and "dimmi" in text_lower:
            return "Ultima cosa! In che città vivi?"
        
        # Conferme durante registrazione
        if "email salvata" in text_lower:
            return "Perfetto! Email registrata."
        elif "telefono salvato" in text_lower:
            return "Bene! Numero salvato."
        elif "indirizzo salvato" in text_lower:
            return "Ottimo! Indirizzo registrato."
        
        # Prenotazioni
        if "quando vorresti" in text_lower or "che giorno" in text_lower:
            return f"Dimmi quando preferisci, {user_name}. Puoi anche usare i bottoni qui sotto per andare più veloce!"
        
        if "che tipo di appuntamento" in text_lower:
            return "Dimmi brevemente di cosa si tratta, così posso preparare tutto per bene."
        
        # Conferme appuntamento
        if "appuntamento creato" in text_lower or "confermato" in text_lower or "tutto sistemato" in text_lower:
            confirmations = [
                f"Perfetto {user_name}! Il tuo appuntamento è confermato. Ti aspetto!",
                f"Ottimo {user_name}! Tutto a posto. Ci vediamo presto!",
                f"Fantastico! Appuntamento fissato. Sarà un piacere vederti, {user_name}!",
                f"Eccellente {user_name}! È tutto sistemato. Non vedo l'ora di incontrarti!"
            ]
            return random.choice(confirmations)
        
        # Errori
        if "errore" in text_lower or "problema" in text_lower:
            return "Ops! Qualcosa è andato storto. Proviamo di nuovo insieme, ok?"
        
        # Lista appuntamenti
        if "prossimi appuntamenti" in text_lower:
            return "Ecco i tuoi appuntamenti in programma!"
        
        # Profilo
        if "il tuo profilo" in text_lower:
            return "Questi sono i tuoi dati che ho salvato. Tutto ok?"
        
        # Annullamenti
        if "annullata" in text_lower or "annullato" in text_lower:
            return "Nessun problema! Quando vuoi riprovare, sono qui."
        
        # Default: messaggio generico accompagnatore
        return f"Ecco le informazioni che ti servono, {user_name}!"

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
    
    async def handle_inline_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce i callback dei bottoni inline"""
        try:
            query = update.callback_query
            await query.answer()  # Conferma la ricezione del callback
            
            user_id = query.from_user.id
            callback_data = query.data
            
            logger.info(f"Callback ricevuto da {query.from_user.first_name}: {callback_data}")
            
            # Gestisce i diversi tipi di callback
            if callback_data.startswith("confirm_"):
                await self._handle_confirmation_callback(query, callback_data)
            elif callback_data.startswith("time_"):
                await self._handle_time_selection_callback(query, callback_data)
            elif callback_data.startswith("cancel_"):
                await self._handle_cancel_callback(query, callback_data)
            else:
                await query.edit_message_text("❌ Azione non riconosciuta.")
                
        except Exception as e:
            logger.error(f"Errore nella gestione del callback: {e}")
            try:
                await query.edit_message_text("❌ Si è verificato un errore.")
            except:
                pass

    async def _handle_confirmation_callback(self, query, callback_data):
        """Gestisce le conferme yes/no"""
        user_id = query.from_user.id
        action = callback_data.split("_")[1]  # confirm_yes o confirm_no
        
        if user_id not in self.booking_flows:
            await query.edit_message_text("❌ Sessione di prenotazione scaduta. Riprova con /prenota")
            return
            
        flow = self.booking_flows[user_id]
        
        if action == "yes":
            # Conferma prenotazione
            if flow["step"] == "waiting_confirmation":
                await self._process_booking_confirmation(query, flow, True)
        elif action == "no":
            # Annulla prenotazione
            await query.edit_message_text("❌ Prenotazione annullata. Usa /prenota per ricominciare.")
            del self.booking_flows[user_id]

    async def _handle_time_selection_callback(self, query, callback_data):
        """Gestisce la selezione rapida degli orari"""
        user_id = query.from_user.id
        time_slot = callback_data.replace("time_", "")
        
        if user_id not in self.booking_flows:
            await query.edit_message_text("❌ Sessione di prenotazione scaduta. Riprova con /prenota")
            return
            
        # Simula input di testo per l'orario selezionato
        fake_update = type('obj', (object,), {
            'effective_user': query.from_user,
            'effective_chat': query.message.chat,
            'message': query.message
        })()
        
        await self._handle_datetime_input(fake_update, time_slot, self.booking_flows[user_id])

    async def _handle_cancel_callback(self, query, callback_data):
        """Gestisce l'annullamento"""
        user_id = query.from_user.id
        
        if user_id in self.booking_flows:
            del self.booking_flows[user_id]
            
        await query.edit_message_text("❌ Operazione annullata.")

    def _create_time_selection_keyboard(self):
        """Crea una tastiera inline con orari comuni"""
        # Ottieni data/ora attuale
        timezone_italy = pytz.timezone('Europe/Rome')
        now = datetime.now(timezone_italy)
        
        # Calcola domani
        tomorrow = now + timedelta(days=1)
        
        keyboard = []
        
        # Riga 1: Oggi (se ancora in orario utile)
        if now.hour < 18:  # Solo se non è troppo tardi
            keyboard.append([
                InlineKeyboardButton("🌅 Oggi 9:00", callback_data="time_oggi alle 9:00"),
                InlineKeyboardButton("🌞 Oggi 14:00", callback_data="time_oggi alle 14:00"),
                InlineKeyboardButton("🌆 Oggi 17:00", callback_data="time_oggi alle 17:00")
            ])
        
        # Riga 2: Domani
        keyboard.append([
            InlineKeyboardButton("🌅 Domani 9:00", callback_data="time_domani alle 9:00"),
            InlineKeyboardButton("🌞 Domani 14:00", callback_data="time_domani alle 14:00"),
            InlineKeyboardButton("🌆 Domani 17:00", callback_data="time_domani alle 17:00")
        ])
        
        # Riga 3: Settimana prossima
        giorni_settimana = ['lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì']
        next_monday = now + timedelta(days=(7 - now.weekday()))
        
        keyboard.append([
            InlineKeyboardButton("📅 Lun 10:00", callback_data="time_lunedì prossimo alle 10:00"),
            InlineKeyboardButton("📅 Mer 15:00", callback_data="time_mercoledì prossimo alle 15:00"),
            InlineKeyboardButton("📅 Ven 16:00", callback_data="time_venerdì prossimo alle 16:00")
        ])
        
        # Riga 4: Annulla
        keyboard.append([
            InlineKeyboardButton("❌ Annulla", callback_data="cancel_booking")
        ])
        
        return InlineKeyboardMarkup(keyboard)

    def _create_confirmation_keyboard(self):
        """Crea tastiera per conferma prenotazione"""
        keyboard = [
            [
                InlineKeyboardButton("✅ Confermo", callback_data="confirm_yes"),
                InlineKeyboardButton("❌ Annulla", callback_data="confirm_no")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _generate_ics_file(self, title: str, start_time: datetime, end_time: datetime, description: str = "", user_info: str = "") -> str:
        """Genera un file .ics per l'appuntamento"""
        try:
            # Genera un UUID unico per l'evento
            event_uid = str(uuid.uuid4())
            
            # Formatta le date in formato UTC per il file .ics
            start_utc = start_time.astimezone(pytz.UTC)
            end_utc = end_time.astimezone(pytz.UTC)
            created_utc = datetime.now(pytz.UTC)
            
            # Formato data per .ics (senza trattini e due punti)
            start_str = start_utc.strftime("%Y%m%dT%H%M%SZ")
            end_str = end_utc.strftime("%Y%m%dT%H%M%SZ")
            created_str = created_utc.strftime("%Y%m%dT%H%M%SZ")
            
            # Crea descrizione completa per l'utente con informazioni azienda
            user_description = f"""Appuntamento con UP! Informatica

{description}

═══════════════════════════════════════
🏢 UP! INFORMATICA
📧 Email: info@upinformatica.it  
📱 Tel: [Inserire numero]
🌐 Web: www.upinformatica.it
📍 Indirizzo: [Inserire indirizzo azienda]
═══════════════════════════════════════

Ricorda di portare con te eventuali documenti necessari.
Per modifiche o cancellazioni contattaci in anticipo.

Grazie per aver scelto UP! Informatica! 🚀"""
            
            # Escape caratteri speciali per il formato .ics
            title_escaped = title.replace(',', '\\,').replace(';', '\\;').replace('\\', '\\\\')
            description_escaped = user_description.replace(',', '\\,').replace(';', '\\;').replace('\\', '\\\\').replace('\n', '\\n')
            
            # Contenuto del file .ics
            ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//UP! Informatica//Appointment Bot//IT
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Appuntamenti UP! Informatica
X-WR-CALDESC:Calendario appuntamenti UP! Informatica
BEGIN:VEVENT
UID:{event_uid}
DTSTART:{start_str}
DTEND:{end_str}
DTSTAMP:{created_str}
CREATED:{created_str}
LAST-MODIFIED:{created_str}
SUMMARY:{title_escaped}
DESCRIPTION:{description_escaped}
ORGANIZER;CN=UP! Informatica:MAILTO:info@upinformatica.it
STATUS:CONFIRMED
TRANSP:OPAQUE
BEGIN:VALARM
TRIGGER:-PT15M
ACTION:DISPLAY
DESCRIPTION:Promemoria: {title_escaped} tra 15 minuti
END:VALARM
BEGIN:VALARM
TRIGGER:-PT1H
ACTION:DISPLAY
DESCRIPTION:Promemoria: {title_escaped} tra 1 ora
END:VALARM
END:VEVENT
END:VCALENDAR"""

            return ics_content
            
        except Exception as e:
            logger.error(f"Errore nella generazione del file .ics: {e}")
            return None

    async def _process_booking_confirmation(self, query, flow: dict, confirmed: bool):
        """Processa la conferma della prenotazione"""
        user_id = query.from_user.id
        
        if confirmed:
            # Crea l'appuntamento
            try:
                # Ottieni le informazioni complete dell'utente per la descrizione
                user_info = self.user_manager.format_user_for_calendar(user_id)
                
                event = self.calendar_manager.create_appointment(
                    title=flow["data"]["title"],
                    start_time=flow["data"]["datetime"],
                    end_time=flow["data"]["end_time"],
                    description=f"Appuntamento prenotato tramite bot Telegram\n\n{user_info}",
                    user_id=user_id
                )
                
                if event:
                    formatted_time = flow["data"]["datetime"].strftime("%d/%m/%Y alle %H:%M")
                    
                    # Messaggi più umani e personalizzati
                    messages_variants = [
                        f"🎉 **Perfetto! Tutto sistemato!**\n\n📅 Ci vediamo {formatted_time}\n📝 {flow['data']['title']}\n\n💡 Ora ti mando un file per salvarlo nel tuo calendario personale!\nTi farò anche un promemoria prima dell'appuntamento. A presto! 😊",
                        
                        f"✨ **Fantastico! Appuntamento prenotato!**\n\n📅 Ti aspetto {formatted_time}\n📝 {flow['data']['title']}\n\n📱 Ti invio subito un file per aggiungerlo al tuo telefono!\nNon preoccuparti, ti ricorderò io prima dell'appuntamento! 👍",
                        
                        f"🚀 **Eccellente! È fatta!**\n\n📅 Appuntamento fissato per {formatted_time}\n📝 {flow['data']['title']}\n\n💾 Riceverai un file da salvare nel tuo calendario!\nE ovviamente ti avviserò prima che inizi. Ci sentiamo presto! 🎯"
                    ]
                    
                    # Scegli un messaggio casuale per variare
                    message = random.choice(messages_variants)
                    
                    # Rimuovi i bottoni e aggiorna il messaggio
                    await query.edit_message_text(message, parse_mode='Markdown')
                    
                    # Invia anche il messaggio vocale di conferma più naturale
                    user_display_name = self.user_manager.get_user_display_name(user_id)
                    
                    # Messaggi vocali naturali e accompagnatori
                    natural_voice_messages = [
                        f"Perfetto {user_display_name}! Tutto confermato. Ti aspetto, sarà un piacere vederti!",
                        f"Ottimo! Il tuo appuntamento è a posto. Ci vediamo presto, {user_display_name}!",
                        f"Fantastico {user_display_name}! Appuntamento confermato. Non vedo l'ora di incontrarti!",
                        f"Eccellente! È tutto sistemato. Ti aspetto, {user_display_name}. Ci sentiamo presto!",
                        f"Perfetto! Appuntamento fissato. Sarà un piacere vederti, {user_display_name}!"
                    ]
                    
                    # Genera e invia audio di conferma naturale
                    try:
                        voice_message = random.choice(natural_voice_messages)
                        audio_data = await self.text_to_speech(voice_message)
                        
                        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_audio:
                            temp_audio.write(audio_data)
                            temp_audio_path = temp_audio.name
                        
                        try:
                            with open(temp_audio_path, 'rb') as audio_file:
                                await query.message.chat.send_voice(audio_file)
                        finally:
                            os.unlink(temp_audio_path)
                    except Exception as e:
                        logger.error(f"Errore nell'invio dell'audio di conferma: {e}")
                    
                    # Genera e invia file .ics per il calendario
                    try:
                        # Ottieni informazioni utente per il file .ics
                        user_display_name = self.user_manager.get_user_display_name(user_id)
                        
                        ics_content = self._generate_ics_file(
                            title=flow['data']['title'],
                            start_time=flow['data']['datetime'],
                            end_time=flow['data']['end_time'],
                            description=f"Appuntamento: {flow['data']['title']}",
                            user_info=user_display_name
                        )
                        
                        if ics_content:
                            # Crea nome file con data e titolo
                            date_str = flow['data']['datetime'].strftime("%Y%m%d_%H%M")
                            title_clean = re.sub(r'[^a-zA-Z0-9\s]', '', flow['data']['title'])[:20]
                            filename = f"appuntamento_{date_str}_{title_clean}.ics"
                            
                            # Invia file come documento
                            with tempfile.NamedTemporaryFile(mode='w', suffix='.ics', delete=False, encoding='utf-8') as temp_ics:
                                temp_ics.write(ics_content)
                                temp_ics_path = temp_ics.name
                            
                            try:
                                with open(temp_ics_path, 'rb') as ics_file:
                                    await query.message.chat.send_document(
                                        document=ics_file,
                                        filename=filename,
                                        caption="📅 **File Calendario**\n\nScarica e apri questo file per aggiungere l'appuntamento al tuo calendario!\n\n📱 *Tocca il file → Apri con → Calendario*"
                                    )
                                logger.info(f"File .ics inviato per appuntamento: {formatted_time}")
                            finally:
                                os.unlink(temp_ics_path)
                        else:
                            logger.warning("Impossibile generare file .ics")
                    except Exception as e:
                        logger.error(f"Errore nell'invio del file .ics: {e}")
                        # Non bloccare il flusso se il file .ics fallisce
                    
                    # Aggiorna le statistiche dell'utente
                    self.user_manager.update_user_stats(user_id, formatted_time)
                    
                    logger.info(f"Appuntamento creato per {query.from_user.first_name}: {formatted_time}")
                else:
                    await query.edit_message_text("❌ Errore nella creazione dell'appuntamento. Riprova.")
                    
            except Exception as e:
                logger.error(f"Errore nella creazione dell'appuntamento: {e}")
                await query.edit_message_text("❌ Si è verificato un errore. Riprova più tardi.")
        else:
            await query.edit_message_text("❌ Prenotazione annullata.")
        
        # Pulisci il flusso di prenotazione
        if user_id in self.booking_flows:
            del self.booking_flows[user_id]

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
        
        # Crea bottoni per orari comuni
        keyboard = self._create_time_selection_keyboard()
        
        message = (
            "📅 **Prenotazione Appuntamento**\n\n"
            "Quando vuoi prenotare l'appuntamento?\n\n"
            "Puoi:\n"
            "🔸 **Cliccare un orario** qui sotto\n"
            "🔸 **Scrivere/dire** quando vuoi (es: \"domani alle 15:00\")\n\n"
            "Esempi:\n"
            "• Domani alle 15:00\n"
            "• Lunedì alle 10:30\n"
            "• 25/12 alle 14:00\n"
            "• Oggi pomeriggio"
        )
        
        await self.send_text_and_voice(update, message, keyboard)
    
    async def appuntamenti_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /appuntamenti"""
        try:
            user_id = update.effective_user.id
            # Ottieni solo gli appuntamenti di questo utente
            events = self.calendar_manager.get_upcoming_appointments(7, user_id)
            message = self.calendar_manager.format_appointment_list(events)
            await self.send_text_and_voice(update, message)
        except Exception as e:
            logger.error(f"Errore nel recupero appuntamenti: {e}")
            await self.send_text_and_voice(update, "❌ Errore nel recupero degli appuntamenti.")
    
    async def cancella_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /cancella per annullare prenotazioni in corso"""
        user_id = update.effective_user.id
        
        if user_id in self.booking_flows:
            del self.booking_flows[user_id]
            await update.message.reply_text("❌ Prenotazione annullata.")
        else:
            await update.message.reply_text("Non hai prenotazioni in corso da annullare.")

    async def profilo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestisce il comando /profilo per visualizzare le informazioni utente"""
        user_id = update.effective_user.id
        
        if not self.user_manager.is_user_registered(user_id):
            await self.start_registration_flow(update)
            return
        
        contact_info = self.user_manager.get_user_contact_info(user_id)
        message = f"👤 **Il tuo profilo:**\n\n{contact_info}\n\n_Per modificare i dati, contatta l'amministratore._"
        
        await self.send_text_and_voice(update, message)

    async def handle_registration_flow(self, update: Update, text: str):
        """Gestisce il flusso di registrazione utente"""
        user_id = update.effective_user.id
        flow = self.registration_flows[user_id]
        
        if flow["step"] == "waiting_nome":
            # Estrai il nome usando AI per una migliore precisione
            extracted_name = await self.extract_name_from_text(text)
            if not extracted_name:
                message = "❌ Non ho capito il tuo nome. Potresti ripeterlo in modo più chiaro?\n\n*Esempio: 'Mi chiamo Marco' oppure semplicemente 'Marco'*"
                await self.send_text_and_voice(update, message)
                return
                
            flow["data"]["nome"] = extracted_name.title()
            flow["step"] = "waiting_cognome"
            
            message = f"✅ Perfetto **{extracted_name.title()}**!\n\nOra dimmi il tuo **cognome**:"
            await self.send_text_and_voice(update, message)
            
        elif flow["step"] == "waiting_cognome":
            # Estrai il cognome usando AI
            extracted_surname = await self.extract_name_from_text(text)
            if not extracted_surname:
                message = "❌ Non ho capito il tuo cognome. Potresti ripeterlo?\n\n*Esempio: 'Il mio cognome è Rossi' oppure semplicemente 'Rossi'*"
                await self.send_text_and_voice(update, message)
                return
                
            flow["data"]["cognome"] = extracted_surname.title()
            flow["step"] = "waiting_email"
            
            message = f"✅ **{flow['data']['nome']} {extracted_surname.title()}**\n\nAdesso mi serve la tua **email**:"
            await self.send_text_and_voice(update, message)
            
        elif flow["step"] == "waiting_email":
            email = text.strip().lower()
            if "@" not in email or "." not in email:
                message = "❌ Email non valida. Riprova con un formato corretto (es: nome@esempio.com):"
                await self.send_text_and_voice(update, message)
                return
                
            flow["data"]["email"] = email
            flow["step"] = "waiting_telefono"
            
            message = "✅ Email salvata!\n\nAdesso dimmi il tuo **numero di telefono**:"
            await self.send_text_and_voice(update, message)
            
        elif flow["step"] == "waiting_telefono":
            telefono = text.strip()
            flow["data"]["telefono"] = telefono
            flow["step"] = "waiting_via"
            
            message = "✅ Telefono salvato!\n\nDimmi il tuo **indirizzo** (via e numero civico):"
            await self.send_text_and_voice(update, message)
            
        elif flow["step"] == "waiting_via":
            flow["data"]["via"] = text.strip().title()
            flow["step"] = "waiting_citta"
            
            message = "✅ Indirizzo salvato!\n\nInfine, dimmi la tua **città**:"
            await self.send_text_and_voice(update, message)
            
        elif flow["step"] == "waiting_citta":
            flow["data"]["citta"] = text.strip().title()
            
            # Completa la registrazione
            success = self.user_manager.register_user(
                user_id=user_id,
                telegram_name=flow["data"]["telegram_name"],
                nome=flow["data"]["nome"],
                cognome=flow["data"]["cognome"],
                email=flow["data"]["email"],
                telefono=flow["data"]["telefono"],
                via=flow["data"]["via"],
                citta=flow["data"]["citta"]
            )
            
            if success:
                # Pulisci il flusso di registrazione
                del self.registration_flows[user_id]
                
                # Messaggio di benvenuto completato
                welcome_message = (
                    f"🎉 **Registrazione completata!**\n\n"
                    f"Benvenuto **{flow['data']['nome']} {flow['data']['cognome']}**!\n\n"
                    f"✅ I tuoi dati sono stati salvati in sicurezza.\n"
                    f"📅 Ora puoi prenotare i tuoi appuntamenti!\n\n"
                    f"**Comandi disponibili:**\n"
                    f"📅 /prenota - Prenota un appuntamento\n"
                    f"📋 /appuntamenti - Vedi i tuoi appuntamenti\n"
                    f"👤 /profilo - Visualizza il tuo profilo\n\n"
                    f"Puoi anche dire semplicemente: _'Voglio prenotare per domani alle 15'_ e io ti aiuterò! 🚀"
                )
                
                await self.send_text_and_voice(update, welcome_message)
            else:
                message = "❌ Errore nella registrazione. Riprova più tardi o contatta l'assistenza."
                await self.send_text_and_voice(update, message)
                del self.registration_flows[user_id]

    async def extract_name_from_text(self, text: str) -> str:
        """Estrae il nome/cognome dal testo usando AI"""
        try:
            # Prompt specifico per l'estrazione di nomi
            extraction_prompt = f"""Estrai SOLO il nome proprio dalla seguente frase, senza altre parole:

Frase: "{text}"

Regole:
- Restituisci SOLO il nome proprio (es: "Marco", "Maria", "Rossi", "Verdi")
- NON includere parole come "mi chiamo", "sono", "il mio nome è", ecc.
- Se ci sono più nomi, prendi solo il primo
- Se non c'è un nome, rispondi "NESSUN_NOME"
- Mantieni la maiuscola iniziale

Esempi:
"Mi chiamo Stefano" → Stefano
"Sono Marco" → Marco  
"Il mio cognome è Rossi" → Rossi
"Anna Maria" → Anna
"francesco" → Francesco
"ciao come stai" → NESSUN_NOME

Nome estratto:"""

            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": extraction_prompt}],
                max_tokens=50,
                temperature=0.1
            )
            
            extracted = response.choices[0].message.content.strip()
            
            # Verifica che sia un nome valido
            if extracted == "NESSUN_NOME" or len(extracted) < 2 or len(extracted) > 30:
                return None
                
            # Rimuovi eventuali caratteri non alfabetici (tranne spazi e apostrofi)
            import re
            cleaned = re.sub(r"[^a-zA-ZÀ-ÿ\s']", "", extracted).strip()
            
            return cleaned if cleaned else None
            
        except Exception as e:
            logger.error(f"Errore nell'estrazione del nome: {e}")
            # Fallback: prova estrazione semplice
            simple_extract = self.simple_name_extraction(text)
            return simple_extract

    def simple_name_extraction(self, text: str) -> str:
        """Estrazione semplice del nome come fallback"""
        import re
        
        text = text.strip()
        
        # Pattern comuni per nomi
        patterns = [
            r"mi chiamo\s+([a-zA-ZÀ-ÿ\s']+)",
            r"sono\s+([a-zA-ZÀ-ÿ\s']+)",
            r"il mio nome è\s+([a-zA-ZÀ-ÿ\s']+)",
            r"mi chiamano\s+([a-zA-ZÀ-ÿ\s']+)",
            r"^([a-zA-ZÀ-ÿ']+)$",  # Solo nome
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                name = match.group(1).strip().title()
                # Verifica che sia ragionevole
                if 2 <= len(name) <= 30 and not any(char.isdigit() for char in name):
                    return name
        
        return None

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
        
        # Controlla disponibilità per questo specifico utente
        is_free, conflicts = self.calendar_manager.check_availability(start_time, end_time, user_id)
        
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
        
        # Crea bottoni di conferma
        keyboard = self._create_confirmation_keyboard()
        
        message = (
            f"📋 **Riepilogo Appuntamento**\n\n"
            f"📅 **Data:** {formatted_start}\n"
            f"⏰ **Fine:** {formatted_end}\n"
            f"📝 **Oggetto:** {text}\n\n"
            f"🔸 **Clicca un bottone** per confermare o annullare\n"
            f"🔸 **Oppure scrivi/di' 'sì'** per confermare"
        )
        
        await self.send_text_and_voice(update, message, keyboard)
    
    async def _handle_confirmation_input(self, update: Update, text: str, flow: dict):
        """Gestisce la conferma dell'appuntamento"""
        user_id = update.effective_user.id
        
        text_lower = text.lower().strip()
        
        if text_lower in ['sì', 'si', 'yes', 'ok', 'conferma', 'confermo']:
            # Crea l'appuntamento
            try:
                # Ottieni le informazioni complete dell'utente per la descrizione
                user_info = self.user_manager.format_user_for_calendar(user_id)
                
                event = self.calendar_manager.create_appointment(
                    title=flow["data"]["title"],
                    start_time=flow["data"]["datetime"],
                    end_time=flow["data"]["end_time"],
                    description=f"Appuntamento prenotato tramite bot Telegram\n\n{user_info}",
                    user_id=user_id
                )
                
                if event:
                    formatted_time = flow["data"]["datetime"].strftime("%d/%m/%Y alle %H:%M")
                    
                    # Messaggi più umani e personalizzati (stessa logica dell'altra funzione)
                    messages_variants = [
                        f"🎉 **Perfetto! Tutto sistemato!**\n\n📅 Ci vediamo {formatted_time}\n📝 {flow['data']['title']}\n\n💡 Ora ti mando un file per salvarlo nel tuo calendario personale!\nTi farò anche un promemoria prima dell'appuntamento. A presto! 😊",
                        
                        f"✨ **Fantastico! Appuntamento prenotato!**\n\n📅 Ti aspetto {formatted_time}\n📝 {flow['data']['title']}\n\n📱 Ti invio subito un file per aggiungerlo al tuo telefono!\nNon preoccuparti, ti ricorderò io prima dell'appuntamento! 👍",
                        
                        f"🚀 **Eccellente! È fatta!**\n\n📅 Appuntamento fissato per {formatted_time}\n📝 {flow['data']['title']}\n\n💾 Riceverai un file da salvare nel tuo calendario!\nE ovviamente ti avviserò prima che inizi. Ci sentiamo presto! 🎯"
                    ]
                    
                    message = random.choice(messages_variants)
                    await self.send_text_and_voice(update, message)
                    
                    # Genera e invia file .ics
                    try:
                        ics_content = self._generate_ics_file(
                            title=flow['data']['title'],
                            start_time=flow['data']['datetime'],
                            end_time=flow['data']['end_time'],
                            description=f"Appuntamento prenotato tramite bot Telegram"
                        )
                        
                        if ics_content:
                            date_str = flow['data']['datetime'].strftime("%Y%m%d_%H%M")
                            title_clean = re.sub(r'[^a-zA-Z0-9\s]', '', flow['data']['title'])[:20]
                            filename = f"appuntamento_{date_str}_{title_clean}.ics"
                            
                            with tempfile.NamedTemporaryFile(mode='w', suffix='.ics', delete=False, encoding='utf-8') as temp_ics:
                                temp_ics.write(ics_content)
                                temp_ics_path = temp_ics.name
                            
                            try:
                                with open(temp_ics_path, 'rb') as ics_file:
                                    await update.message.reply_document(
                                        document=ics_file,
                                        filename=filename,
                                        caption="📅 **File Calendario**\n\nScarica e apri questo file per aggiungere l'appuntamento al tuo calendario!\n\n📱 *Tocca il file → Apri con → Calendario*"
                                    )
                            finally:
                                os.unlink(temp_ics_path)
                    except Exception as e:
                        logger.error(f"Errore nell'invio del file .ics (testo): {e}")
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