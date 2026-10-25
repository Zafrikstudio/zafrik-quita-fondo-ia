import io
import os

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageEnhance, ImageFilter
from rembg import remove, new_session


# ==========================================================
# ZAFRIK QUITA FONDO IA PRO V4
# Motor propio con REMBG / ONNX
# ==========================================================

app = FastAPI(
    title="ZAFRIK IA PRO",
    version="4.0.0"
)

session = None


# ==========================================================
# CARGAR MODELO IA
# ==========================================================

def get_session():
    global session

    if session is None:
        print("Cargando motor IA ZAFRIK...")

        # Modelo relativamente ligero para comenzar en Render.
        session = new_session("u2netp")

        print("Motor IA cargado correctamente.")

    return session


# ==========================================================
# ESTADO
# ==========================================================

@app.get("/api/status")
def status():
    return {
        "ok": True,
        "app": "ZAFRIK QUITA FONDO IA PRO",
        "version": "4.0.0",
        "motor": "REMBG / ONNX",
        "api_externa": False
    }


# ==========================================================
# ANALIZAR IMAGEN
# ==========================================================

@app.post("/api/analizar")
async def analizar(
    imagen: UploadFile = File(...)
):
    try:
        contenido = await imagen.read()

        if len(contenido) > 15 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="La imagen supera el límite de 15 MB."
            )

        img = Image.open(
            io.BytesIO(contenido)
        )

        ancho, alto = img.size

        tiene_alpha = (
            img.mode in ("RGBA", "LA")
            or (
                img.mode == "P"
                and "transparency" in img.info
            )
        )

        return {
            "ok": True,
            "imagen": {
                "ancho": ancho,
                "alto": alto,
                "megapixeles": round(
                    (ancho * alto) / 1_000_000,
                    2
                ),
                "formato": img.format or "IMAGE",
                "alpha": tiene_alpha
            }
        }

    except HTTPException:
        raise

    except Exception as e:
        print("ERROR ANALIZAR:", str(e))

        raise HTTPException(
            status_code=400,
            detail="La imagen no es válida."
        )


# ==========================================================
# PROCESAR IMAGEN
# ==========================================================

