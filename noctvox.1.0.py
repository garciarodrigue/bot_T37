import os
import telebot
from flask import Flask
from google import genai

# Inicializar tokens y cliente
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask('')

@app.route('/')
def home():
    return "Bot con Gemini en Render activo y operando 24/7."

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "🤖 *¡Hola! Soy tu bot con Inteligencia Artificial (Gemini).* 🚀\n\n"
        "Estoy ejecutándome 24/7 en la nube de Render.\n"
        "Puedes escribirme directamente cualquier pregunta, duda o tarea y te responderé usando IA."
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# Manejador para cualquier texto que no sea un comando explícito
@bot.message_handler(func=lambda message: True)
def handle_ai_chat(message):
    user_text = message.text
    
    # Mostrar un aviso de que la IA está procesando
    processing_msg = bot.reply_to(message, "🧠 Pensando respuesta...")
    
    try:
        # Generar respuesta usando el modelo estándar de Gemini
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_text,
        )
        
        reply_text = response.text
        
        # Si la respuesta es muy larga para Telegram, recortarla o enviarla por partes
        if len(reply_text) > 4000:
            reply_text = reply_text[:4000] + "\n\n[Respuesta recortada por límite de longitud]"
            
        # Editar el mensaje de "Pensando..." con la respuesta final de Gemini
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=reply_text
        )
        
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=f"❌ Ocurrió un error al consultar con Gemini: {e}"
        )

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    import threading
    threading.Thread(target=run_flask).start()
    print("Bot con IA iniciado en Render...")
    bot.infinity_polling()

