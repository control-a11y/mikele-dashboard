"""
============================================================
HELADERIA MIKELE - Script de Reporte Diario con Envio por Email
============================================================
Este script:
1. Se conecta a Supabase (Mikele_DB)
2. Consulta las tablas Gelatos, Inventario y Consumos
3. Carga el archivo de ventas CSV (TOP ARTICULOS)
4. Ejecuta los analisis de consumo, produccion, inventario y ventas
5. Detecta inconsistencias en los datos
6. Genera un reporte HTML y lo envia por correo electronico

INSTRUCCIONES DE USO:
---------------------
1. Instalar dependencias:
   pip install supabase pandas

2. Configurar:
   - SUPABASE_KEY con tu anon key de Supabase
   - RUTA_VENTAS_CSV con la ruta al archivo de ventas del dia

3. Ejecutar:
   python enviar_reporte.py

4. Para programar ejecucion diaria, usar Task Scheduler (Windows):
   schtasks /create /tn "ReporteMikele" /tr "python C:\\ruta\\enviar_reporte.py" /sc daily /st 22:30
============================================================
"""

import pandas as pd
import smtplib
import sys
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIGURACION
# ============================================================
from supabase import create_client, Client

SUPABASE_URL = "https://kuppijsfihzgjcxsyhin.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt1cHBpanNmaWh6Z2pjeHN5aGluIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEwMDk1NzUsImV4cCI6MjA5NjU4NTU3NX0.AhAJl-blQHg-YOAAbVttjWTUPf5GJtwJbtIDnUvtpMY"

# Email
EMAIL_USUARIO = "willa.ia26@gmail.com"
EMAIL_PASSWORD = "jezz eekl ubnl cblj"
EMAIL_DESTINO = "control@yoops.hn"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Archivo de ventas CSV
# Puedes pasar la ruta como argumento: python enviar_reporte.py "C:\ruta\ventas.csv"
# O definirla aqui directamente:
RUTA_VENTAS_CSV = r"C:\Users\geisy\Downloads\block6098 (5).csv"

# Precios y costos por tamano de gelato
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

# Umbrales
UMBRAL_PERDIDA = 30      # gramos - diferencia maxima aceptable en transferencia
UMBRAL_PRODUCCION = 2000  # gramos - debajo de esto se necesita producir


def conectar_supabase() -> Client:
    """Conecta con Supabase y retorna el cliente."""
    print("Conectando con Supabase (Mikele_DB)...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Conexion exitosa")
    return supabase


def obtener_datos(supabase: Client):
    """Descarga datos de las tablas principales.

    Tablas:
    - InventarioDiario: pesos diarios de cada sabor (fecha, sabor, peso_final)
    - Transferencias: transferencias produccion->tienda (producto, transferencia, recepcion)
    - Gelatos: catalogo de sabores
    """
    print("Descargando datos de Supabase...")

    res_consumos = supabase.table('InventarioDiario').select('*').execute()
    res_inventario = supabase.table('Transferencias').select('*').execute()
    res_gelatos = supabase.table('Gelatos').select('*').execute()

    df_consumos = pd.DataFrame(res_consumos.data)
    df_inventario = pd.DataFrame(res_inventario.data)
    df_gelatos = pd.DataFrame(res_gelatos.data)

    # Fallback: Si Gelatos esta vacia (puede pasar por RLS), extraer de InventarioDiario
    if df_gelatos.empty and not df_consumos.empty:
        unique_gelatos = df_consumos['sabor'].dropna().unique().tolist()
        df_gelatos = pd.DataFrame({'gelato': unique_gelatos})
        print(f"   Gelatos: {len(df_gelatos)} sabores (extraidos de InventarioDiario - fallback)")
    else:
        print(f"   Gelatos: {len(df_gelatos)} sabores en catalogo")

    print(f"   InventarioDiario: {len(df_consumos)} registros")
    print(f"   Transferencias: {len(df_inventario)} registros")

    return df_consumos, df_inventario, df_gelatos


def calcular_promedios_historicos(df_consumos, df_inventario):
    """Calcula promedios historicos de consumo excluyendo anomalias.

    Usa InventarioDiario (sabor, fecha, peso_final) + Transferencias (recepcion).
    Anomalias excluidas: consumo negativo o > 55,000g por dia.
    """
    print("Calculando promedios historicos...")
    UMBRAL_ANOMALIA_ALTO = 55000

    df_c = df_consumos.copy()
    df_c['fecha'] = pd.to_datetime(df_c['fecha'], format='mixed').dt.date
    df_c['peso_final'] = pd.to_numeric(df_c['peso_final'], errors='coerce').fillna(0)

    df_inv = df_inventario.copy()
    df_inv['created_at'] = pd.to_datetime(df_inv['created_at'], format='ISO8601')
    df_inv['recepcion'] = pd.to_numeric(df_inv['recepcion'], errors='coerce').fillna(0)
    df_inv['date_only'] = df_inv['created_at'].dt.date
    inv_daily = df_inv.groupby(['producto', 'date_only'])['recepcion'].sum().reset_index()

    # Un registro por sabor por dia (ultimo si hay duplicados)
    daily = df_c.sort_values('created_at').groupby(['sabor', 'fecha']).last().reset_index()

    # Calcular consumo dia a dia por sabor
    consumos_diarios = []
    for sabor in daily['sabor'].unique():
        art_data = daily[daily['sabor'] == sabor].sort_values('fecha')
        for i in range(1, len(art_data)):
            fecha = art_data.iloc[i]['fecha']
            peso_actual = art_data.iloc[i]['peso_final']
            peso_anterior = art_data.iloc[i-1]['peso_final']

            recep = inv_daily[
                (inv_daily['producto'] == sabor) &
                (inv_daily['date_only'] == fecha)
            ]['recepcion'].sum()

            consumo = peso_anterior + recep - peso_actual
            consumos_diarios.append({
                'fecha': fecha, 'articulo': sabor, 'consumo': consumo
            })

    df_consumo_diario = pd.DataFrame(consumos_diarios)

    if df_consumo_diario.empty:
        print("   No hay suficientes datos historicos")
        return {'consumo_kg_avg': 0, 'consumo_kg_dias': 0}

    # Consumo total por dia
    consumo_por_dia = df_consumo_diario.groupby('fecha')['consumo'].sum().reset_index()

    # Excluir anomalias
    consumo_limpio = consumo_por_dia[
        (consumo_por_dia['consumo'] >= 0) &
        (consumo_por_dia['consumo'] <= UMBRAL_ANOMALIA_ALTO)
    ]

    n_excluidos = len(consumo_por_dia) - len(consumo_limpio)
    consumo_avg_g = consumo_limpio['consumo'].mean() if not consumo_limpio.empty else 0
    consumo_avg_kg = consumo_avg_g / 1000
    n_dias = len(consumo_limpio)

    print(f"   {n_dias} dias validos, {n_excluidos} excluidos por anomalia")
    print(f"   Consumo promedio historico: {consumo_avg_kg:.1f} kg/dia")

    return {
        'consumo_kg_avg': consumo_avg_kg,
        'consumo_kg_dias': n_dias,
        'consumo_kg_min': consumo_limpio['consumo'].min() / 1000 if not consumo_limpio.empty else 0,
        'consumo_kg_max': consumo_limpio['consumo'].max() / 1000 if not consumo_limpio.empty else 0,
    }


