# api_server.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import psycopg2
import psycopg2.extras
import os
import base64
import numpy as np
from dotenv import load_dotenv

# Carga las variables de entorno (como la DATABASE_URL)
load_dotenv()

# Inicializa la aplicación FastAPI
app = FastAPI(title="SIOMA API")

# --- Modelos de Datos (lo que la API espera recibir y enviar) ---
class OperarioLogin(BaseModel):
    usuario: str
    password: str

class AsistenciaRecord(BaseModel):
    cedula: str
    timestamp: str
    tipo_evento: str

# --- Función de Conexión a la Base de Datos ---
def get_db_connection():
    """Se conecta a la base de datos PostgreSQL usando la URL del entorno."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL no está configurada en el servidor.")
    try:
        conn = psycopg2.connect(db_url)
        return conn
    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Error de conexión con la base de datos: {e}")

# --- Endpoints (las URLs de tu API) ---

@app.get("/")
def root():
    return {"message": "API de SIOMA funcionando correctamente."}

@app.post("/login")
def login_operario(credenciales: OperarioLogin):
    """Valida un operario y devuelve sus datos, incluyendo la sede."""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, nombre, sede_id, rol, password_hash FROM OPERARIO WHERE usuario = %s", (credenciales.usuario,))
        operario = cur.fetchone()
    conn.close()

    if not operario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # Aquí deberías comparar la contraseña con bcrypt. Por ahora, asumimos que es correcta si el usuario existe.
    # Por ejemplo: if not bcrypt.checkpw(credenciales.password.encode(), operario['password_hash'].encode()):
    #     raise HTTPException(status_code=401, detail="Contraseña incorrecta.")

    return {
        "id": operario['id'],
        "nombre": operario['nombre'],
        "sede_id": operario['sede_id'],
        "rol": operario['rol']
    }

@app.get("/sede/{sede_id}/trabajadores")
def get_trabajadores_por_sede(sede_id: int):
    """Devuelve todos los trabajadores de una sede, incluyendo su embedding facial."""
    conn = get_db_connection()
    trabajadores_con_embedding = []
    
    # --- CONSULTA SQL CORREGIDA USANDO JOIN ---
    # Unimos TRABAJADOR (alias 't') con EMBEDDING_BIOMETRICO (alias 'eb')
    # para obtener los datos de ambas tablas en una sola consulta.
    sql_query = """
        SELECT 
            t.id, 
            t.cedula, 
            t.nombre_completo, 
            eb.embedding_data AS embedding  -- Seleccionamos la columna correcta y le ponemos un alias
        FROM 
            TRABAJADOR t
        JOIN 
            EMBEDDING_BIOMETRICO eb ON t.id = eb.trabajador_id
        WHERE 
            t.sede_id = %s AND t.activo = TRUE;
    """
    
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql_query, (sede_id,)) # <-- Ejecutamos la nueva consulta
        trabajadores = cur.fetchall()
        
        for trab in trabajadores:
            embedding_b64 = None
            # Ahora la columna se llama 'embedding' gracias al alias 'AS'
            if trab['embedding']:
                embedding_b64 = base64.b64encode(trab['embedding']).decode('utf-8')
            
            trabajadores_con_embedding.append({
                "id": trab['id'],
                "cedula": trab['cedula'],
                "nombre_completo": trab['nombre_completo'],
                "embedding_b64": embedding_b64
            })
            
    conn.close()
    return trabajadores_con_embedding


@app.post("/asistencia/sincronizar")
def sincronizar_asistencia(registros: List[AsistenciaRecord]):
    """Recibe una lista de registros de asistencia y los guarda en la base de datos."""
    conn = get_db_connection()
    guardados = 0
    errores = []
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        for reg in registros:
            try:
                cur.execute("SELECT id FROM TRABAJADOR WHERE cedula = %s", (reg.cedula,))
                trabajador = cur.fetchone()
                if trabajador:
                    trabajador_id = trabajador['id']
                    cur.execute(
                        "INSERT INTO REGISTRO_ASISTENCIA (trabajador_id, timestamp, tipo_evento) VALUES (%s, %s, %s)",
                        (trabajador_id, reg.timestamp, reg.tipo_evento)
                    )
                    guardados += 1
                else:
                    errores.append({"cedula": reg.cedula, "error": "Cédula no encontrada."})
            except Exception as e:
                conn.rollback() # Deshacer si hay un error
                errores.append({"cedula": reg.cedula, "error": str(e)})
    conn.commit()
    conn.close()
    return {
        "resumen": {"recibidos": len(registros), "guardados_exitosamente": guardados},
        "errores": errores
    }