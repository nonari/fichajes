# Hoja de requisitos funcionales

## 1. Propósito de la aplicación
La aplicación automatiza el registro de fichajes de entrada y salida en la plataforma corporativa de la Universidade de Santiago de Compostela (USC) a través de un bot de Telegram. Facilita recordar al usuario que registre su jornada laboral, permite ejecutar fichajes manuales y programar acciones automáticas para entradas y salidas.

## 2. Alcance
El sistema cubre las interacciones entre un único usuario autorizado y el bot de Telegram configurado. Incluye la consulta y el alta de marcajes en la web de fichaje, la programación de acciones diferidas y el envío de recordatorios. Quedan fuera del alcance la gestión multiusuario, la administración de credenciales y cualquier modificación de datos ajenos al fichaje diario.

## 3. Actores
- **Usuario final**: Persona que interactúa con el bot desde el chat configurado para registrar sus fichajes.
- **Bot de Telegram**: Interfaz conversacional que procesa comandos y envía notificaciones.
- **Sistema de fichaje USC**: Portal web externo contra el que se ejecutan las acciones de entrada y salida.

## 4. Supuestos y dependencias
- El fichero `config.json` contiene token del bot, identificador de chat y credenciales válidas del portal de fichaje.
- El bot dispone de acceso a Internet y puede iniciar una sesión de navegador sin interfaz (Selenium + ChromeDriver).
- La zona horaria de referencia es `Europe/Madrid`.
- Las festividades de Galicia determinan cuándo omitir recordatorios automáticos.

## 5. Requisitos funcionales

### RF-1. Inicio y ayuda
1. Al recibir el comando `/start`, el bot responde con un mensaje de bienvenida que indica la hora diaria de consulta y los comandos disponibles.
2. El mensaje debe estar en español y formateado con emojis informativos.

### RF-2. Pregunta diaria de fichaje
1. El bot programa una tarea diaria de lunes a viernes a la hora configurada (`daily_question_time`).
2. En el momento configurado, si no hay marcajes pendientes, el bot envía al chat configurado la pregunta «¿Quieres fichar hoy?» con un teclado rápido de respuestas `Sí/No`.
3. La pregunta no debe enviarse en fines de semana ni en días festivos autonómicos de Galicia.
4. Si el bot se inicia después de la hora configurada y no hay marcajes pendientes, debe lanzar la pregunta inmediatamente.

### RF-3. Gestión de recordatorios
1. Tras enviar la pregunta diaria, el bot activa un recordatorio recurrente con la frecuencia configurada (`reminder_interval`).
2. Cada recordatorio repite la pregunta inicial mientras no se reciba una respuesta válida.
3. Al superar el número máximo de intentos (`max_reminders`), el bot deja de enviar recordatorios y cierra la espera de respuesta.
4. Cada vez que el usuario responde o se programa un marcaje manual, se cancelan los recordatorios activos asociados a la pregunta del día.

### RF-4. Respuesta afirmativa a la pregunta diaria
1. Si el usuario responde `Sí` mientras la aplicación espera respuesta, el bot intenta registrar un fichaje de entrada inmediato en el portal de la USC.
2. Antes de ejecutar la acción comprueba que no existan marcajes pendientes en el scheduler. En caso afirmativo informa al usuario y finaliza el flujo.
3. Tras ejecutar el fichaje, el bot informa del resultado (éxito, alerta o error) utilizando el mensaje devuelto por el portal.
4. Si la entrada se registra correctamente y la configuración define un retardo automático (`auto_checkout_delay`), el bot programa una salida automática para la hora calculada y notifica al usuario.
5. Si la entrada falla, el bot comunica que no se programará la salida automática.

### RF-5. Respuesta negativa a la pregunta diaria
1. Si el usuario responde `No` mientras la aplicación espera respuesta, el bot confirma que no se realizará fichaje ese día.
2. El bot registra internamente la fecha de la pregunta y desactiva recordatorios pendientes.

### RF-6. Gestión de respuestas no válidas
1. Si el usuario envía cualquier texto distinto de `Sí`, `Si` o `No` mientras se espera una respuesta, el bot solicita que se responda con una opción válida.
2. Mensajes que comiencen por `/marcar` o `/cancelar` se gestionan por sus comandos específicos y no cuentan como respuesta a la pregunta diaria.

