import os
import io
import struct
import zlib
import secrets
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor

import requests
from PIL import Image, ImageEnhance
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse


# ============================================================
# ZAFRIK IA PRO V6.0
# Remove.bg + mejora de imagen + escalado + interfaz web
# Optimizado para Render Free 512 MB
# ============================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="6.0.0",
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
        con.commit(); cur.close(); con.close()
        print("ZAFRIK IA DB LISTA",flush=True)
    except Exception as e:
        print("INIT DB ERROR:",repr(e),flush=True)

@app.on_event("startup")
def startup_database():
    init_database()

def clean_email(v):
    return str(v or "").strip().lower()

def user_public(r):
    plan=str(r["plan"] or "TRIAL").upper()
    used=int(r["used_count"] or 0); limit=int(r["free_limit"] or FREE_LIMIT)
    return {"id":r["id"],"email":r["email"],"plan":plan,
            "premium":plan=="PREMIUM","used":used,"limit":limit,
            "remaining":None if plan=="PREMIUM" else max(0,limit-used),
            "active":bool(r["is_active"])}

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
    q="""SELECT id,email,password_hash,plan,free_limit,used_count,is_active
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
                       RETURNING id,email,plan,free_limit,used_count,is_active""",(email,ph))
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
        cur.execute("""SELECT id,email,password_hash,plan,free_limit,used_count,is_active
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
    con,cur,r=get_user(auth_id(authorization))
    try:return {"ok":True,"user":user_public(r)}
    finally:cur.close();con.close()

def reserve_use(uid):
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
                "version": "6.0.0",
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
        "version": "6.0.0"
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
        "version": "6.0.0"
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

def resize_safe(image: Image.Image, scale: int):
    """
    V5.6 - Escalado mejorado para impresión.

    - Mantiene transparencia RGBA.
    - Usa LANCZOS por etapas para 2X/3X/4X.
    - Aplica nitidez moderada después de cada etapa para reducir
      la apariencia borrosa sin crear halos fuertes.
    - Respeta MAX_OUTPUT_PIXELS para Render Free.
    """

    scale = int(scale)

    if scale not in [1, 2, 3, 4]:
        scale = 1

    width, height = image.size

    if scale == 1:
        return image, 1.0

    requested_width = width * scale
    requested_height = height * scale
    requested_pixels = requested_width * requested_height

    real_scale = float(scale)

    if requested_pixels > MAX_OUTPUT_PIXELS:
        real_scale = (
            MAX_OUTPUT_PIXELS /
            float(width * height)
        ) ** 0.5

    # Nunca superar la escala solicitada.
    real_scale = min(real_scale, float(scale))

    final_width = max(1, int(round(width * real_scale)))
    final_height = max(1, int(round(height * real_scale)))

    if final_width == width and final_height == height:
        return image, 1.0

    current = image.convert("RGBA")
    current_w, current_h = current.size

    # Escalado por etapas de hasta 1.5X. Suele producir un resultado
    # visualmente más limpio que un único salto grande.
    while current_w < final_width or current_h < final_height:
        next_w = min(final_width, max(current_w + 1, int(round(current_w * 1.5))))
        next_h = min(final_height, max(current_h + 1, int(round(current_h * 1.5))))

        current = current.resize(
            (next_w, next_h),
            Image.Resampling.LANCZOS
        )

        # Nitidez suave sobre RGB, conservando el alfa original.
        alpha = current.getchannel("A")
        rgb = current.convert("RGB")

        # Un pequeño refuerzo por etapa evita exagerar contornos.
        rgb = ImageEnhance.Sharpness(rgb).enhance(1.08)

        current = rgb.convert("RGBA")
        current.putalpha(alpha)

        current_w, current_h = current.size

    return current, real_scale
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
# PROCESAMIENTO PRINCIPAL
# ============================================================

@app.post("/api/process")
async def process_image(

    image: UploadFile = File(...),

    scale: int = Form(1),

    sharpness: float = Form(35),

    mode: str = Form("DTF"),

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


        # ====================================================
        # 2. REMOVE.BG
        # ====================================================

        transparent_data = (
            remove_background(data)
        )

        # Liberamos original
        del data


        # ====================================================
        # 3. ABRIR PNG TRANSPARENTE
        # ====================================================

        try:

            result = Image.open(
                io.BytesIO(
                    transparent_data
                )
            ).convert("RGBA")

        except Exception:

            raise HTTPException(
                status_code=500,
                detail=(
                    "El motor devolvió "
                    "una imagen inválida."
                )
            )

        del transparent_data


        # ====================================================
        # 4. MEJORA
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
                scale
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
                "6.0.0",

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
                "LANCZOS-STAGED-V5.6",

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

    mode: str = Form("DTF")

):

    return await process_image(
        image=image,
        scale=scale,
        sharpness=sharpness,
        mode=mode
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
