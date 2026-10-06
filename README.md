# Hype Radar

**Español** / [English](README.en.md)

**Alertas de mercado con evidencia, contexto de liquidez y un abogado del diablo.**

Describe una condición de mercado por voz o texto, síguela con un motor determinista y revisa la evidencia de cada evento. Hype Radar reúne precio, interés abierto, financiación y condiciones del libro de órdenes en una aplicación para perpetuos de Hyperliquid y determinados mercados HIP-3.

[![Portada de Hype Radar: alertas de mercado con evidencia, con los colores carbón, oro y verde de la plataforma](docs/images/pitch-cover.png)](pitch-deck/Hype-Radar-Pitch.pdf)

Consulta el [MVP actual](#mvp-actual), el [flujo multimodal](#flujo-multimodal) y la [ejecución local](#ejecución-local). Para el despliegue, revisa las [imágenes de producción](#imágenes-de-producción) y la [infraestructura de demostración en AWS](#infraestructura-de-demostración-en-aws).

## Pitch deck

La presentación está en español y sigue el enunciado del taller de FinTech multimodal.

| Formato | Uso |
| --- | --- |
| [PowerPoint](pitch-deck/Hype-Radar-Pitch.pptx) | Diapositivas editables, notas del presentador y animaciones. Abre el modo Presentación para ver las apariciones por etapas. |
| [PDF](pitch-deck/Hype-Radar-Pitch.pdf) | Doce diapositivas completas, con el estado final de cada animación. |

## MVP actual

- **Datos de mercado en tiempo real.** Velas, libro de órdenes, operaciones recientes y variación del precio de marca en 24 horas para BTC, ETH, SP500, XYZ100 y BRENTOIL.
- **Interacción por voz y texto.** Graba una petición breve, revisa su transcripción y envíala al asistente. La transcripción nunca se envía automáticamente.
- **Reglas creadas por el agente.** Diez herramientas tipadas consultan los datos compartidos, crean alertas o borradores, gestionan su estado y recuperan la evidencia de los eventos.
- **Evaluación determinista.** Combina umbrales de métricas y cambios de interés abierto con ventanas explícitas, persistencia, periodos de espera entre eventos y políticas de calidad.
- **Comprobaciones de liquidez y datos.** Evalúa diferencial, profundidad, antigüedad e integridad de los datos por separado de la condición de la regla. La ausencia de datos obligatorios impide confirmar una señal.
- **Historial web y evidencia.** Revisa la versión original de la regla, los valores observados, las marcas temporales y los motivos de calidad. Pausa o reanuda reglas desde el panel de alertas o el asistente.

El MVP funciona como una instancia compartida. La entrega externa de notificaciones y las cuentas por usuario forman parte del plan de desarrollo. No ejecuta operaciones ni requiere credenciales de trading. SP500, XYZ100 y BRENTOIL representan perpetuos de Hyperliquid, no cotizaciones oficiales de los mercados subyacentes.

### Ejemplo de uso

> Crea una alerta para BTC cuando el interés abierto suba un 5 % en 15 minutos. Bloquea el evento si el diferencial supera 10 puntos básicos.

1. Escribe la petición o grábala y revisa la transcripción antes de enviarla.
2. El agente crea la regla solicitada e indica sus condiciones exactas. Pide una vista previa para obtener un borrador inactivo.
3. Sigue el estado de monitorización. Las condiciones de interés abierto pueden necesitar un periodo de calentamiento para reunir las observaciones requeridas.
4. Consulta los eventos en el historial web. La condición y la calidad de los datos tienen resultados separados: una condición verdadera puede producir un evento bloqueado.

## Flujo multimodal

La configuración predeterminada utiliza `openai/whisper-1` para la transcripción y `deepseek/deepseek-v4-flash` para el agente de lenguaje a través de OpenRouter. Ambos modelos son configurables. LangChain coordina las herramientas del agente con los servicios de mercado y alertas de la aplicación.

```mermaid
flowchart TD
    VOICE[Grabación de voz] --> STT[Modelo de transcripción]
    STT --> REVIEW[Transcripción revisada por el usuario]
    REVIEW --> AGENT[Agente de lenguaje y herramientas tipadas]
    TEXT[Petición escrita] --> AGENT
    HYPERLIQUID[WebSocket y REST de Hyperliquid] --> DATA[Datos de mercado compartidos]
    DATA --> AGENT
    AGENT --> RULE[Regla de alerta validada y almacenada]
    RULE --> ENGINE[Evaluador determinista y comprobaciones de calidad]
    DATA --> ENGINE
    ENGINE --> EVIDENCE[Evidencia registrada del evento]
    EVIDENCE --> HISTORY[Historial web]
```

El agente y el evaluador consultan el mismo servicio de datos de mercado. Las llamadas a la IA quedan fuera de la evaluación de cada actualización del mercado. Los gráficos representan datos numéricos; el análisis de imágenes y la síntesis de voz no son modalidades implementadas.

## Visión del producto

El **abogado del diablo** aporta motivos para examinar una alerta con cuidado: observaciones antiguas, poca liquidez, señales contradictorias y contexto de mercado incierto. El motor actual ya registra comprobaciones de liquidez, antigüedad e integridad; ampliar el contexto de sesión y las observaciones verificadas del oráculo requiere validar más fuentes.

Los siguientes pasos incluyen autorización por usuario y notificaciones externas mediante la bandeja de salida persistente. El [plan de desarrollo](#plan-de-desarrollo) describe la visión completa y sus criterios de aceptación. Los usuarios mantienen el control de sus reglas y decisiones de trading.

## Ejecución local

El gráfico transmite velas de los perpetuos BTC, ETH, SP500, XYZ100 y BRENTOIL de Hyperliquid mediante FastAPI. Los selectores de mercado e intervalo de shadcn/ui permiten cambiar de instrumento y duración de las velas. Los intervalos admitidos son `1m`, `3m`, `5m` (predeterminado), `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `3d`, `1w` y `1M` (un mes), conforme a la [API de suscripción a velas de Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions). SP500, XYZ100 y BRENTOIL utilizan los mercados de trade[XYZ] (`xyz:SP500`, `xyz:XYZ100` y `xyz:BRENTOIL`). Una tarea de ingestión compartida por cada par mercado/intervalo sirve a todos los navegadores; las últimas 200 velas de cada par permanecen en memoria.

El libro de órdenes sigue al mercado seleccionado junto al gráfico, o debajo en móvil. Muestra profundidad acumulada de compra y venta, diferencial, agrupación de precios calculada por el mercado y tamaños en unidades base o USD. Utiliza el [flujo de instantáneas `l2Book` de Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) con `fast: true` mediante `/ws/book?symbol=BTC&precision=5`. La agrupación admite 5 cifras significativas (predeterminado), 4, 3 o 2. Cada flujo de mercado/agrupación se comparte entre los lectores y se libera cuando sale el último. El flujo rápido proporciona hasta 5 niveles por lado, con actualizaciones medidas aproximadamente cada 0,5 segundos; la cadencia depende del mercado. El panel muestra cinco niveles por lado. Debajo aparece la lista de las últimas 40 ejecuciones, de más reciente a más antigua, con lado comprador o vendedor, precio, tamaño en la unidad base o USD seleccionada y hora local. La suscripción `trades` se comparte por mercado entre todas las precisiones del libro y elimina ejecuciones duplicadas. Los libros y las operaciones tienen conexiones independientes y limpian sus propios datos al reconectar. Los libros ausentes o desconectados se vacían durante la reconexión.

El panel de chat a la derecha del libro de órdenes utiliza OpenRouter a través del backend. `POST /api/chat` acepta un `message` no vacío de hasta 4.000 caracteres y hasta 20 mensajes opcionales del historial con roles `user`/`assistant`. Ejecuta el agente de mercado de LangChain y transmite eventos SSE de texto, herramientas y cambios de alertas. La clave de API nunca llega al navegador. El estado de la conversación permanece en la sesión del navegador y se envía con cada petición; Hype Radar no lo almacena. Intro envía el mensaje; Mayús+Intro añade una línea. Las respuestas pueden detenerse o reintentarse tras un fallo, y New chat borra la conversación. El panel respeta las preferencias de movimiento reducido y se coloca debajo del libro en móvil. El botón del micrófono graba hasta 60 segundos, envía el audio a `POST /api/chat/transcribe` e inserta la transcripción en el editor para revisarla, sin enviarla automáticamente. OpenRouter y su proveedor de modelo procesan peticiones, respuestas y grabaciones; no envíes secretos ni información personal.

El panel inferior izquierdo de alertas ocupa el ancho del gráfico y del libro de órdenes. Incluye las vistas **Active**, **Paused** e **History**. En móvil aparece justo debajo del gráfico. Se actualiza cada cinco segundos, pagina 25 filas y permite filtrar por el mercado actual. La paginación aparece solo cuando hay más de una página. Las alertas se crean mediante el asistente; el panel permite pausarlas, reanudarlas y consultar la evidencia. Las tablas tienen desplazamiento independiente y, cuando falla una actualización, indican que las filas conservadas son los últimos datos cargados.

Las alertas activas corresponden a versiones de reglas configuradas como activas. Una columna independiente muestra **Monitoring**, **Warming up**, **Stale / interrupted** o **Evaluator unavailable**, según las observaciones actuales del motor determinista. Las reglas creadas por el agente y las acciones de pausa o reanudación se aplican inmediatamente; otros cambios en la base de datos se concilian en un máximo de cinco segundos. El historial conserva el nombre y la versión originales de cada regla y distingue el estado del evento, la verdad de la condición y la calidad de los datos. **Evidence** abre valores registrados, umbrales, tiempos de observación y evaluación, referencias de OI y motivos de calidad. La aplicación sigue siendo una instancia compartida tras la frontera de acceso del despliegue; se excluyen los identificadores de propietario y los destinatarios de entrega. Las notificaciones solo aparecen en el historial web.

El panel reutiliza el servicio de alertas del agente mediante estos endpoints:

- `GET /api/alerts?view=rules|history&status=active|paused&symbol=BTC&limit=25&offset=0`: filas y `has_more`; los filtros de estado solo se aplican a las reglas.
- `PATCH /api/alerts/{alert_id}`: `{ "status": "active" }` o `{ "status": "paused" }`.
- `GET /api/alerts/events/{event_id}`: definición inmutable de la regla y evidencia registrada del evento.

Requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) y Node.js 22.12+.

Desde la raíz del repositorio, inicia PostgreSQL, aplica las migraciones y arranca el backend:

```sh
docker compose up -d postgres
cd backend
cp .env.example .env  # first setup only
uv sync
uv run alembic upgrade head
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

En otra terminal, inicia el frontend desde la raíz del repositorio:

```sh
cd frontend
npm ci
npm run dev
```

Abre **http://127.0.0.1:5173**. El frontend redirige `/ws/candles?symbol=BTC&interval=5m` y `/health` al backend. El parámetro `symbol` admite `BTC` (predeterminado), `ETH`, `SP500`, `XYZ100` o `BRENTOIL`; `interval` admite los intervalos anteriores. Se rechazan símbolos o intervalos no admitidos. Ejecuta un solo worker del backend para mantener una conexión al mercado por cada par mercado/intervalo. Cada flujo comienza con la primera suscripción y se detiene al desconectarse el último consumidor, liberando la conexión. El historial acotado en caché sigue disponible para lectura; una nueva suscripción lo concilia con el historial reciente del mercado. Los navegadores que consultan el mismo par comparten el flujo. El historial se carga con la primera petición y se actualiza después de reconectar; el gráfico conserva sus últimos datos durante una reconexión y los borra al cambiar de mercado o intervalo. `/health` informa del estado de cada par solicitado con claves como `BTC:5m`.

Para comprobar los tipos y la compilación de producción del frontend, ejecuta `npm run build` en `frontend/`. No se necesitan claves de API.

La franja de mercados de la cabecera muestra los instrumentos del selector y la variación de su precio de marca en 24 horas, calculada como `(markPx / prevDayPx - 1) * 100` a partir de `metaAndAssetCtxs` de Hyperliquid, para los mercados nativos y `xyz`. Este dato es independiente del intervalo de las velas y no representa la rentabilidad diaria oficial de una acción subyacente. Al pulsar un instrumento se selecciona su gráfico. El frontend actualiza `/api/markets` cada 15 segundos después de cada respuesta; el backend comparte los resultados durante 10 segundos. Los datos ausentes o fallidos aparecen como `—`, con reintento automático.

El componente Select adapta [shadcn/ui](https://ui.shadcn.com/docs/components/radix/select) al CSS existente. Los iconos de mercado se incluyen localmente a partir de los recursos públicos `/icons/` de Hyperdash: [BTC](https://hyperdash.com/icons/BTC.svg), [ETH](https://hyperdash.com/icons/ETH.svg), [SP500](https://hyperdash.com/icons/SP500.png), [XYZ100](https://hyperdash.com/icons/XYZ100.png) y [BRENTOIL](https://hyperdash.com/icons/BRENTOIL.png).

Ejecuta las pruebas de regresión del backend con `uv run python -m unittest -v` en `backend/`.

## Imágenes de producción

Construye las imágenes de producción del backend y del frontend desde la raíz del repositorio:

```sh
docker build -t hype-radar-backend ./backend
docker build -t hype-radar-frontend ./frontend
```

La imagen del backend instala las dependencias fijadas, se ejecuta con un usuario sin privilegios y arranca un worker de Uvicorn en el puerto 8000. La imagen del frontend compila la aplicación Vite y la sirve con Nginx en el puerto 80. Nginx redirige `/api`, `/health` y `/ws` a un contenedor accesible como `backend:8000`; desactiva el almacenamiento intermedio de respuestas para el chat y permite la actualización de conexiones a WebSocket para los datos de mercado.

El arranque de la base de datos, las migraciones, los secretos y el orden de los servicios pertenecen al despliegue y no se ejecutan al construir las imágenes. Prepara el entorno de producción con la plantilla incluida:

```sh
cp .env.production.example .env.production
chmod 600 .env.production
```

Sustituye todos los valores de ejemplo de `.env.production`. `POSTGRES_PASSWORD` debe ser válido dentro de una URL y coincidir con la contraseña incluida en `DATABASE_URL`. Configura `OPENROUTER_SITE_URL` con la URL HTTPS pública del despliegue. Git ignora el archivo de entorno de producción.

Crea el archivo de contraseñas de Basic Auth antes de arrancar los servicios. El siguiente comando solicita la contraseña de forma interactiva para no exponerla en el historial de la terminal:

```sh
mkdir -p .secrets
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B -c /auth/htpasswd demo
chmod 644 .secrets/htpasswd
```

Utiliza `-c` solo al crear el archivo. Para añadir otro usuario sin sustituir los existentes:

```sh
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B /auth/htpasswd teammate@example.com
```

Puedes utilizar una dirección de correo como nombre de usuario, pero Basic Auth no verifica que esa cuenta de correo pertenezca al usuario. El archivo de contraseñas, ignorado por Git, almacena hashes bcrypt y no contraseñas en texto plano. Accede a la aplicación por HTTPS: fuera de HTTPS las credenciales de Basic Auth solo están codificadas en base64, no cifradas.

Construye y arranca todos los servicios de producción:

```sh
docker compose --env-file .env.production -f compose.prod.yml up -d --build
docker compose --env-file .env.production -f compose.prod.yml ps --all
```

El despliegue inicia PostgreSQL, espera a que esté sano, ejecuta `alembic upgrade head` una vez y después arranca el backend y Nginx. Solo publica en el host el puerto configurado del frontend; PostgreSQL y FastAPI permanecen accesibles únicamente mediante sus redes de Docker. Los registros de los contenedores rotan para limitar el uso de disco. Nginx protege el frontend y todas las rutas `/api/` con Basic Auth. Las rutas `/ws/` transmiten datos públicos de mercado de solo lectura sin solicitar Basic Auth, para evitar un segundo diálogo de acceso durante la conexión WebSocket; se limitan a cuatro conexiones simultáneas por IP de origen. El tráfico general de API se limita a 30 peticiones por segundo e IP de origen. Además, el chat y la transcripción se limitan a 6 peticiones por minuto y credencial de Basic Auth, con una pequeña tolerancia de ráfaga.

Consulta los servicios o detén el despliegue sin borrar los datos de PostgreSQL:

```sh
docker compose --env-file .env.production -f compose.prod.yml logs --follow
docker compose --env-file .env.production -f compose.prod.yml down
```

Ejecutar `down --volumes` también elimina el volumen de PostgreSQL y todos los datos persistidos de la aplicación.

## Infraestructura de demostración en AWS

La configuración Terraform de [`infra/terraform`](infra/terraform/README.md) prepara el entorno de demostración en AWS: una instancia Lightsail preparada para Docker, una IP estática asociada, reglas de cortafuegos, una distribución CDN de Lightsail y un presupuesto mensual de 15 USD para la cuenta con alertas por correo. Los planes predeterminados de instancia y CDN tienen un coste aproximado de 9,50 USD al mes, antes de impuestos y excesos de consumo. Consulta el README de infraestructura para las instrucciones de autenticación, despliegue, seguridad y eliminación de recursos.

## API compartida de datos de mercado

El mismo servicio atiende los WebSockets del navegador, las lecturas HTTP y las alertas deterministas. El agente puede utilizarlo directamente sin un framework de LLM ni clave de API. Las lecturas independientes de datos de mercado no requieren base de datos; el backend FastAPI siempre inicia la evaluación de alertas y necesita PostgreSQL.

| Objeto | Responsabilidad |
| --- | --- |
| `HyperliquidClient` (`app/ingestion/client.py`) | Cliente HTTP asíncrono reutilizable, peticiones al mercado, suscripciones, comprobaciones de actividad y reconexiones. |
| `MarketDataService` (`app/application/market_data.py`) | Consulta del registro, suscripciones compartidas, lecturas en caché, instantáneas tipadas y ciclo de vida. |
| Objetos de flujo por canal (`feed.py`) | Conciliación de velas, sustitución del libro, observaciones de contexto y eliminación de operaciones duplicadas. |
| `HyperliquidNormalizer` | Conversión de valores validados del mercado a registros deterministas `MarketObservation`. |
| `EvaluationWorker` | Consume el servicio compartido y persiste los resultados de las reglas; no mantiene conexiones propias al mercado. |

`app/domain/markets.py` contiene el registro configurado: BTC, ETH, HYPE, SP500, XYZ100 y BRENTOIL. Los alias de visualización (`SP500`) y los nombres del mercado (`xyz:SP500`) se resuelven a la misma identidad. La interfaz sigue mostrando sus cinco mercados originales. `HYPERLIQUID_NETWORK=mainnet` es la opción predeterminada; `testnet` cambia ambas URL de transporte. Los mercados configurados deben existir en la red seleccionada.

Desde `backend/`, un cliente de lectura independiente puede utilizarse así:

```python
import asyncio
from app.application.market_data import MarketDataService
from app.ingestion.client import HyperliquidClient

async def main():
    async with MarketDataService(HyperliquidClient()) as markets:
        print(await markets.list_markets())
        snapshot = await markets.get_snapshot("BTC")
        print(snapshot.fields["mark_price"].value)
        print(snapshot.model_dump(mode="json"))
        candles = await markets.get_candles("BTC", interval="5m", limit=200)
        book = await markets.get_order_book("BTC", precision=5, fast=True)
        trades = await markets.get_recent_trades("BTC", limit=40)
        async with markets.subscribe("BTC", channels=("context", "book")) as updates:
            async for update in updates:
                print(update.channel, update.status, update.data)
                if update.status == "live":
                    break

asyncio.run(main())
```

Dentro del backend, reutiliza `request.app.state.markets` o inyéctalo en el constructor del consumidor; no crees un segundo servicio. Un script ejecutado por separado mantiene conexiones independientes. Las suscripciones son gestores de contexto asíncronos: al salir liberan la participación del consumidor. Las opciones de suscripción idénticas comparten una sola tarea de conexión. Las operaciones se comparten independientemente de la precisión del libro. El libro suscrito de forma predeterminada tiene profundidad normal y no agrupa precios; los adaptadores de la interfaz solicitan explícitamente libros rápidos agrupados. Las colas acotadas terminan los consumidores lentos con `SlowConsumer`; un evaluador se detiene antes que perder observaciones silenciosamente.

Equivalentes HTTP, todos mediante GET:

| Ruta | Parámetros |
| --- | --- |
| `/api/market-data` | Identidades configuradas, incluida HYPE. |
| `/api/market-data/{market}/snapshot` | Contexto combinado y libro de profundidad normal sin agrupación. |
| `/api/market-data/{market}/candles` | `interval=5m`, `limit=200` (1–200). |
| `/api/market-data/{market}/book` | `precision=5` (2–5 o `full`), `fast=true`; `full` equivale a `None` en Python. |
| `/api/market-data/{market}/trades` | `limit=40` (1–40); devuelve hasta ese número de operaciones observadas. |

```sh
curl 'http://127.0.0.1:8000/api/market-data/xyz:SP500/snapshot'
curl 'http://127.0.0.1:8000/api/market-data/BTC/book?precision=full&fast=false'
```

Los valores financieros se representan como cadenas Decimal en JSON. Los campos de las instantáneas incluyen `source_at`, `received_at`, `timestamp_basis` y los estados `fresh`/`stale`/`missing`. El límite de antigüedad del contexto es de 30 segundos por defecto; el del libro utiliza la marca temporal del mercado y 15 segundos. Los límites de cada regla siguen controlando su evaluación. `assembled_at` no es un tiempo de observación común del mercado. Los campos ausentes permanecen nulos; una actualización del libro nunca actualiza la antigüedad de OI ni de financiación. El diferencial y el precio medio se calculan a partir del mismo libro. La profundidad suma únicamente los niveles suministrados e incluye agrupación, cantidades reales de niveles por lado y unidades `quote_notional`; no estima la liquidez de todo el mercado. La financiación es la tasa bruta observada, y el volumen y la variación corresponden al mercado del perpetuo, no a la rentabilidad oficial de una acción subyacente.

Las lecturas reutilizan el estado reciente o realizan peticiones REST acotadas, con transmisión temporal para las operaciones y un plazo total de diez segundos. Una instantánea puede contener campos explícitamente antiguos o ausentes; si no hay datos utilizables, devuelve 503. Los demás endpoints de lectura devuelven 503 si no disponen de un resultado suficientemente reciente. Los mercados desconocidos devuelven 404 y las opciones de consulta no válidas, 422. Se conserva la compatibilidad del mapa numérico de porcentajes de `/api/markets` y de las rutas WebSocket existentes. `/health` mantiene sus secciones de gráfico y libro y añade las suscripciones activas y el estado del evaluador; los flujos activos no saludables producen 503.

## Núcleo determinista de alertas

El evaluador de alertas es una tarea supervisada que se ejecuta continuamente en el mismo proceso que FastAPI y utiliza el servicio compartido de datos de mercado. Almacena muestras normalizadas, versiones inmutables de reglas, puntos de recuperación del estado de ejecución, eventos, evidencia e intención de notificación en PostgreSQL. Los cambios de evento, evidencia, estado y bandeja de salida se confirman atómicamente. Los valores económicos utilizan `Decimal` en Python y `NUMERIC(38, 18)` en PostgreSQL.

Los primeros predicados deterministas admiten umbrales de valor actual y cambios absolutos o porcentuales del interés abierto durante ventanas explícitas. Las reglas combinan predicados con `all` o `any` y lógica de tres valores `true / false / unknown`. La persistencia, el periodo entre eventos, la antigüedad, el diferencial, la profundidad, las interrupciones, el desorden temporal y las comprobaciones de oráculo verificado se evalúan sin un LLM. El canal `activeAssetCtx` no proporciona una marca temporal de origen, por lo que sus observaciones utilizan explícitamente el momento de recepción del backend; las observaciones `l2Book` conservan la marca temporal del mercado.

Inicia PostgreSQL desde la raíz del repositorio:

```sh
docker compose up -d postgres
```

Prepara el backend y aplica las migraciones:

```sh
cd backend
cp .env.example .env
uv sync --locked
uv run alembic upgrade head
```

Ejecuta el caso de reproducción determinista versionado:

```sh
uv run python -m app.workers.replay tests/fixtures/hype_breakout.json
```

Registra una definición de regla revisada para BTC, ETH, SP500, XYZ100 o BRENTOIL en la red configurada. Un backend en ejecución recoge los cambios en un máximo de cinco segundos. La creación de reglas y nuevas versiones rechaza los mercados no admitidos antes de escribirlos. Las definiciones HIP-3 deben utilizar `dex: "xyz"` y el símbolo sin prefijo, por ejemplo, `coin: "SP500"`. El caso de reproducción con HYPE anterior contiene datos sintéticos de prueba y no puede registrarse como regla activa.

```sh
uv run python -m app.workers.seed_rule path/to/your-rule.json
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

El evaluador mantiene suscripciones compartidas de contexto y de libro sin agrupación y profundidad normal para cada mercado activo. Cerrar las pestañas del navegador no detiene esas suscripciones. Reconstruye las ventanas necesarias a partir de muestras almacenadas, evalúa observaciones y temporizadores y persiste los eventos en PostgreSQL. Carga las reglas al arrancar, aplica inmediatamente los cambios del agente y concilia las versiones activas almacenadas cada cinco segundos. La evaluación se inicia automáticamente con el backend y permanece inactiva si no hay reglas activas. Los fallos de base de datos o carga de reglas impiden el arranque. Un fallo del evaluador en ejecución detiene la evaluación y hace que `/health` devuelva 503; reinicia después de resolver la causa. El agente puede pausar o reanudar alertas confirmadas. Se ha eliminado el antiguo comando independiente de evaluación en directo; se mantienen los comandos de registro y reproducción. La entrega externa de notificaciones, la autenticación por usuario y la edición de condiciones de reglas existentes siguen pendientes; las filas de la bandeja de salida son persistentes, pero todavía no se envían.

Ejecuta la prueba de integración aislada con PostgreSQL:

```sh
docker compose --profile test up -d postgres-test
cd backend
DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run python -m unittest -v tests.test_persistence
```

## Chat con OpenRouter

OpenRouter sigue siendo el proveedor predeterminado. La [configuración opcional de prueba de Qwen en EC2](infra/qwen-ec2/README.md) utiliza variables de entorno privadas superpuestas para probar vLLM autoalojado sin cambiar los valores predeterminados de la aplicación.

Crea una clave de API en OpenRouter y añádela a `backend/.env`; nunca incluyas su valor real en un commit:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_TRANSCRIPTION_MODEL=openai/whisper-1
```

Reinicia el backend después de modificar `.env`. `OPENROUTER_MODEL` admite cualquier identificador de modelo disponible para la cuenta que soporte llamadas a herramientas. El predeterminado es `deepseek/deepseek-v4-flash`; el enrutamiento requiere proveedores compatibles con los parámetros solicitados. `OPENROUTER_SITE_URL` y `OPENROUTER_APP_NAME` configuran cabeceras opcionales de atribución. `OPENROUTER_TIMEOUT_SECONDS` y `OPENROUTER_MAX_COMPLETION_TOKENS` limitan cada petición al proveedor. La entrada por voz utiliza el endpoint específico `/api/v1/audio/transcriptions` de OpenRouter. `OPENROUTER_TRANSCRIPTION_LANGUAGE` puede contener una pista ISO-639-1, como `es`; déjala vacía para detectar el idioma automáticamente. `OPENROUTER_MAX_AUDIO_BYTES` tiene un valor predeterminado de 10 MiB.

`backend/app/application/agent.py` utiliza `langchain.agents.create_agent` y `ChatOpenRouter` con diez herramientas tipadas:

- `list_markets`, `get_market_snapshot`, `get_candles`, `get_market_microstructure`, `get_metric_history`.
- `preview_alert`, `create_alert`, `list_alerts`, `set_alert_status`, `get_alert_event`.

Las herramientas de mercado comparten el `MarketDataService` de la interfaz. Informan de fuentes, marcas temporales, unidades y límites de cobertura. Las velas contienen precios OHLC sin volumen; el historial de métricas se limita a 200 observaciones de un máximo de 24 horas y puede estar incompleto si el mercado no se estaba monitorizando. La búsqueda de noticias, las condiciones de indicadores técnicos y la entrega externa de notificaciones siguen previstas para el futuro.

Una petición explícita de creación llama a `create_alert` con sus condiciones tipadas y activa la alerta en el mismo turno. No hay tarjeta de confirmación, botón adicional ni token de confirmación. El agente informa de la definición guardada y del estado real de monitorización. Las peticiones ambiguas requieren completar las condiciones que faltan. `preview_alert` permite peticiones explícitas de vista previa: guarda un borrador inactivo y lo explica en la conversación sin un componente de activación. Internamente, la creación valida una versión inmutable antes de activarla. `request_id` y las condiciones normalizadas identifican una regla guardada entre reintentos, independientemente del orden de llamadas a herramientas, nombres generados, formato decimal u orden de predicados. Las condiciones equivalentes reutilizan la definición guardada y no reactivan una regla pausada posteriormente; distintos mercados o condiciones identifican reglas separadas. Esto elimina llamadas equivalentes, no cambios arbitrarios en condiciones generadas por el modelo. La activación de borradores caduca después de una hora. `list_alerts` devuelve hasta 30 reglas o eventos por página; utiliza `next_offset` con los mismos filtros para recuperar entradas anteriores. Detener una respuesta no revierte una regla ya confirmada en la base de datos. Consulta la lista de alertas después de una escritura interrumpida. El evaluador también concilia escrituras confirmadas cuya respuesta HTTP se perdió. La pausa libera las suscripciones del evaluador cuando ninguna otra regla utiliza ese mercado; las suscripciones del navegador siguen siendo independientes. La reanudación inicia un nuevo episodio de condición y periodo de persistencia, conservando el periodo anterior entre eventos y su secuencia.

La aplicación sigue sin autenticación interna y funciona como una instancia compartida. Las nuevas reglas del agente utilizan una identidad de instancia controlada por el servidor; el modelo no puede elegir propietarios ni destinatarios. Utiliza la frontera de acceso existente del despliegue.

`POST /api/chat` devuelve `text/event-stream` con tramas JSON `data:` de tipos `text`, `tool_start`, `tool_end`, `alert_changed`, `done` y `error`. Cada turno se limita a seis llamadas al modelo, doce llamadas a herramientas y 75 segundos. La falta de configuración devuelve `503`; los fallos durante la ejecución del agente producen un evento explícito `error`, no una finalización correcta. Los reintentos conservan su identificador de petición. El texto de conversación permanece acotado en el navegador; el servidor persiste borradores de alertas, reglas y evidencia. `/health` informa del modelo configurado y del estado del evaluador sin exponer credenciales. Las grabaciones de audio solo permanecen en memoria para transcribirlas; Hype Radar no las almacena.

## Arquitectura propuesta

El monolito modular actual ejecuta las tareas de ingestión, evaluación y API en un solo proceso. En el futuro, un worker de notificaciones podrá consumir la bandeja de salida persistente de forma independiente. Ejecuta exactamente un worker de Uvicorn: las suscripciones y el estado en memoria se comparten dentro de ese proceso.

```mermaid
flowchart TD
    WS[WebSocket de Hyperliquid] --> ING[Ingestión y normalización]
    REST[Instantáneas REST de arranque y conciliación] --> ING
    ING --> STATE[Estado reciente y ventanas de observación]
    ING --> SAMPLES[(Muestras de mercado en PostgreSQL)]
    STATE --> EVAL[Evaluador determinista y comprobaciones de calidad]
    EVAL --> EVENTS[(Eventos, evidencia y bandeja de salida en PostgreSQL)]
    EVENTS --> NOTIFY[Worker de notificaciones]
    NOTIFY --> TG[Telegram]
    NOTIFY --> WEB[Notificaciones web]
    SAMPLES --> API[FastAPI]
    EVENTS --> API
    API --> UI[Aplicación React mediante HTTP y WebSocket o SSE]
    UI --> API
    API --> AI[Propuestas de reglas y explicaciones diferidas con IA]
    AI --> API
```

Los flujos del mercado y el contenido de proveedores externos son datos, no instrucciones. La IA queda fuera de la evaluación de señales. Los eventos, su evidencia y la intención de notificación deben persistir antes de enviar cualquier notificación externa.

### Tecnologías propuestas

| Componente | Elección y responsabilidad |
| --- | --- |
| Frontend | Vite, React, TypeScript: gestión de reglas, historial de eventos, evidencia y estado de los flujos. Las actualizaciones en directo llegan desde nuestro backend. |
| API HTTP | Python, FastAPI, Pydantic: autenticación, autorización por usuario, reglas validadas, consultas de eventos, actualizaciones en directo y endpoints de salud. |
| Ingestión | Python `asyncio`: suscripciones WebSocket compartidas de Hyperliquid y conciliación REST. |
| Motor de reglas | Python determinista: ventanas en memoria, evaluación incremental, comprobaciones de calidad y puntos de recuperación. |
| Almacenamiento | PostgreSQL, SQLAlchemy 2, Alembic: observaciones de mercado, versiones inmutables de reglas, eventos, evidencia y registros de entrega. Cada tarea concurrente mantiene su propia sesión de base de datos. |
| Notificaciones | Worker independiente con bandeja de salida transaccional, reintentos con espera creciente e idempotencia por evento/canal/destinatario. |
| Asistencia de IA | LangChain y salidas estructuradas de Pydantic mediante una interfaz `ModelProvider`; la elección de proveedor y modelo depende de su evaluación. |
| Entrega | Docker, CI de estilo/tipos/pruebas/migraciones, TLS, secretos gestionados, copias de seguridad verificadas y métricas operativas. |

Redis, pgvector, almacenamiento de objetos para grandes conjuntos de datos brutos y una configuración completa de OpenTelemetry son opciones posteriores si la escala o el uso medido lo requieren.

## Datos de mercado e interpretación

El servicio actual utiliza `activeAssetCtx`, `l2Book`, `trades` y velas bajo demanda, con `metaAndAssetCtxs` mediante REST para cargar el contexto inicial y solicitudes REST de libros y velas para lecturas sin caché previa. Los nuevos mercados y operadores HIP-3 requieren validar el comportamiento de los canales, sus campos, cadencias y límites de API.

| Dato | Interpretación requerida |
| --- | --- |
| Precio | Almacenar por separado marca, oráculo, compra, venta y precio medio. El precio de marca no es necesariamente ejecutable. Cada regla identifica la referencia que compara. |
| OI | Conservar las unidades y calcular cambios a partir de muestras compatibles y válidas en una ventana definida. Una actualización de precio no demuestra que se haya actualizado OI. |
| Financiación | Conservar tasa, periodo y tiempo de observación; distinguir tasas observadas, predicciones y pagos liquidados. |
| Liquidez | Comprobar diferencial en puntos básicos, profundidad para un nominal de referencia configurable, antigüedad del libro y actividad. Una instantánea no garantiza liquidez de ejecución. |
| Contexto HIP-3 | Utilizar evidencia específica del operador y metadatos de sesión. No deducir el modo del oráculo a partir del reloj, el diferencial o la divergencia de precios. |
| Acciones subyacentes | No presentar `prevDayPx` de un perpetuo como el cierre anterior oficial de la acción. Los datos bursátiles con licencia y los calendarios son una integración posterior. |

### Comprobación de viabilidad de HIP-3

La referencia propone investigar trade[XYZ] como posible primer operador; no es una elección definitiva. Determina si el estado explícito externo/interno del oráculo es accesible de forma programática, incorpora una marca temporal y es estable.

Utiliza `EXTERNO` o `INTERNO` únicamente con evidencia directa y validada. En caso contrario, muestra `NO_VERIFICADO` de forma consistente en la aplicación, la API y las notificaciones. Se pueden informar transiciones observables de sesión, cambios de diferencial y divergencias del precio de referencia, pero esos datos no demuestran un cambio de modo del oráculo. Los resultados de un operador no deben generalizarse a los demás.

## Ciclo de vida de reglas y alertas

1. **Solicitud y creación.** Una petición explícita permite al agente generar predicados tipados y activar directamente una versión inmutable. El backend valida mercados, unidades, ventanas y umbrales. Las vistas previas permanecen inactivas; el análisis de solo lectura no autoriza cambios.
2. **Calentamiento.** Carga reglas activas, indéxalas por mercado y métrica dependiente y construye las ventanas de observación necesarias. Las ventanas incompletas permanecen desconocidas.
3. **Ingestión.** Registra el tiempo de recepción, valida identidad y esquema, gestiona duplicados y observaciones desordenadas según las garantías del canal y actualiza el estado reciente. Persiste las muestras por lotes fuera de la ruta crítica de evaluación.
4. **Evaluación.** Reevalúa las reglas afectadas al cambiar métricas relevantes y mediante temporizadores de persistencia, antigüedad y espera entre eventos. Cada campo tiene su propio límite de antigüedad.
5. **Política de calidad.** Evalúa por separado la condición y las comprobaciones del abogado del diablo. La falta de observaciones obligatorias produce `data_unknown`, nunca un resultado automático `false`.
6. **Persistencia atómica.** Escribe el evento, evidencia suficiente para reproducirlo y la entrada de la bandeja de salida en una transacción. Una huella única basada en versión de regla, episodio y transición evita eventos duplicados.
7. **Entrega y trazabilidad.** El worker envía de forma asíncrona, registra intentos y resultados y enlaza el detalle del evento. Los cambios posteriores de la regla no alteran la evidencia histórica.
8. **Recuperación transparente.** Tras un reinicio o desconexión, marca los datos afectados como no fiables, concilia instantáneas y reconstruye ventanas antes de reanudar la evaluación dependiente. No presentes transiciones no observadas como alertas en directo. Un evento detectado durante la recuperación debe indicar la interrupción y el retraso de detección.

La entrega a proveedores externos es **al menos una vez**; los reintentos y la unicidad de la base de datos no garantizan una entrega exactamente una vez en Telegram.

## Comprobaciones del abogado del diablo

Cada comprobación devuelve `PASS`, `WARN`, `BLOCK` o `UNKNOWN`, junto con un código de motivo y evidencia. Los umbrales deben calibrarse por mercado, sesión y nominal de referencia.

| Comprobación | Ejemplos de códigos de motivo |
| --- | --- |
| Antigüedad de cada campo | `STALE_PRICE`, `STALE_OI`, `STALE_BOOK` |
| Integridad del flujo y observaciones suficientes | `GAP`, `OUT_OF_ORDER`, `CLOCK_SKEW` |
| Diferencial y profundidad disponible | `WIDE_SPREAD`, `THIN_DEPTH` |
| Referencias comparables de marca/precio medio/oráculo | `MARK_ORACLE_DIVERGENCE`, `MID_ORACLE_DIVERGENCE` |
| Actividad y confirmación temporal | `ISOLATED_TRADE`, `THIN_VOLUME` |
| Muestras compatibles de OI y financiación fechada | `OI_UNCONFIRMED`, `FUNDING_UNDATED` |
| Contexto de sesión y observaciones verificadas del oráculo | `SESSION_TRANSITION`, `ORACLE_MODE_UNKNOWN` |
| Señales de apoyo contradictorias | `PRICE_UP_OI_DOWN`, `BREAKOUT_NO_BOOK_SUPPORT` |

Las políticas permiten notificar, notificar con advertencias o bloquear por datos insuficientes. La falta de datos imprescindibles impide confirmar una señal; un estado de contexto no observable permanece desconocido. La interfaz separa la verdad de la condición (`true / false / unknown`) de la calidad (`valid / warned / blocked`). Las señales contradictorias son hechos que deben explicarse, no una probabilidad inventada de éxito de trading.

## Modelo de datos y auditabilidad

La identidad de un instrumento es `(network, dex, coin)`, nunca solo `coin`. Almacena los valores económicos como `NUMERIC` en PostgreSQL y `Decimal` en Python, con unidades y versiones de esquema. Conserva las marcas temporales de origen disponibles, los tiempos UTC de recepción y evaluación y la latencia o el desfase de reloj medibles.

| Tablas | Finalidad |
| --- | --- |
| `markets` | Identidad del instrumento, unidades, zona horaria/sesión aplicable y versión de metadatos. |
| `market_samples` | Valores por canal, procedencia, tiempos, secuencias o identificadores disponibles, indicadores de interrupción y datos de origen trazables. |
| `regime_observations` | Fuente, tiempo de observación, estado verificado o desconocido, evidencia de confirmación y motivo. |
| `alert_rules`, `alert_rule_versions` | Propietario, ciclo de vida, política, espera entre eventos, límites de entrega y definiciones tipadas inmutables con marca temporal de activación (`confirmed_at`). |
| `rule_runtime` | Último estado de evaluación, duración de persistencia, último disparo, espera entre eventos, posición de datos procesados y puntos de recuperación. |
| `alert_events`, `alert_evidence` | Versión de regla, transición, huella, estado del evento, observaciones, resultados de predicados, decisiones de calidad y códigos de motivo. |
| `notification_outbox`, `deliveries` | Intención de notificación, destinatario/canal, intentos, resultados del proveedor y unicidad de `(event_id, channel, recipient)`. |

Los estados de evento propuestos son `candidate`, `warn`, `confirmed`, `blocked` y `data_unknown`. Indexa muestras por mercado/tiempo y reglas/eventos por propietario/estado; particiona las muestras solo cuando el volumen lo justifique.

Las propuestas iniciales de conservación son 7–30 días para muestras detalladas y 6–12 meses para agregados, según el volumen medido y los derechos sobre los datos. Las versiones de reglas y la evidencia se conservan mientras la cuenta esté activa más un plazo definido de eliminación. Evita almacenar libros completos indefinidamente. Permite exportar y eliminar datos del usuario, separa los datos personales de los datos públicos de mercado y cifra los tokens de Telegram almacenados.

## Límites de la IA y seguridad

- Evalúa proveedores con un conjunto fijo en español que incluya reglas ambiguas, unidades, referencias HIP-3, rechazos y explicaciones basadas en evidencia. Mide corrección de salidas estructuradas, cifras inventadas, latencia, coste y políticas de datos.
- Comienza con un proveedor de API si supera esa evaluación. Considera vLLM autoalojado solo cuando el coste medido, la privacidad o los requisitos operativos lo justifiquen; conserva la misma interfaz y pruebas.
- Genera explicaciones a partir de evidencia persistida. Recurre a una plantilla determinista si la salida de IA carece de respaldo, contradice los valores registrados o el proveedor no está disponible. Versiona los prompts y proveedores como metadatos de la explicación.
- Aplica la propiedad de los datos en todas las consultas de API y canales en directo; valida entradas, limita peticiones, utiliza HTTPS, minimiza datos personales, protege secretos y verifica copias de seguridad. El MVP no tiene credenciales de trading ni endpoints de ejecución.

## Validación y objetivos de rendimiento

Utiliza casos de muestra versionados y un reloj simulado para reproducir evaluaciones deterministas. Las comprobaciones de aceptación incluyen un evento por transición elegible, explicaciones acordes con la evidencia, bloqueo ante datos obligatorios antiguos, recuperación tras reconexión o reinicio, espera entre eventos y competencia entre workers. Incluye umbrales exactos, muestras de OI ausentes, diferencial cero, operaciones aisladas y marcas temporales que retroceden.

Mide p50/p95/p99 en `received_at → evaluated_at → committed_at → delivered_at`, y también `source_at → received_at` solo cuando las marcas temporales de origen sean comparables. Los objetivos iniciales, sujetos a una carga definida y pruebas de rendimiento, son:

- p95 de evaluación inferior a **100 ms** desde la recepción, con las reglas precargadas.
- p95 de persistencia y encolado inferior a **300 ms** bajo la carga acordada.
- Pruebas de ráfagas que informen de p95/p99 y tasas de pérdida o duplicación.

El tiempo de entrega de Telegram y la cadencia de publicación del mercado son factores externos. Mantén las llamadas SQL y de IA fuera de la evaluación de cada actualización sin renunciar a la persistencia de evidencia ni a la detección de interrupciones.

## Plan de desarrollo

| Fase | Trabajo | Criterio de salida |
| --- | --- | --- |
| **0 — Viabilidad** | Prototipo o conjunto pequeño de ingestión; verificar identidad de mercados, campos, cadencia, antigüedad, límites de API, permisos y observabilidad del estado del oráculo. | Matriz de viabilidad por mercado y decisión de continuar o detener, con comportamiento explícito `NO_VERIFICADO` cuando corresponda. |
| **1 — Núcleo determinista** | Esquema/migraciones, suscripciones, muestras, evaluación por ventanas, comprobaciones de calidad, reproducción, recuperación y eventos auditables. | Alertas reproducibles en consola con evidencia registrada, sin IA. |
| **2 — Beta del producto** | FastAPI, autenticación, interfaz React, reglas creadas por el agente, historial, Telegram/bandeja de salida, actualizaciones en directo y observabilidad. | Beta para un grupo pequeño con detección y entrega trazables. |
| **3 — Asistencia de IA** | Propuestas de reglas en lenguaje natural y explicaciones controladas; comparar proveedores con el conjunto fijo. | Reglas solicitadas por usuarios y explicaciones basadas en evidencia que superen la evaluación. |

El progreso depende de la calidad observada de los flujos, pruebas reproducibles de falsas alarmas y fallos, contexto verificado del oráculo o una alternativa explícita de estado desconocido y utilidad demostrada de alertas con motivos de cautela frente a alertas convencionales.

## Decisiones abiertas

- Red inicial, operador HIP-3 y conjunto de mercados.
- Fuente fiable del estado del oráculo y permisos de acceso; calendarios de sesión y reglas de zona horaria.
- Límites de antigüedad por campo, umbrales, política de calidad predeterminada y recuperación.
- Número objetivo de usuarios, reglas y mercados, y carga esperada de ráfagas.
- Conservación de datos, costes de alojamiento y notificaciones, y futuras licencias de datos bursátiles.
- Modelo de producción, política de datos del proveedor, límites de coste y peticiones, y comportamiento ante fallos.

## Documentación de referencia

Estos enlaces son los citados por el PDF de arquitectura. Sus contratos actuales y el comportamiento específico de cada proveedor deben verificarse durante la fase 0 y la implementación.

- Hyperliquid: [suscripciones WebSocket](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions), [ciclo de vida de WebSocket](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket), [endpoint de información de perpetuos](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) y [límites de API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).
- trade[XYZ]: [mecánica del precio del oráculo](https://docs.trade.xyz/perpetuals/mechanics/oracle-price).
- IA: [inicio rápido de OpenRouter](https://openrouter.ai/docs/quickstart), [transmisión de OpenRouter](https://openrouter.ai/docs/api/reference/streaming), [transcripción de OpenRouter](https://openrouter.ai/docs/guides/overview/multimodal/stt), [salidas estructuradas de LangChain](https://docs.langchain.com/oss/python/langchain/structured-output) y [salidas estructuradas de vLLM](https://docs.vllm.ai/en/stable/features/structured_outputs).
- Almacenamiento: [sesiones de SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) y [pgvector](https://github.com/pgvector/pgvector) como opción.
