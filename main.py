import os
import io
import json
import struct
import zlib
import secrets
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor

import requests
from PIL import Image, ImageEnhance, ImageDraw, ImageChops, ImageFilter
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse


# ============================================================
# ZAFRIK IA PRO V7.8 BORDE FINAL
# Remove.bg + mejora de imagen + escalado + interfaz web
# Optimizado para Render Free 512 MB
# ============================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="6.7.0",
    description=(
        "Eliminación de fondo y preparación de imágenes "
        "para DTF, DTG y sublimación."
    )
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

MAX_FILE_MB = 15

# Máximo aproximado de píxeles de entrada.
# Protege la instancia de 512 MB.
MAX_INPUT_PIXELS = 16_000_000

# Máximo aproximado de píxeles de salida.
MAX_OUTPUT_PIXELS = 24_000_000



# ============================================================
# USUARIOS / 10 IMAGENES GRATIS / PREMIUM
# ============================================================

FREE_LIMIT = 10
JWT_SECRET = os.getenv("JWT_SECRET") or os.getenv("REMOVE_BG_API_KEY") or secrets.token_hex(32)
JWT_ALGORITHM = "HS256"

def db_connect():
    url=os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status_code=500,detail="DATABASE_URL no está configurada.")
    try:
        return psycopg2.connect(url,sslmode="require",connect_timeout=10)
    except Exception as e:
        print("DB ERROR:",repr(e),flush=True)
        raise HTTPException(status_code=503,detail="No se pudo conectar con la base de datos.")

def init_database():
    try:
        con=db_connect(); cur=con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS zafrik_ia_users(
          id SERIAL PRIMARY KEY,
          email VARCHAR(255) UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          plan VARCHAR(20) NOT NULL DEFAULT 'TRIAL',
          free_limit INTEGER NOT NULL DEFAULT 10,
          used_count INTEGER NOT NULL DEFAULT 0,
          is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS zafrik_ia_usage(
          id BIGSERIAL PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES zafrik_ia_users(id) ON DELETE CASCADE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        cur.execute("ALTER TABLE zafrik_ia_users ADD COLUMN IF NOT EXISTS premium_started_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE zafrik_ia_users ADD COLUMN IF NOT EXISTS premium_expires_at TIMESTAMPTZ")
        con.commit(); cur.close(); con.close()
        print("ZAFRIK IA DB LISTA",flush=True)
    except Exception as e:
        print("INIT DB ERROR:",repr(e),flush=True)

@app.on_event("startup")
def startup_database():
    init_database()

def clean_email(v):
    return str(v or "").strip().lower()

def iso_value(v):
    return v.isoformat() if v else None

def user_public(r):
    plan=str(r["plan"] or "TRIAL").upper()
    used=int(r["used_count"] or 0); limit=int(r["free_limit"] or FREE_LIMIT)
    started = r.get("premium_started_at") if hasattr(r, "get") else None
    expires = r.get("premium_expires_at") if hasattr(r, "get") else None
    created = r.get("created_at") if hasattr(r, "get") else None
    updated = r.get("updated_at") if hasattr(r, "get") else None
    return {"id":r["id"],"email":r["email"],"plan":plan,
            "premium":plan=="PREMIUM","used":used,"limit":limit,
            "remaining":None if plan=="PREMIUM" else max(0,limit-used),
            "active":bool(r["is_active"]),
            "created_at":iso_value(created),
            "updated_at":iso_value(updated),
            "premium_started_at":iso_value(started),
            "premium_expires_at":iso_value(expires)}

def expire_premium_for_user(uid=None):
    con=db_connect()
    try:
        cur=con.cursor()
        if uid is None:
            cur.execute("""
                UPDATE zafrik_ia_users
                SET plan='TRIAL', premium_started_at=NULL,
                    premium_expires_at=NULL, updated_at=NOW()
                WHERE UPPER(plan)='PREMIUM'
                  AND premium_expires_at IS NOT NULL
                  AND premium_expires_at <= NOW()
            """)
        else:
            cur.execute("""
                UPDATE zafrik_ia_users
                SET plan='TRIAL', premium_started_at=NULL,
                    premium_expires_at=NULL, updated_at=NOW()
                WHERE id=%s AND UPPER(plan)='PREMIUM'
                  AND premium_expires_at IS NOT NULL
                  AND premium_expires_at <= NOW()
            """,(uid,))
        con.commit()
    finally:
        con.close()

def make_token(uid,email):
    now=datetime.now(timezone.utc)
    return jwt.encode({"sub":str(uid),"email":email,"iat":now,
                       "exp":now+timedelta(days=30)},
                      JWT_SECRET,algorithm=JWT_ALGORITHM)

def auth_id(authorization):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401,detail="Debes iniciar sesión.")
    try:
        d=jwt.decode(authorization.split(" ",1)[1],JWT_SECRET,algorithms=[JWT_ALGORITHM])
        return int(d["sub"])
    except Exception:
        raise HTTPException(status_code=401,detail="Sesión vencida o inválida.")

def get_user(uid,lock=False):
    con=db_connect(); cur=con.cursor(cursor_factory=RealDictCursor)
    q="""SELECT id,email,password_hash,plan,free_limit,used_count,is_active,created_at,updated_at,premium_started_at,premium_expires_at
         FROM zafrik_ia_users WHERE id=%s"""
    if lock:q+=" FOR UPDATE"
    cur.execute(q,(uid,)); r=cur.fetchone()
    if not r:
        cur.close();con.close()
        raise HTTPException(status_code=401,detail="Usuario no encontrado.")
    if not r["is_active"]:
        cur.close();con.close()
        raise HTTPException(status_code=403,detail="Cuenta bloqueada.")
    return con,cur,r

@app.post("/api/auth/register")
def register(email:str=Form(...),password:str=Form(...)):
    email=clean_email(email)
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400,detail="Correo no válido.")
    if len(password)<6:
        raise HTTPException(status_code=400,detail="La contraseña debe tener al menos 6 caracteres.")
    ph=bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode()
    con=db_connect()
    try:
        cur=con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""INSERT INTO zafrik_ia_users(email,password_hash)
                       VALUES(%s,%s)
                       RETURNING id,email,plan,free_limit,used_count,is_active,created_at,updated_at,premium_started_at,premium_expires_at""",(email,ph))
        r=cur.fetchone();con.commit()
        return {"ok":True,"token":make_token(r["id"],r["email"]),"user":user_public(r)}
    except psycopg2.IntegrityError:
        con.rollback()
        raise HTTPException(status_code=409,detail="Ya existe una cuenta con este correo.")
    finally: con.close()

@app.post("/api/auth/login")
def login(email:str=Form(...),password:str=Form(...)):
    con=db_connect()
    try:
        cur=con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT id,email,password_hash,plan,free_limit,used_count,is_active,created_at,updated_at,premium_started_at,premium_expires_at
                       FROM zafrik_ia_users WHERE email=%s""",(clean_email(email),))
        r=cur.fetchone()
        if not r or not bcrypt.checkpw(password.encode(),r["password_hash"].encode()):
            raise HTTPException(status_code=401,detail="Correo o contraseña incorrectos.")
        if not r["is_active"]:
            raise HTTPException(status_code=403,detail="Cuenta bloqueada.")
        return {"ok":True,"token":make_token(r["id"],r["email"]),"user":user_public(r)}
    finally: con.close()