def cargar_ventas(ruta_csv: str):
    """Carga y limpia el archivo de ventas CSV.

    Soporta dos formatos:
    1. DETALLADO (block6098): Una fila por linea de transaccion.
       Columnas: Fecha;Hora;Serie / Número;...;Uds.V;Artículo;...;Neto
       Los sabores tienen Neto=0, los tamanos (Pequeño/Mediano/Grande) tienen Neto>0.
    2. RESUMEN (TOP ARTICULOS): Una fila por articulo con totales.
       Columnas: ;Artículo;Subartículo;Uds.V;...;Venta;...

    Se auto-detecta el formato por la presencia de la columna 'Hora'.
    """
    print(f"Cargando archivo de ventas: {ruta_csv}")

    if not os.path.exists(ruta_csv):
        print(f"ERROR: No se encontro el archivo de ventas: {ruta_csv}")
        return None

    df_raw = pd.read_csv(ruta_csv, sep=';', encoding='utf-8')
    df_raw.columns = df_raw.columns.str.strip()

    # Renombrar Articulo con acento
    if 'Artículo' in df_raw.columns:
        df_raw = df_raw.rename(columns={'Artículo': 'Articulo'})

    # Normalizar nombres de articulos y mapear variantes del CSV
    df_raw['Articulo'] = df_raw['Articulo'].astype(str).str.strip()
    df_raw['Articulo'] = df_raw['Articulo'].replace("Mandorla & Cocco Rafaello Pequeño", "Mandorla & Cocco Rafaello")

    # --- Auto-detectar formato ---
    is_detailed = 'Hora' in df_raw.columns

    if is_detailed:
        print("   Formato detectado: DETALLADO (transaccion por transaccion)")

        # Eliminar fila de totales (ultima fila sin Fecha)
        df_raw['Fecha'] = df_raw['Fecha'].astype(str).str.strip()
        df_raw = df_raw[df_raw['Fecha'].notna() & (df_raw['Fecha'] != '') & (df_raw['Fecha'] != 'nan')]

        df_raw['Articulo'] = df_raw['Articulo'].astype(str).str.strip()
        df_raw['Uds.V'] = pd.to_numeric(df_raw['Uds.V'], errors='coerce').fillna(0)
        df_raw['Neto'] = pd.to_numeric(df_raw['Neto'], errors='coerce').fillna(0)

        # Agrupar por Articulo y sumar unidades
        df_ventas = df_raw.groupby('Articulo', as_index=False).agg(
            **{'Uds.V': ('Uds.V', 'sum'),
               'Venta': ('Neto', 'sum')}
        )

        # Filtrar articulos vacios
        df_ventas = df_ventas[df_ventas['Articulo'].notna() & (df_ventas['Articulo'] != 'nan')]

        print(f"   {len(df_raw)} lineas de transaccion procesadas")
        print(f"   {len(df_ventas)} articulos unicos agrupados")

    else:
        print("   Formato detectado: RESUMEN (TOP ARTICULOS)")
        df_raw['Articulo'] = df_raw['Articulo'].astype(str).str.strip()
        df_raw['Uds.V'] = pd.to_numeric(df_raw['Uds.V'], errors='coerce').fillna(0)

        if 'Venta' in df_raw.columns:
            df_raw['Venta'] = df_raw['Venta'].astype(str).str.replace(',', '').str.strip()
            df_raw['Venta'] = pd.to_numeric(df_raw['Venta'], errors='coerce').fillna(0)

        df_ventas = df_raw[df_raw['Articulo'].notna() & (df_raw['Articulo'] != 'nan')].copy()
        print(f"   {len(df_ventas)} articulos cargados")

    return df_ventas


