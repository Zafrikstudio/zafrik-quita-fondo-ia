require("dotenv").config();

const express = require("express");
const cors = require("cors");
const multer = require("multer");
const sharp = require("sharp");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json({ limit: "25mb" }));
app.use(express.urlencoded({ extended: true, limit: "25mb" }));
app.use(express.static(path.join(__dirname, "public")));

/* =========================================================
   ZAFRIK QUITA FONDO IA PRO V3
   IA + DTF + LIMPIEZA + ESCALADO + 300 DPI
========================================================= */

const upload = multer({
  storage: multer.memoryStorage(),

  limits: {
    fileSize: 15 * 1024 * 1024
  },

  fileFilter: (req, file, cb) => {
    const permitidos = [
      "image/png",
      "image/jpeg",
      "image/webp"
    ];

    if (!permitidos.includes(file.mimetype)) {
      return cb(
        new Error("Formato no permitido. Utiliza PNG, JPG o WEBP.")
      );
    }

    cb(null, true);
  }
});

/* =========================================================
   UTILIDADES
========================================================= */

function numero(valor, min, max, defecto) {
  let n = Number(valor);

  if (!Number.isFinite(n)) {
    n = defecto;
  }

  return Math.max(min, Math.min(max, n));
}

function entero(valor, min, max, defecto) {
  let n = parseInt(valor, 10);

  if (!Number.isFinite(n)) {
    n = defecto;
  }

  return Math.max(min, Math.min(max, n));
}

/* =========================================================
   ESTADO
========================================================= */

app.get("/api/status", (req, res) => {
  res.json({
    ok: true,
    app: "ZAFRIK QUITA FONDO IA PRO",
    version: "3.0.0",
    iaConfigurada: Boolean(process.env.REMOVE_BG_API_KEY),
    motor: "IA + ZAFRIK DTF PRO"
  });
});

/* =========================================================
   ANALIZAR IMAGEN ORIGINAL
========================================================= */

app.post(
  "/api/analizar",
  upload.single("imagen"),
  async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({
          ok: false,
          error: "No se recibió ninguna imagen."
        });
      }

      const meta = await sharp(req.file.buffer).metadata();

      const ancho = meta.width || 0;
      const alto = meta.height || 0;

      const megapixeles =
        ancho && alto
          ? Number(((ancho * alto) / 1000000).toFixed(2))
          : 0;

      res.json({
        ok: true,

        imagen: {
          ancho,
          alto,
          megapixeles,
          formato: meta.format,
          canales: meta.channels,
          alpha: Boolean(meta.hasAlpha),
          espacioColor: meta.space
        }
      });

    } catch (error) {
      console.error("ANALIZAR:", error);

      res.status(500).json({
        ok: false,
        error: "No fue posible analizar la imagen."
      });
    }
  }
);

/* =========================================================
   REMOVE.BG
========================================================= */

async function quitarFondoIA(file) {
  const apiKey = process.env.REMOVE_BG_API_KEY;

  if (!apiKey) {
    throw new Error(
      "REMOVE_BG_API_KEY no está configurada en Render."
    );
  }

  const form = new FormData();

  const blob = new Blob(
    [file.buffer],
    {
      type: file.mimetype || "application/octet-stream"
    }
  );

  form.append(
    "image_file",
    blob,
    file.originalname || "imagen.png"
  );

  form.append("size", "full");
  form.append("format", "png");

  const response = await fetch(
    "https://api.remove.bg/v1.0/removebg",
    {
      method: "POST",

      headers: {
        "X-Api-Key": apiKey
      },

      body: form
    }
  );

  if (!response.ok) {
    const detalle = await response.text();

    console.error(
      "REMOVE.BG:",
      response.status,
      detalle
    );

    if (response.status === 402) {
      throw new Error(
        "No hay créditos suficientes en el motor de eliminación de fondo."
      );
    }

    if (
      response.status === 401 ||
      response.status === 403
    ) {
      throw new Error(
        "La API Key del motor de IA no es válida."
      );
    }

    if (response.status === 429) {
      throw new Error(
        "Se alcanzó temporalmente el límite de procesamiento."
      );
    }

    throw new Error(
      "La IA no pudo eliminar el fondo."
    );
  }

  const arrayBuffer = await response.arrayBuffer();

  return Buffer.from(arrayBuffer);
}

