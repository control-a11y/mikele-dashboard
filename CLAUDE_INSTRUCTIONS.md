# 🍦 HELADERÍA MIKELE — Instrucciones Completas para Agente IA (Claude)

> Este documento contiene todo lo necesario para que Claude u otro agente de IA pueda continuar el desarrollo y mantenimiento del sistema de reportes de Heladería Mikele, sin necesidad de historial previo de conversación.

---

## 📌 Contexto del Proyecto

**Heladería Mikele** es una heladería artesanal de gelato italiano ubicada en Honduras. Este proyecto es un **sistema automatizado de análisis y reportes diarios** que:

1. Lee archivos CSV de ventas exportados del sistema POS (punto de venta llamado "Block").
2. Consulta inventarios en Supabase (base de datos en la nube).
3. Calcula consumo de gelato por sabor, márgenes de venta y alertas de producción.
4. Genera un reporte HTML profesional y lo envía por correo a `control@yoops.hn`.

---

## 🗂 Estructura del Proyecto

```
MKL agents Antigravity/
├── app.py                        # Backend FastAPI — API REST principal
├── enviar_reporte.py             # Script alternativo para generar/enviar reportes local
├── instruccion_agente_mikele.md  # Instrucciones del agente (versión anterior)
├── CLAUDE_INSTRUCTIONS.md        # Este archivo
├── templates/
│   └── index.html                # Frontend web (dashboard visual)
├── requirements.txt              # Dependencias Python
├── Procfile                      # Para despliegue en Render.com
├── render.yaml                   # Configuración de Render.com
├── .env                          # Variables de entorno LOCALES (NO subir a GitHub)
├── .gitignore
└── README.md
```

---

## 🔐 Credenciales y Variables de Entorno

### Archivo `.env` (solo local, ya existe en el proyecto):
```
SUPABASE_URL=https://kuppijsfihzgjcxsyhin.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt1cHBpanNmaWh6Z2pjeHN5aGluIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEwMDk1NzUsImV4cCI6MjA5NjU4NTU3NX0.AhAJl-blQHg-YOAAbVttjWTUPf5GJtwJbtIDnUvtpMY
EMAIL_USUARIO=willa.ia26@gmail.com
EMAIL_PASSWORD=jezz eekl ubnl cblj
EMAIL_DESTINO=control@yoops.hn
```

### En Render.com (producción):
Las mismas 5 variables deben estar configuradas en:
**Render Dashboard → Tu Servicio → Environment**

---

## 🌐 Despliegue en Producción

- **URL pública:** https://mikele-dashboard.onrender.com
- **Plataforma:** Render.com (plan gratuito, puede dormir si hay inactividad)
- **Repositorio GitHub:** conectado automáticamente para auto-deploy
- **Endpoint de salud:** `GET /api/health` → verifica que las variables de entorno estén configuradas

---

## 🗄 Base de Datos: Supabase

**Proyecto:** `Mikele_DB`
**URL:** `https://kuppijsfihzgjcxsyhin.supabase.co`

### Tabla: `InventarioDiario`
Registra el inventario físico de gelato al cierre de cada día.
```
id              (int, PK)
fecha           (date)        → formato YYYY-MM-DD, en timezone Honduras (UTC-6)
sabor           (text)        → nombre del sabor de gelato
peso_final      (numeric)     → peso en GRAMOS al cierre del día
registrado_por  (text)        → quién hizo el registro
```

### Tabla: `Transferencias`
Registra movimientos de gelato entre laboratorio y tienda.
```
id              (int, PK)
producto        (text)        → nombre del sabor
transferencia   (numeric)     → gramos enviados desde laboratorio (NULL si no aplica)
recepcion       (numeric)     → gramos recibidos en tienda (NULL si no aplica)
estado          (text)        → estado de la transferencia
creado_por_rol  (text)        → "mikele" = tienda, "laboratorio" = lab
created_at      (timestamptz) → en UTC, restar 6h para Honduras
```

### Tabla: `Gelatos`
Catálogo de sabores activos.
```
id      (int, PK)
gelato  (text)   → nombre oficial del sabor
```

### ⚠️ IMPORTANTE: Timezone Honduras
Honduras está en **UTC-6** y NO cambia por horario de verano.
- El día HN comienza a las `06:00:00 UTC` (medianoche en Honduras)
- Al filtrar transferencias de un día HN (ej. 2026-06-24):
  - `inicio_utc = "2026-06-24T06:00:00Z"`
  - `fin_utc    = "2026-06-25T06:00:00Z"`

---

## 📊 Lógica del Reporte Diario

### Fuentes de datos:
1. **CSV de ventas** → exportado del sistema POS "Block"
   - Separador: `;` (punto y coma)
   - Encoding: latin-1 / windows-1252 / utf-8 (el código prueba varios)
   - Columnas: `Fecha` (DD/MM/YYYY), `Artículo`, `Uds.V`, `Neto`

2. **InventarioDiario** en Supabase → inventario del día del CSV y del día anterior

3. **Transferencias** en Supabase → recepciones de laboratorio en el rango HN del día

### Fórmula de consumo por sabor:
```
consumo = peso_anterior + recepcion_del_dia - peso_actual
```

### Precios de venta (Lempiras):
```python
Gelato Pequeño: L 105  (costo: L 17.02)
Gelato Mediano: L 135  (costo: L 27.69)
Gelato Grande:  L 165  (costo: L 36.92)
```

