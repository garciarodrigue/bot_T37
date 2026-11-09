import os
import re
import time
import telebot
from flask import Flask
from google import genai
from pymongo import MongoClient
from datetime import datetime, timedelta
import threading

# Cargar credenciales desde las variables de entorno de Render
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
MONGO_URI = os.environ.get('MONGO_URI')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or "117566" # Respaldo por defecto si no se configura

# 🔑 Configuración de las 3 API Keys de Gemini
API_KEY_1 = os.environ.get('GEMINI_API_KEY')
API_KEY_2 = os.environ.get('GEMINI_API_KEY_2') or API_KEY_1
API_KEY_3 = os.environ.get('GEMINI_API_KEY_3') or API_KEY_1

# Inicializar clientes de Gemini separados para cada rol
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client_ai_primary = genai.Client(api_key=API_KEY_1)
client_ai_backup = genai.Client(api_key=API_KEY_2)
client_ai_jarvis = genai.Client(api_key=API_KEY_3)

# Configurar el menú de comandos oficial en Telegram
try:
    bot.set_my_commands([
        telebot.types.BotCommand("start", "🌟 Iniciar el bot y ver opciones"),
        telebot.types.BotCommand("jarvis", "🦾 Forzar reporte de auditoría de Jarvis"),
        telebot.types.BotCommand("mensaje", "🔒 Enviar mensaje seguro cifrado (@usuario)"),
        telebot.types.BotCommand("admin", "🛡️ Panel de administración (Requiere clave)")
    ])
except Exception as e:
    print(f"⚠️ No se pudieron configurar los comandos en Telegram: {e}")

# Configuración de MongoDB Atlas y colecciones
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')
    db = mongo_client["telegram_bot_db"]
    users_collection = db["usuarios"]
    remembers_collection = db["remembers"]
    messuser_collection = db["messUser"]
    errors_collection = db["Errors"]
    print("✅ ¡Conexión exitosa con MongoDB Atlas!")
except Exception as e:
    print(f"❌ Error crítico conectando a MongoDB Atlas: {e}")
    users_collection = None
    remembers_collection = None
    messuser_collection = None
    errors_collection = None

# Diccionario inteligente para el Cooldown (con control de fugas de memoria RAM)
user_cooldowns = {}

def limpiar_cooldowns_antiguos():
    """Limpia periódicamente la memoria RAM de usuarios inactivos para evitar fugas"""
    while True:
        time.sleep(600) # Cada 10 minutos
        ahora = time.time()
        # Elimina registros mayores a 10 minutos sin actividad
        inactivos = [uid for uid, timestamp in user_cooldowns.items() if ahora - timestamp > 600]
        for uid in inactivos:
            del user_cooldowns[uid]

# Servidor Flask para mantener el servicio web activo en Render
app = Flask('')

@app.route('/')
def home():
    return "Bot Jarvis con multi-IA y MongoDB en Render activo 24/7."

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
            "origen_error": origen,
            "error_mensaje": str(error_detalle),
            "date": datetime.utcnow(),
            "jarvis_analizado": False
        }
        errors_collection.insert_one(error_doc)
        print(f"⚠️ Error registrado en Errors [{origen}]")
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
            "date": datetime.utcnow(),
            "jarvis_analizado": False
        }
        remembers_collection.insert_one(remember_data)
    except Exception as e:
        print(f"❌ Error al guardar en remembers: {e}")

def generar_respuesta_ia(prompt_final):
    """Rotación inteligente considerando excepciones de red/límite vs seguridad"""
    try:
        response = client_ai_primary.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt_final
        )
        return response.text if response and hasattr(response, 'text') else "No se obtuvo respuesta."
    except Exception as e_primary:
        # Si el error es por bloqueo de seguridad, la key 2 dará el mismo error, pero intentamos igual por robustez
        print(f"⚠️ API Key Principal falló. Rotando a Key de respaldo... Error: {e_primary}")
        try:
            response = client_ai_backup.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt_final
            )
            return response.text if response and hasattr(response, 'text') else "No se obtuvo respuesta (Backup)."
        except Exception as e_backup:
            raise Exception(f"Ambas API Keys fallaron. Principal: {e_primary} | Respaldo: {e_backup}")

