# Requirements Document

## Introduction

mhub es un bot de Discord modular que unifica dos bots previos (Mplux y mprof) como módulos enchufables sobre un núcleo compartido. Actualmente expone dos módulos: "signups" (inscripción a eventos con roles y cupos mediante slash commands y botones) y "registry" (directorio genérico por categorías). El estado se persiste en bases de datos SQLite bajo el volumen `/data` de Fly.io, y cada módulo se integra mediante el contrato `setup(bot)`/`restore(bot)` sin modificar el núcleo.

Esta feature, **mhub-platform**, convierte mhub en una plataforma con dos caras —Discord y Web— sobre una única fuente de verdad. El objetivo es: (1) consolidar mhub como bot único y retirar los bots antiguos; (2) permitir crear eventos con composición numérica ad-hoc sin editar archivos; (3) soportar configuración por servidor (multi-comunidad) en base de datos; (4) añadir un módulo puente que conecta skills/personas del directorio con grupos por tarea; (5) servir una API HTTP dentro del proceso de mhub que comparte los mismos managers y stores que el bot, consumida por un frontend web separado con login Discord OAuth2, paridad de acciones respecto a Discord, sincronización web→Discord y actualizaciones en tiempo real; y (6) introducir un ciclo de vida de eventos con fecha/hora y estados (activo, completado, archivado).

El caso de uso guía es una empresa de desarrollo que forma equipos para tareas basándose en las skills registradas de cada persona: define un grupo con la composición que requiere la tarea, recibe sugerencias de personas cuya skill encaja con cada rol y gestiona la inscripción tanto desde Discord como desde la web.

## Glossary

- **Plataforma**: El sistema mhub en su conjunto, incluyendo el bot de Discord y la API web sobre una única fuente de verdad.
- **HubBot**: El proceso del bot de Discord que carga los módulos vía el contrato `setup(bot)`/`restore(bot)` y sincroniza los slash commands.
- **Modulo**: Paquete enchufable dentro de `modules/` que expone `async def setup(bot)` y opcionalmente `async def restore(bot)`.
- **Modulo_Signups**: Módulo de inscripción a eventos con roles y cupos.
- **Modulo_Registry**: Módulo de directorio genérico de entradas por categorías (skills/personas).
- **Modulo_Puente**: Módulo que integra Modulo_Registry y Modulo_Signups para sugerir personas según skills y para localizar grupos que encajan con una skill.
- **API_Web**: Servidor HTTP (FastAPI) ejecutado en un hilo dentro del proceso de HubBot, que comparte los mismos managers y stores que el bot.
- **EventManager**: Componente de lógica de negocio de eventos, agnóstico de Discord.
- **EventStore**: Componente de persistencia de eventos en SQLite.
- **RegistryStore**: Componente de persistencia del directorio de entradas en SQLite.
- **Evento**: Grupo de inscripción con un título, una composición de roles y un estado de ocupación. Equivale a "grupo".
- **Composicion**: Conjunto de roles de un evento, donde cada rol tiene una clave, un emoji opcional y un número de plazas.
- **Plantilla**: Composición predefinida y reutilizable, identificada por una clave (p. ej. "raid", "mitica").
- **Composicion_Ad_Hoc**: Composición definida al vuelo durante la creación de un evento, sin referencia a una Plantilla y sin editar archivos de configuración.
- **Estado_Ocupacion**: Estructura que asocia cada rol del evento con la lista de personas inscritas en ese rol.
- **Ciclo_De_Vida**: Conjunto de estados de un Evento: "activo", "completado" y "archivado".
- **Servidor**: Servidor de Discord (guild), identificado por su `guild_id`.
- **Configuracion_Por_Servidor**: Plantillas y categorías propias de un Servidor concreto, almacenadas en base de datos.
- **Skill**: Capacidad o especialidad asociada a una persona en Modulo_Registry (mapea a la "categoría"/"nombre" de una entrada del directorio).
- **Persona**: Entrada del directorio asociada a un `user_id` de Discord, con su nombre, enlace, proveedor y skill.
- **Identidad**: Usuario único identificado por su `user_id` de Discord, compartido entre Discord y la web.
- **Discord_OAuth2**: Mecanismo de autenticación de la web mediante el inicio de sesión con la cuenta de Discord del usuario.
- **Sesion_Web**: Sesión autenticada de un usuario en la API_Web tras completar Discord_OAuth2.
- **Canal_Tiempo_Real**: Canal de actualizaciones en vivo (SSE o WebSocket) que la API_Web usa para notificar cambios de estado al frontend.
- **Frontend_Web**: Aplicación web estática, generada y desplegada aparte (Vercel/Netlify/Cloudflare Pages), que consume la API_Web.
- **Lectura_Publica**: Acceso de solo lectura a la API_Web que no requiere autenticación.
- **Escritura_Autenticada**: Operación de modificación de estado en la API_Web que requiere una Sesion_Web válida.
- **Dato_Sensible**: Información que no debe exponerse en respuestas de Lectura_Publica, incluyendo `user_id` y tokens de sesión.

