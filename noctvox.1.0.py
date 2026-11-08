import os
import telebot
from flask import Flask
import requests

TOKEN = os.environ.get('BOT_TOKEN')
TERMUX_BRIDGE_URL = os.environ.get('TERMUX_BRIDGE_URL') # URL pública de Cloudflare + /exec
SECRET_TOKEN = "clave_secreta_super_segura_123"

bot = telebot.TeleBot(TOKEN)
app = Flask('')

@app.route('/')
def home():
    return "Servidor central en Render activo."

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "🤖 Nodo central en Render activo. Usa `/ls` para consultar el nodo local en Termux.", parse_mode="Markdown")

@bot.message_handler(commands=['ls', 'cmd'])
def handle_remote_command(message):
    if not TERMUX_BRIDGE_URL:
        bot.reply_to(message, "⚠️ El nodo periférico (Termux) no está vinculado en las variables de entorno.")
        return
        
    command_to_run = "ls -la" if message.text == "/ls" else message.text.replace("/cmd", "").strip()
    
    bot.reply_to(message, f"🔄 Solicitando ejecución a nodo local: `{command_to_run}`...", parse_mode="Markdown")
    
    try:
        payload = {
            "secret": SECRET_TOKEN,
            "cmd": command_to_run
        }
        
        # Petición saliente desde Render hacia tu Termux mediante el túnel
        response = requests.post(TERMUX_BRIDGE_URL, json=payload, timeout=12)
        data = response.json()
        output = data.get("output", "Sin respuesta del nodo local.")
        
        if len(output) > 4000:
            output = output[:4000] + "\n[... Recortado ...]"
            
        bot.reply_to(message, f"💻 **Respuesta desde Termux:**\n```text\n{output}\n```", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ No se pudo conectar con el nodo local de Termux: {e}")

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    import threading
    threading.Thread(target=run_flask).start()
    bot.infinity_polling()