### RF-7. Fichaje manual
1. El comando `/marcar entrada|salida [HH:MM]` permite ejecutar un fichaje inmediato o programar uno futuro.
2. Si se indica una hora (`HH:MM`), el bot valida que el formato sea correcto y que corresponda a un momento futuro del mismo día.
3. Los marcajes programados se almacenan en un scheduler persistente y se informan al usuario con la hora local.
4. Un fichaje inmediato ejecuta la acción contra el portal y devuelve el mensaje del resultado.
5. Tras una entrada exitosa, se aplica la misma lógica de programación de salida automática que en la respuesta afirmativa.
6. Tras una salida exitosa, se eliminan otras salidas programadas y se notifica cuántas se cancelaron.
7. Si la validación falla o la acción no es reconocida, el bot envía un mensaje aclaratorio con el formato de uso esperado.

### RF-8. Consulta de marcajes programados
1. El comando `/pendientes` lista todos los fichajes programados ordenados por fecha y hora.
2. El mensaje incluye la acción (Entrada/Salida) y la hora local en formato `dd/mm HH:MM`.
3. Si no existen marcajes programados, el bot informa de ello explícitamente.

### RF-9. Cancelación de marcajes programados
1. El comando `/cancelar` elimina todos los marcajes programados.
2. El bot confirma la cancelación o informa si no había marcajes pendientes.

### RF-10. Consulta de marcajes realizados
1. El comando `/marcajes` consulta la web de la USC y devuelve la lista de registros de entrada y salida del día.
2. Antes de responder, el bot muestra un mensaje de espera indicando que está consultando la información.
3. Si ocurre un error durante la consulta, el bot indica la causa recibida.
4. Si no existen marcajes, se devuelve un mensaje informativo específico.

### RF-11. Ejecución de marcajes programados
1. Cada marcaje programado debe ejecutarse a la hora indicada a través del scheduler interno del bot.
2. Al ejecutarse, el bot informa de la acción realizada y del resultado devuelto por el portal de fichaje.
3. Si se ejecuta una entrada programada con éxito y existe `auto_checkout_delay`, se programa automáticamente una salida y se notifica.
4. Los marcajes ejecutados se eliminan del listado de pendientes y se persisten los cambios.

### RF-12. Persistencia y restauración del scheduler
1. Todos los marcajes programados se guardan en el fichero `.schedule.data` en formato JSON ordenado.
2. Al iniciar el bot, se restauran del disco aquellos marcajes con fecha futura válida y se reprograman.
3. Los marcajes caducados o inválidos se descartan y se registra en los logs la causa.
4. Tras la restauración, el bot notifica al usuario los marcajes reactivados.

## 6. Requisitos no funcionales clave
- El bot debe utilizar la zona horaria de Madrid para todas las operaciones de fecha y hora.
- Debe registrar en logs informaciones y errores relevantes durante el proceso de fichaje y de scheduler.
- Todas las notificaciones al usuario deben estar redactadas en español y pueden incluir emojis descriptivos.
- El sistema debe operar en modo sin interfaz gráfica mediante Selenium en modo `headless`.

## 7. Manejo de errores y mensajes
- Si el portal de la USC devuelve una situación no prevista, el bot responde con un mensaje de error genérico acompañado del texto devuelto por la excepción.
- Cuando un fichaje solicitado no se realiza (por ejemplo, porque ya existía un registro previo), el bot debe devolver un mensaje de advertencia indicando que no se confirmó el cambio.
- Cualquier problema al leer la configuración o al crear un navegador debe registrarse en los logs para diagnóstico.

## 8. Configuración
- `telegram_token`: Token de acceso al bot.
- `telegram_chat_id`: Identificador del chat autorizado.
- `usc_user` y `usc_pass`: Credenciales del portal de fichajes.
- `daily_question_time`: Hora diaria para preguntar por el fichaje (formato `HH:MM`).
- `auto_checkout_delay_minutes`: Minutos tras una entrada exitosa para programar la salida automática; `0` desactiva la función.
- `max_reminders`: Número máximo de recordatorios tras la pregunta diaria.
- `reminder_interval_minutes`: Intervalo entre recordatorios sucesivos.