@app.get("/api/auth/me")
def me(authorization:str|None=Header(default=None)):
    uid=auth_id(authorization)
    expire_premium_for_user(uid)
    con,cur,r=get_user(uid)
    try:return {"ok":True,"user":user_public(r)}
    finally:cur.close();con.close()

def reserve_use(uid):
    expire_premium_for_user(uid)
    con,cur,r=get_user(uid,True)
    try:
        if str(r["plan"]).upper()=="PREMIUM":
            con.commit(); return False
        if int(r["used_count"])>=int(r["free_limit"]):
            con.rollback()
            raise HTTPException(status_code=402,detail="Has utilizado tus 10 imágenes gratuitas. Activa ZAFRIK PREMIUM para continuar.")
        cur.execute("UPDATE zafrik_ia_users SET used_count=used_count+1,updated_at=NOW() WHERE id=%s",(uid,))
        cur.execute("INSERT INTO zafrik_ia_usage(user_id) VALUES(%s)",(uid,))
        con.commit(); return True
    finally:cur.close();con.close()

def refund_use(uid):
    con=db_connect()
    try:
        cur=con.cursor()
        cur.execute("""UPDATE zafrik_ia_users SET used_count=GREATEST(used_count-1,0),updated_at=NOW()
                       WHERE id=%s AND UPPER(plan)<>'PREMIUM'""",(uid,))
        cur.execute("""DELETE FROM zafrik_ia_usage WHERE id=(
                       SELECT id FROM zafrik_ia_usage WHERE user_id=%s ORDER BY id DESC LIMIT 1)""",(uid,))
        con.commit()
    except Exception as e:
        con.rollback();print("REFUND ERROR:",repr(e),flush=True)
    finally:con.close()


# ============================================================
# ADMINISTRACION ZAFRIK IA PRO
# Protegida con ADMIN_SECRET configurado en Render.
# ============================================================

def require_admin(x_admin_secret: str | None):
    configured = os.getenv("ADMIN_SECRET")

    if not configured:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_SECRET no está configurada en Render."
        )

    supplied = str(x_admin_secret or "")

    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(
            status_code=401,
            detail="Clave de administrador incorrecta."
        )


@app.get("/api/admin/users")
def admin_users(
    q: str = "",
    limit: int = 50,
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)
    expire_premium_for_user()

    q = clean_email(q)
    limit = max(1, min(int(limit or 50), 100))

    con = db_connect()

    try:
        cur = con.cursor(cursor_factory=RealDictCursor)

        if q:
            cur.execute("""
                SELECT id,email,plan,free_limit,used_count,is_active,
                       created_at,updated_at,premium_started_at,premium_expires_at
                FROM zafrik_ia_users
                WHERE LOWER(email) LIKE %s
                ORDER BY id DESC
                LIMIT %s
            """, (f"%{q}%", limit))
        else:
            cur.execute("""
                SELECT id,email,plan,free_limit,used_count,is_active,
                       created_at,updated_at,premium_started_at,premium_expires_at
                FROM zafrik_ia_users
                ORDER BY id DESC
                LIMIT %s
            """, (limit,))

        rows = cur.fetchall()

        return {
            "ok": True,
            "count": len(rows),
            "users": [user_public(row) for row in rows]
        }

    finally:
        con.close()


