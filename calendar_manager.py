import os
import json
import tempfile
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pytz

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GOOGLE_CREDENTIALS_JSON, CALENDAR_ID

logger = logging.getLogger(__name__)

class CalendarManager:
    def __init__(self):
        self.service = None
        self.calendar_id = CALENDAR_ID
        self.timezone = pytz.timezone('Europe/Rome')
        self.setup_service()
    
    def setup_service(self):
        """Inizializza il servizio Google Calendar"""
        try:
            logger.info("Inizializzazione servizio Google Calendar...")
            
            if not GOOGLE_CREDENTIALS_JSON:
                raise ValueError("GOOGLE_CREDENTIALS_JSON non configurata")
            
            # Crea file temporaneo per le credenziali
            credentials_data = json.loads(GOOGLE_CREDENTIALS_JSON)
            logger.info(f"Credenziali caricate per project_id: {credentials_data.get('project_id')}")
            logger.info(f"Client email: {credentials_data.get('client_email')}")
            
            # Crea un file temporaneo per le credenziali
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(credentials_data, f)
                credentials_file = f.name
            
            # Configurazione scopes
            scopes = ['https://www.googleapis.com/auth/calendar']
            
            # Crea credenziali
            credentials = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=scopes
            )
            
            # Crea il servizio
            self.service = build('calendar', 'v3', credentials=credentials)
            
            # Test della connessione
            calendar_list = self.service.calendarList().list().execute()
            logger.info(f"Calendari accessibili: {len(calendar_list.get('items', []))}")
            
            # Pulisci il file temporaneo
            os.unlink(credentials_file)
            
            logger.info(f"Servizio Google Calendar inizializzato con successo. Calendar ID: {self.calendar_id}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Errore nel parsing delle credenziali JSON: {e}")
            self.service = None
        except Exception as e:
            logger.error(f"Errore nell'inizializzazione del servizio Google Calendar: {e}")
            self.service = None
    
    def parse_datetime_natural(self, text: str) -> Optional[datetime]:
        """Parsa una data/ora in linguaggio naturale in italiano"""
        import re
        from datetime import date
        
        now = datetime.now(self.timezone)
        text = text.lower().strip()
        
        # Pattern per date assolute
        date_patterns = [
            # gg/mm o gg/mm/aaaa
            (r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', lambda m: self._parse_date_dmy(m, now.year)),
            # oggi
            (r'\boggi\b', lambda m: now.replace(hour=9, minute=0, second=0, microsecond=0)),
            # domani
            (r'\bdomani\b', lambda m: (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)),
            # dopodomani
            (r'\bdopodomani\b', lambda m: (now + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)),
        ]
        
        # Pattern per giorni della settimana
        giorni = {
            'lunedì': 0, 'martedì': 1, 'mercoledì': 2, 'giovedì': 3, 
            'venerdì': 4, 'sabato': 5, 'domenica': 6
        }
        
        parsed_date = None
        
        # Prova pattern di date
        for pattern, parser in date_patterns:
            match = re.search(pattern, text)
            if match:
                parsed_date = parser(match)
                break
        
        # Prova giorni della settimana
        if not parsed_date:
            for giorno, weekday in giorni.items():
                if giorno in text:
                    days_ahead = weekday - now.weekday()
                    if days_ahead <= 0:  # Se è oggi o passato, prendi la prossima settimana
                        days_ahead += 7
                    parsed_date = (now + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
                    break
        
        if not parsed_date:
            return None
        
        # Cerca pattern di orario
        time_patterns = [
            # HH:MM
            (r'(\d{1,2}):(\d{2})', lambda h, m: (int(h), int(m))),
            # HH (ore)
            (r'\b(\d{1,2})\s*(?:ore|h)\b', lambda h: (int(h), 0)),
            # alle HH
            (r'alle\s+(\d{1,2})(?::(\d{2}))?', lambda h, m=None: (int(h), int(m) if m else 0)),
        ]
        
        for pattern, parser in time_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    if len(match.groups()) == 2:
                        hour, minute = parser(match.group(1), match.group(2))
                    else:
                        hour, minute = parser(match.group(1))
                    
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        parsed_date = parsed_date.replace(hour=hour, minute=minute)
                        break
                except (ValueError, TypeError):
                    continue
        
        return parsed_date
    
    def _parse_date_dmy(self, match, default_year):
        """Parsa una data in formato gg/mm/aaaa"""
        try:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3)) if match.group(3) else default_year
            
            return self.timezone.localize(datetime(year, month, day, 9, 0))
        except (ValueError, TypeError):
            return None
    
    def check_availability(self, start_time: datetime, end_time: datetime) -> Tuple[bool, List[Dict]]:
        """Controlla disponibilità nel calendario"""
        if not self.service:
            return False, []
        
        try:
            # Converti in UTC per l'API
            start_utc = start_time.astimezone(pytz.UTC)
            end_utc = end_time.astimezone(pytz.UTC)
            
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=start_utc.isoformat(),
                timeMax=end_utc.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            is_free = len(events) == 0
            
            return is_free, events
            
        except HttpError as e:
            logger.error(f"Errore nel controllo disponibilità: {e}")
            return False, []
    
    def create_appointment(self, title: str, start_time: datetime, end_time: datetime, description: str = "") -> Optional[Dict]:
        """Crea un appuntamento nel calendario"""
        if not self.service:
            logger.error("Servizio Google Calendar non disponibile")
            return None
        
        try:
            logger.info(f"Tentativo di creare appuntamento: {title}")
            logger.info(f"Start time: {start_time}")
            logger.info(f"Calendar ID: {self.calendar_id}")
            
            # Converti in UTC per l'API
            start_utc = start_time.astimezone(pytz.UTC)
            end_utc = end_time.astimezone(pytz.UTC)
            
            event = {
                'summary': title,
                'description': description,
                'start': {
                    'dateTime': start_utc.isoformat(),
                    'timeZone': 'Europe/Rome',
                },
                'end': {
                    'dateTime': end_utc.isoformat(),
                    'timeZone': 'Europe/Rome',
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 15},
                        {'method': 'email', 'minutes': 60},
                    ],
                },
            }
            
            logger.info(f"Dati evento da creare: {event}")
            
            # Prima verifica se il calendario esiste e abbiamo accesso
            try:
                calendar_info = self.service.calendars().get(calendarId=self.calendar_id).execute()
                logger.info(f"Calendario trovato: {calendar_info.get('summary')}")
            except HttpError as cal_error:
                logger.error(f"Errore accesso calendario {self.calendar_id}: {cal_error}")
                if cal_error.resp.status == 404:
                    logger.error("Calendario non trovato! Verifica CALENDAR_ID e permessi del service account")
                return None
            
            # Crea l'evento
            event = self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
            logger.info(f"✅ Appuntamento creato con successo!")
            logger.info(f"Event ID: {event.get('id')}")
            logger.info(f"Link: {event.get('htmlLink')}")
            return event
            
        except HttpError as e:
            logger.error(f"❌ Errore HTTP nella creazione dell'appuntamento: {e}")
            logger.error(f"Status: {e.resp.status}")
            logger.error(f"Reason: {e.resp.reason}")
            if e.resp.status == 403:
                logger.error("Errore 403: Il service account non ha i permessi necessari sul calendario")
            return None
        except Exception as e:
            logger.error(f"❌ Errore generico nella creazione dell'appuntamento: {e}")
            return None
    
    def get_upcoming_appointments(self, days: int = 7) -> List[Dict]:
        """Ottieni i prossimi appuntamenti"""
        if not self.service:
            return []
        
        try:
            now = datetime.now(self.timezone)
            end_time = now + timedelta(days=days)
            
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=now.astimezone(pytz.UTC).isoformat(),
                timeMax=end_time.astimezone(pytz.UTC).isoformat(),
                singleEvents=True,
                orderBy='startTime',
                maxResults=20
            ).execute()
            
            return events_result.get('items', [])
            
        except HttpError as e:
            logger.error(f"Errore nel recupero appuntamenti: {e}")
            return []
    
    def delete_appointment(self, event_id: str) -> bool:
        """Cancella un appuntamento"""
        if not self.service:
            return False
        
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            logger.info(f"Appuntamento {event_id} cancellato")
            return True
            
        except HttpError as e:
            logger.error(f"Errore nella cancellazione dell'appuntamento: {e}")
            return False
    
    def format_appointment_list(self, events: List[Dict]) -> str:
        """Formatta la lista degli appuntamenti per la risposta"""
        if not events:
            return "📅 Non hai appuntamenti nei prossimi giorni."
        
        formatted = "📅 **I tuoi prossimi appuntamenti:**\n\n"
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            title = event.get('summary', 'Appuntamento senza titolo')
            
            # Parsa la data
            if 'T' in start:  # dateTime
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                dt = dt.astimezone(self.timezone)
                formatted_time = dt.strftime("%d/%m/%Y alle %H:%M")
            else:  # date
                dt = datetime.fromisoformat(start)
                formatted_time = dt.strftime("%d/%m/%Y")
            
            formatted += f"🕐 {formatted_time}\n📝 {title}\n\n"
        
        return formatted
    
    def suggest_free_slots(self, preferred_date: datetime, duration_minutes: int = 60) -> List[datetime]:
        """Suggerisci slot liberi per una data"""
        if not self.service:
            return []
        
        # Orari di lavoro: 9:00 - 18:00
        start_hour, end_hour = 9, 18
        slot_duration = timedelta(minutes=duration_minutes)
        
        # Crea slot ogni 30 minuti
        slots = []
        current_time = preferred_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        end_time = preferred_date.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        
        while current_time + slot_duration <= end_time:
            slot_end = current_time + slot_duration
            is_free, _ = self.check_availability(current_time, slot_end)
            
            if is_free:
                slots.append(current_time)
            
            current_time += timedelta(minutes=30)
        
        return slots[:5]  # Restituisci max 5 slot 