@app.post("/api/quitar-fondo")
async def quitar_fondo(
    imagen: UploadFile = File(...),
    modo: str = Form("DTF"),
    escala: int = Form(1),
    nitidez: int = Form(35),
    halo: int = Form(70),
    edge: int = Form(0),
    nivelDTF: str = Form("normal"),
    conservarDetalles: str = Form("true"),
    precision: int = Form(90)
):

    try:

        # --------------------------------------------------
        # LEER ARCHIVO
        # --------------------------------------------------

        contenido = await imagen.read()

        if not contenido:
            raise HTTPException(
                status_code=400,
                detail="No se recibió ninguna imagen."
            )

        if len(contenido) > 15 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="La imagen supera el límite de 15 MB."
            )

        print("====================================")
        print("ZAFRIK IA PRO V4")
        print("Archivo:", imagen.filename)
        print("Modo:", modo)
        print("Escala:", escala)
        print("====================================")


        # --------------------------------------------------
        # 1. QUITAR FONDO CON IA
        # --------------------------------------------------

        print("1/6 IA eliminando fondo...")

        resultado_ia = remove(
            contenido,
            session=get_session(),
            alpha_matting=False
        )


        img = Image.open(
            io.BytesIO(resultado_ia)
        ).convert("RGBA")


        # --------------------------------------------------
        # 2. ANALIZAR ALFA ORIGINAL
        # --------------------------------------------------

        print("2/6 Analizando transparencia...")

        alpha = img.getchannel("A")

        alpha_data = list(
            alpha.getdata()
        )

        semi_antes = sum(
            1
            for a in alpha_data
            if 0 < a < 255
        )


        # --------------------------------------------------
        # 3. LIMPIEZA DTF
        # --------------------------------------------------

        if modo.upper() == "DTF":

            print("3/6 Limpieza DTF...")

            niveles = {
                "suave": 3,
                "normal": 7,
                "fuerte": 13
            }

            limite = niveles.get(
                nivelDTF,
                7
            )

            conservar = (
                conservarDetalles.lower()
                == "true"
            )

            halo = max(
                0,
                min(100, halo)
            )

            edge = max(
                -10,
                min(10, edge)
            )

            # Ajuste del halo
            limite += int(
                halo * 0.08
            )

            # Protege cabello, pelaje y detalles finos.
            if conservar:
                limite = max(
                    1,
                    int(limite * 0.55)
                )

            nuevo_alpha = []

            for a in alpha_data:

                # Transparencias casi invisibles
                if a <= limite:
                    a = 0

                # Píxeles casi sólidos
                solido_desde = (
                    250
                    if conservar
                    else 242
                )

                if a >= solido_desde:
                    a = 255

                # Contraer / expandir
                if 0 < a < 255 and edge != 0:
                    a += edge * 2

                a = max(
                    0,
                    min(255, int(a))
                )

                nuevo_alpha.append(a)


            alpha_limpio = Image.new(
                "L",
                img.size
            )

            alpha_limpio.putdata(
                nuevo_alpha
            )

            img.putalpha(
                alpha_limpio
            )

        else:
            print("3/6 Limpieza DTF omitida.")


        # --------------------------------------------------
        # 4. MEJORA DE COLOR
        # --------------------------------------------------

        print("4/6 Mejorando imagen...")

        alpha_actual = img.getchannel("A")

        rgb = img.convert("RGB")

        rgb = ImageEnhance.Contrast(
            rgb
        ).enhance(1.025)

        rgb = ImageEnhance.Color(
            rgb
        ).enhance(1.035)

        rgb = ImageEnhance.Brightness(
            rgb
        ).enhance(1.01)

        img = rgb.convert("RGBA")

        img.putalpha(
            alpha_actual
        )


        # --------------------------------------------------
        # 5. NITIDEZ
        # --------------------------------------------------

        nitidez = max(
            0,
            min(100, nitidez)
        )

        if nitidez > 0:

            cantidad = int(
                80 +
                (nitidez * 1.4)
            )

            img = img.filter(
                ImageFilter.UnsharpMask(
                    radius=1.0,
                    percent=cantidad,
                    threshold=3
                )
            )


        # --------------------------------------------------
        # 6. ESCALADO
        # --------------------------------------------------

        print("5/6 Escalando imagen...")

        escala = max(
            1,
            min(4, escala)
        )

        if escala > 1:

            nuevo_ancho = (
                img.width * escala
            )

            nuevo_alto = (
                img.height * escala
            )

            # Protección para Render.
            max_dimension = 10000
            max_pixels = 45_000_000

            factor = 1.0

            if (
                nuevo_ancho > max_dimension
                or
                nuevo_alto > max_dimension
            ):
                factor = min(
                    factor,
                    max_dimension / nuevo_ancho,
                    max_dimension / nuevo_alto
                )

            total_pixels = (
                nuevo_ancho *
                nuevo_alto
            )

            if total_pixels > max_pixels:

                factor_pixels = (
                    max_pixels /
                    total_pixels
                ) ** 0.5

                factor = min(
                    factor,
                    factor_pixels
                )


            nuevo_ancho = max(
                1,
                int(
                    nuevo_ancho *
                    factor
                )
            )

            nuevo_alto = max(
                1,
                int(
                    nuevo_alto *
                    factor
                )
            )


            img = img.resize(
                (
                    nuevo_ancho,
                    nuevo_alto
                ),
                Image.Resampling.LANCZOS
            )


        # --------------------------------------------------
        # ANALISIS FINAL
        # --------------------------------------------------

        print("6/6 Generando PNG 300 DPI...")

        alpha_final = img.getchannel(
            "A"
        )

        semi_despues = sum(
            1
            for a in alpha_final.getdata()
            if 0 < a < 255
        )


        # --------------------------------------------------
        # PNG 300 DPI
        # --------------------------------------------------

        output = io.BytesIO()

        img.save(
            output,
            format="PNG",
            dpi=(300, 300),
            optimize=True
        )

        resultado = output.getvalue()


        # --------------------------------------------------
        # HEADERS PARA EL FRONTEND
        # --------------------------------------------------

        expose = (
            "X-Zafrik-Width, "
            "X-Zafrik-Height, "
            "X-Zafrik-DPI, "
            "X-Zafrik-Mode, "
            "X-Zafrik-Scale, "
            "X-Zafrik-Semi-Before, "
            "X-Zafrik-Semi-After"
        )

        headers = {
            "X-Zafrik-Width":
                str(img.width),

            "X-Zafrik-Height":
                str(img.height),

            "X-Zafrik-DPI":
                "300",

            "X-Zafrik-Mode":
                modo.upper(),

            "X-Zafrik-Scale":
                str(escala),

            "X-Zafrik-Semi-Before":
                str(semi_antes),

            "X-Zafrik-Semi-After":
                str(semi_despues),

            "Access-Control-Expose-Headers":
                expose,

            "Cache-Control":
                "no-store",

            "Content-Disposition":
                'attachment; filename="ZAFRIK-IA-PRO-300DPI.png"'
        }


        print(
            "TERMINADO:",
            img.width,
            "x",
            img.height
        )


        return Response(
            content=resultado,
            media_type="image/png",
            headers=headers
        )


    except HTTPException:
        raise

    except Exception as e:

        print(
            "ERROR PROCESAMIENTO:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "No fue posible procesar "
                "la imagen con ZAFRIK IA."
            )
        )


# ==========================================================
# SERVIR LA INTERFAZ
# IMPORTANTE: SIEMPRE AL FINAL
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