@app.get("/api/admin/user")
def admin_user(
    email: str,
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)
    email = clean_email(email)

    con = db_connect()

    try:
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id,email,plan,free_limit,used_count,is_active,
                   created_at,updated_at
            FROM zafrik_ia_users
            WHERE email=%s
        """, (email,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )

        return {"ok": True, "user": user_public(row)}

    finally:
        con.close()


@app.post("/api/admin/user/plan")
def admin_set_plan(
    email: str = Form(...),
    plan: str = Form(...),
    months: int = Form(1),
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)

    email = clean_email(email)
    plan = str(plan or "").strip().upper()

    if plan not in ("TRIAL", "PREMIUM"):
        raise HTTPException(status_code=400,detail="El plan debe ser TRIAL o PREMIUM.")

    if plan == "PREMIUM" and months not in (1, 6, 12):
        raise HTTPException(status_code=400,detail="Premium debe ser de 1, 6 o 12 meses.")

    con = db_connect()
    try:
        cur = con.cursor(cursor_factory=RealDictCursor)

        if plan == "PREMIUM":
            cur.execute("""
                UPDATE zafrik_ia_users
                SET plan='PREMIUM',
                    premium_started_at=NOW(),
                    premium_expires_at=NOW() + (%s * INTERVAL '1 month'),
                    updated_at=NOW()
                WHERE email=%s
                RETURNING id,email,plan,free_limit,used_count,is_active,
                          created_at,updated_at,premium_started_at,premium_expires_at
            """, (months, email))
        else:
            cur.execute("""
                UPDATE zafrik_ia_users
                SET plan='TRIAL',
                    premium_started_at=NULL,
                    premium_expires_at=NULL,
                    updated_at=NOW()
                WHERE email=%s
                RETURNING id,email,plan,free_limit,used_count,is_active,
                          created_at,updated_at,premium_started_at,premium_expires_at
            """, (email,))

        row = cur.fetchone()
        if not row:
            con.rollback()
            raise HTTPException(status_code=404,detail="Usuario no encontrado.")

        con.commit()
        return {
            "ok": True,
            "message": f"Premium activado por {months} mes(es)." if plan=="PREMIUM" else "Cuenta devuelta a prueba.",
            "user": user_public(row)
        }
    finally:
        con.close()


@app.post("/api/admin/user/renew")
def admin_renew_premium(
    email: str = Form(...),
    months: int = Form(...),
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)

    email = clean_email(email)
    if months not in (1, 6, 12):
        raise HTTPException(status_code=400,detail="La renovación debe ser de 1, 6 o 12 meses.")

    con=db_connect()
    try:
        cur=con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE zafrik_ia_users
            SET plan='PREMIUM',
                premium_started_at=COALESCE(premium_started_at,NOW()),
                premium_expires_at=
                    GREATEST(COALESCE(premium_expires_at,NOW()),NOW())
                    + (%s * INTERVAL '1 month'),
                updated_at=NOW()
            WHERE email=%s
            RETURNING id,email,plan,free_limit,used_count,is_active,
                      created_at,updated_at,premium_started_at,premium_expires_at
        """,(months,email))
        row=cur.fetchone()
        if not row:
            con.rollback()
            raise HTTPException(status_code=404,detail="Usuario no encontrado.")
        con.commit()
        return {"ok":True,
                "message":f"Premium renovado por {months} mes(es).",
                "user":user_public(row)}
    finally:
        con.close()


@app.post("/api/admin/user/reset")
def admin_reset_uses(
    email: str = Form(...),
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)
    email = clean_email(email)

    con = db_connect()

    try:
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE zafrik_ia_users
            SET used_count=0, updated_at=NOW()
            WHERE email=%s
            RETURNING id,email,plan,free_limit,used_count,is_active,created_at,updated_at,premium_started_at,premium_expires_at
        """, (email,))
        row = cur.fetchone()

        if not row:
            con.rollback()
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )

        cur.execute("""
            DELETE FROM zafrik_ia_usage
            WHERE user_id=%s
        """, (row["id"],))

        con.commit()

        return {
            "ok": True,
            "message": "Los usos gratuitos fueron restablecidos.",
            "user": user_public(row)
        }

    finally:
        con.close()


@app.post("/api/admin/user/active")
def admin_set_active(
    email: str = Form(...),
    active: str = Form(...),
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)
    email = clean_email(email)

    value = str(active or "").strip().lower()
    if value not in ("true","false","1","0","yes","no","on","off"):
        raise HTTPException(status_code=400, detail="Valor active inválido.")

    is_active = value in ("true","1","yes","on")

    con = db_connect()
    try:
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE zafrik_ia_users
            SET is_active=%s, updated_at=NOW()
            WHERE email=%s
            RETURNING id,email,plan,free_limit,used_count,is_active,
                      created_at,updated_at,premium_started_at,premium_expires_at
        """, (is_active, email))
        row = cur.fetchone()

        if not row:
            con.rollback()
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")

        # Al bloquear, cerrar todas las sesiones activas inmediatamente.
        if not is_active:
            cur.execute("DELETE FROM zafrik_ia_sessions WHERE user_id=%s", (row["id"],))

        con.commit()

        return {
            "ok": True,
            "message": "Cuenta desbloqueada." if is_active else "Cuenta bloqueada y sesiones cerradas.",
            "user": user_public(row)
        }
    finally:
        con.close()


@app.post("/api/admin/user/delete")
def admin_delete_user(
    email: str = Form(...),
    confirm_email: str = Form(...),
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)

    email = clean_email(email)
    confirm_email = clean_email(confirm_email)

    if not email or email != confirm_email:
        raise HTTPException(
            status_code=400,
            detail="La confirmación del correo no coincide."
        )

    con = db_connect()
    try:
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            DELETE FROM zafrik_ia_users
            WHERE email=%s
            RETURNING id,email
        """, (email,))
        row = cur.fetchone()

        if not row:
            con.rollback()
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")

        # sessions y usage se borran por ON DELETE CASCADE.
        con.commit()

        return {
            "ok": True,
            "message": f"Usuario {row['email']} eliminado permanentemente."
        }
    finally:
        con.close()


@app.get("/api/admin/stats")
def admin_stats(
    x_admin_secret: str | None = Header(default=None)
):
    require_admin(x_admin_secret)
    expire_premium_for_user()

    con = db_connect()

    try:
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT
              COUNT(*)::int AS total,
              COUNT(*) FILTER (WHERE UPPER(plan)='PREMIUM')::int AS premium,
              COUNT(*) FILTER (WHERE UPPER(plan)<>'PREMIUM')::int AS trial,
              COUNT(*) FILTER (WHERE is_active=FALSE)::int AS blocked,
              COALESCE(SUM(used_count),0)::int AS processed
            FROM zafrik_ia_users
        """)
        row = cur.fetchone()

        return {"ok": True, "stats": dict(row)}

    finally:
        con.close()


# ============================================================
# PANEL ADMINISTRADOR
# MUESTRA public/admin.html
# ============================================================

@app.get("/admin.html", include_in_schema=False)
def admin_page():

    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    admin_path = os.path.join(
        base_dir,
        "public",
        "admin.html"
    )

    if not os.path.exists(admin_path):

        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "app": "ZAFRIK IA PRO",
                "version": "7.8.0",
                "message": "No se encontró public/admin.html"
            }
        )

    return FileResponse(
        admin_path,
        media_type="text/html"
    )