def analizar_consumo(df_consumos, df_inventario):
    """
    Calcula el consumo diario por articulo usando InventarioDiario:
    consumo = peso_dia_anterior + recepcion_transferencias - peso_final_hoy

    Columnas de InventarioDiario: fecha (date), sabor (text), peso_final (numeric)
    Columnas de Transferencias: producto, recepcion, created_at

    Detecta articulos cuyo ultimo registro NO corresponde al dia
    mas reciente. Esos se marcan DESFASADOS y su consumo se invalida.
    """
    print("Analizando consumo diario (InventarioDiario)...")

    # Preparar InventarioDiario
    df_consumos['fecha'] = pd.to_datetime(df_consumos['fecha'], format='mixed').dt.date
    df_consumos['peso_final'] = pd.to_numeric(df_consumos['peso_final'], errors='coerce').fillna(0)

    # Preparar Transferencias
    df_inventario['created_at'] = pd.to_datetime(df_inventario['created_at'], format='ISO8601')
    df_inventario['recepcion'] = pd.to_numeric(df_inventario['recepcion'], errors='coerce').fillna(0)

    # Ordenar por sabor y fecha
    df_consumos = df_consumos.sort_values(['sabor', 'fecha'])

    # Fecha mas reciente global
    fecha_mas_reciente = df_consumos['fecha'].max()

    # Ultimo registro por sabor
    latest = df_consumos.groupby('sabor').tail(1).copy()
    latest = latest.rename(columns={
        'peso_final': 'peso_ultima_fecha',
        'fecha': 'date_only',
        'sabor': 'Articulo'
    })

    # Penultimo registro por sabor (dia anterior)
    previous = df_consumos.groupby('sabor').nth(-2).copy()
    previous = previous.reset_index()
    if 'sabor' in previous.columns:
        previous = previous.rename(columns={'sabor': 'Articulo'})
    previous = previous[['Articulo', 'peso_final']].rename(columns={'peso_final': 'peso_dia_anterior'})

    result = pd.merge(
        latest[['Articulo', 'peso_ultima_fecha', 'date_only']],
        previous, on='Articulo', how='left'
    )

    # Merge recepciones de Transferencias por sabor y fecha
    df_inventario['date_only'] = df_inventario['created_at'].dt.date

    result = pd.merge(
        result,
        df_inventario[['producto', 'recepcion', 'date_only']],
        left_on=['Articulo', 'date_only'],
        right_on=['producto', 'date_only'],
        how='left'
    ).rename(columns={'recepcion': 'latest_recepcion'})

    result['peso_dia_anterior'] = result['peso_dia_anterior'].fillna(0)
    result['latest_recepcion'] = result['latest_recepcion'].fillna(0)

    # ============================================================
    # DETECCION DE DESFASE
    # ============================================================
    result['fecha_reporte'] = fecha_mas_reciente
    result['desfasado'] = result['date_only'] < fecha_mas_reciente

    result['consumo_del_dia'] = (
        result['peso_dia_anterior']
        + result['latest_recepcion']
        - result['peso_ultima_fecha']
    )

    def determinar_estado(row):
        if row['desfasado']:
            dias = (row['fecha_reporte'] - row['date_only']).days
            return f"DESFASADO ({dias} dia(s) atras - dato NO confiable)"
        elif row['consumo_del_dia'] < 0:
            return "Inventario anterior no reportado"
        else:
            return "OK"

    result['Estado'] = result.apply(determinar_estado, axis=1)

    # Invalidar consumo de desfasados
    result.loc[result['desfasado'], 'consumo_del_dia'] = float('nan')

    n_desfasados = result['desfasado'].sum()
    n_ok = (~result['desfasado']).sum()
    if n_desfasados > 0:
        nombres = result.loc[result['desfasado'], 'Articulo'].tolist()
        print(f"   ALERTA: {n_desfasados} articulo(s) DESFASADO(S): {', '.join(nombres)}")
        print(f"   Sus datos NO son del dia {fecha_mas_reciente}, consumo EXCLUIDO de totales")
    print(f"   {n_ok} articulos con datos validos del {fecha_mas_reciente}")

    produccion = result[result['peso_ultima_fecha'] < UMBRAL_PRODUCCION].copy()

    return result, produccion


def analizar_inventario(df_inventario):
    """Analiza transferencias para detectar perdidas (tabla Transferencias)."""
    print("Analizando transferencias (transferencia vs recepcion)...")

    df_inventario['created_at'] = pd.to_datetime(df_inventario['created_at'], format='ISO8601')
    df_inventario['transferencia'] = pd.to_numeric(df_inventario['transferencia'], errors='coerce').fillna(0)
    df_inventario['recepcion'] = pd.to_numeric(df_inventario['recepcion'], errors='coerce').fillna(0)

    ultima_fecha = df_inventario['created_at'].max()
    reporte = df_inventario[df_inventario['created_at'].dt.date == ultima_fecha.date()].copy()

    reporte['diferencia'] = reporte['transferencia'] - reporte['recepcion']
    reporte['Perdida'] = reporte['diferencia'].apply(
        lambda x: f"Perdida > {UMBRAL_PERDIDA}g" if x > UMBRAL_PERDIDA else "OK"
    )
    reporte['fecha'] = reporte['created_at'].dt.date

    return reporte, ultima_fecha


