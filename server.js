require("dotenv").config();

const express = require("express");
const cors = require("cors");
const multer = require("multer");
const sharp = require("sharp");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

/* =====================================================
   ZAFRIK QUITA FONDO IA PRO
   Backend Node + Remove.bg + Sharp
===================================================== */

app.use(cors());

app.use(
  express.json({
    limit: "25mb"
  })
);

app.use(
  express.urlencoded({
    extended: true,
    limit: "25mb"
  })
);

app.use(express.static(path.join(__dirname, "public")));


/* =====================================================
   SUBIDA DE ARCHIVOS
===================================================== */

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
        new Error(
          "Formato no permitido. Utiliza PNG, JPG o WEBP."
        )
      );

    }

    cb(null, true);

  }

});


/* =====================================================
   ESTADO DEL SERVIDOR
===================================================== */

app.get("/api/status", (req, res) => {

  res.json({

    ok: true,

    app: "ZAFRIK QUITA FONDO IA PRO",

    version: "2.0.0",

    ia: Boolean(process.env.REMOVE_BG_API_KEY),

    motor: "remove.bg + ZAFRIK DTF Engine"

  });

});


/* =====================================================
   ANALIZAR IMAGEN
===================================================== */

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

      const metadata =
        await sharp(req.file.buffer).metadata();

      const megapixeles =
        ((metadata.width || 0) *
        (metadata.height || 0)) /
        1000000;

      res.json({

        ok: true,

        imagen: {

          ancho: metadata.width,

          alto: metadata.height,

          megapixeles:
            Number(megapixeles.toFixed(2)),

          formato: metadata.format,

          canales: metadata.channels,

          alpha: Boolean(metadata.hasAlpha),

          espacioColor: metadata.space

        }

      });

    }

    catch (error) {

      console.error(
        "ERROR ANALIZAR:",
        error
      );

      res.status(500).json({

        ok: false,

        error:
          "No fue posible analizar la imagen."

      });

    }

  }
);


/* =====================================================
   MOTOR REMOVE.BG
===================================================== */

async function quitarFondoIA(file) {

  const apiKey =
    process.env.REMOVE_BG_API_KEY;

  if (!apiKey) {

    throw new Error(
      "REMOVE_BG_API_KEY no está configurada en Render."
    );

  }


  const form = new FormData();


  const blob = new Blob(
    [file.buffer],
    {
      type:
        file.mimetype ||
        "application/octet-stream"
    }
  );


  form.append(
    "image_file",
    blob,
    file.originalname || "imagen.png"
  );


  /*
    FULL:
    solicitamos buena resolución
    para el archivo de impresión.
  */

  form.append(
    "size",
    "full"
  );


  form.append(
    "format",
    "png"
  );


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

    const mensaje =
      await response.text();

    console.error(
      "REMOVE.BG:",
      response.status,
      mensaje
    );


    if (response.status === 402) {

      throw new Error(
        "No hay créditos suficientes en remove.bg."
      );

    }


    if (
      response.status === 401 ||
      response.status === 403
    ) {

      throw new Error(
        "La API Key de remove.bg no es válida."
      );

    }


    if (response.status === 429) {

      throw new Error(
        "Se alcanzó temporalmente el límite de remove.bg."
      );

    }


    throw new Error(
      "La IA no pudo eliminar el fondo."
    );

  }


  const arrayBuffer =
    await response.arrayBuffer();


  return Buffer.from(
    arrayBuffer
  );

}


/* =====================================================
   LIMPIEZA ALFA ZAFRIK
===================================================== */

async function limpiarAlpha(
  buffer,
  opciones = {}
) {

  const halo =
    Math.max(
      0,
      Math.min(
        100,
        Number(opciones.halo || 0)
      )
    );


  const edge =
    Math.max(
      -10,
      Math.min(
        10,
        Number(opciones.edge || 0)
      )
    );


  const smooth =
    Math.max(
      0,
      Math.min(
        100,
        Number(opciones.smooth || 0)
      )
    );


  /*
    Primero garantizamos RGBA.
  */

  const base =
    sharp(buffer)
      .ensureAlpha();


  const {
    data,
    info
  } =
    await base
      .raw()
      .toBuffer({
        resolveWithObject: true
      });


  /*
    Limpieza controlada de
    píxeles semitransparentes.
  */

  const haloStrength =
    halo / 100;


  const edgeOffset =
    edge * 3;


  for (
    let i = 0;
    i < data.length;
    i += info.channels
  ) {

    let alpha =
      data[i + 3];


    /*
      Eliminamos transparencia
      extremadamente débil.
    */

    const limiteBajo =
      Math.round(
        10 +
        (55 * haloStrength)
      );


    if (alpha <= limiteBajo) {

      alpha = 0;

    }


    /*
      Fortalecemos píxeles
      suficientemente sólidos.
    */

    else if (
      alpha >=
      255 - (30 * haloStrength)
    ) {

      alpha = 255;

    }


    /*
      Contraer / expandir borde.
    */

    alpha =
      Math.max(
        0,
        Math.min(
          255,
          alpha + edgeOffset
        )
      );


    data[i + 3] =
      Math.round(alpha);

  }


  let pipeline =
    sharp(
      data,
      {
        raw: {
          width: info.width,
          height: info.height,
          channels: info.channels
        }
      }
    );


  /*
    Suavizado muy leve.
    No queremos destruir
    cabello ni detalles.
  */

  if (smooth > 0) {

    const sigma =
      0.3 +
      (smooth / 100) * 0.45;

    pipeline =
      pipeline.sharpen({
        sigma
      });

  }


  return await pipeline

    .png({
      compressionLevel: 9,
      adaptiveFiltering: true
    })

    .withMetadata({
      density: 300
    })

    .toBuffer();

}