## Requirements

### Requirement 1: Bot único consolidado

**User Story:** Como administrador de la comunidad, quiero que mhub sea el único bot activo y que los bots antiguos queden retirados, para evitar comandos duplicados y mantener una sola fuente de verdad.

#### Acceptance Criteria

1. THE Plataforma SHALL operar como un único proceso de bot identificado por el token `DISCORD_TOKEN_HUB`.
2. THE Plataforma SHALL cargar Modulo_Signups y Modulo_Registry mediante el contrato `setup(bot)`/`restore(bot)` sin modificar el núcleo.
3. WHEN HubBot completa la carga de módulos, THE HubBot SHALL sincronizar los slash commands de todos los módulos activos.
4. IF la sincronización de slash commands falla tras cargar los módulos correctamente, THEN THE HubBot SHALL continuar operando con los módulos ya cargados y registrar el fallo de sincronización.
5. THE documentación de la Plataforma SHALL declarar los bots Mplux y mprof como retirados y describir sus funciones como módulos de mhub.

### Requirement 2: Crear eventos con composición ad-hoc

**User Story:** Como organizador, quiero crear un evento definiendo su composición numérica al vuelo (por ejemplo "2 backend, 3 frontend, 1 QA"), para formar grupos sin necesidad de editar archivos de configuración.

#### Acceptance Criteria

1. WHEN un organizador crea un Evento aportando una Composicion_Ad_Hoc con pares de rol y número de plazas, THE EventManager SHALL crear el Evento con esa Composicion.
2. WHEN un Evento se crea con una Composicion_Ad_Hoc, THE EventStore SHALL persistir la Composicion completa junto al Evento, sin depender de una referencia a una Plantilla.
3. IF una Composicion_Ad_Hoc contiene un rol cuyo número de plazas no es un entero mayor o igual a 1, THEN THE EventManager SHALL rechazar la creación del Evento y devolver un mensaje de error que identifique el rol inválido.
4. IF una Composicion_Ad_Hoc no contiene ningún rol, THEN THE EventManager SHALL rechazar la creación del Evento y devolver un mensaje de error indicando que se requiere al menos un rol.
5. IF una Composicion_Ad_Hoc contiene dos roles con la misma clave, THEN THE EventManager SHALL rechazar la creación del Evento y devolver un mensaje de error indicando la clave duplicada.
6. WHEN un organizador crea un Evento seleccionando una Plantilla existente, THE EventManager SHALL crear el Evento copiando la Composicion de la Plantilla y persistiéndola junto al Evento.

### Requirement 3: Configuración por servidor (multi-comunidad)

**User Story:** Como administrador de un servidor, quiero definir mis propias plantillas y categorías por servidor, para que cada comunidad use su propia configuración sin afectar a las demás.

#### Acceptance Criteria

1. THE Plataforma SHALL almacenar las Plantillas y las categorías de cada Servidor como Configuracion_Por_Servidor en base de datos, identificada por `guild_id`.
2. WHEN una operación de Modulo_Signups o Modulo_Registry se ejecuta en un Servidor, THE Plataforma SHALL aplicar la Configuracion_Por_Servidor correspondiente a ese `guild_id`.
3. IF un Servidor no tiene Configuracion_Por_Servidor almacenada, THEN THE Plataforma SHALL aplicar la configuración por defecto incluida en la Plataforma.
4. WHEN un administrador añade, modifica o elimina una Plantilla o categoría de su Servidor, THE Plataforma SHALL crear o actualizar la Configuracion_Por_Servidor asociada al `guild_id` de ese Servidor a partir de esa modificación, sin requerir un paso de configuración inicial previo.
5. THE Plataforma SHALL aislar la Configuracion_Por_Servidor de un Servidor de modo que un cambio en un Servidor no altere la configuración de otro Servidor.