def analizar_ventas(df_ventas, result):
    """
    Calcula consumo por unidad vendida, ventas en dinero, costos y margenes.
    """
    print("Analizando ventas y margenes...")

    # --- Consumo por Unidad Vendida ---
    merged_df = pd.merge(
        result[['Articulo', 'consumo_del_dia']],
        df_ventas[['Articulo', 'Uds.V']],
        on='Articulo',
        how='inner'
    )
    merged_df['consumo_del_dia'] = pd.to_numeric(merged_df['consumo_del_dia'], errors='coerce')
    merged_df['Uds.V'] = pd.to_numeric(merged_df['Uds.V'], errors='coerce')
    merged_df['Uds.V'] = merged_df['Uds.V'].replace(0, pd.NA)
    merged_df['consumo_por_unidad'] = merged_df['consumo_del_dia'] / merged_df['Uds.V']

    # --- Ventas y margenes por tamano ---
    gelato_sales = df_ventas[df_ventas['Articulo'].isin(GELATO_PRICES.keys())].copy()
    gelato_sales['Ventas_Dinero'] = gelato_sales.apply(
        lambda r: r['Uds.V'] * GELATO_PRICES.get(r['Articulo'], 0), axis=1
    )
    gelato_sales['Costo_Produccion'] = gelato_sales.apply(
        lambda r: r['Uds.V'] * GELATO_COSTS.get(r['Articulo'], 0), axis=1
    )

    total_sales = gelato_sales['Ventas_Dinero'].sum()
    total_costs = gelato_sales['Costo_Produccion'].sum()
    total_margin = total_sales - total_costs
    margin_pct = (total_margin / total_sales) * 100 if total_sales > 0 else 0

    total_consumo_g = merged_df['consumo_del_dia'].sum()
    total_consumo_kg = total_consumo_g / 1000
    avg_price_kg = total_sales / total_consumo_kg if total_consumo_kg > 0 else 0

    resumen = {
        'total_ventas': total_sales,
        'total_costos': total_costs,
        'total_margen': total_margin,
        'margen_pct': margin_pct,
        'total_consumo_kg': total_consumo_kg,
        'precio_promedio_kg': avg_price_kg,
        'total_unidades': int(gelato_sales['Uds.V'].sum())
    }

    print(f"   Ventas totales: L {total_sales:,.2f}")
    print(f"   Margen: {margin_pct:.1f}%")
    print(f"   Consumo por unidad vendida calculado para {len(merged_df)} sabores")

    return merged_df, gelato_sales, resumen


def detectar_inconsistencias(df_consumos, df_inventario, df_gelatos, result_consumo=None):
    """Detecta multiples tipos de inconsistencias usando InventarioDiario."""
    print("Detectando inconsistencias...")
    inconsistencias = []

    df_consumos['fecha'] = pd.to_datetime(df_consumos['fecha'], format='mixed').dt.date
    df_consumos['peso_final'] = pd.to_numeric(df_consumos['peso_final'], errors='coerce').fillna(0)
    df_inventario['created_at'] = pd.to_datetime(df_inventario['created_at'], format='ISO8601')

    # ============================================================
    # 0. CRITICA: Articulos DESFASADOS (dato de dia anterior)
    # ============================================================
    if result_consumo is not None and 'desfasado' in result_consumo.columns:
        desfasados = result_consumo[result_consumo['desfasado']].copy()
        if not desfasados.empty:
            fecha_esperada = result_consumo['fecha_reporte'].iloc[0]
            items = []
            for _, row in desfasados.iterrows():
                dias_atras = (fecha_esperada - row['date_only']).days
                items.append({
                    'articulo': row['Articulo'],
                    'ultimo_registro': str(row['date_only']),
                    'fecha_esperada': str(fecha_esperada),
                    'dias_atras': dias_atras,
                    'impacto': 'Consumo EXCLUIDO de totales'
                })
            inconsistencias.append({
                'tipo': f'Datos DESFASADOS - No registrados el {fecha_esperada}',
                'severidad': 'CRITICA',
                'emoji': '🚨',
                'descripcion': (
                    f'{len(desfasados)} articulo(s) NO tienen registro del dia {fecha_esperada}. '
                    f'Su consumo calculado pertenece a un dia anterior y ha sido EXCLUIDO '
                    f'de todos los totales para evitar datos incorrectos. '
                    f'Los totales de consumo, consumo/unidad vendida y precio/KG '
                    f'estan calculados SIN estos articulos.'
                ),
                'items': items
            })

    # 1. Recepcion NULL en Transferencias
    null_recepcion = df_inventario[df_inventario['recepcion'].isna()]
    if not null_recepcion.empty:
        items = []
        for _, row in null_recepcion.iterrows():
            items.append({
                'producto': row['producto'],
                'fecha': str(row['created_at'].date()),
                'transferencia': row['transferencia']
            })
        inconsistencias.append({
            'tipo': 'NULL en Recepcion de Transferencias',
            'severidad': 'ALTA',
            'emoji': '🔴',
            'descripcion': f'{len(null_recepcion)} registros con recepcion sin confirmar',
            'items': items
        })

    # 2. Gelatos sin reporte en ultima fecha
    ultima_fecha_consumo = df_consumos['fecha'].max()
    articulos_ultima = df_consumos[
        df_consumos['fecha'] == ultima_fecha_consumo
    ]['sabor'].unique()
    gelatos_faltantes = df_gelatos[
        ~df_gelatos['gelato'].isin(articulos_ultima)
    ]['gelato'].tolist()

    if gelatos_faltantes:
        inconsistencias.append({
            'tipo': f'Consumos faltantes ({ultima_fecha_consumo})',
            'severidad': 'MEDIA',
            'emoji': '🟡',
            'descripcion': f'{len(gelatos_faltantes)} gelatos sin reporte de inventario',
            'items': [{'gelato': g} for g in gelatos_faltantes]
        })

    # 3. Duplicados por sabor y fecha
    duplicados = df_consumos.groupby(['sabor', 'fecha']).size().reset_index(name='count')
    duplicados = duplicados[duplicados['count'] > 1]

    if not duplicados.empty:
        items = []
        for _, row in duplicados.iterrows():
            items.append({
                'articulo': row['sabor'],
                'fecha': str(row['fecha']),
                'registros': int(row['count'])
            })
        inconsistencias.append({
            'tipo': 'Registros Duplicados en InventarioDiario',
            'severidad': 'BAJA',
            'emoji': '🔵',
            'descripcion': f'{len(duplicados)} casos de registros duplicados',
            'items': items
        })

    # 4. Articulos en 0 consecutivo
    ultimas_3_fechas = sorted(df_consumos['fecha'].unique())[-3:]
    zeros_consecutivos = []

    for art in df_gelatos['gelato'].unique():
        art_data = df_consumos[
            (df_consumos['sabor'] == art) &
            (df_consumos['fecha'].isin(ultimas_3_fechas))
        ]
        if not art_data.empty and (art_data['peso_final'] == 0).all():
            zeros_consecutivos.append(art)

    if zeros_consecutivos:
        inconsistencias.append({
            'tipo': 'Articulos en 0 por multiples dias',
            'severidad': 'MEDIA',
            'emoji': '🟠',
            'descripcion': f'{len(zeros_consecutivos)} articulos con inventario 0 consecutivo',
            'items': [{'articulo': a} for a in zeros_consecutivos]
        })

    print(f"   Se encontraron {len(inconsistencias)} categorias de inconsistencias")
    return inconsistencias