/* =========================================================
   ANÁLISIS DE ALFA
========================================================= */

async function analizarAlpha(buffer) {
  const { data, info } =
    await sharp(buffer)
      .ensureAlpha()
      .raw()
      .toBuffer({
        resolveWithObject: true
      });

  let transparentes = 0;
  let semitransparentes = 0;
  let opacos = 0;

  for (
    let i = 0;
    i < data.length;
    i += info.channels
  ) {
    const alpha = data[i + 3];

    if (alpha === 0) {
      transparentes++;
    } else if (alpha === 255) {
      opacos++;
    } else {
      semitransparentes++;
    }
  }

  return {
    ancho: info.width,
    alto: info.height,
    transparentes,
    semitransparentes,
    opacos,
    total:
      transparentes +
      semitransparentes +
      opacos
  };
}

/* =========================================================
   LIMPIEZA DTF
========================================================= */

async function limpiezaDTF(
  buffer,
  {
    nivel = "normal",
    halo = 70,
    edge = 0,
    conservarDetalles = true
  } = {}
) {
  halo = numero(halo, 0, 100, 70);
  edge = numero(edge, -10, 10, 0);

  const niveles = {
    suave: {
      base: 2,
      haloMax: 20
    },

    normal: {
      base: 5,
      haloMax: 35
    },

    fuerte: {
      base: 10,
      haloMax: 55
    }
  };

  const config =
    niveles[nivel] ||
    niveles.normal;

  const { data, info } =
    await sharp(buffer)
      .ensureAlpha()
      .raw()
      .toBuffer({
        resolveWithObject: true
      });

  const haloFactor = halo / 100;

  let limite =
    config.base +
    config.haloMax * haloFactor;

  /*
    Cuando se conservan detalles finos,
    somos menos agresivos con el alfa.
  */

  if (conservarDetalles) {
    limite *= 0.55;
  }

  const edgeOffset =
    Math.round(edge * 2);

  for (
    let i = 0;
    i < data.length;
    i += info.channels
  ) {
    let alpha = data[i + 3];

    /*
      Transparencia prácticamente invisible.
    */

    if (alpha <= limite) {
      alpha = 0;
    }

    /*
      Convertimos alfa casi sólido
      en completamente sólido.
    */

    const solidoDesde =
      conservarDetalles
        ? 250
        : 242;

    if (alpha >= solidoDesde) {
      alpha = 255;
    }

    /*
      Contraer / expandir.
      Negativo = contraer.
      Positivo = expandir.
    */

    if (
      alpha > 0 &&
      alpha < 255 &&
      edgeOffset !== 0
    ) {
      alpha += edgeOffset;
    }

    alpha =
      Math.max(
        0,
        Math.min(
          255,
          Math.round(alpha)
        )
      );

    data[i + 3] = alpha;
  }

  return sharp(
    data,
    {
      raw: {
        width: info.width,
        height: info.height,
        channels: info.channels
      }
    }
  )
    .png({
      compressionLevel: 9,
      adaptiveFiltering: true
    })
    .toBuffer();
}

/* =========================================================
   MEJORA DE COLOR / NITIDEZ
========================================================= */

async function mejorarImagen(
  buffer,
  {
    nitidez = 35,
    mejorarColor = true,
    mejorarContraste = true
  } = {}
) {
  nitidez =
    numero(
      nitidez,
      0,
      100,
      35
    );

  let pipeline =
    sharp(buffer)
      .ensureAlpha();

  if (mejorarColor) {
    pipeline =
      pipeline.modulate({
        brightness: 1.015,
        saturation: 1.035
      });
  }

  if (mejorarContraste) {
    pipeline =
      pipeline.linear(
        1.025,
        -2
      );
  }

  if (nitidez > 0) {
    const sigma =
      0.45 +
      (nitidez / 100) *
      0.9;

    pipeline =
      pipeline.sharpen({
        sigma
      });
  }

  return pipeline
    .png({
      compressionLevel: 9,
      adaptiveFiltering: true
    })
    .toBuffer();
}

/* =========================================================
   ESCALADO
========================================================= */

