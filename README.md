# Sitio de creadora (Flask)

## Arrancar (Windows, terminal de VS Code, dentro de esta carpeta)
    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt
    python app.py crear-admin     # una sola vez
    python app.py                 # http://127.0.0.1:5000

Si ya tenías una base de datos de la versión anterior, no la borres: se actualiza sola al arrancar.

## Flujo
1. Admin -> crea la cuenta de la creadora y la **verifica** (edad y consentimiento revisados). Sin verificar no puede publicar.
2. Admin -> Ajustes: nombre del sitio e instrucciones de pago. Planes: edita precios y días.
3. El usuario se registra, elige plan en **Mi cuenta** y manda su referencia de pago. El admin **aprueba** y se activa.
   (También puedes "Dar días" a mano.) Al vencer, vuelve a ver todo bloqueado.
4. Cualquiera registrado puede reportar publicaciones; el admin las revisa en su panel.

## Comandos
    python app.py respaldo        # zip con base de datos + archivos en /respaldos

## Publicar en internet
    set PROD=1
    python -m waitress --port=8000 app:app
Ponlo detrás de HTTPS (Cloudflare, Caddy o nginx). Con PROD=1 la cookie de sesión solo viaja por HTTPS.

## Pendiente fuera del código
- Revisar/completar Términos y Privacidad (`templates/legal.html`) con un abogado.
- Guardar fuera del sitio la verificación de edad/identidad y el consentimiento de quien aparezca en el contenido.
- Pagos automáticos: hoy son manuales (transferencia + aprobación). Antes de integrar un procesador, confirma que acepta este tipo de contenido.
