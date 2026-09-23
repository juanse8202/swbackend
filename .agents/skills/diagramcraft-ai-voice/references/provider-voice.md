# Gemini, cuotas y voz

## Configuración

Usar modelo preentrenado: instrucciones de sistema, ejemplos y contexto no son entrenamiento. Empezar por integración API y evaluar comandos representativos antes de considerar fine-tuning.

Consultar documentación oficial vigente al implementar; verificar SDK, modelo disponible en el proyecto y soporte de salida estructurada. Usar SDK Python `google-genai` comprobado y fijar una versión compatible en requirements. No copiar nombres de modelos de ejemplos antiguos sin comprobar disponibilidad.

Variables propuestas solo en backend: GEMINI_API_KEY, GEMINI_MODEL, AI_REQUEST_TIMEOUT_SECONDS, AI_MAX_OPERATIONS y límites de texto/contexto. Añadir placeholders a .env.example. No crear VITE_GEMINI_API_KEY ni exponer claves en frontend, logs, URL, errores o Git.

Configurar timeouts y reintentos acotados con backoff para errores transitorios; no reintentar indefinidamente 429 ni repetir mutaciones. Separar timeout, credenciales inválidas, modelo no disponible, cupo, rechazo de contenido y fallo de validación. Registrar request_id, latencia y resultado sin transcripciones completas ni secretos por defecto.

No asumir que Google AI Pro estudiantil concede llamadas API gratis. Los beneficios de suscripción en la interfaz AI Studio y la facturación/cuotas de la API externa se gestionan por separado. Verificar beneficios y créditos concretos de la cuenta sin prometer elegibilidad. Existe Free Tier para ciertos modelos y límites; no activar facturación automáticamente ni fijar cuotas universales en código.

Enviar solo datos necesarios del diagrama. Revisar términos de tratamiento de datos del tier elegido antes de transmitir información confidencial; explicar al usuario dónde se procesa el texto/audio.

## Voz para MVP

Separar transcripción e interpretación: navegador convierte voz a texto; Django envía texto y contexto a Gemini. Esta arquitectura no requiere Live API ni enviar audio a Gemini.

Detectar `window.SpeechRecognition || window.webkitSpeechRecognition`, usar español configurable y resultado final; probar soporte en navegador real. Mantener textarea/input como alternativa. Manejar permisos denegados, no-speech, falta de micrófono, errores de red, abort y end. No reiniciar escucha infinitamente. Preferir HTTPS o localhost para desarrollo.

No presentar Web Speech como necesariamente local: algunos navegadores usan servicio remoto. Si se solicita transcripción con audio en servidor o Live API, evaluar aparte formatos, tamaños, duración, disponibilidad, costos y credenciales. Nunca poner una clave duradera en navegador; usar backend o credenciales efímeras según interfaz oficialmente soportada.

## Fuentes oficiales

Verificadas el 23-09-2026; volver a consultar para decisiones dependientes de versión o plan:
- https://ai.google.dev/gemini-api/docs/google-ai-plans : beneficios de planes en AI Studio frente a API externa.
- https://ai.google.dev/gemini-api/docs/billing : tiers y gestión de facturación.
- https://ai.google.dev/gemini-api/docs/pricing : disponibilidad gratuita y costos por modelo.
- https://ai.google.dev/gemini-api/docs/get-started : SDK y autenticación.
- https://ai.google.dev/gemini-api/docs/structured-output : esquema de respuesta y limitaciones.
- https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition : soporte y comportamiento de reconocimiento de voz.