# ============================================================
# RUTA PRINCIPAL
# MUESTRA public/index.html
# ============================================================

@app.get("/")
def home():

    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    index_path = os.path.join(
        base_dir,
        "public",
        "index.html"
    )

    if not os.path.exists(index_path):

        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "app": "ZAFRIK IA PRO",
                "version": "7.8.0",
                "message": "No se encontró public/index.html"
            }
        )

    return FileResponse(
        index_path,
        media_type="text/html"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True,
        "status": "healthy",
        "app": "ZAFRIK IA PRO",
        "version": "7.8.0"
    }


# ============================================================
# ESTADO DEL SERVIDOR
# ============================================================

@app.get("/api/status")
def api_status():

    api_key = os.getenv("REMOVE_BG_API_KEY")

    return {
        "ok": True,
        "server": "online",
        "remove_bg_configured": bool(api_key),
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "auth_enabled": True,
        "free_limit": FREE_LIMIT,
        "version": "7.8.0"
    }


# ============================================================
# VALIDAR IMAGEN
# ============================================================

def validate_image(data: bytes):

    if not data:

        raise HTTPException(
            status_code=400,
            detail="No se recibió ninguna imagen."
        )

    size_mb = len(data) / (1024 * 1024)

    if size_mb > MAX_FILE_MB:

        raise HTTPException(
            status_code=413,
            detail=(
                f"La imagen supera el límite "
                f"de {MAX_FILE_MB} MB."
            )
        )

    try:

        image = Image.open(
            io.BytesIO(data)
        )

        image.load()

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="El archivo seleccionado no es una imagen válida."
        )

    width, height = image.size

    if width <= 0 or height <= 0:

        raise HTTPException(
            status_code=400,
            detail="La imagen tiene dimensiones inválidas."
        )

    pixels = width * height

    if pixels > MAX_INPUT_PIXELS:

        raise HTTPException(
            status_code=413,
            detail=(
                "La imagen es demasiado grande para este servidor. "
                "Reduce sus dimensiones e inténtalo nuevamente."
            )
        )

    return width, height


# ============================================================
# REMOVE.BG
# ============================================================

def remove_background(data: bytes):

    api_key = os.getenv("REMOVE_BG_API_KEY")

    if not api_key:

        raise HTTPException(
            status_code=500,
            detail=(
                "REMOVE_BG_API_KEY no está configurada "
                "en Render."
            )
        )

    try:

        response = requests.post(
            "https://api.remove.bg/v1.0/removebg",

            files={
                "image_file": (
                    "zafrik-image.png",
                    data,
                    "application/octet-stream"
                )
            },

            data={
                "size": "auto",
                "format": "png"
            },

            headers={
                "X-Api-Key": api_key
            },

            timeout=90
        )

    except requests.Timeout:

        raise HTTPException(
            status_code=504,
            detail=(
                "El motor de eliminación de fondo "
                "tardó demasiado en responder."
            )
        )

    except requests.RequestException as error:

        print(
            "ERROR REMOVE.BG:",
            repr(error),
            flush=True
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo conectar con el motor "
                "de eliminación de fondo."
            )
        )

    if response.status_code != 200:

        message = (
            "Error del motor de eliminación de fondo."
        )

        try:

            error_data = response.json()

            errors = error_data.get(
                "errors",
                []
            )

            if errors:

                message = errors[0].get(
                    "title",
                    message
                )

        except Exception:

            if response.text:

                message = response.text[:300]

        raise HTTPException(
            status_code=response.status_code,
            detail=message
        )

    return response.content


# ============================================================
# MEJORAR IMAGEN
# ============================================================

def enhance_image(
    image: Image.Image,
    sharpness: float
):

    image = image.convert("RGBA")

    # Guardar transparencia
    alpha = image.getchannel("A")

    # Procesamos RGB
    rgb = image.convert("RGB")

    # Contraste moderado
    rgb = ImageEnhance.Contrast(
        rgb
    ).enhance(1.04)

    # Saturación moderada
    rgb = ImageEnhance.Color(
        rgb
    ).enhance(1.04)

    # Nitidez
    try:

        sharpness = float(sharpness)

    except Exception:

        sharpness = 35

    sharpness = max(
        0,
        min(sharpness, 100)
    )

    sharp_factor = (
        1.0 +
        (sharpness / 100.0) * 0.75
    )

    rgb = ImageEnhance.Sharpness(
        rgb
    ).enhance(sharp_factor)

    # Volver a RGBA
    result = rgb.convert("RGBA")

    # Restaurar transparencia
    result.putalpha(alpha)

    return result


# ============================================================
# ESCALADO SEGURO
# ============================================================

