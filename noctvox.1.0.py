import os
import telebot
from flask import Flask

# Obtener el token de las variables de entorno de producción
TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# Servidor web ligero para mantener el bot activo en Render
app = Flask('')

@app.route('/')
def home():
    return "Bot de Telegram activo y en ejecución."

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "🤖 ¡Hola! El bot está en línea y funcionando correctamente en producción.")

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    import threading
    # Ejecutamos Flask en un hilo secundario
    t = threading.Thread(target=run_flask)
    t.start()
    
    print("Bot iniciado en modo producción...")
    bot.infinity_polling()

