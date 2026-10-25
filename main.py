import io
import os
import gc

# Limitar hilos ANTES de importar ONNX/rembg.
# En una instancia pequeña ayuda a reducir consumo de memoria.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OMP_THREAD_LIMIT"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageEnhance, ImageFilter
from rembg import remove, new_session


# ==========================================================
# ZAFRIK IA PRO V5 ULTRALIGERA
# Diseñada para servidores con poca memoria
# ==========================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="5.0.0"
)

SESSION = None

MAX_UPLOAD_MB = 12

# La IA NO recibe la imagen completa si es enorme.
AI_MAX_SIDE = 1024

# Protección general.
MAX_INPUT_SIDE = 5000
MAX_OUTPUT_SIDE = 8000
MAX_OUTPUT_PIXELS = 28_000_000


# ==========================================================
# MODELO IA
# ==========================================================

def get_session():

    global SESSION

    if SESSION is None:

        print("====================================")
        print("CARGANDO ZAFRIK IA V5")
        print("Modelo: u2netp")
        print("Modo: memoria reducida")
        print("====================================")

        SESSION = new_session("u2netp")

        print("IA LISTA")

    return SESSION


# ==========================================================
# UTILIDADES
# ==========================================================

def resize_inside(img, max_side):

    width, height = img.size

    mayor = max(width, height)

    if mayor <= max_side:
        return img.copy()

    factor = max_side / float(mayor)

    nw = max(
        1,
        int(width * factor)
    )

    nh = max(
        1,
        int(height * factor)
    )

    return img.resize(
        (nw, nh),
        Image.Resampling.LANCZOS
    )


def limpiar_alpha(
    alpha,
    nivel="normal",
    halo=50,
    edge=0,
    conservar=True
):

    niveles = {
        "suave": 2,
        "normal": 5,
        "fuerte": 9
    }

    limite = niveles.get(
        str(nivel).lower(),
        5
    )

    halo = max(
        0,
        min(100, int(halo))
    )

    edge = max(
        -10,
        min(10, int(edge))
    )

    limite += int(
        halo * 0.045
    )

    if conservar:
        limite = max(
            1,
            int(limite * 0.55)
        )

    # point() evita convertir toda la máscara
    # en una enorme lista de Python.
    def ajustar(a):

        if a <= limite:
            return 0

        solido = (
            252
            if conservar
            else 245
        )

        if a >= solido:
            return 255

        if edge != 0:
            a += edge * 2

        return max(
            0,
            min(255, int(a))
        )

    return alpha.point(ajustar)


def calcular_salida(
    width,
    height,
    escala
):

    escala = max(
        1,
        min(4, int(escala))
    )

    nw = width * escala
    nh = height * escala

    factor = 1.0

    if max(nw, nh) > MAX_OUTPUT_SIDE:

        factor = min(
            factor,
            MAX_OUTPUT_SIDE /
            max(nw, nh)
        )

    pixels = nw * nh

    if pixels > MAX_OUTPUT_PIXELS:

        factor_pixels = (
            MAX_OUTPUT_PIXELS /
            float(pixels)
        ) ** 0.5

        factor = min(
            factor,
            factor_pixels
        )

    nw = max(
        1,
        int(nw * factor)
    )

    nh = max(
        1,
        int(nh * factor)
    )

    return nw, nh, escala


# ==========================================================
# STATUS
# ==========================================================

@app.get("/api/status")
def status():

    return {
        "ok": True,
        "app": "ZAFRIK IA PRO",
        "version": "5.0.0",
        "motor": "IA propia / rembg / ONNX",
        "modelo": "u2netp",
        "remove_bg": False,
        "modo_memoria": "ULTRALIGERO"
    }


# ==========================================================
# ANALIZAR
# ==========================================================