def generar_html(result, produccion, inventario_reporte, ultima_fecha_inv,
                 inconsistencias, df_gelatos, merged_ventas, gelato_sales, resumen_ventas,
                 promedios_hist=None):
    """Genera el HTML completo del reporte incluyendo ventas."""
    print("Generando reporte HTML...")

    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Calcular fecha del reporte y dia de la semana en espanol
    dias_semana = {
        0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
        4: "Viernes", 5: "Sabado", 6: "Domingo"
    }
    fecha_rep = result['fecha_reporte'].iloc[0]
    if isinstance(fecha_rep, str):
        fecha_rep = pd.to_datetime(fecha_rep).date()
    dia_nombre = dias_semana[fecha_rep.weekday()]
    fecha_formateada = fecha_rep.strftime("%d/%m/%Y")

    html = f"""
    <html>
    <head>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; color: #333; padding: 20px; }}
        .container {{ max-width: 850px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 30px; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 13px; }}
        th {{ background: #3498db; color: white; padding: 10px 8px; text-align: left; }}
        td {{ padding: 8px; border-bottom: 1px solid #eee; }}
        tr:nth-child(even) {{ background: #f8f9fa; }}
        tr:hover {{ background: #e8f4f8; }}
        .alert {{ padding: 15px; border-radius: 8px; margin: 15px 0; }}
        .alert-danger {{ background: #fdecea; border-left: 4px solid #e74c3c; }}
        .alert-warning {{ background: #fef9e7; border-left: 4px solid #f39c12; }}
        .alert-info {{ background: #eaf4fc; border-left: 4px solid #3498db; }}
        .alert-success {{ background: #eafaf1; border-left: 4px solid #27ae60; }}
        .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
        .badge-red {{ background: #e74c3c; color: white; }}
        .badge-orange {{ background: #f39c12; color: white; }}
        .badge-green {{ background: #27ae60; color: white; }}
        .badge-blue {{ background: #3498db; color: white; }}
        .metric-grid {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }}
        .metric-card {{ flex: 1; min-width: 120px; color: white; padding: 18px; border-radius: 10px; text-align: center; }}
        .metric-card h3 {{ color: white; margin: 0; font-size: 24px; }}
        .metric-card p {{ margin: 5px 0 0; font-size: 11px; opacity: 0.9; }}
        .footer {{ text-align: center; margin-top: 30px; color: #95a5a6; font-size: 12px; border-top: 1px solid #eee; padding-top: 15px; }}
    </style>
    </head>
    <body>
    <div class="container">
        <h1>Reporte Diario - Heladeria Mikele</h1>
        <p><strong>Fecha del reporte:</strong> {fecha_formateada} ({dia_nombre})</p>
        <p><strong>Generado el:</strong> {ahora}</p>
        <p><strong>Base de datos:</strong> Mikele_DB (Supabase)</p>
    """

    # === METRICAS RESUMEN ===
    # Solo contar consumo de articulos validos (no desfasados)
    total_consumo_valido = result.loc[~result['desfasado'], 'consumo_del_dia'].sum()
    n_desfasados = result['desfasado'].sum()
    total_produccion = len(produccion)

    html += f"""
        <div class="metric-grid">
            <div class="metric-card" style="background: linear-gradient(135deg, #667eea, #764ba2);">
                <h3>{total_consumo_valido/1000:.1f} kg</h3>
                <p>Consumo Total (valido)</p>
            </div>
            <div class="metric-card" style="background: linear-gradient(135deg, #f093fb, #f5576c);">
                <h3>{total_produccion}</h3>
                <p>A Produccion</p>
            </div>
            <div class="metric-card" style="background: linear-gradient(135deg, #43e97b, #38f9d7);">
                <h3>L {resumen_ventas['total_ventas']:,.0f}</h3>
                <p>Ventas Totales</p>
            </div>
            <div class="metric-card" style="background: linear-gradient(135deg, #fa709a, #fee140);">
                <h3>{resumen_ventas['margen_pct']:.1f}%</h3>
                <p>Margen</p>
            </div>
            <div class="metric-card" style="background: linear-gradient(135deg, #4facfe, #00f2fe);">
                <h3>L {resumen_ventas['precio_promedio_kg']:,.0f}</h3>
                <p>Precio/KG</p>
            </div>
            <div class="metric-card" style="background: linear-gradient(135deg, #e74c3c, #c0392b);">
                <h3>{n_desfasados}</h3>
                <p>Desfasados</p>
            </div>
        </div>
    """

    # Alerta de desfase si existe
    if n_desfasados > 0:
        nombres_desf = result.loc[result['desfasado'], 'Articulo'].tolist()
        html += f"""
        <div class="alert alert-danger">
            <strong>🚨 ATENCION: {n_desfasados} articulo(s) con datos DESFASADOS</strong><br>
            <strong>{', '.join(nombres_desf)}</strong> no fueron registrados ayer.
            Su consumo ha sido <strong>EXCLUIDO</strong> de todos los calculos para evitar datos incorrectos.
            Los totales mostrados corresponden unicamente a los {len(result) - n_desfasados} articulos con datos validos.
        </div>
        """

    # === CONSUMO DIARIO ===
    html += "<h2>Consumo Diario</h2>"
    html += "<table><tr><th>Articulo</th><th>Peso Anterior (g)</th><th>Recepcion (g)</th><th>Peso Actual (g)</th><th>Consumo (g)</th><th>Estado</th></tr>"

    # Primero los validos (ordenados por consumo), luego los desfasados al final
    validos = result[~result['desfasado']].sort_values('consumo_del_dia', ascending=False)
    desfasados_df = result[result['desfasado']].sort_values('Articulo')

    for _, row in validos.iterrows():
        consumo = row['consumo_del_dia']
        badge = '<span class="badge badge-green">OK</span>' if consumo >= 0 else '<span class="badge badge-red">Error</span>'
        html += f"""<tr>
            <td><strong>{row['Articulo']}</strong></td>
            <td>{row['peso_dia_anterior']:,.0f}</td>
            <td>{row['latest_recepcion']:,.0f}</td>
            <td>{row['peso_ultima_fecha']:,.0f}</td>
            <td><strong>{consumo:,.0f}</strong></td>
            <td>{badge}</td>
        </tr>"""

    # Desfasados con estilo diferente
    for _, row in desfasados_df.iterrows():
        html += f"""<tr style="background: #fdecea; opacity: 0.8;">
            <td><strong>{row['Articulo']}</strong></td>
            <td colspan="3" style="text-align: center; color: #e74c3c;">⚠️ Ultimo registro: {row['date_only']} (no hay dato del {row['fecha_reporte']})</td>
            <td><strong style="color: #e74c3c;">EXCLUIDO</strong></td>
            <td><span class="badge badge-red">DESFASADO</span></td>
        </tr>"""

    html += "</table>"

    # === CONSUMO POR UNIDAD VENDIDA ===
    if merged_ventas is not None and not merged_ventas.empty:
        html += '<h2>Consumo por Unidad Vendida (g/ud)</h2>'
        html += '<div class="alert alert-info"><em>Gramos consumidos por cada unidad vendida de ese sabor. Valores altos pueden indicar desperdicio o porciones excesivas.</em></div>'
        html += "<table><tr><th>Articulo</th><th>Consumo Dia (g)</th><th>Uds. Vendidas</th><th>g/Unidad</th><th>Estado</th></tr>"

        for _, row in merged_ventas.sort_values('consumo_por_unidad', ascending=False).iterrows():
            cpu = row['consumo_por_unidad']
            if pd.isna(cpu):
                badge_cpu = '<span class="badge badge-orange">Sin datos</span>'
                cpu_str = "N/A"
            elif cpu > 200:
                badge_cpu = '<span class="badge badge-red">Alto</span>'
                cpu_str = f"{cpu:,.1f}"
            elif cpu > 150:
                badge_cpu = '<span class="badge badge-orange">Moderado</span>'
                cpu_str = f"{cpu:,.1f}"
            else:
                badge_cpu = '<span class="badge badge-green">Normal</span>'
                cpu_str = f"{cpu:,.1f}"

            html += f"""<tr>
                <td><strong>{row['Articulo']}</strong></td>
                <td>{row['consumo_del_dia']:,.0f}</td>
                <td>{row['Uds.V']:,.0f}</td>
                <td><strong>{cpu_str}</strong></td>
                <td>{badge_cpu}</td>
            </tr>"""
        html += "</table>"

    # === VENTAS POR TAMANO ===
    if gelato_sales is not None and not gelato_sales.empty:
        html += '<h2>Ventas y Margenes por Tamano</h2>'
        html += "<table><tr><th>Tamano</th><th>Uds. Vendidas</th><th>Ventas (L)</th><th>Costo (L)</th><th>Margen (L)</th></tr>"

        for _, row in gelato_sales.iterrows():
            margen = row['Ventas_Dinero'] - row['Costo_Produccion']
            html += f"""<tr>
                <td><strong>{row['Articulo']}</strong></td>
                <td>{row['Uds.V']:,.0f}</td>
                <td>L {row['Ventas_Dinero']:,.2f}</td>
                <td>L {row['Costo_Produccion']:,.2f}</td>
                <td><strong>L {margen:,.2f}</strong></td>
            </tr>"""

        # Fila total
        html += f"""<tr style="background: #2c3e50; color: white; font-weight: bold;">
            <td>TOTAL</td>
            <td>{gelato_sales['Uds.V'].sum():,.0f}</td>
            <td>L {resumen_ventas['total_ventas']:,.2f}</td>
            <td>L {resumen_ventas['total_costos']:,.2f}</td>
            <td>L {resumen_ventas['total_margen']:,.2f}</td>
        </tr>"""
        html += "</table>"

    # === TABLA RESUMEN FINANCIERO CON PROMEDIOS HISTORICOS ===
    ph = promedios_hist or {}
    consumo_hoy_kg = resumen_ventas['total_consumo_kg']
    consumo_avg_kg = ph.get('consumo_kg_avg', 0)
    n_dias_hist = ph.get('consumo_kg_dias', 0)
    pkg_hoy = resumen_ventas['precio_promedio_kg']

    # Funcion para evaluar cualitativamente
    def evaluar(metrica, valor):
        """Retorna (badge_html, texto) segun la metrica."""
        if metrica == 'margen_pct':
            if valor >= 82: return '<span class="badge badge-green">EXCELENTE</span>'
            elif valor >= 78: return '<span class="badge badge-green">BUENO</span>'
            elif valor >= 70: return '<span class="badge badge-orange">NORMAL</span>'
            else: return '<span class="badge badge-red">BAJO</span>'
        elif metrica == 'precio_kg':
            if valor == 0: return '<span class="badge badge-orange">Sin datos</span>'
            elif valor >= 650: return '<span class="badge badge-orange">ALTO</span>'
            elif valor >= 570: return '<span class="badge badge-green">OPTIMO</span>'
            elif valor >= 500: return '<span class="badge badge-green">NORMAL</span>'
            elif valor >= 400: return '<span class="badge badge-orange">BAJO</span>'
            else: return '<span class="badge badge-red">CRITICO</span>'
        elif metrica == 'consumo_kg':
            if consumo_avg_kg == 0: return '<span class="badge badge-orange">Sin hist.</span>'
            ratio = valor / consumo_avg_kg
            if ratio > 1.5: return '<span class="badge badge-orange">MUY ALTO</span>'
            elif ratio > 1.15: return '<span class="badge badge-blue">ALTO</span>'
            elif ratio >= 0.85: return '<span class="badge badge-green">NORMAL</span>'
            elif ratio >= 0.6: return '<span class="badge badge-orange">BAJO</span>'
            else: return '<span class="badge badge-red">MUY BAJO</span>'
        return ''

    # Variacion vs promedio
    def variacion_str(valor, promedio):
        if promedio == 0: return 'N/A'
        pct = ((valor / promedio) - 1) * 100
        arrow = '▲' if pct > 0 else '▼' if pct < 0 else '='
        color = '#27ae60' if abs(pct) <= 15 else '#e67e22' if abs(pct) <= 30 else '#e74c3c'
        return f'<span style="color:{color};font-weight:bold">{arrow} {pct:+.1f}%</span>'

    html += '<h2>Resumen Financiero</h2>'
    html += f'<div class="alert alert-info"><em>Promedios calculados con {n_dias_hist} dias de datos historicos (anomalias excluidas)</em></div>'
    html += """<table>
        <tr><th>Metrica</th><th>Valor Hoy</th><th>Promedio Hist.</th><th>vs Promedio</th><th>Evaluacion</th></tr>"""

    # Fila: Ventas
    html += f"""
        <tr>
             <td>Total Ventas en Dinero</td>
             <td><strong>L {resumen_ventas['total_ventas']:,.2f}</strong></td>
             <td style="color:#95a5a6">—</td>
             <td style="color:#95a5a6">—</td>
             <td style="color:#95a5a6">—</td>
         </tr>"""

    # Fila: Costo
    html += f"""
         <tr>
             <td>Total Costo de Produccion</td>
             <td>L {resumen_ventas['total_costos']:,.2f}</td>
             <td style="color:#95a5a6">—</td>
             <td style="color:#95a5a6">—</td>
             <td style="color:#95a5a6">—</td>
         </tr>"""

    # Fila: Margen Total
    html += f"""
         <tr>
             <td>Margen Total</td>
             <td><strong>L {resumen_ventas['total_margen']:,.2f}</strong></td>
            <td style="color:#95a5a6">—</td>
            <td style="color:#95a5a6">—</td>
            <td style="color:#95a5a6">—</td>
        </tr>"""

    # Fila: Margen %
    html += f"""
        <tr style="background: #eafaf1;">
            <td><strong>Margen Promedio</strong></td>
            <td><strong>{resumen_ventas['margen_pct']:.2f}%</strong></td>
            <td>~80%</td>
            <td>{variacion_str(resumen_ventas['margen_pct'], 80)}</td>
            <td>{evaluar('margen_pct', resumen_ventas['margen_pct'])}</td>
        </tr>"""

    # Fila: Consumo del dia
    consumo_avg_str = f"{consumo_avg_kg:.1f} kg" if consumo_avg_kg > 0 else "N/A"
    html += f"""
        <tr style="background: #eaf4fc;">
            <td><strong>Total Consumo del Dia</strong></td>
            <td><strong>{consumo_hoy_kg:.3f} kg</strong></td>
            <td>{consumo_avg_str}</td>
            <td>{variacion_str(consumo_hoy_kg, consumo_avg_kg)}</td>
            <td>{evaluar('consumo_kg', consumo_hoy_kg)}</td>
        </tr>"""

    # Fila: Precio/KG
    pkg_ref = 576  # promedio historico de referencia de los 3 dias con datos
    html += f"""
        <tr style="background: #fef9e7;">
            <td><strong>Precio Promedio por KG</strong></td>
            <td><strong>L {pkg_hoy:,.2f}</strong></td>
            <td>L {pkg_ref:,.0f}</td>
            <td>{variacion_str(pkg_hoy, pkg_ref)}</td>
            <td>{evaluar('precio_kg', pkg_hoy)}</td>
        </tr>"""

    html += "</table>"

    # === PRODUCCION ===
    if not produccion.empty:
        html += '<h2>Gelatos a Produccion (Peso menor a 2,000g)</h2>'
        html += '<div class="alert alert-danger">'
        html += "<table><tr><th>Articulo</th><th>Peso Actual (g)</th><th>Urgencia</th></tr>"
        for _, row in produccion.sort_values('peso_ultima_fecha').iterrows():
            peso = row['peso_ultima_fecha']
            if peso == 0:
                urgencia = '<span class="badge badge-red">AGOTADO</span>'
            elif peso < 500:
                urgencia = '<span class="badge badge-red">CRITICO</span>'
            else:
                urgencia = '<span class="badge badge-orange">BAJO</span>'
            html += f"<tr><td><strong>{row['Articulo']}</strong></td><td>{peso:,.0f}</td><td>{urgencia}</td></tr>"
        html += "</table></div>"

    # === INVENTARIO ===
    html += f'<h2>Inventario - Transferencias ({ultima_fecha_inv.date()})</h2>'
    if not inventario_reporte.empty:
        html += "<table><tr><th>Producto</th><th>Transferencia (g)</th><th>Recepcion (g)</th><th>Diferencia (g)</th><th>Estado</th></tr>"
        for _, row in inventario_reporte.sort_values('diferencia', ascending=False).iterrows():
            diff = row['diferencia']
            badge = '<span class="badge badge-red">Perdida</span>' if diff > UMBRAL_PERDIDA else '<span class="badge badge-green">OK</span>'
            html += f"""<tr>
                <td>{row['producto']}</td>
                <td>{row['transferencia']:,.0f}</td>
                <td>{row['recepcion']:,.0f}</td>
                <td>{diff:,.0f}</td>
                <td>{badge}</td>
            </tr>"""
        html += "</table>"

    # === INCONSISTENCIAS ===
    if inconsistencias:
        html += '<h2>Inconsistencias Detectadas</h2>'
        for inc in inconsistencias:
            sev_class = 'danger' if inc['severidad'] in ('ALTA', 'CRITICA') else ('warning' if inc['severidad'] == 'MEDIA' else 'info')
            html += f"""
                <div class="alert alert-{sev_class}">
                    <strong>{inc['emoji']} {inc['tipo']}</strong> - Severidad: {inc['severidad']}<br>
                    <em>{inc['descripcion']}</em><br><br>
            """
            if inc['items']:
                html += "<table>"
                headers = inc['items'][0].keys()
                html += "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
                for item in inc['items']:
                    html += "<tr>" + "".join(f"<td>{v}</td>" for v in item.values()) + "</tr>"
                html += "</table>"
            html += "</div>"

    # === FOOTER ===
    html += f"""
        <div class="footer">
            <p>Heladeria Mikele - Sistema de Control de Inventario y Ventas</p>
            <p>Reporte generado automaticamente el {ahora}</p>
            <p>Powered by Supabase + Python Analytics</p>
        </div>
    </div>
    </body>
    </html>
    """

    return html


