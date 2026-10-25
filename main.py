import os
import io

import requests
from PIL import Image, ImageEnhance, ImageFilter
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse


# ============================================================
# ZAFRIK IA PRO V5.1
# Backend ligero para Render
# Remove.bg + mejora + escalado
# ============================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="5.1.0",
    description="Eliminación de fondo y preparación para DTF, DTG y sublimación"
)

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

# Evitamos imágenes gigantes en una instancia de 512 MB.
MAX_INPUT_PIXELS = 16_000_000

# Límite de seguridad de salida.
MAX_OUTPUT_PIXELS = 24_000_000


# ============================================================
# RUTAS DE ESTADO
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "ZAFRIK IA PRO",
        "version": "5.1.0",
        "motor": "remove.bg API",
        "message": "Servidor funcionando correctamente"
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "healthy"
    }


@app.get("/api/status")
def api_status():
    api_key = os.getenv("REMOVE_BG_API_KEY")

    return {
        "ok": True,
        "server": "online",
        "remove_bg_configured": bool(api_key),
        "version": "5.1.0"
    }


# ============================================================
# UTILIDADES
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
            detail=f"La imagen supera el límite de {MAX_FILE_MB} MB."
        )

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="El archivo no es una imagen válida."
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
                "La imagen es demasiado grande para procesarla "
                "en este servidor. Reduce sus dimensiones e inténtalo nuevamente."
            )
        )

    return image, width, height


def remove_background(data: bytes):
    api_key = os.getenv("REMOVE_BG_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="REMOVE_BG_API_KEY no está configurada en Render."
        )

    try:
        response = requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={
                "image_file": (
                    "imagen.png",
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
            detail="El motor de eliminación de fondo tardó demasiado en responder."
        )

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con el motor de eliminación: {error}"
        )

    if response.status_code != 200:

        try:
            error_data = response.json()

            errors = error_data.get("errors", [])

            if errors:
                message = errors[0].get(
                    "title",
                    "Error del motor de eliminación de fondo."
                )
            else:
                message = "Error del motor de eliminación de fondo."

        except Exception:
            message = response.text[:300]

        raise HTTPException(
            status_code=response.status_code,
            detail=message
        )

    return response.content


def enhance_image(image: Image.Image, sharpness: float):
    """
    Mejora moderada para impresión.
    Evitamos filtros pesados para proteger la RAM.
    """

    image = image.convert("RGBA")

    # Contraste suave
    rgb = image.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.04)

    # Color suave
    rgb = ImageEnhance.Color(rgb).enhance(1.04)

    # Nitidez
    sharpness = max(0.0, min(float(sharpness), 100.0))

    factor = 1.0 + (sharpness / 100.0) * 0.75

    rgb = ImageEnhance.Sharpness(rgb).enhance(factor)

    # Recuperamos alpha original
    alpha = image.getchannel("A")

    result = rgb.convert("RGBA")
    result.putalpha(alpha)

    return result


def resize_safe(image: Image.Image, scale: int):
    if scale not in (1, 2, 3, 4):
        scale = 1

    width, height = image.size

    new_width = width * scale
    new_height = height * scale

    output_pixels = new_width * new_height

    # Si el escalado solicitado excede la RAM razonable,
    # reducimos automáticamente la escala.
    while output_pixels > MAX_OUTPUT_PIXELS and scale > 1:

        scale -= 1

        new_width = width * scale
        new_height = height * scale

        output_pixels = new_width * new_height

    if scale > 1:
        image = image.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS
        )

    return image, scale


# ============================================================
# PROCESAMIENTO PRINCIPAL
# ============================================================

@app.post("/api/process")
async def process_image(
    image: UploadFile = File(...),
    scale: int = Form(1),
    sharpness: float = Form(35),
    mode: str = Form("DTF")
):

    try:

        # ----------------------------------------------------
        # 1. LEER ARCHIVO
        # ----------------------------------------------------

        data = await image.read()

        original, original_width, original_height = validate_image(data)

        del original


        # ----------------------------------------------------
        # 2. QUITAR FONDO CON API
        # ----------------------------------------------------

        transparent_data = remove_background(data)

        # Ya no necesitamos el archivo original.
        del data


        # ----------------------------------------------------
        # 3. ABRIR RESULTADO
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # 4. MEJORA
        # ----------------------------------------------------

        result = enhance_image(
            result,
            sharpness
        )


        # ----------------------------------------------------
        # 5. ESCALADO
        # ----------------------------------------------------

        result, real_scale = resize_safe(
            result,
            int(scale)
        )


        # ----------------------------------------------------
        # 6. PNG FINAL
        # ----------------------------------------------------

        output = io.BytesIO()

        result.save(
            output,
            format="PNG",
            optimize=False,
            dpi=(300, 300)
        )

        final_width, final_height = result.size

        del result

        png_data = output.getvalue()

        output.close()


        # ----------------------------------------------------
        # RESPUESTA
        # ----------------------------------------------------

        return Response(
            content=png_data,
            media_type="image/png",
            headers={
                "Content-Disposition":
                    'inline; filename="zafrik-ia-pro.png"',

                "X-Zafrik-Version":
                    "5.1.0",

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
                    "300"
            }
        )


    except HTTPException:
        raise

    except MemoryError:

        raise HTTPException(
            status_code=507,
            detail=(
                "La imagen requiere más memoria de la disponible. "
                "Prueba una imagen más pequeña o utiliza escala 1X."
            )
        )

    except Exception as error:

        print(
            "ERROR ZAFRIK IA PRO:",
            repr(error),
            flush=True
        )

        raise HTTPException(
            status_code=500,
            detail="No se pudo procesar la imagen."
        )


# ============================================================
# COMPATIBILIDAD CON FRONTENDS ANTERIORES
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
async def not_found(request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "message": "Ruta no encontrada.",
            "app": "ZAFRIK IA PRO"
        }
    )
