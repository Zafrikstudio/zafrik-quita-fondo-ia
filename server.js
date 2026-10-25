require("dotenv").config();

const express = require("express");
const cors = require("cors");
const multer = require("multer");
const sharp = require("sharp");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

/* ==============================
   CONFIGURACIÓN
================================ */

app.use(cors());
app.use(express.json({ limit: "25mb" }));
app.use(express.urlencoded({ extended: true, limit: "25mb" }));

app.use(express.static(path.join(__dirname, "public")));

/* ==============================
   SUBIDA TEMPORAL EN MEMORIA
================================ */

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
        new Error("Formato no permitido. Usa PNG, JPG o WEBP.")
      );
    }

    cb(null, true);
  }
});

/* ==============================
   ESTADO DEL SERVIDOR
================================ */

app.get("/api/status", (req, res) => {

  res.json({
    ok: true,
    app: "ZAFRIK QUITA FONDO IA PRO",
    version: "1.0.0"
  });

});

/* ==============================
   INFORMACIÓN DE IMAGEN
================================ */

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

      const metadata = await sharp(req.file.buffer).metadata();

      res.json({
        ok: true,

        imagen: {
          ancho: metadata.width,
          alto: metadata.height,
          formato: metadata.format,
          canales: metadata.channels,
          alpha: Boolean(metadata.hasAlpha),
          espacioColor: metadata.space
        }
      });

    } catch (error) {

      console.error(error);

      res.status(500).json({
        ok: false,
        error: "No fue posible analizar la imagen."
      });

    }
  }
);

/* ==============================
   MEJORAR IMAGEN
================================ */

app.post(
  "/api/mejorar",
  upload.single("imagen"),
  async (req, res) => {

    try {

      if (!req.file) {
        return res.status(400).json({
          error: "No se recibió imagen."
        });
      }

      const escala = Math.min(
        Math.max(parseInt(req.body.escala || "1"), 1),
        4
      );

      const metadata = await sharp(req.file.buffer).metadata();

      const nuevoAncho = metadata.width * escala;
      const nuevoAlto = metadata.height * escala;

      const salida = await sharp(req.file.buffer)

        .resize({
          width: nuevoAncho,
          height: nuevoAlto,
          kernel: sharp.kernel.lanczos3
        })

        .modulate({
          brightness: 1.03,
          saturation: 1.06
        })

        .sharpen({
          sigma: 1
        })

        .png({
          compressionLevel: 9,
          adaptiveFiltering: true
        })

        .withMetadata({
          density: 300
        })

        .toBuffer();

      res.set("Content-Type", "image/png");

      res.set(
        "Content-Disposition",
        'attachment; filename="zafrik-pro.png"'
      );

      res.send(salida);

    } catch (error) {

      console.error(error);

      res.status(500).json({
        error: "Error procesando la imagen."
      });

    }
  }
);

/* ==============================
   QUITAR FONDO IA
   Lo conectaremos al motor IA
   en el siguiente paso.
================================ */

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

      /*
       Aquí conectaremos el modelo
       de eliminación de fondo IA.

       NO utilizaremos simplemente
       detección de blanco/color.
      */

      res.status(503).json({

        ok: false,

        pendingAI: true,

        message:
          "Motor ZAFRIK IA preparado. Falta conectar el modelo de eliminación de fondo."

      });

    } catch (error) {

      console.error(error);

      res.status(500).json({
        ok: false,
        error: "Error interno."
      });

    }

  }
);

/* ==============================
   FRONTEND
================================ */

app.get("*", (req, res) => {

  res.sendFile(
    path.join(__dirname, "public", "index.html")
  );

});

/* ==============================
   ERRORES
================================ */

app.use((error, req, res, next) => {

  console.error(error);

  if (error instanceof multer.MulterError) {

    if (error.code === "LIMIT_FILE_SIZE") {

      return res.status(413).json({
        ok: false,
        error: "La imagen supera el límite de 15 MB."
      });

    }
  }

  res.status(500).json({
    ok: false,
    error: error.message || "Error del servidor."
  });

});

/* ==============================
   INICIAR
================================ */

app.listen(PORT, () => {

  console.log(
    `ZAFRIK QUITA FONDO IA PRO funcionando en puerto ${PORT}`
  );

});
