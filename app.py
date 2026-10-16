import os, sqlite3, uuid, secrets, sys, getpass, time, zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
DB = os.path.join(BASE, "sitio.db")
SITE_NAME = os.environ.get("SITE_NAME", "Mi Espacio")  # cambia el nombre aquí o con la variable SITE_NAME
IMG = {"jpg", "jpeg", "png", "webp", "gif"}
VID = {"mp4", "webm", "mov"}

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=500 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=os.environ.get("PROD") == "1", SESSION_COOKIE_SAMESITE="Lax")
os.makedirs(UPLOADS, exist_ok=True)
kf = os.path.join(BASE, ".secret_key")
if not os.path.exists(kf):
    open(kf, "w").write(secrets.token_hex(32))
app.secret_key = open(kf).read()


def init():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE,
      pw TEXT, role TEXT DEFAULT 'user', active INTEGER DEFAULT 1, bio TEXT DEFAULT '',
      avatar TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, title TEXT, caption TEXT,
      filename TEXT, kind TEXT, public INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY, name TEXT, days INTEGER, price REAL, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS subs(id INTEGER PRIMARY KEY, user_id INTEGER, plan_id INTEGER, ends TEXT, source TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, user_id INTEGER, plan_id INTEGER, ref TEXT, status TEXT DEFAULT 'pendiente', created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, post_id INTEGER, user_id INTEGER, reason TEXT, done INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS fails(k TEXT, t REAL);
    """)
    try: c.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    if not c.execute("SELECT 1 FROM plans").fetchone():
        c.execute("INSERT INTO plans(name,days,price) VALUES('Mensual',30,199)")
    c.commit(); c.close()
init()
FMT = "%Y-%m-%d %H:%M:%S"


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def _close(e):
    d = g.pop("db", None)
    if d: d.close()

def me():
    uid = session.get("uid")
    if not uid: return None
    return db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()

def now(): return datetime.now(timezone.utc).strftime(FMT)

def setting(k, d=""):
    r = db().execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r and r["v"] else d

def sub_end(uid):
    return db().execute("SELECT MAX(ends) FROM subs WHERE user_id=?", (uid,)).fetchone()[0]

def has_access(u):
    if not u: return False
    if u["role"] in ("admin", "creator"): return True
    e = sub_end(u["id"]); return bool(e and e > now())

def grant(uid, days, plan_id, source):
    cur = sub_end(uid); base = max(now(), cur) if cur else now()
    end = (datetime.strptime(base, FMT) + timedelta(days=days)).strftime(FMT)
    db().execute("INSERT INTO subs(user_id,plan_id,ends,source) VALUES(?,?,?,?)", (uid, plan_id, end, source)); db().commit()

def too_many(key):
    d = db(); d.execute("DELETE FROM fails WHERE t<?", (time.time() - 900,))
    return d.execute("SELECT COUNT(*) FROM fails WHERE k=?", (key,)).fetchone()[0] >= 5

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

@app.context_processor
def _ctx():
    u = me(); return dict(me=u, access=has_access(u), csrf=csrf, site_name=setting("site_name", SITE_NAME))

@app.before_request
def _guard():
    if request.method == "POST":
        t = request.form.get("csrf")
        if not t or t != session.get("csrf"): abort(400)
    if not session.get("adult") and request.endpoint not in ("edad", "static"):
        return redirect(url_for("edad"))

@app.after_request
def _hdr(r):
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self'"})
    return r

def need(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u: return redirect(url_for("login", next=request.path))
            if roles and u["role"] not in roles: abort(403)
            return f(*a, **k)
        return w
    return deco

def save_file(f, allowed):
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in allowed: return None, None
    name = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOADS, name))
    return name, ("video" if ext in VID else "image")

def rm_file(name):
    try: os.remove(os.path.join(UPLOADS, os.path.basename(name)))
    except (OSError, TypeError): pass


@app.route("/edad", methods=["GET", "POST"])
def edad():
    if request.method == "POST":
        if request.form.get("si"): session["adult"] = True; return redirect(url_for("index"))
        return redirect("https://www.google.com")
    return render_template("edad.html")

@app.route("/")
def index():
    d = db()
    creator = d.execute("SELECT * FROM users WHERE role='creator' AND active=1 ORDER BY id LIMIT 1").fetchone()
    posts = d.execute("SELECT * FROM posts ORDER BY id DESC").fetchall()
    return render_template("index.html", creator=creator, posts=posts)

@app.route("/media/<name>")
def media(name):
    name = os.path.basename(name)
    pub = db().execute("SELECT 1 FROM posts WHERE filename=? AND public=1 UNION SELECT 1 FROM users WHERE avatar=?",
                       (name, name)).fetchone()
    if not pub and not has_access(me()): abort(403)   # solo miembros activos, creadora y admin
    r = send_from_directory(UPLOADS, name)
    r.headers["Cache-Control"] = "private, max-age=3600"
    return r

@app.route("/registro", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        email = request.form.get("email", "").strip().lower()[:120]
        pw = request.form.get("password", "")
        if not name or "@" not in email: flash("Revisa tu nombre y correo.")
        elif len(pw) < 8: flash("La contraseña debe tener al menos 8 caracteres.")
        elif not request.form.get("adult"): flash("Debes confirmar que eres mayor de 18 años.")
        else:
            try:
                cur = db().execute("INSERT INTO users(name,email,pw) VALUES(?,?,?)",
                                   (name, email, generate_password_hash(pw)))
                db().commit()
                session.clear(); session["adult"] = True; session["uid"] = cur.lastrowid
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                flash("Ese correo ya está registrado.")
    return render_template("auth.html", mode="register")

@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = request.remote_addr + "|" + email
        if too_many(key): flash("Demasiados intentos. Espera 15 minutos e inténtalo de nuevo."); return render_template("auth.html", mode="login")
        u = db().execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
        if u and check_password_hash(u["pw"], request.form.get("password", "")):
            db().execute("DELETE FROM fails WHERE k=?", (key,)); db().commit()
            session.clear(); session["adult"] = True; session["uid"] = u["id"]
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"): nxt = url_for("index")
            return redirect(nxt)
        db().execute("INSERT INTO fails VALUES(?,?)", (key, time.time())); db().commit()
        flash("Correo o contraseña incorrectos.")
    return render_template("auth.html", mode="login")

@app.route("/salir", methods=["POST"])
def logout():
    session.clear(); session["adult"] = True
    return redirect(url_for("index"))

@app.route("/creadora", methods=["GET", "POST"])
@need("creator")
def creator():
    u, d = me(), db()
    if request.method == "POST":
        a = request.form.get("action")
        if a == "post" and not u["verified"]:
            flash("El administrador debe verificar tu cuenta (edad y consentimiento) antes de que puedas publicar.")
        elif a == "post":
            f = request.files.get("file")
            title = request.form.get("title", "").strip()[:100]
            if not f or not f.filename or not title: flash("Agrega un título y un archivo.")
            else:
                name, kind = save_file(f, IMG | VID)
                if not name: flash("Formato no permitido. Usa JPG, PNG, WEBP, GIF, MP4, WEBM o MOV.")
                else:
                    d.execute("INSERT INTO posts(title,caption,filename,kind,public) VALUES(?,?,?,?,?)",
                              (title, request.form.get("caption", "").strip()[:500], name, kind,
                               1 if request.form.get("public") else 0))
                    d.commit(); flash("Publicado.")
        elif a == "profile":
            av = u["avatar"]; f = request.files.get("avatar")
            if f and f.filename:
                n, _ = save_file(f, IMG)
                if n: rm_file(av); av = n
                else: flash("La foto de perfil debe ser JPG, PNG, WEBP o GIF.")
            d.execute("UPDATE users SET name=?, bio=?, avatar=? WHERE id=?",
                      (request.form.get("name", u["name"]).strip()[:60] or u["name"],
                       request.form.get("bio", "").strip()[:600], av, u["id"]))
            d.commit(); flash("Perfil guardado.")
        elif a == "delete":
            p = d.execute("SELECT * FROM posts WHERE id=?", (request.form.get("id"),)).fetchone()
            if p: rm_file(p["filename"]); d.execute("DELETE FROM posts WHERE id=?", (p["id"],)); d.commit(); flash("Eliminado.")
        return redirect(url_for("creator"))
    return render_template("creator.html", u=me(), posts=d.execute("SELECT * FROM posts ORDER BY id DESC").fetchall())

@app.route("/cuenta", methods=["GET", "POST"])
@need()
def cuenta():
    u, d = me(), db(); a = request.form.get("action")
    if request.method == "POST":
        if a == "password":
            if not check_password_hash(u["pw"], request.form.get("old", "")): flash("La contraseña actual no es correcta.")
            elif len(request.form.get("new", "")) < 8: flash("La nueva contraseña debe tener al menos 8 caracteres.")
            else:
                d.execute("UPDATE users SET pw=? WHERE id=?", (generate_password_hash(request.form["new"]), u["id"])); d.commit(); flash("Contraseña actualizada.")
        elif a == "solicitar":
            plan = d.execute("SELECT * FROM plans WHERE id=? AND active=1", (request.form.get("plan"),)).fetchone()
            ref = request.form.get("ref", "").strip()[:200]
            if not plan or not ref: flash("Elige un plan e indica tu referencia de pago.")
            elif d.execute("SELECT 1 FROM requests WHERE user_id=? AND status='pendiente'", (u["id"],)).fetchone(): flash("Ya tienes una solicitud en revisión.")
            else:
                d.execute("INSERT INTO requests(user_id,plan_id,ref) VALUES(?,?,?)", (u["id"], plan["id"], ref)); d.commit()
                flash("Solicitud enviada. Se activa en cuanto se confirme tu pago.")
        return redirect(url_for("cuenta"))
    return render_template("cuenta.html", ends=sub_end(u["id"]), how=setting("payment_instructions", "Pide al administrador los datos de pago."),
        plans=d.execute("SELECT * FROM plans WHERE active=1").fetchall(),
        reqs=d.execute("SELECT r.*, p.name pname FROM requests r LEFT JOIN plans p ON p.id=r.plan_id WHERE user_id=? ORDER BY r.id DESC LIMIT 5", (u["id"],)).fetchall())

@app.route("/reportar/<int:pid>", methods=["POST"])
@need()
def report(pid):
    db().execute("INSERT INTO reports(post_id,user_id,reason) VALUES(?,?,?)", (pid, me()["id"], request.form.get("reason", "").strip()[:300])); db().commit()
    flash("Gracias, revisaremos el reporte."); return redirect(url_for("index"))

@app.route("/legal/<which>")
def legal(which):
    if which not in ("terminos", "privacidad"): abort(404)
    return render_template("legal.html", which=which)

@app.route("/admin", methods=["GET", "POST"])
@need("admin")
def admin():
    u, d = me(), db()
    if request.method == "POST":
        f = request.form; a = f.get("action"); i = f.get("id")
        if a == "crear_creadora":
            email = f.get("email", "").strip().lower(); pw = f.get("password", "")
            if "@" not in email or len(pw) < 8 or not f.get("name", "").strip(): flash("Completa nombre, correo y contraseña (mínimo 8 caracteres).")
            else:
                try:
                    d.execute("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,'creator')", (f["name"].strip()[:60], email, generate_password_hash(pw)))
                    d.commit(); flash("Cuenta de creadora creada. Verifícala para que pueda publicar.")
                except sqlite3.IntegrityError: flash("Ese correo ya existe.")
        elif a in ("toggle", "borrar") and str(i) != str(u["id"]):
            if a == "toggle": d.execute("UPDATE users SET active=1-active WHERE id=? AND role!='admin'", (i,))
            else:
                d.execute("DELETE FROM users WHERE id=? AND role!='admin'", (i,))
                for t in ("subs", "requests"): d.execute("DELETE FROM %s WHERE user_id=?" % t, (i,))
            d.commit()
        elif a == "borrar_post":
            p = d.execute("SELECT * FROM posts WHERE id=?", (i,)).fetchone()
            if p: rm_file(p["filename"]); d.execute("DELETE FROM posts WHERE id=?", (p["id"],)); d.execute("UPDATE reports SET done=1 WHERE post_id=?", (p["id"],)); d.commit()
        elif a == "ajustes":
            for k in ("site_name", "payment_instructions"): d.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, f.get(k, "").strip()[:1000]))
            d.commit(); flash("Ajustes guardados.")
        elif a == "plan_nuevo":
            try: d.execute("INSERT INTO plans(name,days,price) VALUES(?,?,?)", (f["name"].strip()[:40], int(f["days"]), float(f["price"]))); d.commit()
            except (ValueError, KeyError): flash("Revisa nombre, días y precio.")
        elif a == "plan_toggle": d.execute("UPDATE plans SET active=1-active WHERE id=?", (i,)); d.commit()
        elif a in ("aprobar", "rechazar"):
            r = d.execute("SELECT r.*, p.days FROM requests r JOIN plans p ON p.id=r.plan_id WHERE r.id=? AND r.status='pendiente'", (i,)).fetchone()
            if r:
                if a == "aprobar": grant(r["user_id"], r["days"], r["plan_id"], "solicitud")
                d.execute("UPDATE requests SET status=? WHERE id=?", ("aprobada" if a == "aprobar" else "rechazada", i)); d.commit()
        elif a == "dar_dias":
            try: grant(int(i), int(f["days"]), None, "manual"); flash("Membresía extendida.")
            except (ValueError, KeyError): flash("Indica los días.")
        elif a == "verificar": d.execute("UPDATE users SET verified=1-verified WHERE id=? AND role='creator'", (i,)); d.commit()
        elif a == "reset_pw":
            tmp = secrets.token_urlsafe(8)
            d.execute("UPDATE users SET pw=? WHERE id=? AND role!='admin'", (generate_password_hash(tmp), i)); d.commit()
            flash("Contraseña temporal: %s (compártela de forma privada)." % tmp)
        elif a == "resolver": d.execute("UPDATE reports SET done=1 WHERE id=?", (i,)); d.commit()
        return redirect(url_for("admin"))
    q = lambda sql, *p: d.execute(sql, p).fetchall()
    return render_template("admin.html",
        users=q("SELECT u.*, (SELECT MAX(ends) FROM subs s WHERE s.user_id=u.id) ends FROM users u ORDER BY id DESC"),
        posts=q("SELECT * FROM posts ORDER BY id DESC"), plans=q("SELECT * FROM plans"),
        reqs=q("SELECT r.*, u.name uname, u.email, p.name pname FROM requests r JOIN users u ON u.id=r.user_id LEFT JOIN plans p ON p.id=r.plan_id WHERE r.status='pendiente'"),
        reports=q("SELECT rp.*, p.title, u.name uname FROM reports rp LEFT JOIN posts p ON p.id=rp.post_id LEFT JOIN users u ON u.id=rp.user_id WHERE rp.done=0"),
        miembros=d.execute("SELECT COUNT(DISTINCT user_id) FROM subs WHERE ends>?", (now(),)).fetchone()[0],
        site_name_cfg=setting("site_name", SITE_NAME), pay=setting("payment_instructions"))

@app.errorhandler(403)
def _403(e): return render_template("auth.html", mode="denied"), 403


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "crear-admin":
        email = input("Correo del administrador: ").strip().lower()
        name = input("Nombre: ").strip() or "Admin"
        pw = getpass.getpass("Contraseña (mínimo 8): ")
        if len(pw) < 8: sys.exit("Contraseña muy corta.")
        c = sqlite3.connect(DB)
        c.execute("INSERT INTO users(name,email,pw,role) VALUES(?,?,?,'admin')", (name, email, generate_password_hash(pw)))
        c.commit(); print("Administrador creado. Ahora corre: python app.py")
    elif len(sys.argv) > 1 and sys.argv[1] == "respaldo":
        os.makedirs(os.path.join(BASE, "respaldos"), exist_ok=True)
        out = os.path.join(BASE, "respaldos", "respaldo-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M"))
        tmp = os.path.join(BASE, "respaldos", "tmp.db"); src = sqlite3.connect(DB); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "sitio.db")
            for n in os.listdir(UPLOADS): z.write(os.path.join(UPLOADS, n), "uploads/" + n)
        os.remove(tmp); print("Respaldo creado:", out)
    else:
        app.run(debug=os.environ.get("DEBUG") == "1")
