import os
import sys
import io
import smtplib
import pandas as pd
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from pydantic import BaseModel

# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

app = FastAPI(title="Mikele Gelato Dashboard API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Credentials from environment variables ──────────────────────────────────
# Set these in Render.com dashboard (Environment > Secret Files / Env Vars)
# or in a local .env file for development.
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

EMAIL_USUARIO = os.getenv("EMAIL_USUARIO", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO", "control@yoops.hn")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

GELATO_PRICES = {
    'Gelato Pequeño': 105,
    'Gelato Mediano': 135,
    'Gelato Grande': 165
}
GELATO_COSTS = {
    'Gelato Pequeño': 17.02,
    'Gelato Mediano': 27.69,
    'Gelato Grande': 36.92
}

UMBRAL_PERDIDA = 30
UMBRAL_PRODUCCION = 2000

def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def read_sales_csv(contents: bytes):
    """Read CSV content robustly.
    Tries multiple encodings and common separators, prioritising semicolon which is used
    by the exported Block CSV. After loading, empty columns (e.g., the leading
    unnamed column caused by a leading ';') are dropped. If only one column remains,
    the function continues trying other separators.
    """
    last_error = None
    encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'windows-1252', 'cp1252']
    # Prioritise semicolon, then comma, tab, and finally let pandas infer.
    separators = [';', ',', '\t', None]
    for encoding in encodings:
        try:
            decoded = contents.decode(encoding)
        except UnicodeDecodeError as enc_err:
            last_error = str(enc_err)
            continue
        for separator in separators:
            try:
                df = pd.read_csv(io.StringIO(decoded), sep=separator, engine='python')
                # Remove completely empty columns (common with leading ';')
                df = df.dropna(axis=1, how='all')
                # If after dropping we still have more than one column, accept it.
                if df.shape[1] > 1:
                    df.columns = df.columns.str.strip()
                    return df
                # Otherwise keep trying other separators.
                last_error = f"Only one column detected with separator {separator!r}"
            except Exception as csv_err:
                last_error = str(csv_err)
    raise HTTPException(status_code=400, detail=f"No se pudo leer el CSV. Revisa que sea un archivo CSV valido exportado de Block. Último error: {last_error}")

def find_column(df, candidates):
    normalized = {
        str(column).strip().lower().replace('.', '').replace(' ', '').replace('_', ''): column
        for column in df.columns
    }
    for candidate in candidates:
        key = candidate.strip().lower().replace('.', '').replace(' ', '').replace('_', '')
        if key in normalized:
            return normalized[key]
    return None

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(
            status_code=503,
            detail="Variables de entorno SUPABASE_URL y SUPABASE_KEY no configuradas. Agrégalas en Render.com > Environment."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/api/health")
def health_check():
    """Endpoint de diagnóstico — muestra si las variables de entorno están configuradas."""
    return {
        "status": "running",
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_key_set": bool(SUPABASE_KEY),
        "email_usuario_set": bool(EMAIL_USUARIO),
        "email_password_set": bool(EMAIL_PASSWORD),
        "render_env": os.getenv("RENDER", "false")
    }

def get_honduras_dates():
    # Honduras is UTC-6
    hn_now = datetime.now(timezone.utc) - timedelta(hours=6)
    fecha_hoy = hn_now.strftime('%Y-%m-%d')
    fecha_ayer = (hn_now - timedelta(days=1)).strftime('%Y-%m-%d')
    return fecha_hoy, fecha_ayer

class EmailRequest(BaseModel):
    html: str
    fecha: str

@app.get("/")
def read_root():
    # Return templates/index.html if exists, otherwise a simple message
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return JSONResponse(content={"message": "Backend is running. Place index.html inside the templates folder."})

@app.get("/api/status")
def get_status():
    try:
        supabase = get_supabase_client()
        
        # 1. Fetch latest date in InventarioDiario
        res_latest = supabase.table('InventarioDiario').select('fecha').order('fecha', desc=True).limit(1).execute()
        if not res_latest.data:
            return {"error": "No records in InventarioDiario"}
        
        fecha_mas_reciente = res_latest.data[0]['fecha']
        
        # Fetch previous date
        res_prev = supabase.table('InventarioDiario').select('fecha').lt('fecha', fecha_mas_reciente).order('fecha', desc=True).limit(1).execute()
        fecha_anterior = res_prev.data[0]['fecha'] if res_prev.data else None
        
        # 2. Fetch inventories
        inv_actual = supabase.table('InventarioDiario').select('*').eq('fecha', fecha_mas_reciente).execute()
        df_act = pd.DataFrame(inv_actual.data)
        
        df_prev = pd.DataFrame()
        if fecha_anterior:
            inv_anterior = supabase.table('InventarioDiario').select('*').eq('fecha', fecha_anterior).execute()
            df_prev = pd.DataFrame(inv_anterior.data)
            
        # 3. Fetch transferences of the latest day (in HN time)
        res_trans = supabase.table('Transferencias').select('*').order('created_at', desc=True).limit(50).execute()
        df_trans = pd.DataFrame(res_trans.data)
        
        # Build inventory detail list
        inventory_list = []
        production_alerts = []
        
        for _, row in df_act.iterrows():
            sabor = row['sabor']
            peso = float(row['peso_final'])
            
            # Check previous weight
            peso_ant = None
            if not df_prev.empty:
                match_prev = df_prev[df_prev['sabor'] == sabor]
                if not match_prev.empty:
                    peso_ant = float(match_prev.iloc[0]['peso_final'])
            
            # Check production level
            estado_prod = "OK"
            if peso == 0:
                estado_prod = "AGOTADO"
                production_alerts.append({"sabor": sabor, "peso": peso, "estado": "🔴 AGOTADO"})
            elif peso < 500:
                estado_prod = "CRITICO"
                production_alerts.append({"sabor": sabor, "peso": peso, "estado": "🔴 CRÍTICO"})
            elif peso < UMBRAL_PRODUCCION:
                estado_prod = "BAJO"
                production_alerts.append({"sabor": sabor, "peso": peso, "estado": "🟠 BAJO"})
                
            inventory_list.append({
                "sabor": sabor,
                "peso_final": peso,
                "peso_anterior": peso_ant,
                "estado_produccion": estado_prod,
                "registrado_por": row.get('registrado_por', 'desconocido')
            })
            
        # Filter recent transferences for display
        transferences_list = []
        if not df_trans.empty:
            df_trans['created_at'] = pd.to_datetime(df_trans['created_at'], format="ISO8601")
            # Only show transferences of the last 3 days
            three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
            df_recent_trans = df_trans[df_trans['created_at'] >= three_days_ago]
            for _, row_t in df_recent_trans.iterrows():
                transferencia = safe_float(row_t.get('transferencia'), None)
                recepcion = safe_float(row_t.get('recepcion'), None)
                tipo = "Envío Lab" if transferencia is not None and transferencia != 0 else "Recepción Tienda"
                cant = transferencia if transferencia is not None else (recepcion if recepcion is not None else 0)
                fecha_t = row_t['created_at'].strftime("%d/%m %H:%M")
                transferences_list.append({
                    "id": row_t['id'],
                    "producto": row_t['producto'],
                    "tipo": tipo,
                    "cantidad": cant,
                    "estado": row_t['estado'],
                    "fecha": fecha_t,
                    "creado_por_rol": row_t['creado_por_rol']
                })
        
        # Check timezone offsets (if today has records already)
        fecha_hoy, fecha_ayer = get_honduras_dates()
        res_today = supabase.table('InventarioDiario').select('id').eq('fecha', fecha_hoy).execute()
        today_has_records = len(res_today.data) > 0
        
        return {
            "fecha_mas_reciente": fecha_mas_reciente,
            "fecha_anterior": fecha_anterior,
            "today_has_records": today_has_records,
            "fecha_hoy_hn": fecha_hoy,
            "fecha_ayer_hn": fecha_ayer,
            "inventory": inventory_list,
            "production_alerts": production_alerts,
            "transferences": transferences_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/process")
async def process_csv(file: UploadFile = File(...)):
    try:
        # Read uploaded CSV file — try multiple encodings (Windows CSV files often use Latin-1)
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="El archivo CSV esta vacio.")

        df_ventas_raw = read_sales_csv(contents)
        
        df_ventas_raw.columns = df_ventas_raw.columns.str.strip()

        fecha_col = find_column(df_ventas_raw, ['Fecha', 'Date'])
        articulo_col = find_column(df_ventas_raw, ['Articulo', 'Artículo', 'Art\u00edculo', 'Producto', 'Item'])
        unidades_col = find_column(df_ventas_raw, ['Uds.V', 'Uds V', 'UdsV', 'Unidades', 'Cantidad'])
        venta_col = find_column(df_ventas_raw, ['Venta', 'Neto', 'Total', 'Importe'])

        if not fecha_col:
            raise HTTPException(status_code=400, detail="El CSV no tiene la columna requerida: Fecha (o Date)")

        rename_map = {fecha_col: 'Fecha'}
        if articulo_col:
            rename_map[articulo_col] = 'Articulo'
        if unidades_col:
            rename_map[unidades_col] = 'Uds.V'
        if venta_col:
            rename_map[venta_col] = 'Venta'
            
        df_ventas_raw = df_ventas_raw.rename(columns=rename_map)

        is_summary = False
        if 'Articulo' not in df_ventas_raw.columns or 'Uds.V' not in df_ventas_raw.columns:
            is_summary = True
            if 'Articulo' not in df_ventas_raw.columns:
                df_ventas_raw['Articulo'] = 'Desconocido'
            if 'Uds.V' not in df_ventas_raw.columns:
                df_ventas_raw['Uds.V'] = 0
            
        # Get unique date from CSV
        parsed_dates = pd.to_datetime(df_ventas_raw['Fecha'], dayfirst=True, errors='coerce')
        unique_dates = parsed_dates.dropna().dt.strftime("%Y-%m-%d").unique()
        if len(unique_dates) == 0:
            raise HTTPException(status_code=400, detail="No se encontraron fechas válidas en el CSV.")
        
        csv_date = str(unique_dates[0])
        df_ventas_raw['_fecha_normalizada'] = parsed_dates.dt.strftime("%Y-%m-%d")
        
        # Connect to Supabase
        supabase = get_supabase_client()
        
        # Fetch actual and anterior weights
        # FECHA_ACTUAL is the CSV date
        FECHA_ACTUAL = csv_date
        
        # Find fecha anterior (the date less than FECHA_ACTUAL in the database)
        res_prev = supabase.table('InventarioDiario').select('fecha').lt('fecha', FECHA_ACTUAL).order('fecha', desc=True).limit(1).execute()
        if not res_prev.data:
            raise HTTPException(status_code=400, detail=f"No se encontró un registro de inventario anterior a la fecha del CSV: {FECHA_ACTUAL}.")
            
        FECHA_ANTERIOR = res_prev.data[0]['fecha']
        
        inv_actual = supabase.table('InventarioDiario').select('*').eq('fecha', FECHA_ACTUAL).execute()
        inv_anterior = supabase.table('InventarioDiario').select('*').eq('fecha', FECHA_ANTERIOR).execute()
        trans = supabase.table('Transferencias').select('*').execute()
        gelatos = supabase.table('Gelatos').select('*').execute()
        
        df_actual = pd.DataFrame(inv_actual.data)
        df_anterior = pd.DataFrame(inv_anterior.data)
        df_trans = pd.DataFrame(trans.data)
        catalogo = [g["gelato"] for g in gelatos.data]
        
        if df_actual.empty:
            raise HTTPException(status_code=400, detail=f"No hay registros de inventario final en Supabase para la fecha del reporte: {FECHA_ACTUAL}. Recuerda corregir el timezone si es necesario.")
            
        # Parse CSV sales
        df_ventas = df_ventas_raw[df_ventas_raw["_fecha_normalizada"] == FECHA_ACTUAL].copy()
        df_ventas['Articulo'] = df_ventas['Articulo'].astype(str).str.strip()
        
        # Apply name normalization for Mandorla
        df_ventas['Articulo'] = df_ventas['Articulo'].replace("Mandorla & Cocco Rafaello Pequeño", "Mandorla & Cocco Rafaello")
        df_ventas['Uds.V'] = pd.to_numeric(df_ventas['Uds.V'], errors='coerce').fillna(0)
        
        if 'Venta' in df_ventas.columns:
            df_ventas['Venta'] = df_ventas['Venta'].astype(str).str.replace(',', '', regex=False).str.strip()
            df_ventas['Venta'] = pd.to_numeric(df_ventas['Venta'], errors='coerce').fillna(0)
        else:
            df_ventas['Venta'] = 0.0
            
        # Filter transferences for the HN day interval (UTC-6)
        df_trans['created_at'] = pd.to_datetime(df_trans['created_at'], format="ISO8601")
        fecha_act_dt = datetime.strptime(FECHA_ACTUAL, "%Y-%m-%d")
        inicio_utc = f"{FECHA_ACTUAL}T06:00:00Z"
        fin_utc = f"{(fecha_act_dt + timedelta(days=1)).strftime('%Y-%m-%d')}T06:00:00Z"
        
        recep_intervalo = df_trans[
            (df_trans["created_at"] >= inicio_utc) &
            (df_trans["created_at"] < fin_utc) &
            (df_trans["recepcion"].notna()) &
            (df_trans["creado_por_rol"] == "mikele")
        ].copy()
        
        recep_intervalo["recepcion"] = pd.to_numeric(recep_intervalo["recepcion"], errors="coerce")
        recepciones = recep_intervalo.groupby("producto")["recepcion"].sum().to_dict()
        
        # Calculate daily consumption
        consumo_rows = []
        desfasados = []
        
        for _, row_act in df_actual.iterrows():
            sabor = row_act["sabor"]
            peso_act = float(row_act["peso_final"])
            
            # Find previous weight
            match_ant = df_anterior[df_anterior["sabor"] == sabor]
            if match_ant.empty:
                desfasados.append(sabor)
                continue
                
            peso_ant = float(match_ant.iloc[0]["peso_final"])
            recep = recepciones.get(sabor, 0)
            consumo = peso_ant + recep - peso_act
            
            consumo_rows.append({
                "sabor": sabor,
                "peso_anterior": peso_ant,
                "recepcion": recep,
                "peso_actual": peso_act,
                "consumo": consumo,
                "consumo_kg": round(consumo / 1000, 3)
            })
            
        df_consumo = pd.DataFrame(consumo_rows)
        valid_consumo = df_consumo[df_consumo["consumo"] >= 0]
        consumo_total_kg = valid_consumo["consumo_kg"].sum()
        
        # Gelatos to production
        produccion_rows = []
        for _, row in df_actual.iterrows():
            peso = float(row["peso_final"])
            sabor = row["sabor"]
            if peso == 0:
                estado = "🔴 AGOTADO"
            elif peso < 500:
                estado = "🔴 CRÍTICO"
            elif peso < UMBRAL_PRODUCCION:
                estado = "🟠 BAJO"
            else:
                continue
            produccion_rows.append({"sabor": sabor, "peso_g": peso, "estado": estado})
            
        # Sales and margins by size
        tamanos = ["Gelato Pequeño", "Gelato Mediano", "Gelato Grande"]
        ventas_tamano = {}
        for t in tamanos:
            mask = df_ventas["Articulo"] == t
            if mask.any():
                uds = df_ventas.loc[mask, "Uds.V"].sum()
                ventas_tamano[t] = int(uds)
            else:
                ventas_tamano[t] = 0
                
        total_ventas_dinero = 0
        total_costo = 0
        ventas_detail = []
        for t, uds in ventas_tamano.items():
            ingreso = uds * GELATO_PRICES[t]
            costo = uds * GELATO_COSTS[t]
            ventas_detail.append({
                "tamano": t,
                "unidades": uds,
                "ingreso": float(ingreso),
                "costo": float(round(costo, 2)),
                "margen": float(round(ingreso - costo, 2))
            })
            total_ventas_dinero += ingreso
            total_costo += costo
            
        # Fallback to total Venta (Neto) if detailed sizes are missing (Summary CSV)
        if total_ventas_dinero == 0 and is_summary and 'Venta' in df_ventas.columns:
            total_ventas_dinero = df_ventas['Venta'].sum()
            total_costo = total_ventas_dinero * 0.25  # 25% average cost estimation
            
        margen = total_ventas_dinero - total_costo
        margen_pct = (margen / total_ventas_dinero * 100) if total_ventas_dinero > 0 else 0
        precio_kg = (total_ventas_dinero / consumo_total_kg) if consumo_total_kg > 0 else 0
        
        # Portion weights
        sabores_csv = df_ventas[~df_ventas["Articulo"].isin(tamanos + [
            "AUA 500ML", "San Benedetto G", "San Benedetto P",
            "CAFE AMERICANO 8OZ (MIKELE)", "Cafe Americano 12oz (Mikele )", "Cafe Latte 12oz (Mikele)",
            "Caffé Cortado", "Caffé Latte 8oz", "Cappuccino 12oz", "Cappuccino 8oz", "Espresso",
            "Leche de Almendra", "Leche deslactosada", "Saborizante Amaretto", "Saborizante Vainilla",
            "SPLENDA", "TIBIO", "BIEN CALIENTE", "Leche de Avena", "Cerveza Mikele",
            "Pastel de Zanahoria", "GALLETA CHISPAS DE CHOCOLATE (MIKELE)", "GALLETA DOBLE CHOCOLATE  (MIKELE)",
            "Limonata alla Fragola", "Affogato", "Afogato con alcohol", "Tisana 12oz",
            "CHEESECAKE", "Vaso Mikele", "Cheesecake di fruto della pasione",
            "LECHE ENTERA", "LECHE DE AVENA", "LECHE DESCREMADA", "LECHE DESLACTOSADA"
        ])].copy()
        
        sabor_uds = sabores_csv.groupby("Articulo")["Uds.V"].sum().reset_index()
        sabor_uds.columns = ["sabor", "unidades"]
        
        peso_promedio_rows = []
        for _, row_s in sabor_uds.iterrows():
            sabor_csv = row_s["sabor"]
            uds = int(row_s["unidades"])
            if uds <= 0:
                continue
            match_consumo = df_consumo[df_consumo["sabor"] == sabor_csv]
            if not match_consumo.empty and match_consumo.iloc[0]["consumo"] > 0:
                consumo_sabor = match_consumo.iloc[0]["consumo"]
                peso_prom = consumo_sabor / uds
                
                estado_cpu = "OK"
                if peso_prom > 200:
                    estado_cpu = "Alto"
                elif peso_prom > 120:
                    estado_cpu = "Medio"
                
                peso_promedio_rows.append({
                    "sabor": sabor_csv,
                    "unidades": uds,
                    "consumo_g": float(consumo_sabor),
                    "peso_promedio_g": float(round(peso_prom, 1)),
                    "estado": estado_cpu
                })
                
        # Inconsistencies detection
        inconsistencias = []
        if desfasados:
            inconsistencias.append({
                "nivel": "🚨 CRÍTICA",
                "msg": f"Datos DESFASADOS: {', '.join(desfasados)} - sin registro del {FECHA_ANTERIOR}."
            })
            
        rec_null = df_trans[
            (df_trans["recepcion"].isna()) & 
            (df_trans["transferencia"].notna()) &
            (df_trans["created_at"] >= f"{FECHA_ANTERIOR}")
        ]
        if not rec_null.empty:
            prods = rec_null["producto"].unique().tolist()
            inconsistencias.append({
                "nivel": "🔴 ALTA",
                "msg": f"Recepción vacía (NULL) para transferencias de: {', '.join(prods)}."
            })
            
        sabores_actuales = set(df_actual["sabor"].tolist())
        faltantes = [g for g in catalogo if g not in sabores_actuales]
        if faltantes:
            inconsistencias.append({
                "nivel": "🟡 MEDIA",
                "msg": f"Consumos faltantes (sabor no reportado ayer): {', '.join(faltantes)}."
            })
            
        # Zeros consecutivos (last 3 dates)
        zeros_act = set(df_actual[df_actual["peso_final"].astype(float) == 0]["sabor"].tolist())
        if zeros_act:
            inconsistencias.append({
                "nivel": "🟠 MEDIA",
                "msg": f"Artículos en 0g al cierre: {', '.join(zeros_act)}."
            })
            
        negativos = df_consumo[df_consumo["consumo"] < 0]
        if not negativos.empty:
            neg_list = negativos["sabor"].tolist()
            inconsistencias.append({
                "nivel": "🔵 BAJA",
                "msg": f"Consumo negativo detectado (posible recepción de laboratorio omitida): {', '.join(neg_list)}."
            })
            
        # Historical stats (17 days)
        ph = {}
        df_c_all = supabase.table('InventarioDiario').select('*').execute()
        df_consumos_all = pd.DataFrame(df_c_all.data)
        if not df_consumos_all.empty:
            df_consumos_all['fecha'] = pd.to_datetime(df_consumos_all['fecha']).dt.date
            # Calculate daily total consumptions
            daily_total = []
            dates = sorted(df_consumos_all['fecha'].unique())
            for i in range(1, len(dates)):
                d_act = dates[i]
                d_prev = dates[i-1]
                
                # Fetch final weights
                act_w = df_consumos_all[df_consumos_all['fecha'] == d_act].set_index('sabor')['peso_final'].to_dict()
                prev_w = df_consumos_all[df_consumos_all['fecha'] == d_prev].set_index('sabor')['peso_final'].to_dict()
                
                # Fetch receptions in UTC HN range
                d_act_str = d_act.strftime('%Y-%m-%d')
                start_range = f"{d_act_str}T06:00:00Z"
                end_range = f"{(d_act + timedelta(days=1)).strftime('%Y-%m-%d')}T06:00:00Z"
                
                recep_val = df_trans[
                    (df_trans["created_at"] >= start_range) &
                    (df_trans["created_at"] < end_range) &
                    (df_trans["recepcion"].notna()) &
                    (df_trans["creado_por_rol"] == "mikele")
                ]
                recep_dict = recep_val.groupby("producto")["recepcion"].sum().to_dict()
                
                total_c = 0
                for s in act_w.keys():
                    if s in prev_w:
                        w_act = float(act_w[s])
                        w_prev = float(prev_w[s])
                        rec = float(recep_dict.get(s, 0))
                        total_c += (w_prev + rec - w_act)
                
                # Clean anomalies (0g to 55,000g)
                if 0 <= total_c <= 55000:
                    daily_total.append(total_c / 1000)
            
            if daily_total:
                ph['consumo_kg_avg'] = round(sum(daily_total) / len(daily_total), 1)
                ph['consumo_kg_dias'] = len(daily_total)
            else:
                ph['consumo_kg_avg'] = 29.4
                ph['consumo_kg_dias'] = 16
        else:
            ph['consumo_kg_avg'] = 29.4
            ph['consumo_kg_dias'] = 16
            
        # Get day of week in Spanish
        dias_semana = {
            0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
            4: "Viernes", 5: "Sabado", 6: "Domingo"
        }
        dia_nombre = dias_semana[fecha_act_dt.weekday()]
        
        # Build consumption detailed table response
        consumption_response = []
        for _, row_c in df_consumo.iterrows():
            s = row_c['sabor']
            uds_v = int(sabor_uds[sabor_uds['sabor'] == s]['unidades'].iloc[0]) if s in sabor_uds['sabor'].values else 0
            g_ud = round(row_c['consumo'] / uds_v, 1) if (row_c['consumo'] > 0 and uds_v > 0) else None
            
            status_cpu = "OK"
            if g_ud:
                if g_ud > 200: status_cpu = "Alto"
                elif g_ud > 120: status_cpu = "Medio"
                
            consumption_response.append({
                "sabor": s,
                "peso_anterior": float(row_c['peso_anterior']),
                "recepcion": float(row_c['recepcion']),
                "peso_actual": float(row_c['peso_actual']),
                "consumo": float(row_c['consumo']),
                "uds_vendidas": uds_v,
                "g_unidad": g_ud,
                "status_cpu": status_cpu
            })
            
        # Sort consumption by value descending
        consumption_response.sort(key=lambda x: x['consumo'], reverse=True)
        
        # Generate HTML report matching the revised enviar_reporte.py structure
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        html_content = f"""
        <html>
        <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; }}
            .container {{ max-width: 800px; margin: 0 auto; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); overflow: hidden; }}
            .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #fff; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; letter-spacing: 2px; }}
            .header p {{ margin: 5px 0 0; opacity: 0.8; font-size: 14px; }}
            .metrics {{ display: flex; justify-content: space-around; padding: 20px; background: #f8f9fa; }}
            .metric {{ text-align: center; padding: 10px; }}
            .metric .value {{ font-size: 28px; font-weight: bold; color: #1a1a2e; }}
            .metric .label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 1px; }}
            .section {{ padding: 20px 30px; }}
            .section h2 {{ color: #1a1a2e; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; font-size: 18px; }}
            table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
            th {{ background: #1a1a2e; color: #fff; padding: 10px 8px; text-align: left; }}
            td {{ padding: 8px; border-bottom: 1px solid #eee; }}
            tr:nth-child(even) {{ background: #f8f9fa; }}
            .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
            .badge-red {{ background: #ffe0e0; color: #c0392b; }}
            .badge-orange {{ background: #fff3e0; color: #e67e22; }}
            .badge-green {{ background: #e8f5e9; color: #27ae60; }}
            .badge-blue {{ background: #e3f2fd; color: #2196f3; }}
            .alert {{ padding: 10px 15px; border-radius: 8px; margin: 8px 0; font-size: 13px; }}
            .alert-critical {{ background: #fce4ec; border-left: 4px solid #c62828; }}
            .alert-high {{ background: #fff3e0; border-left: 4px solid #e65100; }}
            .alert-medium {{ background: #fff8e1; border-left: 4px solid #f9a825; }}
            .alert-low {{ background: #e3f2fd; border-left: 4px solid #1565c0; }}
            .footer {{ background: #f8f9fa; padding: 15px; text-align: center; font-size: 11px; color: #999; }}
        </style>
        </head>
        <body>
        <div class="container">
            <div class="header">
                <h1>🍦 HELADERÍA MIKELE</h1>
                <p>Reporte Diario — {datetime.strptime(FECHA_ACTUAL, "%Y-%m-%d").strftime("%d/%m/%Y")} ({dia_nombre})</p>
            </div>
            
            <div class="metrics">
                <div class="metric">
                    <div class="value">{consumo_total_kg:.2f} kg</div>
                    <div class="label">Consumo Total</div>
                </div>
                <div class="metric">
                    <div class="value">L {total_ventas_dinero:,.0f}</div>
                    <div class="label">Ventas Gelato</div>
                </div>
                <div class="metric">
                    <div class="value">{margen_pct:.1f}%</div>
                    <div class="label">Margen</div>
                </div>
                <div class="metric">
                    <div class="value">L {precio_kg:,.0f}</div>
                    <div class="label">Precio/KG</div>
                </div>
            </div>

            <div class="section">
                <h2>📊 Consumo por Sabor</h2>
                <table>
                    <tr><th>Sabor</th><th>Peso Anterior (g)</th><th>Recepción (g)</th><th>Peso Actual (g)</th><th>Consumo (g)</th></tr>
        """
        
        for item in consumption_response:
            color = "style='color:#c0392b;'" if item['consumo'] < 0 else ""
            html_content += f"<tr><td>{item['sabor']}</td><td>{item['peso_anterior']:.0f}</td><td>{item['recepcion']:.0f}</td><td>{item['peso_actual']:.0f}</td><td {color}><b>{item['consumo']:.0f}</b></td></tr>\n"
            
        html_content += f"""
                    <tr style="background:#1a1a2e;color:#fff;font-weight:bold;">
                        <td colspan="4">TOTAL</td>
                        <td>{df_consumo[df_consumo['consumo']>=0]['consumo'].sum():.0f}g ({consumo_total_kg:.2f} kg)</td>
                    </tr>
                </table>
            </div>

            <div class="section">
                <h2>💰 Ventas por Tamaño</h2>
                <table>
                    <tr><th>Tamaño</th><th>Unidades</th><th>Ingreso (L)</th><th>Costo (L)</th><th>Margen (L)</th></tr>
        """
        
        for v in ventas_detail:
            html_content += f"<tr><td>{v['tamano']}</td><td>{v['unidades']}</td><td>L {v['ingreso']:,.0f}</td><td>L {v['costo']:,.0f}</td><td>L {v['margen']:,.0f}</td></tr>\n"
            
        html_content += f"""
                    <tr style="background:#1a1a2e;color:#fff;font-weight:bold;">
                        <td>TOTAL</td><td>{sum(v['unidades'] for v in ventas_detail)}</td>
                        <td>L {total_ventas_dinero:,.0f}</td><td>L {total_costo:,.0f}</td><td>L {margen:,.0f}</td>
                    </tr>
                </table>
            </div>

            <div class="section">
                <h2>⚖️ Peso Promedio por Sabor Vendido</h2>
        """
        
        if peso_promedio_rows:
            html_content += "<table><tr><th>Sabor</th><th>Uds Vendidas</th><th>Consumo (g)</th><th>Peso Prom/Ud (g)</th></tr>\n"
            for pp in sorted(peso_promedio_rows, key=lambda x: x['peso_promedio_g'], reverse=True):
                badge_class = "badge-red" if pp['estado'] == "Alto" else ("badge-orange" if pp['estado'] == "Medio" else "badge-green")
                html_content += f"<tr><td>{pp['sabor']}</td><td>{pp['unidades']}</td><td>{pp['consumo_g']:.0f}</td><td><b>{pp['peso_promedio_g']:.0f}g</b> <span class='badge {badge_class}'>{pp['estado'].upper()}</span></td></tr>\n"
            
            prom_gral = df_consumo[df_consumo['sabor'].isin([p['sabor'] for p in peso_promedio_rows])]['consumo'].sum() / sum(p['unidades'] for p in peso_promedio_rows) if peso_promedio_rows else 0
            html_content += f"""<tr style="background:#1a1a2e;color:#fff;font-weight:bold;">
                <td>PROMEDIO GENERAL</td><td>{sum(p['unidades'] for p in peso_promedio_rows)}</td>
                <td>{df_consumo[df_consumo['sabor'].isin([p['sabor'] for p in peso_promedio_rows])]['consumo'].sum():.0f}</td><td>{prom_gral:.0f}g</td></tr></table>"""
        else:
            html_content += "<p>No hay datos suficientes para calcular</p>"
            
        html_content += """
            </div>

            <div class="section">
                <h2>🏭 Gelatos a Producción</h2>
        """
        
        if produccion_rows:
            html_content += "<table><tr><th>Sabor</th><th>Peso Actual (g)</th><th>Estado</th></tr>\n"
            for p in produccion_rows:
                badge_class = "badge-red" if "AGOTADO" in p["estado"] or "CRÍTICO" in p["estado"] else "badge-orange"
                html_content += f"<tr><td>{p['sabor']}</td><td>{p['peso_g']:.0f}</td><td><span class='badge {badge_class}'>{p['estado']}</span></td></tr>\n"
            html_content += "</table>"
        else:
            html_content += "<p>✅ Todos los gelatos tienen stock suficiente</p>"
            
        html_content += """
            </div>

            <div class="section">
                <h2>⚠️ Inconsistencias</h2>
        """
        
        if inconsistencias:
            for inc in inconsistencias:
                cls = "alert-critical" if "CRÍTICA" in inc['nivel'] else ("alert-high" if "ALTA" in inc['nivel'] else ("alert-medium" if "MEDIA" in inc['nivel'] else "alert-low"))
                html_content += f"<div class='alert {cls}'><b>{inc['nivel']}:</b> {inc['msg']}</div>\n"
        else:
            html_content += "<p>✅ Sin inconsistencias detectadas</p>"
            
        html_content += f"""
            </div>

            <div class="footer">
                Reporte generado automáticamente — Heladería Mikele Analytics<br>
                {now_str}
            </div>
        </div>
        </body>
        </html>
        """
        
        # Format dates for presentation
        fmt_date = datetime.strptime(FECHA_ACTUAL, "%Y-%m-%d").strftime("%d/%m/%Y")
        
        return {
            "fecha_reporte": FECHA_ACTUAL,
            "fecha_reporte_formateada": fmt_date,
            "dia_nombre": dia_nombre,
            "metrics": {
                "total_ventas": float(total_ventas_dinero),
                "total_costos": float(total_costo),
                "total_margen": float(margen),
                "margen_pct": float(round(margen_pct, 1)),
                "total_consumo_kg": float(round(consumo_total_kg, 2)),
                "precio_promedio_kg": float(round(precio_kg, 2)),
                "total_unidades": sum(ventas_tamano.values())
            },
            "consumption": consumption_response,
            "production": produccion_rows,
            "inconsistencias": inconsistencias,
            "sales_detail": ventas_detail,
            "portion_weights": peso_promedio_rows,
            "historical_stats": ph,
            "html_report": html_content
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/send-email")
def send_email_api(req: EmailRequest):
    print(f"Enviando correo a {EMAIL_DESTINO}...")
    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_USUARIO
    msg['To'] = EMAIL_DESTINO
    msg['Subject'] = f"Reporte Diario Mikele - {req.fecha}"
    
    text_part = MIMEText("Visualiza este correo en HTML.", 'plain', 'utf-8')
    html_part = MIMEText(req.html, 'html', 'utf-8')
    msg.attach(text_part)
    msg.attach(html_part)
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(EMAIL_USUARIO, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USUARIO, EMAIL_DESTINO, msg.as_string())
        server.quit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/fix-dates")
def fix_dates():
    try:
        supabase = get_supabase_client()
        fecha_hoy, fecha_ayer = get_honduras_dates()
        
        # Select records with date = today
        res = supabase.table('InventarioDiario').select('id, sabor, fecha').eq('fecha', fecha_hoy).execute()
        
        count = len(res.data)
        if count == 0:
            return {"success": True, "updated_count": 0, "message": f"No se encontraron registros para la fecha de hoy: {fecha_hoy}."}
            
        updated = 0
        for r in res.data:
            supabase.table('InventarioDiario').update({'fecha': fecha_ayer}).eq('id', r['id']).execute()
            updated += 1
            
        return {
            "success": True, 
            "updated_count": updated, 
            "message": f"Se corrigieron {updated} registros de la fecha '{fecha_hoy}' a la fecha de ayer '{fecha_ayer}'."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Running app — supports local dev and Render.com (uses PORT env var)
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    host = "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1"
    uvicorn.run(app, host=host, port=port)