def enviar_correo(html_content: str, fecha_str: str):
    """Envia el reporte HTML por correo electronico."""
    print(f"Enviando correo a {EMAIL_DESTINO}...")

    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_USUARIO
    msg['To'] = EMAIL_DESTINO
    msg['Subject'] = f"Reporte Diario Mikele - {fecha_str}"

    text_part = MIMEText(
        "Este correo contiene el reporte diario de la Heladeria Mikele. "
        "Por favor visualice este correo en un cliente que soporte HTML.",
        'plain', 'utf-8'
    )
    html_part = MIMEText(html_content, 'html', 'utf-8')

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
        print(f"Correo enviado exitosamente a {EMAIL_DESTINO}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("Error de autenticacion. Verifica la contrasena de aplicacion de Gmail.")
        return False
    except smtplib.SMTPRecipientsRefused:
        print(f"El destinatario {EMAIL_DESTINO} fue rechazado.")
        return False
    except Exception as e:
        print(f"Error al enviar correo: {e}")
        return False


def main():
    """Funcion principal que orquesta todo el flujo."""
    print("=" * 60)
    print("HELADERIA MIKELE - Generador de Reporte Diario")
    print("=" * 60)
    print()

    # Ruta del CSV (argumento o valor por defecto)
    ruta_csv = sys.argv[1] if len(sys.argv) > 1 else RUTA_VENTAS_CSV

    # 1. Conectar
    supabase = conectar_supabase()

    # 2. Obtener datos de Supabase
    df_consumos, df_inventario, df_gelatos = obtener_datos(supabase)

    # 3. Cargar ventas
    df_ventas = cargar_ventas(ruta_csv)

    # 4. Analisis de consumo
    result, produccion = analizar_consumo(df_consumos.copy(), df_inventario.copy())

    # 5. Analisis de inventario
    inventario_reporte, ultima_fecha_inv = analizar_inventario(df_inventario.copy())

    # 6. Analisis de ventas
    merged_ventas = None
    gelato_sales = None
    resumen_ventas = {
        'total_ventas': 0, 'total_costos': 0, 'total_margen': 0,
        'margen_pct': 0, 'total_consumo_kg': 0, 'precio_promedio_kg': 0,
        'total_unidades': 0
    }
    if df_ventas is not None:
        merged_ventas, gelato_sales, resumen_ventas = analizar_ventas(df_ventas, result)

    # 7. Calcular promedios historicos
    promedios_hist = calcular_promedios_historicos(
        df_consumos.copy(), df_inventario.copy()
    )

    # 8. Detectar inconsistencias (pasar result para detectar desfasados)
    inconsistencias = detectar_inconsistencias(
        df_consumos.copy(), df_inventario.copy(), df_gelatos.copy(),
        result_consumo=result
    )

    # 9. Generar HTML
    html = generar_html(
        result, produccion, inventario_reporte, ultima_fecha_inv,
        inconsistencias, df_gelatos, merged_ventas, gelato_sales, resumen_ventas,
        promedios_hist=promedios_hist
    )

    # Obtener fecha formateada del reporte para el asunto del correo
    fecha_rep = result['fecha_reporte'].iloc[0]
    if isinstance(fecha_rep, str):
        fecha_rep = pd.to_datetime(fecha_rep).date()
    fecha_formateada = fecha_rep.strftime("%d/%m/%Y")

    # 9. Enviar correo
    exito = enviar_correo(html, fecha_formateada)

    # 10. Resumen
    print()
    print("=" * 60)
    if exito:
        print("REPORTE GENERADO Y ENVIADO EXITOSAMENTE")
    else:
        print("REPORTE GENERADO PERO NO SE PUDO ENVIAR")
        nombre_archivo = f"reporte_mikele_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        with open(nombre_archivo, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"   Reporte guardado localmente: {nombre_archivo}")
    print("=" * 60)

    print("\n--- RESUMEN ---")
    print(f"Consumo total: {result['consumo_del_dia'].sum()/1000:.2f} kg")
    print(f"Gelatos a produccion: {len(produccion)}")
    print(f"Ventas totales: L {resumen_ventas['total_ventas']:,.2f}")
    print(f"Margen: {resumen_ventas['margen_pct']:.1f}%")
    print(f"Precio promedio/KG: L {resumen_ventas['precio_promedio_kg']:,.2f}")
    print(f"Inconsistencias: {len(inconsistencias)} categorias")
    print(f"Correo: {'Enviado' if exito else 'No enviado'}")


if __name__ == "__main__":
    main()