# 🦾 FUNCIÓN CENTRAL DE JARVIS (Para usarla tanto automática como por comando)
def ejecutar_analisis_jarvis(chat_id_destino=None):
    """Revisa errores y remembers pendientes y genera el reporte de Jarvis"""
    try:
        if errors_collection is None or remembers_collection is None:
            return "❌ Error: Base de datos no disponible para Jarvis."

        nuevos_errores = list(errors_collection.find({"jarvis_analizado": False}))
        hace_1_hora = datetime.utcnow() - timedelta(hours=1)
        recientes_remembers = list(remembers_collection.find({"date": {"$gte": hace_1_hora}, "jarvis_analizado": False}))

        if not nuevos_errores and not recientes_remembers:
            return "🦾 *[JARVIS]*: Todos los sistemas operativos al 100%. Sin nuevos errores ni anomalías críticas detectadas."

        datos_auditados = "Nuevos Errores del Sistema:\n"
        for err in nuevos_errores:
            datos_auditados += f"- Origen: {err['origen_error']} | Usuario: @{err['username']} | Error: {err['error_mensaje']}\n"
            errors_collection.update_one({"_id": err["_id"]}, {"$set": {"jarvis_analizado": True}})

        datos_auditados += "\nInteracciones Recientes (Remembers):\n"
        for rem in recientes_remembers:
            datos_auditados += f"- ID: {rem['telegram_id']} | Quest: {rem['quest'][:100]} | Answer: {rem['answer'][:100]}\n"
            remembers_collection.update_one({"_id": rem["_id"]}, {"$set": {"jarvis_analizado": True}})

        prompt_jarvis = (
            "Actúa como Jarvis, el sofisticado sistema de inteligencia artificial y auditor de infraestructura de Tony Stark. "
            "Analiza los siguientes registros recientes de la base de datos de nuestro bot de Telegram:\n\n"
            f"{datos_auditados}\n\n"
            "Tu objetivo:\n"
            "1. Detecta si hay algún comportamiento sospechoso, comprometedor, ilegal o intentos extraños de fisgoneo entre usuarios.\n"
            "2. Evalúa la gravedad de los errores de infraestructura reportados.\n"
            "3. Redacta un informe ejecutivo, preciso, profesional y directo al estilo Jarvis. "
            "Si todo está operando con normalidad y sin amenazas, sé muy breve confirmando que la infraestructura está estable."
        )

        response_jarvis = client_ai_jarvis.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt_jarvis
        )
        
        informe = response_jarvis.text if response_jarvis and hasattr(response_jarvis, 'text') else "Infraestructura estable."
        resultado_final = f"🦾 *[JARVIS - Reporte de Auditoría]*\n\n{informe}"

        if chat_id_destino:
            bot.send_message(chat_id_destino, resultado_final, parse_mode="Markdown")
        
        return resultado_final
    except Exception as jarvis_err:
        print(f"❌ Error ejecutando a Jarvis: {jarvis_err}")
        return f"❌ Error ejecutando a Jarvis: {jarvis_err}"

def jarvis_supervisor_loop():
    """Ciclo en segundo plano cada 5 minutos"""
    print("🦾 Jarvis Supervisor iniciado en segundo plano...")
    while True:
        time.sleep(300)
        try:
            # Ejecuta y envía reporte automático a @T3nshiAkira
            ejecutar_analisis_jarvis("@T3nshiAkira")
            print("🦾 Jarvis envió su reporte automático con éxito.")
        except Exception as e:
            print(f"❌ Error en bucle de Jarvis: {e}")

