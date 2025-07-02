import os
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# Configurazione API keys
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY')
VOICE_ID = os.getenv('VOICE_ID')

# Verifica che tutte le variabili siano configurate
required_vars = {
    'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
    'OPENAI_API_KEY': OPENAI_API_KEY,
    'ELEVENLABS_API_KEY': ELEVENLABS_API_KEY,
    'VOICE_ID': VOICE_ID
}

for var_name, var_value in required_vars.items():
    if not var_value:
        raise ValueError(f"Variabile d'ambiente {var_name} non configurata nel file .env") 