/* =====================================================
   QUITAR FONDO CON IA
===================================================== */

app.post(
  "/api/quitar-fondo",
  upload.single("imagen"),
  async (req, res) => {

    try {

      if (!req.file) {

        return res.status(400).json({

          ok: false,

          error:
            "Selecciona una imagen."

        });

      }


      console.log(
        "Procesando IA:",
        req.file.originalname
      );


      /*
        PASO 1
        IA elimina fondo.
      */

      let resultado =
        await quitarFondoIA(
          req.file
        );


      /*
        PASO 2
        Limpieza ZAFRIK
        para impresión.
      */

      resultado =
        await limpiarAlpha(
          resultado,
          {

            halo:
              req.body.halo,

            edge:
              req.body.edge,

            smooth:
              req.body.smooth

          }
        );


      /*
        PASO 3
        PNG 300 DPI.
      */

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
        'attachment; filename="ZAFRIK-SIN-FONDO.png"'
      );


      res.set(
        "Cache-Control",
        "no-store"
      );


      res.send(
        resultado
      );

    }

    catch (error) {

      console.error(
        "ERROR IA:",
        error
      );


      res.status(500).json({

        ok: false,

        error:
          error.message ||
          "No fue posible quitar el fondo."

      });

    }

  }
);


/* =====================================================
   MEJORAR / AUMENTAR RESOLUCIÓN
===================================================== */

app.post(
  "/api/mejorar",
  upload.single("imagen"),
  async (req, res) => {

    try {

      if (!req.file) {

        return res.status(400).json({

          ok: false,

          error:
            "No se recibió imagen."

        });

      }


      const escala =
        Math.min(
          Math.max(
            parseInt(
              req.body.escala || "1"
            ),
            1
          ),
          4
        );


      const nitidez =
        Math.max(
          0,
          Math.min(
            100,
            Number(
              req.body.nitidez || 35
            )
          )
        );


      const metadata =
        await sharp(
          req.file.buffer
        ).metadata();


      const nuevoAncho =
        Math.round(
          metadata.width *
          escala
        );


      const nuevoAlto =
        Math.round(
          metadata.height *
          escala
        );


      /*
        Evita imágenes
        absurdamente grandes.
      */

      const maxDimension =
        12000;


      let anchoFinal =
        nuevoAncho;


      let altoFinal =
        nuevoAlto;


      if (
        anchoFinal >
          maxDimension ||
        altoFinal >
          maxDimension
      ) {

        const ratio =
          Math.min(
            maxDimension /
              anchoFinal,

            maxDimension /
              altoFinal
          );


        anchoFinal =
          Math.round(
            anchoFinal *
            ratio
          );


        altoFinal =
          Math.round(
            altoFinal *
            ratio
          );

      }


      let pipeline =
        sharp(
          req.file.buffer
        )

        .resize({

          width:
            anchoFinal,

          height:
            altoFinal,

          kernel:
            sharp.kernel.lanczos3,

          fit:
            "fill"

        })

        .modulate({

          brightness:
            1.02,

          saturation:
            1.04

        });


      if (nitidez > 0) {

        const sigma =
          0.5 +
          (
            nitidez /
            100
          ) * 1.2;


        pipeline =
          pipeline.sharpen({
            sigma
          });

      }


      const salida =
        await pipeline

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


      res.send(
        salida
      );

    }

    catch (error) {

      console.error(
        "ERROR MEJORAR:",
        error
      );


      res.status(500).json({

        ok: false,

        error:
          "Error mejorando la imagen."

      });

    }

  }
);


/* =====================================================
   FRONTEND
===================================================== */

app.get("*", (req, res) => {

  res.sendFile(
    path.join(
      __dirname,
      "public",
      "index.html"
    )
  );

});


/* =====================================================
   MANEJO DE ERRORES
===================================================== */

app.use(
  (
    error,
    req,
    res,
    next
  ) => {

    console.error(
      "ERROR SERVIDOR:",
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


/* =====================================================
   INICIAR SERVIDOR
===================================================== */

app.listen(
  PORT,
  () => {

    console.log(
      "===================================="
    );

    console.log(
      "ZAFRIK QUITA FONDO IA PRO"
    );

    console.log(
      `Puerto: ${PORT}`
    );

    console.log(
      process.env.REMOVE_BG_API_KEY
        ? "Motor IA: CONFIGURADO"
        : "Motor IA: FALTA API KEY"
    );

    console.log(
      "===================================="
    );

  }
);