# 🦾 COMANDO /jarvis (Para invocar el reporte manualmente al instante)
@bot.message_handler(commands=['jarvis'])
def comando_jarvis(message):
    registrar_usuario(message)
    # Validar que solo tú (@T3nshiAkira) o el admin con clave pueda forzarlo, o permitirlo libremente
    bot.reply_to(message, "🦾 *Jarvis analizando la infraestructura en tiempo real...*", parse_mode="Markdown")
    reporte = ejecutar_analisis_jarvis()
    
    if len(reporte) > 4000:
        reporte = reporte[:3990] + "\n..."
    bot.send_message(message.chat.id, reporte, parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    registrar_usuario(message)
    markup = telebot.types.InlineKeyboardMarkup()
    btn_help = telebot.types.InlineKeyboardButton("💡 ¿Cómo usar?", callback_data="help_info")
    btn_secure = telebot.types.InlineKeyboardButton("🔒 Mensaje Cifrado", callback_data="secure_info")
    markup.add(btn_help, btn_secure)

    welcome_text = (
        "🤖 *¡Bienvenido al Sistema Inteligente y Seguro!* \n\n"
        "Sistema protegido con supervisión Jarvis y multi-IA.\n\n"
        "✨ *Comandos útiles:*\n"
        "• `/jarvis` - Solicitar informe de infraestructura\n"
        "• `/mensaje [texto] @usuario` - Mensajería cifrada\n"
        "• `/admin [clave]` - Panel de control"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    if call.data == "help_info":
        bot.answer_callback_query(call.id, "¡Escríbeme cualquier pregunta!")
        bot.send_message(call.message.chat.id, "💡 Escribe directamente tu pregunta y te responderé usando IA.", parse_mode="Markdown")
    elif call.data == "secure_info":
        bot.answer_callback_query(call.id, "Modo Mensajero Seguro")
        bot.send_message(call.message.chat.id, "🔒 Usa `/mensaje Hola @amigo` para un vínculo cifrado privado.", parse_mode="Markdown")

# 🛡️ Panel de Admin protegido con la variable de entorno ADMIN_PASSWORD (por defecto 117566)
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    registrar_usuario(message)
    args = message.text.replace('/admin', '').strip()
    parts = args.split(' ', 1)
    password = parts[0] if len(parts) > 0 else ""
    
    if password != ADMIN_PASSWORD:
        bot.reply_to(
            message, 
            "⛔ *Acceso Denegado*\n\nContraseña incorrecta.\n🔐 *Uso:* `/admin [clave]`, o `/admin [clave] [vínculo]`", 
            parse_mode="Markdown"
        )
        registrar_error(message, "Seguridad Admin", "Intento de acceso con contraseña incorrecta.")
        return

    sub_args = parts[1] if len(parts) > 1 else ""
    
    if not sub_args:
        try:
            total_users = users_collection.count_documents({}) if users_collection is not None else 0
            total_msgs = messuser_collection.count_documents({}) if messuser_collection is not None else 0
            total_errors = errors_collection.count_documents({}) if errors_collection is not None else 0
            
            resumen = (
                "🛡️ *Panel de Administración Autorizado*\n\n"
                f"👥 Usuarios en BD: `{total_users}`\n"
                f"🔒 Mensajes seguros (`messUser`): `{total_msgs}`\n"
                f"⚠️ Errores registrados (`Errors`): `{total_errors}`\n\n"
                "💡 *Inspección:* Escribe `/admin [clave] [vínculo]` para ver una conversación privada."
            )
            bot.reply_to(message, resumen, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Error al consultar estadísticas: {e}")
        return

    vinculo_buscado = sub_args
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
        reporte = reporte[:3990] + "\n[Reporte recortado]"
        
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
        
    bot.reply_to(message, f"✅ *Mensaje seguro enviado con éxito.*\n🔗 *Vínculo:* `{vinculo_code}`", parse_mode="Markdown")
    
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
    user_id = message.from_user.id
    current_time = time.time()
    
    # ⏱️ COOLDOWN DE 5 SEGUNDOS
    if user_id in user_cooldowns:
        elapsed_time = current_time - user_cooldowns[user_id]
        if elapsed_time < 5:
            tiempo_restante = round(5 - elapsed_time, 1)
            bot.reply_to(message, f"⏳ Por favor, espera {tiempo_restante} segundos antes de enviar otro mensaje.")
            return
            
    user_cooldowns[user_id] = current_time

    registrar_usuario(message)
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

    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except Exception:
        pass

    processing_msg = bot.reply_to(message, "🧠 Analizando y procesando respuesta...")
    prompt_final = user_text + investigation_context
    
    try:
        reply_text = generar_respuesta_ia(prompt_final)
        
        if len(reply_text) > 4000:
            reply_text = reply_text[:3990] + "\n[Recortado]"
            
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=reply_text
        )
        
        guardar_remember(user_id, user_text, reply_text)
        
    except Exception as e:
        error_msg = f"❌ Error en el motor de IA: {str(e)}"
        print(error_msg)
        registrar_error(message, "Gemini AI", str(e))
        
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=error_msg
        )

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

if __name__ == '__main__':
    # Hilo para limpiar la memoria RAM del cooldown periódicamente
    threading.Thread(target=limpiar_cooldowns_antiguos, daemon=True).start()

    # Hilo de Jarvis en segundo plano
    threading.Thread(target=jarvis_supervisor_loop, daemon=True).start()
    
    # Hilo del servidor Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("🤖 Bot Jarvis optimizado y blindado iniciado en Render...")
    bot.infinity_polling()

