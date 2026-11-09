import os
import telebot
from flask import Flask
from google import genai

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
        "Escríbeme cualquier pregunta o tarea y te responderé con IA."
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_ai_chat(message):
    user_text = message.text
    processing_msg = bot.reply_to(message, "🧠 Pensando respuesta...")
    
    try:
        # Usando el modelo exacto que está activo en tu cuenta
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=user_text
        )
        
        reply_text = response.text if response and hasattr(response, 'text') else "No se obtuvo respuesta del modelo."
        
        if len(reply_text) > 4000:
            reply_text = reply_text[:4000] + "\n\n[Respuesta recortada por límite]"
            
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=reply_text
        )
        
    except Exception as e:
        error_msg = f"❌ Error detallado de IA: {str(e)}"
        print(error_msg)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=error_msg
        )

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    import threading
    threading.Thread(target=run_flask).start()
    print("Bot con IA iniciado en Render...")
    bot.infinity_polling()

