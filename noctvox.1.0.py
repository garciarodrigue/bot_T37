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

# Configuración de MongoDB Atlas y colecciones
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client["telegram_bot_db"]
    users_collection = db["usuarios"]
    remembers_collection = db["remembers"]
    messuser_collection = db["messUser"]
    errors_collection = db["Errors"]  # 🛡️ Nueva colección para registro de errores y auditoría
    print("✅ ¡Conexión exitosa con MongoDB Atlas!")
except Exception as e:
    print(f"❌ Error crítico conectando a MongoDB Atlas: {e}")
    users_collection = None
    remembers_collection = None
    messuser_collection = None
    errors_collection = None

# Servidor Flask para mantener el servicio web activo en Render
app = Flask('')

@app.route('/')
def home():
    return "Bot administrador con IA y MongoDB en Render activo 24/7."

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
            {"$set": user_data, "$setOnInsert": {"first_seen": datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        registrar_error(message, "Base de Datos", f"Error al registrar usuario: {str(e)}")

def registrar_error(message, origen, error_detalle):
    """Guarda de forma estructurada los errores del sistema en la colección Errors"""
    if errors_collection is None:
        return
    try:
        user = message.from_user if message and hasattr(message, 'from_user') else None
        error_doc = {
            "telegram_id": user.id if user else "Sistema/Desconocido",
            "username": user.username if user and user.username else "Sin alias",
            "prompt_o_comando": message.text if message and hasattr(message, 'text') else "N/A",
            "origen_error": origen,  # Ej: 'Gemini AI', 'Render/Telegram', 'Base de Datos'
            "error_mensaje": str(error_detalle),
            "date": datetime.utcnow()
        }
        errors_collection.insert_one(error_doc)
        print(f"⚠️ Error registrado en la colección Errors [{origen}]")
    except Exception as ex:
        print(f"❌ Falló al registrar en Errors: {ex}")

def guardar_remember(user_id, quest, answer):
    """Guarda solo las preguntas y respuestas de IA (excluye comandos)"""
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
        "🤖 *¡Hola! Soy tu bot con panel de administración y seguridad.*\n\n"
        "• Escríbeme cualquier pregunta para hablar con la IA.\n"
        "• `/mensaje [texto] @usuario` para mensajería cifrada.\n"
        "• `/admin [vínculo]` para que el administrador revise conversaciones."
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# 🔍 NUEVO: Panel de Admin para consultar vínculos y conversaciones en messUser
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    registrar_usuario(message)
    
    # Opcional: Puedes restringir esto poniendo tu propio telegram_id de admin aquí abajo
    # ADMIN_ID = 123456789
    # if message.from_user.id != ADMIN_ID:
    #     bot.reply_to(message, "⛔ No tienes permisos de administrador.")
    #     return

    args = message.text.replace('/admin', '').strip()
    
    if not args:
        # Si no pone argumentos, mostrar resumen rápido de la BD
        try:
            total_users = users_collection.count_documents({}) if users_collection is not None else 0
            total_msgs = messuser_collection.count_documents({}) if messuser_collection is not None else 0
            total_errors = errors_collection.count_documents({}) if errors_collection is not None else 0
            
            resumen = (
                "🛠️ *Panel de Administración - Resumen*\n\n"
                f"👥 Usuarios registrados: `{total_users}`\n"
                f"🔒 Mensajes seguros (`messUser`): `{total_msgs}`\n"
                f"⚠️ Errores registrados (`Errors`): `{total_errors}`\n\n"
                "💡 *Uso:* Escribe `/admin [vínculo]` (ej: `/admin MarEdu`) para inspeccionar un chat seguro específico."
            )
            bot.reply_to(message, resumen, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Error al consultar estadísticas: {e}")
        return

    # Si proporcionó un vínculo, buscar en messUser
    vinculo_buscado = args
    if messuser_collection is None:
        bot.reply_to(message, "❌ Base de datos no conectada.")
        return
        
    cursor_mensajes = messuser_collection.find({"vínculo": {"$regex": f"^{vinculo_buscado}$", "$options": "i"}}).sort("date", 1)
    mensajes = list(cursor_mensajes)
    
    if not mensajes:
        bot.reply_to(message, f"🔍 No se encontraron registros con el vínculo: `{vinculo_buscado}`", parse_mode="Markdown")
        return
        
    reporte = f"📂 *Auditoría del Vínculo: `{vinculo_buscado}`*\n\n"
    for m in mensajes:
        fecha_str = m['date'].strftime('%Y-%m-%d %H:%M:%S')
        reporte += f"👤 **De:** {m['name']} ➡️ **Para:** {m['To']}\n"
        reporte += f"💬 **Mensaje:** `{m['ms']}`\n"
        reporte += f"🕒 *Fecha (UTC):* {fecha_str}\n"
        reporte += "-----------------------------------\n"
        
    if len(reporte) > 4000:
        reporte = reporte[:4000] + "\n[Reporte recortado]"
        
    bot.reply_to(message, reporte, parse_mode="Markdown")

@bot.message_handler(commands=['mensaje'])
def handle_secure_message(message):
    registrar_usuario(message)
    sender = message.from_user
    sender_name = sender.first_name or sender.username or "Usuario"
    
    full_text = message.text.replace('/mensaje', '').strip()
    mencionados = re.findall(r'@([a-zA-Z0-9_]+)', full_text)
    
    if not mencionados:
        bot.reply_to(message, "⚠️ Formato incorrecto. Usa: `/mensaje [texto] @usuario`", parse_mode="Markdown")
        return
        
    target_username = mencionados[0]
    mensaje_contenido = re.sub(r'@[a-zA-Z0-9_]+', '', full_text).strip()
    
    if not mensaje_contenido:
        bot.reply_to(message, "⚠️ El mensaje no puede estar vacío.", parse_mode="Markdown")
        return

    if users_collection is None or messuser_collection is None:
        registrar_error(message, "Base de Datos", "Colecciones nulas al intentar enviar mensaje seguro.")
        bot.reply_to(message, "❌ Error de conexión con la base de datos.")
        return

    target_user_doc = users_collection.find_one({
        "username": {"$regex": f"^{target_username}$", "$options": "i"}
    })
    
    if not target_user_doc:
        bot.reply_to(message, f"❌ No se encontró al usuario @{target_username} registrado.")
        return
        
    target_id = target_user_doc["telegram_id"]
    target_name = target_user_doc.get("first_name") or target_username
    
    # Generar vínculo: 3 primeras letras de ambos nombres
    parte1 = sender_name[:3].capitalize()
    parte2 = target_name[:3].capitalize()
    vinculo_code = parte1 + parte2
    
    try:
        mess_data = {
            "vínculo": vinculo_code,
            "name": sender_name,
            "To": target_name,
            "ms": mensaje_contenido,
            "date": datetime.utcnow()
        }
        messuser_collection.insert_one(mess_data)
    except Exception as db_err:
        registrar_error(message, "Base de Datos", f"Fallo al insertar en messUser: {str(db_err)}")
        bot.reply_to(message, "❌ Hubo un error al guardar el mensaje seguro.")
        return
        
    bot.reply_to(message, f"✅ *Mensaje enviado y protegido.*\n🔗 *Vínculo:* `{vinculo_code}`", parse_mode="Markdown")
    
    try:
        alerta_destinatario = (
            f"🔒 *Nuevo mensaje seguro recibido*\n\n"
            f"👤 *De:* {sender_name}\n"
            f"💬 *Mensaje:* {mensaje_contenido}\n"
            f"🔗 *Vínculo:* `{vinculo_code}`"
        )
        bot.send_message(target_id, alerta_destinatario, parse_mode="Markdown")
    except Exception as send_err:
        registrar_error(message, "Render/Telegram", f"Fallo al enviar notificación al target_id {target_id}: {str(send_err)}")

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
                registrar_error(message, "Render/Telegram", f"No se pudo enviar alerta de privacidad: {str(alert_err)}")

    processing_msg = bot.reply_to(message, "🧠 Procesando...")
    prompt_final = user_text + investigation_context
    
    try:
        response = client_ai.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt_final
        )
        
        reply_text = response.text if response and hasattr(response, 'text') else "No se obtuvo respuesta."
        
        if len(reply_text) > 4000:
            reply_text = reply_text[:4000] + "\n[Recortado]"
            
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=reply_text
        )
        
        # 💡 Guardar en remembers SOLAMENTE si es chat con IA (excluyendo comandos puros)
        guardar_remember(user_id, user_text, reply_text)
        
    except Exception as e:
        error_msg = f"❌ Error de IA: {str(e)}"
        print(error_msg)
        # Registrar el error detallado en la colección Errors de MongoDB
        registrar_error(message, "Gemini AI", str(e))
        
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=error_msg
        )

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    print("Bot administrador con MongoDB Atlas e IA iniciado en Render...")
    bot.infinity_polling()