async function aumentarResolucion(
  buffer,
  escala = 1
) {
  escala =
    entero(
      escala,
      1,
      4,
      1
    );

  if (escala === 1) {
    return buffer;
  }

  const meta =
    await sharp(buffer).metadata();

  const originalW =
    meta.width || 1;

  const originalH =
    meta.height || 1;

  let width =
    Math.round(
      originalW * escala
    );

  let height =
    Math.round(
      originalH * escala
    );

  /*
    Protección de memoria.
    Render Free tiene recursos limitados.
  */

  const maxDimension = 12000;
  const maxPixels = 60000000;

  let factor = 1;

  if (
    width > maxDimension ||
    height > maxDimension
  ) {
    factor =
      Math.min(
        factor,
        maxDimension / width,
        maxDimension / height
      );
  }

  if (
    width * height >
    maxPixels
  ) {
    factor =
      Math.min(
        factor,
        Math.sqrt(
          maxPixels /
          (width * height)
        )
      );
  }

  if (factor < 1) {
    width =
      Math.max(
        1,
        Math.floor(
          width * factor
        )
      );

    height =
      Math.max(
        1,
        Math.floor(
          height * factor
        )
      );
  }

  return sharp(buffer)
    .resize({
      width,
      height,
      fit: "fill",
      kernel:
        sharp.kernel.lanczos3
    })
    .png({
      compressionLevel: 9,
      adaptiveFiltering: true
    })
    .toBuffer();
}

/* =========================================================
   PROCESO COMPLETO
========================================================= */

app.post(
  "/api/quitar-fondo",
  upload.single("imagen"),
  async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({
          ok: false,
          error: "Selecciona una imagen."
        });
      }

      const modo =
        String(
          req.body.modo ||
          "DTF"
        ).toUpperCase();

      const escala =
        entero(
          req.body.escala,
          1,
          4,
          1
        );

      const nitidez =
        numero(
          req.body.nitidez,
          0,
          100,
          35
        );

      const halo =
        numero(
          req.body.halo,
          0,
          100,
          70
        );

      const edge =
        numero(
          req.body.edge,
          -10,
          10,
          0
        );

      const nivelDTF =
        ["suave", "normal", "fuerte"]
          .includes(req.body.nivelDTF)
          ? req.body.nivelDTF
          : "normal";

      const conservarDetalles =
        String(
          req.body.conservarDetalles ??
          "true"
        ) !== "false";

      console.log(
        "================================"
      );

      console.log(
        "ZAFRIK IA PRO V3"
      );

      console.log(
        "Archivo:",
        req.file.originalname
      );

      console.log(
        "Modo:",
        modo
      );

      console.log(
        "Escala:",
        escala + "X"
      );

      /*
        1. IA
      */

      console.log(
        "1/5 Eliminando fondo..."
      );

      let resultado =
        await quitarFondoIA(
          req.file
        );

      const analisisInicial =
        await analizarAlpha(
          resultado
        );

      /*
        2. DTF
      */

      if (modo === "DTF") {
        console.log(
          "2/5 Limpieza DTF..."
        );

        resultado =
          await limpiezaDTF(
            resultado,
            {
              nivel: nivelDTF,
              halo,
              edge,
              conservarDetalles
            }
          );
      }

      /*
        3. Mejora
      */

      console.log(
        "3/5 Mejorando imagen..."
      );

      resultado =
        await mejorarImagen(
          resultado,
          {
            nitidez,
            mejorarColor: true,
            mejorarContraste: true
          }
        );

      /*
        4. Escalado
      */

      console.log(
        "4/5 Resolución..."
      );

      resultado =
        await aumentarResolucion(
          resultado,
          escala
        );

      /*
        5. 300 DPI
      */

      console.log(
        "5/5 PNG 300 DPI..."
      );

      resultado =
        await sharp(resultado)
          .png({
            compressionLevel: 9,
            adaptiveFiltering: true
          })
          .withMetadata({
            density: 300
          })
          .toBuffer();

      const metaFinal =
        await sharp(
          resultado
        ).metadata();

      const analisisFinal =
        await analizarAlpha(
          resultado
        );

      /*
        Información para el frontend
        mediante headers.
      */

      res.set(
        "X-Zafrik-Width",
        String(metaFinal.width || "")
      );

      res.set(
        "X-Zafrik-Height",
        String(metaFinal.height || "")
      );

      res.set(
        "X-Zafrik-DPI",
        "300"
      );

      res.set(
        "X-Zafrik-Mode",
        modo
      );

      res.set(
        "X-Zafrik-Scale",
        String(escala)
      );

      res.set(
        "X-Zafrik-Semi-Before",
        String(
          analisisInicial.semitransparentes
        )
      );

      res.set(
        "X-Zafrik-Semi-After",
        String(
          analisisFinal.semitransparentes
        )
      );

      /*
        Para que el navegador pueda
        leer nuestros headers con CORS.
      */

      res.set(
        "Access-Control-Expose-Headers",
        [
          "X-Zafrik-Width",
          "X-Zafrik-Height",
          "X-Zafrik-DPI",
          "X-Zafrik-Mode",
          "X-Zafrik-Scale",
          "X-Zafrik-Semi-Before",
          "X-Zafrik-Semi-After"
        ].join(", ")
      );

      res.set(
        "Content-Type",
        "image/png"
      );

      res.set(
        "Content-Disposition",
        'attachment; filename="ZAFRIK-IA-PRO-300DPI.png"'
      );

      res.set(
        "Cache-Control",
        "no-store"
      );

      res.send(resultado);

      console.log(
        "PROCESO TERMINADO"
      );

      console.log(
        `${metaFinal.width}x${metaFinal.height} 300 DPI`
      );

      console.log(
        "================================"
      );

    } catch (error) {
      console.error(
        "ERROR PROCESAMIENTO:",
        error
      );

      res.status(500).json({
        ok: false,
        error:
          error.message ||
          "No fue posible procesar la imagen."
      });
    }
  }
);