### Requirement 4: Módulo puente skills ↔ grupos

**User Story:** Como responsable de equipo de una empresa de desarrollo, quiero que el sistema relacione las skills registradas de las personas con los roles de los grupos, para formar equipos para tareas basándome en quién tiene cada skill.

#### Acceptance Criteria

1. THE Modulo_Puente SHALL integrarse en la Plataforma como un Modulo mediante el contrato `setup(bot)`/`restore(bot)` sin modificar el núcleo.
2. WHEN un organizador crea o consulta un Evento, THE Modulo_Puente SHALL sugerir, para cada rol del Evento, las Personas de Modulo_Registry cuya Skill se corresponde con ese rol.
3. WHEN se solicita información de una Persona o Skill, THE Modulo_Puente SHALL listar los Eventos cuyos roles se corresponden con esa Skill.
4. WHERE un rol de un Evento no tiene ninguna Persona con Skill correspondiente, THE Modulo_Puente SHALL indicar que no existen sugerencias para ese rol.
5. WHEN el Modulo_Puente sugiere Personas para un rol, THE Modulo_Puente SHALL excluir a las Personas ya inscritas en ese mismo Evento.
6. THE Modulo_Puente SHALL obtener los datos de Personas y Skills desde RegistryStore y los datos de Eventos desde EventStore, sin duplicar la lógica de negocio de Modulo_Registry ni de Modulo_Signups.

### Requirement 5: API web dentro del proceso de mhub

**User Story:** Como desarrollador del frontend, quiero una API HTTP servida dentro del proceso de mhub que comparta los mismos managers y stores que el bot, para construir una web sobre la misma fuente de verdad sin duplicar lógica.

#### Acceptance Criteria

1. THE API_Web SHALL ejecutarse en un hilo dentro del proceso de HubBot.
2. THE API_Web SHALL operar sobre las mismas instancias de EventManager, EventStore y RegistryStore que utiliza HubBot.
3. THE API_Web SHALL integrarse en la Plataforma como un Modulo implementando tanto `async def setup(bot)` como `async def restore(bot)`, sin modificar el núcleo.
4. WHEN HubBot inicia, THE API_Web SHALL quedar disponible para atender peticiones HTTP sin bloquear el funcionamiento del bot de Discord.
5. THE API_Web SHALL exponer la misma información de Eventos y de directorio de Skills que está disponible en Discord, sin reimplementar la lógica de negocio.

### Requirement 6: Lectura pública desde la web

**User Story:** Como visitante, quiero ver los grupos/eventos activos y el directorio de skills sin iniciar sesión, para conocer la actividad de la comunidad antes de participar.

#### Acceptance Criteria

1. WHEN un cliente solicita la lista de Eventos activos mediante Lectura_Publica, THE API_Web SHALL devolver los Eventos cuyo Ciclo_De_Vida es "activo" junto con su título, su Composicion y el número de plazas ocupadas y libres por rol.
2. WHEN un cliente solicita el directorio de Skills mediante Lectura_Publica, THE API_Web SHALL devolver las entradas del directorio con su categoría, nombre, enlace y proveedor.
3. THE API_Web SHALL excluir el `user_id` de todas las respuestas de Lectura_Publica.
4. THE API_Web SHALL atender las peticiones de Lectura_Publica sin requerir una Sesion_Web.

### Requirement 7: Autenticación mediante Discord OAuth2

**User Story:** Como usuario, quiero iniciar sesión en la web con mi cuenta de Discord, para que mi identidad web y mi identidad de Discord sean la misma.

#### Acceptance Criteria

1. WHEN un usuario inicia el flujo de login, THE API_Web SHALL autenticar al usuario mediante Discord_OAuth2.
2. WHEN Discord_OAuth2 confirma la identidad de un usuario, THE API_Web SHALL crear una Sesion_Web asociada al `user_id` de Discord de ese usuario.
3. IF una petición de Escritura_Autenticada llega sin una Sesion_Web válida, THEN THE API_Web SHALL rechazar la petición con un código de estado de no autorizado.
4. THE API_Web SHALL determinar la Identidad de toda Escritura_Autenticada a partir de la Sesion_Web y no a partir de datos enviados en el cuerpo de la petición.