@app.post("/api/analizar")
async def analizar(
    imagen: UploadFile = File(...)
):

    try:

        contenido = await imagen.read()

        if not contenido:

            raise HTTPException(
                status_code=400,
                detail="No se recibió imagen."
            )

        if len(contenido) > MAX_UPLOAD_MB * 1024 * 1024:

            raise HTTPException(
                status_code=413,
                detail=f"La imagen supera {MAX_UPLOAD_MB} MB."
            )

        with Image.open(
            io.BytesIO(contenido)
        ) as temp:

            width, height = temp.size
            formato = temp.format or "IMAGE"

            alpha = (
                temp.mode in ("RGBA", "LA")
                or
                (
                    temp.mode == "P"
                    and
                    "transparency" in temp.info
                )
            )

        return {
            "ok": True,
            "imagen": {
                "ancho": width,
                "alto": height,
                "megapixeles": round(
                    width *
                    height /
                    1_000_000,
                    2
                ),
                "formato": formato,
                "alpha": alpha
            }
        }

    except HTTPException:
        raise

    except Exception as e:

        print(
            "ERROR ANALISIS:",
            repr(e)
        )

        raise HTTPException(
            status_code=400,
            detail="Imagen no válida."
        )


# ==========================================================
# QUITAR FONDO
# ==========================================================