def resize_safe(image: Image.Image, scale: int, hd_mode: str = "ILUSTRACION"):
    """
    V6.7 HD
    - ILUSTRACION: suaviza dientes/píxeles antes de ampliar y recupera contorno.
    - FOTO: conserva textura con un tratamiento más suave.
    - Mantiene transparencia.
    - Respeta MAX_OUTPUT_PIXELS.
    Nota: mejora el reescalado, pero no inventa detalle que no exista en el original.
    """
    try:
        scale = int(scale)
    except Exception:
        scale = 1

    if scale not in [1, 2, 3, 4]:
        scale = 1

    mode = str(hd_mode or "ILUSTRACION").strip().upper()
    if mode not in ("ILUSTRACION", "FOTO"):
        mode = "ILUSTRACION"

    width, height = image.size

    if scale == 1:
        return image.convert("RGBA"), 1.0

    requested_width = width * scale
    requested_height = height * scale
    requested_pixels = requested_width * requested_height

    real_scale = float(scale)
    if requested_pixels > MAX_OUTPUT_PIXELS:
        real_scale = (MAX_OUTPUT_PIXELS / float(width * height)) ** 0.5

    real_scale = min(real_scale, float(scale))

    final_width = max(1, int(round(width * real_scale)))
    final_height = max(1, int(round(height * real_scale)))

    current = image.convert("RGBA")
    if final_width == width and final_height == height:
        return current, 1.0

    # Separar alfa para no ensuciar la transparencia.
    alpha = current.getchannel("A")
    rgb = current.convert("RGB")

    if mode == "ILUSTRACION":
        # Suavizado MUY ligero antes del aumento: reduce escalones y bloques.
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=0.28))
    else:
        # En fotografía se prioriza textura.
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=0.12))

    # Escalado directo LANCZOS de alta calidad.
    rgb = rgb.resize((final_width, final_height), Image.Resampling.LANCZOS)
    alpha = alpha.resize((final_width, final_height), Image.Resampling.LANCZOS)

    if mode == "ILUSTRACION":
        # Recupera línea sin el efecto agresivo de Sharpness al 100%.
        rgb = rgb.filter(
            ImageFilter.UnsharpMask(
                radius=1.15,
                percent=115,
                threshold=3
            )
        )
        # Segundo pase mínimo para limpiar apariencia blanda sin crear halos.
        rgb = rgb.filter(
            ImageFilter.UnsharpMask(
                radius=0.55,
                percent=45,
                threshold=5
            )
        )
    else:
        rgb = rgb.filter(
            ImageFilter.UnsharpMask(
                radius=1.0,
                percent=70,
                threshold=4
            )
        )

    result = rgb.convert("RGBA")
    result.putalpha(alpha)

    return result, real_scale

# ============================================================
# GUARDAR PNG CON 300 DPI REALES
# ============================================================

def add_png_phys_300dpi(png_data: bytes) -> bytes:
    """
    Inserta explícitamente el chunk PNG pHYs.
    300 DPI = 11811 píxeles por metro aproximadamente.
    Esto evita depender únicamente de cómo Pillow escriba el DPI.
    """
    signature = b"\x89PNG\r\n\x1a\n"

    if not png_data.startswith(signature):
        raise ValueError("El archivo generado no es un PNG válido.")

    ppm = 11811
    phys_payload = struct.pack(">IIB", ppm, ppm, 1)
    chunk_type = b"pHYs"
    crc = zlib.crc32(chunk_type + phys_payload) & 0xffffffff
    phys_chunk = (
        struct.pack(">I", len(phys_payload)) +
        chunk_type +
        phys_payload +
        struct.pack(">I", crc)
    )

    # Eliminar cualquier pHYs existente para no duplicarlo.
    pos = len(signature)
    chunks = []

    while pos + 12 <= len(png_data):
        length = struct.unpack(">I", png_data[pos:pos + 4])[0]
        chunk_end = pos + 12 + length

        if chunk_end > len(png_data):
            raise ValueError("PNG incompleto o dañado.")

        chunk = png_data[pos:chunk_end]
        chunk_name = png_data[pos + 4:pos + 8]

        if chunk_name != b"pHYs":
            chunks.append((chunk_name, chunk))

        pos = chunk_end

        if chunk_name == b"IEND":
            break

    # pHYs debe aparecer antes de IDAT.
    output = bytearray(signature)
    inserted = False

    for chunk_name, chunk in chunks:
        if chunk_name == b"IDAT" and not inserted:
            output.extend(phys_chunk)
            inserted = True

        output.extend(chunk)

    if not inserted:
        raise ValueError("No se encontró el chunk IDAT del PNG.")

    return bytes(output)


def save_png_300_dpi(image: Image.Image) -> bytes:
    """
    Guarda RGBA y fuerza un pHYs equivalente a 300 DPI.
    Después verifica tanto el chunk pHYs como la lectura de Pillow.
    """
    output = io.BytesIO()

    image.save(
        output,
        format="PNG",
        optimize=False,
        compress_level=6
    )

    png_data = output.getvalue()
    output.close()

    png_data = add_png_phys_300dpi(png_data)

    # Verificación binaria del chunk pHYs.
    if b"pHYs" not in png_data:
        raise HTTPException(
            status_code=500,
            detail="No se pudo insertar el metadato físico de 300 DPI."
        )

    # Verificación adicional con Pillow.
    try:
        check = Image.open(io.BytesIO(png_data))
        saved_dpi = check.info.get("dpi")
        check.load()
        check.close()

        if not saved_dpi or len(saved_dpi) < 2:
            raise ValueError("Pillow no detectó el DPI.")

        if (
            abs(float(saved_dpi[0]) - 300.0) > 1.0 or
            abs(float(saved_dpi[1]) - 300.0) > 1.0
        ):
            raise ValueError(f"DPI inesperado: {saved_dpi}")

    except HTTPException:
        raise

    except Exception as error:
        print(
            "ERROR VERIFICANDO DPI:",
            repr(error),
            flush=True
        )
        raise HTTPException(
            status_code=500,
            detail="El PNG se creó, pero no se pudo verificar a 300 DPI."
        )

    return png_data



# ============================================================
# FONDO PRECISO POR COLOR CONECTADO A LOS BORDES
# Protege colores iguales que estén encerrados dentro del diseño.
# ============================================================