### Umbrales de alerta de producción (en gramos):
```
>= 2000g  → OK (stock suficiente)
< 2000g   → 🟠 BAJO (producir pronto)
< 500g    → 🔴 CRÍTICO (producir urgente)
= 0g      → 🔴 AGOTADO
```

---

## 🔌 API Endpoints

| Método | Ruta            | Descripción |
|--------|-----------------|-------------|
| GET    | `/`             | Sirve el frontend (index.html) |
| GET    | `/api/health`   | Diagnóstico — verifica variables de entorno |
| GET    | `/api/status`   | Estado actual: inventario, alertas y transferencias |
| POST   | `/api/process`  | Procesa CSV y genera reporte completo |
| POST   | `/api/send-email` | Envía reporte HTML por correo SMTP |
| POST   | `/api/fix-dates`  | Corrige registros con fecha errónea (timezone) |

---

## 📁 Formato del CSV de Ventas

Los gelatos vendidos que contienen en el nombre del artículo:
- `Gelato Pequeño`, `Gelato Mediano`, `Gelato Grande` → Se suman para calcular ingresos

Los sabores (ej. Pistacchio, Stracciatella, Blueberry...) aparecen como artículos con precio 0 (van incluidos en el precio del gelato por tamaño).

### Productos a IGNORAR en análisis de consumo de gelato:
```
AUA 500ML, San Benedetto G, San Benedetto P,
CAFE AMERICANO 8OZ (MIKELE), Cafe Americano 12oz, Cafe Latte 12oz,
Caffé Cortado, Caffé Latte 8oz, Cappuccino 12oz, Cappuccino 8oz,
Espresso, Doppio Espresso, Leche de Almendra, Leche deslactosada,
GALLETA CHISPAS DE CHOCOLATE (MIKELE), GALLETA DOBLE CHOCOLATE (MIKELE),
Limonata alla Fragola, Limonata alla Menta, Affogato, Afogato con alcohol,
Tisana 12oz, CHEESECAKE, Vaso Mikele, Cheesecake di fruto della pasione,
Cheesecake alla Fragola, Carpaccio di Manzo, LECHE ENTERA, LECHE DE AVENA,
LECHE DESCREMADA, LECHE DESLACTOSADA, Saborizante Amaretto, Saborizante Vainilla,
SPLENDA, TIBIO, BIEN CALIENTE, Pastel de Zanahoria, Cerveza Mikele
```

### Normalización de nombres:
```python
"Mandorla & Cocco Rafaello Pequeño" → "Mandorla & Cocco Rafaello"
```

---

## 🚀 Cómo Correr Localmente

```bash
cd "C:\Users\geisy\OneDrive\Desktop\MKL agents Antigravity"
pip install -r requirements.txt
python app.py
# Abrir: http://localhost:8000
```

---

## 🔄 Flujo de Uso Diario

```
1. Cierre del día: empleado registra inventario final → Supabase (InventarioDiario)
2. Exportar CSV de ventas del sistema POS Block
3. Abrir dashboard: https://mikele-dashboard.onrender.com
4. Subir/arrastrar el CSV al dashboard
5. El sistema calcula y muestra el reporte
6. Clic en "Enviar Reporte" → correo llega a control@yoops.hn
```

---

## ⚠️ Problema Conocido: Timezone Supabase

Si los registros de inventario aparecen con `fecha = hoy` siendo que se hicieron anoche:
→ Llamar a `POST /api/fix-dates`
→ Este endpoint mueve registros de `fecha_hoy` a `fecha_ayer` (en horario Honduras)

---

## 🐛 Troubleshooting

| Error | Causa | Solución |
|-------|-------|----------|
| `{"detail":"Not Found"}` | Render aún desplegando | Esperar 2-3 min |
| `No hay registros de inventario` | Timezone incorrecto o aún no registrado | Llamar `/api/fix-dates` o esperar |
| CSV no decodifica | Encoding especial | Guardar CSV como "UTF-8" desde Excel |
| Render tarda 30-60 seg | Plan gratuito, servidor dormido | Es normal, esperar la primera petición |

---

## 💡 Comandos Git para Desplegar Cambios

```bash
cd "C:\Users\geisy\OneDrive\Desktop\MKL agents Antigravity"
git add -A
git commit -m "descripción del cambio"
git push origin main
# Render.com hace auto-deploy automáticamente
```

---

## 🛠 Tareas Pendientes / Mejoras Posibles

- [ ] Mejorar loading state en frontend cuando Render está dormido
- [ ] Agregar gráfica de histórico de consumo por semana
- [ ] Validación automática de timezone antes de procesar CSV
- [ ] Agregar autenticación básica al dashboard
- [ ] Exportar reporte a PDF
- [ ] Historial de reportes enviados en el dashboard
- [ ] Notificación push cuando hay stock crítico

---

## 📞 Información del Proyecto

- **Negocio:** Heladería Mikele — Honduras
- **Email de reportes:** control@yoops.hn
- **Dashboard:** https://mikele-dashboard.onrender.com
- **Base de datos:** Supabase proyecto `Mikele_DB`
- **Moneda:** Lempiras (L)
- **Idioma del sistema:** Español

---

*Documento generado: 26/06/2026 — Sistema de Reportes Heladería Mikele*
