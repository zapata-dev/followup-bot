# Manual Operativo: Sistema de Mensajes del FollowUp Bot

**Para:** Equipo de Mensajes / Coordinadores de Campaña
**Fecha:** Marzo 2026
**Versión:** 2.0

---

## Contexto

WhatsApp es un canal sensible a mensajería repetitiva, de bajo contexto o poco esperada por el cliente. El bot utiliza **personalización básica y variantes controladas de redacción** para que cada primer contacto sea más relevante, más natural y más alineado con el historial comercial del cliente.

La variación de texto es solo uno de los factores. La estabilidad del canal también depende del consentimiento del contacto, el volumen de envío, la cadencia y la calidad de la segmentación.

---

## Tabla de Contenidos

1. [Cómo Decide el Bot Qué Mensaje Enviar](#1-cómo-decide-el-bot-qué-mensaje-enviar)
2. [Nivel 1 — Template Individual (Monday)](#2-nivel-1--template-individual-monday)
3. [Nivel 2 — Mensaje Automático por Tipo de Campaña](#3-nivel-2--mensaje-automático-por-tipo-de-campaña)
4. [Nivel 3 — Template por Defecto del Sistema](#4-nivel-3--template-por-defecto-del-sistema)
5. [La Regla de Presentación del Bot](#5-la-regla-de-presentación-del-bot)
6. [Lo que el Bot No Hace](#6-lo-que-el-bot-no-hace)
7. [Límites Operativos del Sistema](#7-límites-operativos-del-sistema)
8. [Diferencia entre Spintax y Variación Automática](#8-diferencia-entre-spintax-y-variación-automática)
9. [Recomendaciones Operativas](#9-recomendaciones-operativas)
10. [Checklist Antes de Activar una Campaña](#10-checklist-antes-de-activar-una-campaña)

---

## 1. Cómo Decide el Bot Qué Mensaje Enviar

El bot evalúa tres niveles en orden. En cuanto encuentra uno válido, lo usa:

```
1. ¿El contacto tiene un template propio en Monday?        → Lo usa
         ↓ No
2. ¿El nombre del grupo coincide con un tipo de campaña?   → Genera mensaje por campaña
         ↓ No
3. Usa el template por defecto del sistema
```

> **Regla clave:** el template individual del contacto siempre tiene prioridad total. Si existe, los niveles 2 y 3 se ignoran por completo.

---

## 2. Nivel 1 — Template Individual (Monday)

El campo **Template** en la ficha del contacto en Monday acepta texto libre con **variables** y **spintax**.

### Variables disponibles

| Variable | Qué inserta | Fallback si está vacío |
|---|---|---|
| `{nombre}` | Nombre del contacto | `cliente` |
| `{vehiculo}` | Vehículo de interés | `tu unidad de interés` |
| `{bot_name}` | Estefania Fernandez | — |
| `{company_name}` | Go-On Zapata | — |
| `{company_url}` | go-on.mx | — |
| `{notas}` | Campo de notas de Monday | *(vacío)* |
| `{resumen}` | Resumen de conversación previa | *(vacío)* |

> **Nota sobre `{vehiculo}`:** Si este campo no está cargado en Monday, el bot lo reemplaza automáticamente por `"tu unidad de interés"`. El mensaje no truena, pero pierde precisión. Completar este campo es una de las acciones con mayor impacto en la calidad percibida del mensaje.

> **Nota sobre `{nombre}`:** Si el nombre del contacto está vacío en Monday, el bot usa `"cliente"` como comodín. El mensaje queda funcional, pero impersonal.

---

### Spintax — Variantes Manuales

El spintax permite definir opciones de redacción dentro del mismo template. El bot elige una opción al azar **por cada envío**, generando mensajes distintos para cada contacto.

**Sintaxis:**

```
[opción1|opción2|opción3]
```

- Mínimo **2 opciones** separadas por `|`
- Se pueden incluir variables dentro de cada opción: `[soy {bot_name}|me llamo {bot_name}]`
- Se pueden colocar en cualquier parte del mensaje

**Ejemplo escrito:**

```
[Hola|Buenas|Qué tal] {nombre}, vi que estuviste viendo el {vehiculo}.
¿[Sigues interesado|Todavía lo evalúas|Ya resolviste algo]?
```

**Mensajes que llegarían a distintos contactos:**

```
Hola Carlos,   vi que estuviste viendo el Freightliner. ¿Sigues interesado?
Buenas María,  vi que estuviste viendo el Kenworth.     ¿Ya resolviste algo?
Qué tal Pedro, vi que estuviste viendo el Volvo.        ¿Todavía lo evalúas?
```

> **Advertencia sobre corchetes:** El bot intenta procesar todo lo que esté entre `[ ]`. Si escribes un bloque spintax con error (por ejemplo, sin cerrar el corchete o sin el símbolo `|`), el mensaje puede llegar con el código crudo al cliente. Siempre verifica que cada `[` tenga su `]` correspondiente y al menos una `|` adentro.

---

## 3. Nivel 2 — Mensaje Automático por Tipo de Campaña

Cuando no hay template individual, el bot arma el mensaje combinando tres piezas elegidas al azar:

```
[SALUDO]  +  [INTRO]  +  [CUERPO según campaña]
```

### Detección del tipo de campaña

El tipo de campaña se detecta automáticamente por **palabras clave en el nombre del grupo de Monday**:

| Palabras en el nombre del grupo | Tipo de campaña |
|---|---|
| interesado, perdido, sin interés, fin negativo, recuperación | `lost_lead` |
| asignado, cotización, cotizacion | `assigned_lead` |
| cita atendida, encuesta, métricas | `attended_appointment` |
| servicio, atención, calidad, seguimiento vendedor | `customer_service` |

---

### Piezas del Mensaje

#### Saludos — se elige 1 al azar

```
Hola {nombre}
Hola {nombre}!
Hola {nombre}, ¿cómo estás?
Buenas {nombre}
Qué tal {nombre}
Hola {nombre}, buen día
```

#### Intros — se elige 1 al azar

```
te escribo de {company_name}.
te contacto desde {company_name}.
soy {bot_name} de {company_name}.
te habla {bot_name} de {company_name}.
me comunico de {company_name}.
```

#### Cuerpo según tipo de campaña — se elige 1 al azar

**`lost_lead`** — Lead perdido / sin respuesta

```
Hace un tiempo nos preguntaste por el {vehiculo}, ¿todavía lo estás considerando o ya tomaste una decisión?
Anteriormente mostraste interés en el {vehiculo}, ¿sigues evaluando esa opción o ya resolviste tu compra?
Vi que en su momento preguntaste por el {vehiculo}. ¿Sigues buscando o ya lo resolviste?
Hace rato tenías interés en el {vehiculo}. ¿Qué tal va esa búsqueda, ya encontraste algo?
Recuerdo que preguntaste por el {vehiculo}. ¿Aún está en tus planes o ya cerraste algo?
```

**`assigned_lead`** — Lead asignado / cotización

```
¿Te pudieron resolver tu consulta sobre el {vehiculo}?
¿Quedaste bien atendido con lo del {vehiculo}?
¿Cómo te fue con la consulta del {vehiculo}?
¿Te dieron respuesta sobre el {vehiculo}?
¿Te atendieron bien con lo del {vehiculo}?
```

**`attended_appointment`** — Cita atendida

```
¿Qué tal te pareció el {vehiculo} cuando viniste a verlo?
¿Cómo te fue con la visita para ver el {vehiculo}?
¿Qué impresión te llevaste del {vehiculo}?
¿Te convenció el {vehiculo} cuando lo viste?
Después de ver el {vehiculo}, ¿qué te pareció?
```

**`customer_service`** — Servicio / postventa

```
¿Cómo te han atendido con esa unidad?
¿Cómo va todo con el {vehiculo}?
¿Te han dado buen servicio con el {vehiculo}?
¿Qué tal la atención que has recibido?
¿Cómo ha sido tu experiencia con el {vehiculo}?
```

---

### Ejemplo de Resultado Completo

**Campaña:** `lost_lead` | **Contacto:** Carlos | **Vehículo:** Freightliner

```
Qué tal Carlos, me comunico de Go-On Zapata.
Recuerdo que preguntaste por el Freightliner. ¿Aún está en tus planes o ya cerraste algo?
```

---

## 4. Nivel 3 — Template por Defecto del Sistema

Si el contacto no tiene template propio **y** el nombre del grupo no coincide con ninguna campaña conocida, el bot usa este mensaje:

```
Hola {nombre}, te saluda {bot_name} de {company_name}. {mensaje}
```

Con los valores actuales configurados en el servidor:

```
Hola [nombre del contacto], te saluda Estefania Fernandez de Go-On Zapata.
```

> Este nivel es el menos personalizado y el que menos contexto aporta al cliente. Si una campaña cae aquí de forma recurrente, probablemente el **nombre del grupo en Monday necesita revisión** para que coincida con alguna de las palabras clave del Nivel 2.

---

## 5. La Regla de Presentación del Bot

> Esta regla aplica **únicamente a los templates del Nivel 1**.

Si el template no menciona el nombre del bot ni incluye frases como *"soy / te saluda / te escribe / me llamo / le habla"*, el sistema **inyecta automáticamente la presentación** al inicio del mensaje antes de enviarlo.

**Template escrito en Monday:**

```
¿Sigues evaluando el {vehiculo}?
```

**Lo que recibe el cliente:**

```
Hola, soy Estefania Fernandez de Go-On Zapata.
¿Sigues evaluando el Freightliner?
```

Si el template empieza con un saludo, la presentación se inserta **después del saludo**, no al principio:

| Template escrito | Mensaje final |
|---|---|
| `"Hola {nombre}, ¿cómo estás?"` | `"Hola Carlos, soy Estefania Fernandez de Go-On Zapata. ¿cómo estás?"` |
| `"¿Sigues evaluando el {vehiculo}?"` | `"Hola, soy Estefania Fernandez de Go-On Zapata. ¿Sigues evaluando el Freightliner?"` |

> Los mensajes del Nivel 2 siempre incluyen la intro en la pieza de "Intros" — esta regla no aplica para ellos.

---

## 6. Lo que el Bot No Hace

Es importante tener claras estas limitaciones para no generar falsas expectativas:

- **No usa IA para redactar el primer mensaje** — la IA entra únicamente cuando el cliente *responde*
- **No adapta el primer mensaje al historial conversacional** — usa solo los datos disponibles en Monday
- **No prueba múltiples variantes en una misma campaña** — cada contacto recibe un único envío
- **No reintenta con diferente redacción** si el cliente no contesta — el seguimiento es responsabilidad del equipo
- **No envía fuera del horario establecido:** lunes a viernes 9:00–18:00 h, sábados 9:00–14:00 h, domingos no envía
- **No corre dos campañas en paralelo** — si una campaña está activa, la siguiente espera a que termine

---

## 7. Límites Operativos del Sistema

Estos parámetros están configurados en el servidor y son los valores activos por defecto:

| Parámetro | Valor |
|---|---|
| Delay entre mensajes | 20–60 segundos (aleatorio) |
| Tamaño de lote | 10 mensajes |
| Pausa entre lotes | 3–6 minutos |
| Máximo por hora | 25 mensajes |
| Máximo por día | 120 mensajes |
| Errores consecutivos para detener campaña | 3 |

La salud del canal depende de estos límites. No deben modificarse sin evaluar el impacto en el número de WhatsApp.

---

## 8. Diferencia entre Spintax y Variación Automática

Son dos mecanismos distintos con propósitos diferentes:

| | Spintax (Nivel 1) | Variación automática (Nivel 2) |
|---|---|---|
| **Quién lo escribe** | El equipo, en Monday | El sistema (código fijo) |
| **Dónde se configura** | Campo Template del contacto | Nombre del grupo en Monday |
| **Control sobre el mensaje** | Total — se escriben exactamente las opciones | Parcial — se elige el tipo de campaña |
| **Flexibilidad** | Alta | Media |
| **Cuándo usarlo** | Campañas con mensaje específico o contexto especial | Campañas estándar sin redacción personalizada |

---

## 9. Recomendaciones Operativas

1. **Completar siempre el campo vehículo en Monday** — si está vacío el mensaje pierde contexto real
2. **Usar mínimo 3 variantes por bloque spintax** — con solo 2 opciones la variación es demasiado predecible
3. **Terminar siempre con una pregunta** — las preguntas abiertas generan más respuestas que las cerradas
4. **Evitar lenguaje excesivamente promocional** repetido en todas las variantes (precio, oferta, descuento)
5. **No usar el Nivel 3 como destino normal** — si una campaña cae aquí, revisar el nombre del grupo en Monday
6. **Verificar los corchetes al escribir spintax** — un bloque mal cerrado llega como código crudo al cliente
7. **El template individual tiene prioridad total** — si hay algo en el campo Template de Monday, el Nivel 2 se ignora por completo
8. **Priorizar mensajes con contexto real del cliente** — un mensaje con nombre y vehículo correcto siempre supera a cualquier variante genérica

---

## 10. Checklist Antes de Activar una Campaña

Antes de lanzar cualquier campaña, verificar punto por punto:

- [ ] El campo **vehículo** está cargado en los contactos relevantes
- [ ] El **nombre del grupo** en Monday tiene las palabras clave correctas para detectar el tipo de campaña (o hay template individual)
- [ ] Si hay template individual, el **spintax está bien formado**: corchetes cerrados y al menos 2 opciones por bloque separadas con `|`
- [ ] El mensaje **cierra con una pregunta**
- [ ] **No hay otra campaña corriendo** en paralelo en ese momento
- [ ] Los contactos de la lista tienen **expectativa razonable de seguimiento** (no son bases frías sin contexto)
- [ ] Los campos **nombre** y **notas** están completos para los contactos donde aplique

---

*Para dudas técnicas sobre configuración del servidor, variables de entorno o ajuste de límites operativos, contactar al administrador del sistema.*