def remove_edge_connected_background(data: bytes, tolerance: int = 18, manual_points=None):
    """
    V7.8 BORDE FINAL
    - El fondo exterior se elimina por conectividad.
    - Cada clic manual elimina solamente su isla conectada.
    - Los clics demasiado cerca de líneas/detalles se ignoran automáticamente.
    - El umbral manual es más conservador que el de las esquinas.
    - La máscara final protege aproximadamente 1 px del contorno del diseño.
    """
    try:
        original = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo abrir la imagen.")

    w, h = original.size

    # Mantener memoria controlada en Render Free.
    max_side = 1800
    ratio = min(1.0, max_side / max(w, h))

    if ratio < 1.0:
        sw = max(1, int(round(w * ratio)))
        sh = max(1, int(round(h * ratio)))
        work_rgba = original.resize((sw, sh), Image.Resampling.LANCZOS)
    else:
        work_rgba = original.copy()
        sw, sh = w, h

    work = work_rgba.convert("RGB")
    sentinel = (1, 2, 3)

    tol = max(0, min(100, int(tolerance)))

    # Exterior: suficiente para fondos blancos/planos sin ser agresivo.
    edge_threshold = max(2, int(tol * 0.90))

    # Clic manual: MÁS preciso. Evita comerse líneas y colores claros del arte.
    manual_threshold = max(2, int(tol * 0.58))

    # 1) Fondo realmente conectado al exterior.
    for xy in (
        (0, 0),
        (sw - 1, 0),
        (0, sh - 1),
        (sw - 1, sh - 1)
    ):
        if work.getpixel(xy) != sentinel:
            ImageDraw.floodfill(
                work,
                xy=xy,
                value=sentinel,
                thresh=edge_threshold
            )

    # Detecta si un clic está demasiado cerca de un contorno/detalle.
    # En una zona de fondo uniforme los colores vecinos son muy parecidos.
    def safe_manual_point(px, py):
        seed = work.getpixel((px, py))
        if seed == sentinel:
            return False

        radius = 3
        x0 = max(0, px - radius)
        x1 = min(sw - 1, px + radius)
        y0 = max(0, py - radius)
        y1 = min(sh - 1, py + radius)

        max_delta = 0

        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                c = work.getpixel((xx, yy))

                if c == sentinel:
                    continue

                delta = max(
                    abs(int(c[0]) - int(seed[0])),
                    abs(int(c[1]) - int(seed[1])),
                    abs(int(c[2]) - int(seed[2]))
                )

                if delta > max_delta:
                    max_delta = delta

                # Si hay un cambio fuerte tan cerca del clic, probablemente
                # estamos sobre/bordeando una línea del diseño.
                if max_delta > max(22, manual_threshold * 2):
                    return False

        return True

    # 2) Islas interiores seleccionadas manualmente.
    for point in (manual_points or []):
        try:
            nx = max(0.0, min(1.0, float(point.get("x", 0))))
            ny = max(0.0, min(1.0, float(point.get("y", 0))))

            sx = min(sw - 1, max(0, round(nx * (sw - 1))))
            sy = min(sh - 1, max(0, round(ny * (sh - 1))))

            if not safe_manual_point(sx, sy):
                continue

            ImageDraw.floodfill(
                work,
                xy=(sx, sy),
                value=sentinel,
                thresh=manual_threshold
            )

        except Exception:
            continue

    # 3) Convertir resultado a máscara binaria.
    solid = Image.new("RGB", (sw, sh), sentinel)
    diff = ImageChops.difference(work, solid).convert("L")
    mask = diff.point(lambda p: 0 if p == 0 else 255)

    # 4) V6.9: NO expandir el diseño.
    # La expansión anterior conservaba píxeles blancos del fondo y creaba
    # el contorno dentado visible. Solo usamos antialias mínimo.
    mask = mask.filter(ImageFilter.GaussianBlur(0.16))

    if (sw, sh) != (w, h):
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)

    # Respetar transparencia preexistente.
    old_alpha = original.getchannel("A")
    final_alpha = ImageChops.multiply(old_alpha, mask)
    original.putalpha(final_alpha)

    return original

def decontaminate_light_edge(image: Image.Image, strength: int = 40):
    """
    V7.0: reduce contaminación blanca/gris del fondo en píxeles
    semitransparentes del contorno sin modificar el interior opaco.
    No inventa detalle: corrige el color visible del borde.
    """
    rgba = image.convert("RGBA")
    if strength <= 0:
        return rgba

    strength = max(0, min(100, int(strength)))
    r, g, b, a = rgba.split()

    # Trabajar solo en el borde semitransparente.
    # Más fuerza = mayor rango de alfa afectado.
    alpha_limit = 210 + int(strength * 0.35)
    alpha_limit = min(245, alpha_limit)

    px = rgba.load()
    w, h = rgba.size

    # Mantener coste controlado. Solo cambia píxeles semitransparentes claros.
    factor = 0.20 + (strength / 100.0) * 0.55

    for y in range(h):
        for x in range(w):
            rr, gg, bb, aa = px[x, y]
            if aa == 0 or aa >= alpha_limit:
                continue

            # Detectar contaminación clara/neutra típica de fondo blanco.
            mx = max(rr, gg, bb)
            mn = min(rr, gg, bb)
            neutral = (mx - mn) <= 38
            light = (rr + gg + bb) / 3.0 >= 175

            if neutral and light:
                # Oscurecimiento proporcional a transparencia y fuerza.
                edge_amount = (1.0 - aa / 255.0) * factor
                target = min(rr, gg, bb)
                rr = int(rr * (1-edge_amount) + target * edge_amount)
                gg = int(gg * (1-edge_amount) + target * edge_amount)
                bb = int(bb * (1-edge_amount) + target * edge_amount)
                px[x, y] = (rr, gg, bb, aa)

    return rgba


def refine_alpha_edge(image: Image.Image, precision: int = 90, halo: int = 70):
    """
    V6.9: controles REALES de borde.
    precision: endurece/separa alfa dudosa.
    halo: elimina píxeles semitransparentes claros del borde.
    """
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")

    try: precision = max(50, min(100, int(precision)))
    except Exception: precision = 90
    try: halo = max(0, min(100, int(halo)))
    except Exception: halo = 70
    # Precisión alta = transición más firme, pero conserva antialias.
    cutoff = int(8 + (precision - 50) * 0.28)
    if cutoff > 0:
        alpha = alpha.point(lambda a: 0 if a < cutoff else a)

    # Halo: reduce alfa débil sin tocar el interior opaco.
    if halo > 0:
        halo_cut = int(halo * 0.55)
        alpha = alpha.point(
            lambda a: 0 if a <= halo_cut else
            min(255, int((a - halo_cut) * 255 / max(1, 255 - halo_cut)))
        )

    # Antialias final mínimo.
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.12))
    rgba.putalpha(alpha)
    return rgba



