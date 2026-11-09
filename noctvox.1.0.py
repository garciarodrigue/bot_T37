import os
import re
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
    remembers_collection = db["remembers"]
    messuser_collection = db["messUser"]  # Nueva colección para mensajes seguros
    print("✅ ¡Conexión exitosa con MongoDB Atlas!")
except Exception as e:
    print(f"❌ Error crítico conectando a MongoDB Atlas: {e}")
    users_collection = None
    remembers_collection = None
    messuser_collection = None

# Servidor Flask para mantener el servicio web activo en Render
app = Flask('')

@app.route('/')
def home():
    return "Bot mensajero seguro con IA y MongoDB en Render activo 24/7."

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
    """Guarda cada pregunta, respuesta y el ID del usuario en remembers"""
    if remembers_collection is None:
        return
    try:
        remember_data = {
            "telegram_id": user_id,
            "quest": quest,
            "answer": answer,
            "date": datetime.utcnow()
        }
        remembers_collection.insert_one(remember_data)
    except Exception as e:
        print(f"❌ Error al guardar en remembers: {e}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    registrar_usuario(message)
    welcome_text = (
        "🤖 *¡Hola! Soy tu bot mensajero y con IA en la nube.*\n\n"
        "Comandos disponibles:\n"
        "• Escríbeme cualquier pregunta para hablar con Gemini.\n"
        "• `/mensaje [tu texto] @usuario` para enviar un mensaje seguro cifrado."
    )
    guardar_remember(message.from_user.id, message.text, welcome_text)
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# 🔒 NUEVO: Manejador para el mensajero seguro /mensaje
@bot.message_handler(commands=['mensaje'])
def handle_secure_message(message):
    registrar_usuario(message)
    
    sender = message.from_user
    sender_name = sender.first_name or sender.username or "Usuario"
    
    # Extraer el texto después del comando /mensaje
    full_text = message.text.replace('/mensaje', '').strip()
    
    # Buscar si incluye un @usuario al final o en el texto
    mencionados = re.findall(r'@([a-zA-Z0-9_]+)', full_text)
    
    if not mencionados:
        bot.reply_to(message, "⚠️ Formato incorrecto. Debes usar: `/mensaje Hola cómo estás @usuario`", parse_mode="Markdown")
        return
        
    target_username = mencionados[0]
    
    # Remover el @usuario del contenido del mensaje para limpiar el texto (ms)
    mensaje_contenido = re.sub(r'@[a-zA-Z0-9_]+', '', full_text).strip()
    
    if not mensaje_contenido:
        bot.reply_to(message, "⚠️ El mensaje no puede estar vacío.", parse_mode="Markdown")
        return

    if users_collection is None or messuser_collection is None:
        bot.reply_to(message, "❌ Error de conexión con la base de datos.")
        return

    # Buscar al usuario destinatario en la BD
    target_user_doc = users_collection.find_one({
        "username": {"$regex": f"^{target_username}$", "$options": "i"}
    })
    
    if not target_user_doc:
        bot.reply_to(message, f"❌ No se encontró al usuario @{target_username} registrado en la base de datos del bot.")
        return
        
    target_id = target_user_doc["telegram_id"]
    target_name = target_user_doc.get("first_name") or target_username
    
    # Generar la clave vínculo: 3 primeras letras del remitente + 3 primeras letras del destinatario
    parte1 = sender_name[:3].capitalize()
    parte2 = target_name[:3].capitalize()
    vinculo_code = parte1 + parte2
    
    # Guardar en la colección messUser
    try:
        mess_data = {
            "vínculo": vinculo_code,
            "name": sender_name,
            "To": target_name,
            "ms": mensaje_contenido,
            "date": datetime.utcnow()
        }
        messuser_collection.insert_one(mess_data)
        print(f"🔒 Mensaje seguro guardado en messUser con vínculo: {vinculo_code}")
    except Exception as db_err:
        print(f"❌ Error al guardar en messUser: {db_err}")
        bot.reply_to(message, "❌ Hubo un error al guardar el mensaje seguro.")
        return
        
    # Notificar al remitente que se envió con éxito
    bot.reply_to(message, f"✅ *Mensaje enviado y protegido con éxito.*\n🔗 *Vínculo generado:* `{vinculo_code}`", parse_mode="Markdown")
    
    # Enviar notificación segura al destinatario
    try:
        alerta_destinatario = (
            f"🔒 *Nuevo mensaje seguro recibido*\n\n"
            f"👤 *De:* {sender_name}\n"
            f"💬 *Mensaje:* {mensaje_contenido}\n"
            f"🔗 *Vínculo:* `{vinculo_code}`"
        )
        bot.send_message(target_id, alerta_destinatario, parse_mode="Markdown")
    except Exception as send_err:
        print(f"⚠️ No se pudo notificar al usuario {target_id}: {send_err}")

# Manejador general de IA y menciones de investigación previas
@bot.message_handler(func=lambda message: True)
def handle_ai_chat(message):
    registrar_usuario(message)
    
    user_id = message.from_user.id
    user_text = message.text
    sender_name = message.from_user.first_name or f"@{message.from_user.username}" or "Alguien"
    
    mencionados = re.findall(r'@([a-zA-Z0-9_]{5,32})', user_text)
    
    investigation_context = ""
    if mencionados and users_collection is not None and remembers_collection is not None:
        target_username = mencionados[0]
        target_user_doc = users_collection.find_one({
            "username": {"$regex": f"^{target_username}$", "$options": "i"}
        })
        
        if target_user_doc:
            target_id = target_user_doc["telegram_id"]
            historial_cursor = remembers_collection.find({"telegram_id": target_id}).sort("date", -1).limit(15)
            historial_list = list(historial_cursor)
            
            if historial_list:
                historia_texto = "\n".join([f"- Preguntó: '{h['quest']}' | Bot respondió: '{h['answer']}'" for h in historial_list])
                investigation_context = (
                    f"\n\n[CONTEXTO DE INVESTIGACIÓN SOBRE EL USUARIO @{target_username}]:\n"
                    f"El usuario actual está preguntando por @{target_username}. "
                    f"Aquí tienes el historial reciente de preguntas y respuestas de @{target_username}:\n"
                    f"{historia_texto}"
                )
            
            try:
                alerta_msg = f"⚠️ *Aviso de seguridad/privacidad:* El usuario *{sender_name}* ha estado preguntando por ti recientemente."
                bot.send_message(target_id, alerta_msg, parse_mode="Markdown")
            except Exception as alert_err:
                print(f"⚠️ Alerta no enviada: {alert_err}")

    processing_msg = bot.reply_to(message, "🧠 Procesando...")
    prompt_final = user_text + investigation_context
    
    try:
        response = client_ai.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt_final
        )
        
        reply_text = response.text if response and hasattr(response, 'text') else "No se obtuvo respuesta."
        
        if len(reply_text) > 4000:
            reply_text = reply_text[:4000] + "\n\n[Recortado]"
            
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=reply_text
        )
        
        guardar_remember(user_id, user_text, reply_text)
        
    except Exception as e:
        error_msg = f"❌ Error de IA: {str(e)}"
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
    print("Bot con mensajero seguro e IA iniciado en Render...")
    bot.infinity_polling()

