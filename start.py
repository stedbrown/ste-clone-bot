#!/usr/bin/env python3
"""
Script di avvio per il Bot Telegram Vocale
Verifica la configurazione prima di avviare il bot
"""

import sys
import os

def check_dependencies():
    """Verifica che tutte le dipendenze siano installate"""
    required_packages = {
        'telegram': 'python-telegram-bot',
        'openai': 'openai', 
        'elevenlabs': 'elevenlabs',
        'pydub': 'pydub',
        'dotenv': 'python-dotenv'
    }
    
    missing_packages = []
    
    for import_name, package_name in required_packages.items():
        try:
            __import__(import_name)
        except ImportError:
            missing_packages.append(package_name)
    
    if missing_packages:
        print("❌ Pacchetti mancanti:")
        for package in missing_packages:
            print(f"  - {package}")
        print("\n💡 Installa con: pip install -r requirements.txt")
        return False
    
    print("✅ Tutte le dipendenze sono installate")
    return True

def check_env_file():
    """Verifica che il file .env esista"""
    if not os.path.exists('.env'):
        print("❌ File .env non trovato!")
        print("💡 Crea un file .env con le tue API keys:")
        print("""
TELEGRAM_TOKEN=il_tuo_token_telegram
OPENAI_API_KEY=la_tua_api_key_openai
ELEVENLABS_API_KEY=la_tua_api_key_elevenlabs
VOICE_ID=il_tuo_voice_id_elevenlabs
""")
        return False
    
    print("✅ File .env trovato")
    return True

def check_configuration():
    """Verifica la configurazione"""
    try:
        from config import TELEGRAM_TOKEN, OPENAI_API_KEY, ELEVENLABS_API_KEY, VOICE_ID
        
        configs = {
            'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
            'OPENAI_API_KEY': OPENAI_API_KEY, 
            'ELEVENLABS_API_KEY': ELEVENLABS_API_KEY,
            'VOICE_ID': VOICE_ID
        }
        
        missing_configs = []
        for name, value in configs.items():
            if not value:
                missing_configs.append(name)
        
        if missing_configs:
            print("❌ Variabili d'ambiente mancanti:")
            for config in missing_configs:
                print(f"  - {config}")
            return False
        
        print("✅ Configurazione completa")
        return True
        
    except Exception as e:
        print(f"❌ Errore nella configurazione: {e}")
        return False

def check_ffmpeg():
    """Verifica che FFmpeg sia installato"""
    import subprocess
    try:
        # Su Windows, prova prima con cmd per accedere al PATH completo
        if os.name == 'nt':  # Windows
            result = subprocess.run(['cmd', '/c', 'ffmpeg', '-version'], 
                                  capture_output=True, check=True, text=True)
        else:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, check=True)
        print("✅ FFmpeg installato")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ FFmpeg non trovato!")
        print("💡 Installa FFmpeg:")
        print("  Windows: winget install --id=Gyan.FFmpeg -e")
        print("  Linux: sudo apt install ffmpeg") 
        print("  macOS: brew install ffmpeg")
        return False

def main():
    """Funzione principale"""
    print("🤖 Verifica configurazione Bot Telegram Vocale\n")
    
    checks = [
        ("Dipendenze Python", check_dependencies),
        ("File .env", check_env_file),
        ("Configurazione", check_configuration),
        ("FFmpeg", check_ffmpeg)
    ]
    
    all_ok = True
    
    for check_name, check_func in checks:
        print(f"🔍 Controllo {check_name}...")
        if not check_func():
            all_ok = False
        print()
    
    if all_ok:
        print("🚀 Tutto configurato correttamente! Avvio del bot...")
        print("-" * 50)
        
        # Importa e avvia il bot
        from bot import main as bot_main
        bot_main()
    else:
        print("❌ Configurazione incompleta. Risolvi i problemi sopra elencati.")
        sys.exit(1)

if __name__ == "__main__":
    main() 