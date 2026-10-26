import os
import io

import requests
from PIL import Image, ImageEnhance
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse


# ============================================================
# ZAFRIK IA PRO V5.3
# Remove.bg + mejora de imagen + escalado + interfaz web
# Optimizado para Render Free 512 MB
# ============================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="5.3.0",
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
                "version": "5.3.0",
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
        "version": "5.3.0"
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
        "version": "5.3.0"
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

def resize_safe(
    image: Image.Image,
    scale: int
):

    try:

        scale = int(scale)

    except Exception:

        scale = 1

    if scale not in (
        1,
        2,
        3,
        4
    ):

        scale = 1

    width, height = image.size

    new_width = width * scale
    new_height = height * scale

    output_pixels = (
        new_width *
        new_height
    )

    # Reducir automáticamente la escala
    # si la salida sería demasiado grande.
    while (
        output_pixels > MAX_OUTPUT_PIXELS
        and
        scale > 1
    ):

        scale -= 1

        new_width = (
            width *
            scale
        )

        new_height = (
            height *
            scale
        )

        output_pixels = (
            new_width *
            new_height
        )

    if scale > 1:

        image = image.resize(
            (
                new_width,
                new_height
            ),
            Image.Resampling.LANCZOS
        )

    return image, scale


# ============================================================
# GUARDAR PNG CON 300 DPI REALES
# ============================================================

def save_png_300_dpi(image: Image.Image) -> bytes:
    """
    Guarda el PNG con transparencia y metadato físico equivalente
    a 300 DPI. Pillow escribe el chunk pHYs del PNG a partir de dpi.
    Luego reabrimos el archivo para verificar que el metadato exista.
    """

    output = io.BytesIO()

    image.save(
        output,
        format="PNG",
        optimize=False,
        compress_level=6,
        dpi=(300.0, 300.0)
    )

    png_data = output.getvalue()
    output.close()

    # Verificación interna. PNG expresa la resolución en píxeles/metro,
    # por eso al leerlo normalmente aparece ~299.9994 DPI.
    try:
        check = Image.open(io.BytesIO(png_data))
        saved_dpi = check.info.get("dpi")
        check.close()

        if not saved_dpi or len(saved_dpi) < 2:
            raise ValueError("El PNG no contiene metadato DPI.")

        if abs(float(saved_dpi[0]) - 300.0) > 1.0 or abs(float(saved_dpi[1]) - 300.0) > 1.0:
            raise ValueError(f"DPI inesperado: {saved_dpi}")

    except Exception as error:
        print(
            "ERROR VERIFICANDO DPI:",
            repr(error),
            flush=True
        )
        raise HTTPException(
            status_code=500,
            detail="No se pudo generar el PNG final con 300 DPI."
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

    mode: str = Form("DTF")

):

    try:

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

        return Response(
            content=png_data,

            media_type="image/png",

            headers={

                "Content-Disposition":
                'inline; filename="zafrik-ia-pro.png"',

                "X-Zafrik-Version":
                "5.3.0",

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


    # ========================================================
    # ERRORES CONTROLADOS
    # ========================================================

    except HTTPException:

        raise


    except MemoryError:

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
