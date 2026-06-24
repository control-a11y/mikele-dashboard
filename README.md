# 🍦 Mikele Gelato Dashboard

Dashboard inteligente para gestión de inventario, ventas y análisis de la Heladería Mikele.

## Tecnologías
- **Backend:** FastAPI + Python
- **Frontend:** HTML/CSS/JS con ApexCharts
- **Base de datos:** Supabase
- **Deploy:** Render.com

## Funcionalidades
- 📊 KPIs en tiempo real: ventas, inventario, margen
- 📁 Carga de archivos CSV de ventas
- 📈 Gráficas de tendencias y análisis por sabor
- 📧 Envío automático de reportes por email
- 🔧 Corrección de timezone UTC → Honduras

## Configuración local

1. Clonar el repositorio
2. Instalar dependencias:
```bash
pip install -r requirements.txt
```
3. Crear archivo `.env` con las credenciales (ver `.env.example`)
4. Correr el servidor:
```bash
python app.py
```
5. Abrir `http://127.0.0.1:8000`

## Variables de entorno requeridas

```
SUPABASE_URL=...
SUPABASE_KEY=...
EMAIL_USUARIO=...
EMAIL_PASSWORD=...
EMAIL_DESTINO=...
```