def clean_connected_white_fringe(image: Image.Image, strength: int = 55) -> Image.Image:
    """
    Limpia el borde blanco/gris que queda después de quitar un fondo claro.
    Solo trabaja en píxeles del CONTORNO de la máscara alfa; no toca blancos
    interiores completamente opacos como guantes, ojos o luna.
    """
    img = image.convert("RGBA")
    r, g, b, a = img.split()

    try:
        strength = max(0, min(100, int(strength)))
    except Exception:
        strength = 55

    if strength <= 0:
        return img

    # Banda interior muy estrecha junto a transparencia.
    # Dilatamos transparencia hacia dentro 1 px para identificar solo el contorno.
    transparent = a.point(lambda p: 255 if p <= 8 else 0)
    near_outside = transparent.filter(ImageFilter.MaxFilter(3))

    # No modificar fondo ya transparente.
    visible = a.point(lambda p: 255 if p > 8 else 0)
    edge_band = ImageChops.multiply(near_outside, visible)

    # Detectar contaminación clara/neutra (blanco/gris), no colores saturados.
    # minRGB alto = claro; diferencia RGB baja = neutral.
    rgb = Image.merge("RGB", (r, g, b))
    px = rgb.load()
    em = edge_band.load()
    alpha = a.load()
    w, h = img.size

    # Trabajamos únicamente sobre la banda de 1 px; reduce costo y protege arte.
    out = img.load()
    cutoff = int(214 - (strength * 0.58))   # V7.7: borde exterior más limpio
    neutral_limit = int(27 + strength * 0.25)

    for y in range(h):
        for x in range(w):
            if em[x, y] == 0:
                continue

            rr, gg, bb, aa = out[x, y]
            mx = max(rr, gg, bb)
            mn = min(rr, gg, bb)

            if mn >= cutoff and (mx - mn) <= neutral_limit:
                # Blanco/gris exterior: volverlo transparente.
                out[x, y] = (rr, gg, bb, 0)
            elif aa < 245 and mn >= max(150, cutoff - 25) and (mx - mn) <= neutral_limit + 12:
                # Antialias claro: reducir alfa progresivamente.
                whiteness = (mn - max(150, cutoff - 25)) / max(1, 255 - max(150, cutoff - 25))
                reduce = int(aa * whiteness * (0.50 + 0.45 * strength / 100))
                out[x, y] = (rr, gg, bb, max(0, aa - reduce))

    return img


def final_exterior_edge_cleanup(image: Image.Image, strength: int = 65) -> Image.Image:
    """V7.8: limpia únicamente la franja clara/gris pegada al exterior transparente."""
    img = image.convert("RGBA")
    r, g, b, a = img.split()
    try:
        strength = max(0, min(100, int(strength)))
    except Exception:
        strength = 65
    if strength <= 0:
        return img

    transparent = a.point(lambda p: 255 if p <= 12 else 0)
    outside_band = transparent.filter(ImageFilter.MaxFilter(3))
    visible = a.point(lambda p: 255 if p > 12 else 0)
    band = ImageChops.multiply(outside_band, visible)

    bp = band.load()
    out = img.load()
    w, h = img.size
    light_cut = int(205 - strength * 0.28)
    neutral_cut = int(30 + strength * 0.12)

    for y in range(h):
        for x in range(w):
            if bp[x, y] == 0:
                continue
            rr, gg, bb, aa = out[x, y]
            spread = max(rr, gg, bb) - min(rr, gg, bb)
            lum = (rr * 30 + gg * 59 + bb * 11) // 100

            if lum >= light_cut and spread <= neutral_cut:
                out[x, y] = (rr, gg, bb, 0)
            elif aa < 245 and lum >= light_cut - 28 and spread <= neutral_cut + 10:
                factor = min(0.88, 0.42 + strength / 220.0)
                new_a = int(aa * (1.0 - factor))
                if new_a < 24:
                    new_a = 0
                out[x, y] = (rr, gg, bb, new_a)
    return img

