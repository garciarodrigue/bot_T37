import os
import telebot
from flask import Flask
from google import genai
from pymongo import MongoClient
from datetime import datetime
import threading

# Cargar credenciales desde las variables de entorno de Render
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
MONGO_URI = os.environ.get('MONGO_URI')

# Inicializar Telegram y el cliente oficial de Gemini
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client_ai = genai.Client(api_key=GEMINI_API_KEY)

# Configuración de MongoDB Atlas
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client["telegram_bot_db"]
    users_collection = db["usuarios"]
    remembers_collection = db["remembers"]  # Nueva colección para las conversaciones
    print("✅ ¡Conexión exitosa con MongoDB Atlas!")
except Exception as e:
    print(f"❌ Error crítico conectando a MongoDB Atlas: {e}")
    users_collection = None
    remembers_collection = None

# Servidor Flask para mantener el servicio web activo en Render
app = Flask('')

@app.route('/')
def home():
    return "Bot con IA y MongoDB en Render activo y operando 24/7."

def registrar_usuario(message):
    """Guarda o actualiza la información general del usuario"""
    if users_collection is None:
        return
    try:
        user = message.from_user
        user_data = {
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language_code": user.language_code,
            "last_interaction": datetime.utcnow()
        }
        users_collection.update_one(
            {"telegram_id": user.id},
            {
                "$set": user_data,
                "$setOnInsert": {"first_seen": datetime.utcnow()}
            },
            upsert=True
        )
    except Exception as e:
        print(f"❌ Error al guardar usuario en MongoDB: {e}")

def guardar_remember(user_id, quest, answer):
    """Guarda cada pregunta, respuesta y el ID del usuario en la colección remembers"""
    if remembers_collection is None:
        return
    try:
        remember_data = {
            "telegram_id": user_id,
            "quest": quest,
            "answer": answer,
            "date": datetime.utcnow()  # Se guarda con fecha y hora UTC
        }
        remembers_collection.insert_one(remember_data)
        print(f"📝 Remember guardado para el usuario {user_id}")
    except Exception as e:
        print(f"❌ Error al guardar en remembers: {e}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    registrar_usuario(message)
    welcome_text = (
        "🤖 *¡Hola! Soy tu bot con IA (Gemini) y base de datos en la nube.*\n\n"
        "Estoy ejecutándome 24/7 en Render y guardando tus historiales.\n"
        "Escríbeme cualquier pregunta o tarea y te responderé con IA."
    )
    # Opcional: También podemos registrar el comando start en remembers si lo deseas
    guardar_remember(message.from_user.id, message.text, welcome_text)
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_ai_chat(message):
    # Registrar/actualizar perfil del usuario
    registrar_usuario(message)
    
    user_id = message.from_user.id
    user_text = message.text
    processing_msg = bot.reply_to(message, "🧠 Pensando respuesta...")
    
    try:
        # Generar respuesta con la IA
        response = client_ai.models.generate_content(
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
        
        # 💡 Guardar la pregunta y la respuesta en la colección 'remembers'
        guardar_remember(user_id, user_text, reply_text)
        
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
    threading.Thread(target=run_flask).start()
    print("Bot con MongoDB Atlas e IA iniciado en Render...")
    bot.infinity_polling()

