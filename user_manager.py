import json
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self, data_file: str = "users_data.json"):
        self.data_file = data_file
        self.users_data = {}
        self.load_users_data()
    
    def load_users_data(self):
        """Carica i dati degli utenti dal file JSON"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.users_data = json.load(f)
                logger.info(f"Dati utenti caricati: {len(self.users_data)} utenti registrati")
            else:
                self.users_data = {}
                logger.info("File utenti non esistente, creato nuovo database")
        except Exception as e:
            logger.error(f"Errore nel caricamento dati utenti: {e}")
            self.users_data = {}
    
    def save_users_data(self):
        """Salva i dati degli utenti nel file JSON"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.users_data, f, ensure_ascii=False, indent=2)
            logger.info("Dati utenti salvati con successo")
        except Exception as e:
            logger.error(f"Errore nel salvataggio dati utenti: {e}")
    
    def is_user_registered(self, user_id: int) -> bool:
        """Controlla se un utente è già registrato"""
        return str(user_id) in self.users_data
    
    def get_user_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Ottieni le informazioni di un utente"""
        return self.users_data.get(str(user_id))
    
    def register_user(self, user_id: int, telegram_name: str, nome: str, cognome: str, 
                     email: str, telefono: str, via: str, citta: str) -> bool:
        """Registra un nuovo utente"""
        try:
            user_data = {
                "telegram_name": telegram_name,
                "nome": nome,
                "cognome": cognome,
                "email": email,
                "telefono": telefono,
                "via": via,
                "citta": citta,
                "registration_date": datetime.now().isoformat(),
                "total_appointments": 0,
                "last_appointment": None
            }
            
            self.users_data[str(user_id)] = user_data
            self.save_users_data()
            
            logger.info(f"Utente registrato: {nome} {cognome} (ID: {user_id})")
            return True
            
        except Exception as e:
            logger.error(f"Errore nella registrazione utente: {e}")
            return False
    
    def update_user_stats(self, user_id: int, appointment_date: str):
        """Aggiorna le statistiche utente dopo un appuntamento"""
        user_data = self.get_user_info(user_id)
        if user_data:
            user_data["total_appointments"] += 1
            user_data["last_appointment"] = appointment_date
            self.save_users_data()
    
    def get_user_display_name(self, user_id: int) -> str:
        """Ottieni il nome completo per visualizzazione"""
        user_data = self.get_user_info(user_id)
        if user_data:
            return f"{user_data['nome']} {user_data['cognome']}"
        return f"Utente {user_id}"
    
    def get_user_contact_info(self, user_id: int) -> str:
        """Ottieni le informazioni di contatto formattate"""
        user_data = self.get_user_info(user_id)
        if not user_data:
            return f"User ID: {user_id}"
        
        info = f"""👤 {user_data['nome']} {user_data['cognome']}
📧 {user_data['email']}
📱 {user_data['telefono']}
🏠 {user_data['via']}, {user_data['citta']}
📊 Appuntamenti totali: {user_data['total_appointments']}"""
        
        return info
    
    def format_user_for_calendar(self, user_id: int) -> str:
        """Formatta le informazioni utente per la descrizione del calendario"""
        user_data = self.get_user_info(user_id)
        if not user_data:
            return f"User ID: {user_id}"
        
        return f"""═════════════════════════════════════════
👤 INFORMAZIONI CLIENTE
═════════════════════════════════════════

Cliente: {user_data['nome']} {user_data['cognome']}
📧 Email: {user_data['email']}
📱 Telefono: {user_data['telefono']}
🏠 Indirizzo: {user_data['via']}, {user_data['citta']}

📊 Statistiche Cliente:
• Appuntamenti totali: {user_data['total_appointments']}
• Ultimo appuntamento: {user_data['last_appointment'] or 'Primo appuntamento'}
• Cliente dal: {user_data['registration_date'][:10]}

═════════════════════════════════════════
⚠️  INFORMAZIONI RISERVATE - UP! Informatica
═════════════════════════════════════════"""
    
    def get_all_users_count(self) -> int:
        """Ottieni il numero totale di utenti registrati"""
        return len(self.users_data)
    
    def search_user_by_name(self, search_term: str) -> list:
        """Cerca utenti per nome o cognome"""
        results = []
        search_lower = search_term.lower()
        
        for user_id, user_data in self.users_data.items():
            full_name = f"{user_data['nome']} {user_data['cognome']}".lower()
            if search_lower in full_name or search_lower in user_data['email'].lower():
                results.append({
                    'user_id': user_id,
                    'name': f"{user_data['nome']} {user_data['cognome']}",
                    'email': user_data['email']
                })
        
        return results 