### Requirement 8: Escritura autenticada con paridad respecto a Discord

**User Story:** Como usuario autenticado, quiero apuntarme o salir de grupos y registrar o borrar entradas desde la web, para poder hacer desde la web lo mismo que puedo hacer en Discord.

#### Acceptance Criteria

1. WHEN un usuario con Sesion_Web válida se apunta a un rol de un Evento, THE API_Web SHALL aplicar la operación a través de EventManager con la misma lógica de cupos y movimiento de rol que en Discord.
2. WHEN un usuario con Sesion_Web válida sale de un Evento, THE API_Web SHALL retirar a esa Identidad del Evento a través de EventManager.
3. WHEN un usuario con Sesion_Web válida registra una entrada en el directorio, THE API_Web SHALL crear la entrada a través de RegistryStore asociándola al `user_id` de su Sesion_Web.
4. WHEN un usuario con Sesion_Web válida borra una entrada del directorio, THE API_Web SHALL eliminar la entrada a través de RegistryStore solo si el `user_id` de la Sesion_Web coincide con el propietario de la entrada.
5. IF un usuario con Sesion_Web válida intenta borrar una entrada cuyo propietario no es su Identidad, THEN THE API_Web SHALL conservar la entrada sin eliminarla y rechazar la operación con un código de estado de prohibido.
6. IF un usuario con Sesion_Web válida intenta apuntarse a un rol cuyo número de plazas ocupadas ya iguala el número de plazas, THEN THE API_Web SHALL rechazar la inscripción e indicar que el rol está completo.

### Requirement 9: Sincronización web → Discord

**User Story:** Como miembro de la comunidad, quiero que los cambios hechos desde la web se reflejen en el mensaje de Discord del grupo, para que ambas caras muestren siempre el mismo estado.

#### Acceptance Criteria

1. WHEN una Escritura_Autenticada modifica el Estado_Ocupacion de un Evento, THE API_Web SHALL solicitar a HubBot que reedite el mensaje del Evento en Discord.
2. WHEN HubBot recibe la solicitud de reedición de un Evento, THE HubBot SHALL actualizar el embed del mensaje de Discord para reflejar el Estado_Ocupacion vigente.
3. IF HubBot no puede localizar o reeditar el mensaje de Discord de un Evento, THEN THE API_Web SHALL conservar el cambio persistido y registrar el fallo de sincronización.

### Requirement 10: Actualización en tiempo real del dashboard

**User Story:** Como usuario de la web, quiero que el dashboard se actualice en tiempo real cuando alguien actúa en Discord, para ver el estado vigente sin recargar la página.

#### Acceptance Criteria

1. WHEN el Estado_Ocupacion o el Ciclo_De_Vida de un Evento cambia por una acción en Discord, THE API_Web SHALL emitir una notificación de cambio por el Canal_Tiempo_Real.
2. WHEN el Estado_Ocupacion o el Ciclo_De_Vida de un Evento cambia por una Escritura_Autenticada, THE API_Web SHALL emitir una notificación de cambio por el Canal_Tiempo_Real.
3. WHEN un cliente está suscrito al Canal_Tiempo_Real y se emite una notificación de cambio de un Evento, THE API_Web SHALL entregar a ese cliente el estado actualizado del Evento.
4. IF la entrega de una notificación a un cliente del Canal_Tiempo_Real falla por desconexión o error de red, THEN THE API_Web SHALL continuar operando con normalidad y permitir que el cliente recupere el estado vigente al reconectarse.
5. THE API_Web SHALL excluir el `user_id` de las notificaciones emitidas por el Canal_Tiempo_Real hacia clientes no autenticados.

### Requirement 11: Ciclo de vida de eventos

**User Story:** Como organizador, quiero que los eventos tengan fecha/hora y estados (activo, completado, archivado), para distinguir los grupos activos del historial y poder archivarlos.

#### Acceptance Criteria

1. WHEN un Evento se crea, THE EventManager SHALL registrar su fecha y hora y asignarle el estado de Ciclo_De_Vida "activo".
2. WHEN todos los roles de un Evento alcanzan su número de plazas, THE EventManager SHALL marcar el Evento con el estado de Ciclo_De_Vida "completado".
3. WHEN un organizador archiva un Evento, THE EventManager SHALL asignarle el estado de Ciclo_De_Vida "archivado".
4. WHEN un cliente solicita los Eventos del historial, THE Plataforma SHALL devolver los Eventos cuyo estado de Ciclo_De_Vida es "archivado" de forma separada de los Eventos "activos".
5. THE EventStore SHALL persistir la fecha, la hora y el estado de Ciclo_De_Vida de cada Evento.