/* =========================================================
   MEJORAR SIN QUITAR FONDO
========================================================= */

app.post(
  "/api/mejorar",
  upload.single("imagen"),
  async (req, res) => {
    try {
      if (!req.file) {
        return res.status(400).json({
          ok: false,
          error: "No se recibió imagen."
        });
      }

      const escala =
        entero(
          req.body.escala,
          1,
          4,
          1
        );

      const nitidez =
        numero(
          req.body.nitidez,
          0,
          100,
          35
        );

      let resultado =
        await mejorarImagen(
          req.file.buffer,
          {
            nitidez,
            mejorarColor: true,
            mejorarContraste: true
          }
        );

      resultado =
        await aumentarResolucion(
          resultado,
          escala
        );

      resultado =
        await sharp(resultado)
          .png({
            compressionLevel: 9,
            adaptiveFiltering: true
          })
          .withMetadata({
            density: 300
          })
          .toBuffer();

      res.set(
        "Content-Type",
        "image/png"
      );

      res.set(
        "Content-Disposition",
        'attachment; filename="ZAFRIK-HD-300DPI.png"'
      );

      res.send(resultado);

    } catch (error) {
      console.error(
        "MEJORAR:",
        error
      );

      res.status(500).json({
        ok: false,
        error:
          "No fue posible mejorar la imagen."
      });
    }
  }
);

/* =========================================================
   FRONTEND
========================================================= */

app.get("*", (req, res) => {
  res.sendFile(
    path.join(
      __dirname,
      "public",
      "index.html"
    )
  );
});

/* =========================================================
   ERRORES
========================================================= */

app.use(
  (error, req, res, next) => {
    console.error(
      "SERVIDOR:",
      error
    );

    if (
      error instanceof
      multer.MulterError
    ) {
      if (
        error.code ===
        "LIMIT_FILE_SIZE"
      ) {
        return res
          .status(413)
          .json({
            ok: false,
            error:
              "La imagen supera el límite de 15 MB."
          });
      }
    }

    res.status(500).json({
      ok: false,
      error:
        error.message ||
        "Error interno del servidor."
    });
  }
);

/* =========================================================
   INICIO
========================================================= */

app.listen(
  PORT,
  () => {
    console.log(
      "===================================="
    );

    console.log(
      "ZAFRIK QUITA FONDO IA PRO V3"
    );

    console.log(
      `Puerto: ${PORT}`
    );

    console.log(
      process.env.REMOVE_BG_API_KEY
        ? "IA: CONFIGURADA"
        : "IA: FALTA API KEY"
    );

    console.log(
      "===================================="
    );
  }
);