@app.post("/api/quitar-fondo")
async def quitar_fondo(

    imagen: UploadFile = File(...),

    modo: str = Form("DTF"),

    escala: int = Form(1),

    nitidez: int = Form(25),

    halo: int = Form(50),

    edge: int = Form(0),

    nivelDTF: str = Form("normal"),

    conservarDetalles: str = Form("true"),

    precision: int = Form(85)
):

    contenido = None
    original = None
    ai_image = None
    resultado_ai = None
    mascara = None
    output_image = None

    try:

        print("")
        print("====================================")
        print("ZAFRIK IA PRO V5 ULTRALIGERA")
        print("====================================")

        contenido = await imagen.read()

        if not contenido:

            raise HTTPException(
                status_code=400,
                detail="No se recibió ninguna imagen."
            )

        if len(contenido) > MAX_UPLOAD_MB * 1024 * 1024:

            raise HTTPException(
                status_code=413,
                detail=(
                    f"La imagen supera el límite "
                    f"de {MAX_UPLOAD_MB} MB."
                )
            )


        # ==================================================
        # 1. ABRIR IMAGEN
        # ==================================================

        print("1/7 Abriendo imagen...")

        with Image.open(
            io.BytesIO(contenido)
        ) as source:

            source.load()

            width, height = source.size

            if (
                width > MAX_INPUT_SIDE
                or
                height > MAX_INPUT_SIDE
            ):

                raise HTTPException(
                    status_code=413,
                    detail=(
                        "La imagen es demasiado grande. "
                        f"Máximo recomendado: "
                        f"{MAX_INPUT_SIDE}px por lado."
                    )
                )

            # RGB usa menos memoria que RGBA
            # mientras todavía no necesitamos alfa.
            original = source.convert("RGB")


        print(
            "Original:",
            width,
            "x",
            height
        )


        # ==================================================
        # 2. COPIA REDUCIDA PARA IA
        # ==================================================

        print("2/7 Preparando copia para IA...")

        ai_image = resize_inside(
            original,
            AI_MAX_SIDE
        )

        ai_width, ai_height = (
            ai_image.size
        )

        print(
            "IA:",
            ai_width,
            "x",
            ai_height
        )


        # Guardar copia pequeña como JPEG.
        # Esto reduce considerablemente el buffer.
        ai_buffer = io.BytesIO()

        ai_image.save(
            ai_buffer,
            format="JPEG",
            quality=90,
            optimize=False
        )

        ai_bytes = ai_buffer.getvalue()

        ai_buffer.close()

        del ai_buffer
        del ai_image

        ai_image = None

        gc.collect()


        # ==================================================
        # 3. IA
        # ==================================================

        print("3/7 Eliminando fondo con IA...")

        resultado_bytes = remove(
            ai_bytes,
            session=get_session(),
            alpha_matting=False
        )

        del ai_bytes

        gc.collect()


        # ==================================================
        # 4. EXTRAER MÁSCARA
        # ==================================================

        print("4/7 Recuperando máscara...")

        with Image.open(
            io.BytesIO(resultado_bytes)
        ) as resultado_temp:

            resultado_temp.load()

            if resultado_temp.mode != "RGBA":
                resultado_temp = resultado_temp.convert(
                    "RGBA"
                )

            mascara = resultado_temp.getchannel(
                "A"
            ).copy()


        del resultado_bytes

        gc.collect()


        # ==================================================
        # 5. AMPLIAR SOLO LA MÁSCARA
        # ==================================================

        if mascara.size != original.size:

            mascara = mascara.resize(
                original.size,
                Image.Resampling.LANCZOS
            )


        conservar = (
            str(
                conservarDetalles
            ).lower()
            == "true"
        )


        if modo.upper() == "DTF":

            print("5/7 Limpieza DTF...")

            mascara_limpia = limpiar_alpha(
                mascara,
                nivelDTF,
                halo,
                edge,
                conservar
            )

            del mascara

            mascara = mascara_limpia

        else:

            print(
                "5/7 Conservando borde natural..."
            )


        # ==================================================
        # 6. CREAR PNG ORIGINAL + ALFA
        # ==================================================

        print("6/7 Preparando resultado...")

        output_image = original.convert(
            "RGBA"
        )

        output_image.putalpha(
            mascara
        )

        del mascara
        mascara = None

        del original
        original = None

        gc.collect()


        # ==================================================
        # MEJORA SUAVE
        # ==================================================

        nitidez = max(
            0,
            min(100, int(nitidez))
        )

        if nitidez > 0:

            # Una sola pasada.
            output_image = output_image.filter(
                ImageFilter.UnsharpMask(
                    radius=0.8,
                    percent=(
                        60 +
                        nitidez
                    ),
                    threshold=4
                )
            )


        # ==================================================
        # ESCALA
        # ==================================================

        target_w, target_h, escala_real = (
            calcular_salida(
                output_image.width,
                output_image.height,
                escala
            )
        )


        if (
            target_w != output_image.width
            or
            target_h != output_image.height
        ):

            print(
                "Escalando:",
                target_w,
                "x",
                target_h
            )

            resized = output_image.resize(
                (
                    target_w,
                    target_h
                ),
                Image.Resampling.LANCZOS
            )

            del output_image

            output_image = resized

            gc.collect()


        # ==================================================
        # 7. PNG 300 DPI
        # ==================================================

        print("7/7 Generando PNG...")

        final_buffer = io.BytesIO()

        # optimize=False consume menos CPU/RAM.
        output_image.save(
            final_buffer,
            format="PNG",
            dpi=(300, 300),
            optimize=False,
            compress_level=6
        )

        final_bytes = final_buffer.getvalue()

        final_buffer.close()


        final_width = output_image.width
        final_height = output_image.height


        del output_image
        output_image = None

        gc.collect()


        print("====================================")
        print("PROCESAMIENTO TERMINADO")
        print(
            final_width,
            "x",
            final_height,
            "300 DPI"
        )
        print("====================================")


        headers = {

            "X-Zafrik-Width":
                str(final_width),

            "X-Zafrik-Height":
                str(final_height),

            "X-Zafrik-DPI":
                "300",

            "X-Zafrik-Mode":
                modo.upper(),

            "X-Zafrik-Scale":
                str(escala_real),

            "X-Zafrik-Engine":
                "V5-ULTRALIGHT",

            "Access-Control-Expose-Headers":
                (
                    "X-Zafrik-Width, "
                    "X-Zafrik-Height, "
                    "X-Zafrik-DPI, "
                    "X-Zafrik-Mode, "
                    "X-Zafrik-Scale, "
                    "X-Zafrik-Engine"
                ),

            "Cache-Control":
                "no-store",

            "Content-Disposition":
                (
                    'attachment; '
                    'filename="ZAFRIK-IA-PRO-300DPI.png"'
                )
        }


        return Response(
            content=final_bytes,
            media_type="image/png",
            headers=headers
        )


    except HTTPException:
        raise


    except MemoryError:

        gc.collect()

        print(
            "ERROR: MEMORIA INSUFICIENTE"
        )

        raise HTTPException(
            status_code=507,
            detail=(
                "La imagen requiere demasiada memoria. "
                "Prueba una imagen más pequeña o utiliza 1X."
            )
        )


    except Exception as e:

        gc.collect()

        print(
            "ERROR ZAFRIK V5:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible procesar la imagen "
                "con ZAFRIK IA PRO."
            )
        )


    finally:

        # Intentar liberar todo lo posible
        # después de cada solicitud.
        contenido = None
        original = None
        ai_image = None
        resultado_ai = None
        mascara = None
        output_image = None

        gc.collect()


# ==========================================================
# INTERFAZ WEB
# SIEMPRE DE ÚLTIMO
# ==========================================================

if os.path.isdir("public"):

    app.mount(
        "/",
        StaticFiles(
            directory="public",
            html=True
        ),
        name="public"
    )