### Requirement 12: Seguridad de la API

**User Story:** Como responsable de la plataforma, quiero que la API proteja las acciones de escritura y los datos sensibles, para evitar accesos indebidos y exposición de información personal.

#### Acceptance Criteria

1. THE API_Web SHALL permitir Lectura_Publica únicamente sobre datos que no contienen el `user_id`.
2. THE API_Web SHALL exigir una Sesion_Web válida para toda Escritura_Autenticada.
3. WHEN una Identidad realiza una Escritura_Autenticada, THE API_Web SHALL aplicar la operación únicamente sobre los recursos pertenecientes a esa Identidad.
4. WHEN el número de peticiones de un cliente supera el límite configurado dentro de una ventana de tiempo, THE API_Web SHALL rechazar las peticiones adicionales de ese cliente con un código de estado de demasiadas peticiones.
5. WHILE el límite de peticiones configurado es igual a cero, THE API_Web SHALL rechazar toda petición con un código de estado de demasiadas peticiones.
6. THE API_Web SHALL omitir el `user_id` y los tokens de sesión de todas las respuestas dirigidas a clientes no autenticados.

### Requirement 13: Fuente de verdad única y arquitectura modular

**User Story:** Como mantenedor, quiero que el bot y la API compartan los mismos managers y stores y que las nuevas capacidades encajen como módulos, para no duplicar lógica ni reescribir el núcleo.

#### Acceptance Criteria

1. THE Plataforma SHALL mantener una única implementación de la lógica de negocio de Eventos en EventManager, utilizada tanto por HubBot como por API_Web.
2. THE Plataforma SHALL mantener una única implementación de la persistencia compartida entre HubBot y API_Web mediante EventStore y RegistryStore.
3. THE Modulo_Puente y la API_Web SHALL registrarse en `modules/__init__.py` dentro de `ENABLED_MODULES` y exponer `async def setup(bot)` y opcionalmente `async def restore(bot)`.
4. THE Plataforma SHALL permitir añadir el Modulo_Puente y la API_Web sin modificar los archivos del núcleo en `core/`.

### Requirement 14: Persistencia y despliegue

**User Story:** Como operador, quiero que el estado se persista en SQLite bajo el volumen de Fly.io y que el diseño no impida migrar a Postgres, para mantener la operación actual sin cerrar la puerta a una base de datos futura.

#### Acceptance Criteria

1. THE Plataforma SHALL persistir el estado de Eventos, directorio y Configuracion_Por_Servidor en SQLite bajo el directorio de datos configurado (`/data` en Fly.io).
2. THE Plataforma SHALL acceder a la persistencia únicamente a través de los stores (EventStore, RegistryStore), sin acceso directo a la base de datos que los eluda, de modo que el motor de base de datos pueda sustituirse por Postgres sin cambiar la lógica de negocio.
3. WHEN la Plataforma se despliega en Fly.io, THE Plataforma SHALL servir HubBot y API_Web desde el mismo proceso y volumen.
4. THE Frontend_Web SHALL comunicarse con la Plataforma únicamente mediante HTTP a través de la API_Web, tanto si se hospeda aparte como si se sirve junto a la Plataforma.

### Requirement 15: Compatibilidad con la funcionalidad actual de Discord

**User Story:** Como usuario de Discord, quiero que los slash commands y botones actuales sigan funcionando igual, para no perder funcionalidad al introducir la plataforma web.

#### Acceptance Criteria

1. THE Plataforma SHALL conservar los slash commands actuales de Modulo_Signups y Modulo_Registry con su comportamiento vigente.
2. THE Plataforma SHALL conservar las vistas persistentes de botones de Modulo_Signups y restaurarlas tras un reinicio mediante `restore(bot)`.
3. WHEN un usuario interactúa con un botón o slash command existente, THE Plataforma SHALL aplicar la operación a través de los mismos managers y stores que usa la API_Web.
4. WHERE un Evento se creó con una Plantilla antes de esta feature, THE Plataforma SHALL seguir gestionando ese Evento sin requerir migración manual por parte del usuario.