def remove_ground_shadow_precise(image: Image.Image, enabled: bool = False) -> Image.Image:
    """V7.7: elimina sombra gris del suelo conservando zapatos y colores del diseño."""
    if not enabled:
        return image

    img = image.convert("RGBA")
    w, h = img.size
    px = img.load()

    # Trabajar únicamente en el 19% inferior.
    y0 = int(h * 0.81)

    mask = Image.new("L", (w, h), 0)
    mp = mask.load()

    for y in range(y0, h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= 6:
                continue

            mx = max(r, g, b)
            mn = min(r, g, b)
            spread = mx - mn
            lum = (r * 30 + g * 59 + b * 11) // 100

            # Gris/blanco poco saturado típico de sombra sobre fondo claro.
            if 105 <= lum <= 250 and spread <= 34:
                mp[x, y] = 255

    # Unir el tramado de sombra horizontal.
    mask = mask.filter(ImageFilter.MaxFilter(5))
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    mm = mask.load()
    out = img.load()

    for y in range(y0, h):
        # Mayor fuerza cuanto más abajo estamos.
        yf = (y - y0) / max(1, h - y0)
        for x in range(w):
            m = mm[x, y]
            if m <= 8:
                continue

            r, g, b, a = out[x, y]
            spread = max(r, g, b) - min(r, g, b)
            lum = (r * 30 + g * 59 + b * 11) // 100

            # Proteger píxeles oscuros y colores saturados de zapatos/arte.
            if lum < 105 or spread > 38:
                continue

            force = 0.72 + 0.28 * yf
            fade = int(m * force)
            new_a = max(0, a - fade)

            if new_a < 38:
                new_a = 0

            out[x, y] = (r, g, b, new_a)

    return img


@app.post("/api/process")
async def process_image(

    image: UploadFile = File(...),

    scale: int = Form(1),

    sharpness: float = Form(35),

    mode: str = Form("DTF"),

    bg_tolerance: int = Form(18),

    edge_only: bool = Form(False),

    manual_points: str = Form("[]"),

    hd_mode: str = Form("ILUSTRACION"),

    precision: int = Form(90),

    halo: int = Form(70),

    remove_shadow: bool = Form(False),

    authorization: str | None = Header(default=None)

):

    user_id = auth_id(authorization)
    usage_reserved = False

    try:
        usage_reserved = reserve_use(user_id)

        # ====================================================
        # 1. LEER IMAGEN
        # ====================================================

        data = await image.read()

        original_width, original_height = (
            validate_image(data)
        )

        try:
            selected_background_points = json.loads(manual_points or "[]")
            if not isinstance(selected_background_points, list):
                selected_background_points = []
            selected_background_points = selected_background_points[:20]
        except Exception:
            selected_background_points = []


        # ====================================================
        # 2. ELIMINAR FONDO
        # ====================================================

        if edge_only:
            # Modo preciso: SOLO elimina color conectado a los bordes.
            # Los colores iguales dentro del diseño quedan protegidos.
            result = remove_edge_connected_background(
                data,
                bg_tolerance,
                selected_background_points
            )
            background_engine = "EDGE-PRECISE-V6.8"
        else:
            # Modo IA tradicional.
            transparent_data = remove_background(data)

            try:
                result = Image.open(
                    io.BytesIO(transparent_data)
                ).convert("RGBA")
            except Exception:
                raise HTTPException(
                    status_code=500,
                    detail="El motor devolvió una imagen inválida."
                )

            del transparent_data
            background_engine = "REMOVE.BG"

        del data


        # ====================================================
        # 4. BORDE REAL V6.9
        # ====================================================

        result = refine_alpha_edge(
            result,
            precision,
            halo
        )

        # Quitar contaminación blanca del borde usando el mismo
        # control "Eliminar halo".
        result = decontaminate_light_edge(
            result,
            halo
        )

        
        # V7.5: limpieza de borde blanco conectado al exterior.
        result = clean_connected_white_fringe(result, halo)
# Opción independiente para sombras grises del suelo.
        result = remove_ground_shadow_precise(
            result,
            remove_shadow
        )

        # V7.8: pasada final conservadora solo sobre el borde exterior.
        result = final_exterior_edge_cleanup(result, halo)


        # ====================================================
        # 5. MEJORA
        # ====================================================

        result = enhance_image(
            result,
            sharpness
        )


        # ====================================================
        # 5. ESCALADO
        # ====================================================

        result, real_scale = (
            resize_safe(
                result,
                scale,
                hd_mode
            )
        )


        # ====================================================
        # 6. DIMENSIONES FINALES
        # ====================================================

        final_width, final_height = (
            result.size
        )


        # ====================================================
        # 7. CREAR Y VERIFICAR PNG 300 DPI
        # ====================================================

        png_data = save_png_300_dpi(result)

        del result


        # ====================================================
        # 8. RESPUESTA
        # ====================================================

        uc, ucur, urow = get_user(user_id)
        try:
            uinfo = user_public(urow)
        finally:
            ucur.close(); uc.close()

        return Response(
            content=png_data,

            media_type="image/png",

            headers={

                "Content-Disposition":
                'inline; filename="zafrik-ia-pro.png"',

                "X-Zafrik-Version":
                "7.8.0",

                "X-Original-Width":
                str(original_width),

                "X-Original-Height":
                str(original_height),

                "X-Final-Width":
                str(final_width),

                "X-Final-Height":
                str(final_height),

                "X-Scale-Applied":
                str(real_scale),

                "X-Print-Mode":
                str(mode),

                "X-Background-Engine":
                background_engine,

                "X-Background-Tolerance":
                str(bg_tolerance),

                "X-Edge-Only":
                "true" if edge_only else "false",

                "X-Precision":
                str(precision),

                "X-Halo":
                str(halo),

                "X-Remove-Shadow":
                "true" if remove_shadow else "false",

                "X-DPI":
                "300",

                "X-DPI-Verified":
                "true",

                "X-PNG-pHYs":
                "verified",

                "X-Pixels-Per-Meter-X":
                "11811",

                "X-Pixels-Per-Meter-Y":
                "11811",

                "X-Upscale-Engine":
                "ZAFRIK-HD-V7.8",

                "X-User-Plan":
                str(uinfo["plan"]),

                "X-Uses-Remaining":
                "unlimited" if uinfo["premium"] else str(uinfo["remaining"]),

                "X-Uses-Used":
                str(uinfo["used"])
            }
        )


    # ========================================================
    # ERRORES CONTROLADOS
    # ========================================================

    except HTTPException:
        if usage_reserved:
            refund_use(user_id)
        raise


    except MemoryError:
        if usage_reserved:
            refund_use(user_id)
        raise HTTPException(
            status_code=507,
            detail=(
                "La imagen requiere más memoria "
                "de la disponible en el servidor. "
                "Prueba una imagen más pequeña "
                "o utiliza escala 1X."
            )
        )


    except Exception as error:
        if usage_reserved:
            refund_use(user_id)
        print(
            "ERROR ZAFRIK IA PRO:",
            repr(error),
            flush=True
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "No se pudo procesar la imagen."
            )
        )


# ============================================================
# COMPATIBILIDAD CON FRONTEND ANTERIOR
# ============================================================

@app.post("/process")
async def process_legacy(

    image: UploadFile = File(...),

    scale: int = Form(1),

    sharpness: float = Form(35),

    mode: str = Form("DTF"),

    authorization: str | None = Header(default=None)

):

    return await process_image(
        image=image,
        scale=scale,
        sharpness=sharpness,
        mode=mode,
        bg_tolerance=18,
        edge_only=False,
        manual_points="[]",
        hd_mode="ILUSTRACION",
        precision=90,
        halo=70,
        remove_shadow=False,
        authorization=authorization
    )


# ============================================================
# ERROR 404
# ============================================================

@app.exception_handler(404)
async def not_found(
    request,
    exc
):

    return JSONResponse(
        status_code=404,

        content={
            "ok": False,
            "app": "ZAFRIK IA PRO",
            "message": "Ruta no encontrada."
        }
    )
