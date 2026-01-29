import re
import os
import pandas as pd
from io import BytesIO
from dotenv import load_dotenv
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from threading import Thread

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'clave-de-emergencia-por-si-no-carga-el-env')

# --- IMPLEMENTACIÓN DE BASE DE DATOS PROFESIONAL ---
# Intentamos obtener la URL de PostgreSQL de las variables de entorno de Render
db_url = os.getenv('DATABASE_URL')

if db_url:
    # Render entrega postgres://, pero SQLAlchemy requiere postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # Si no hay variable (estás en tu PC), usa SQLite local
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///barberia.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# ---------------------------------------------------

# Configuración de Correo
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_DEBUG'] = True

db = SQLAlchemy(app)
mail = Mail(app)
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# Función para envío asíncrono
def send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            print("Correo enviado correctamente")
        except Exception as e:
            print(f"❌ ERROR CRÍTICO EN MAIL: {type(e).__name__}: {str(e)}")

# ... Resto de tus modelos y rutas aquí abajo ...

# --- MODELOS ---

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(20), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.now)
    puntos_acumulados = db.Column(db.Integer, default=0)
    confirmado = db.Column(db.Boolean, default=False)

class Sucursal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    empleados = db.relationship('Empleado', backref='sucursal_local', lazy=True)

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    unidad = db.Column(db.String(20))

class Empleado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    especialidad = db.Column(db.String(100))
    comision_porcentaje = db.Column(db.Float, default=70.0) # Nueva: % de pago
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    sucursal_id = db.Column(db.Integer, db.ForeignKey('sucursal.id'), nullable=True)

class Turno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre_cliente = db.Column(db.String(100), nullable=False)
    fecha_hora = db.Column(db.DateTime, nullable=False)
    estado = db.Column(db.String(20), default='pendiente')
    cliente_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleado.id'), nullable=False)
    servicio_id = db.Column(db.Integer, db.ForeignKey('servicio.id'), nullable=True)
    servicio = db.relationship('Servicio')
    barbero = db.relationship('Empleado', backref='turnos')
    cliente = db.relationship('Usuario', foreign_keys=[cliente_id])
    monto_total = db.Column(db.Float, default=0.0) 
    extras = db.Column(db.Text, nullable=True)

    def calcular_y_actualizar_total(self):
        base = self.servicio.precio if self.servicio else 0
        adicionales = TurnoAdicional.query.filter_by(turno_id=self.id).all()
        total_adicionales = sum(ad.precio for ad in adicionales)   
        self.monto_total = base + total_adicionales
        return self.monto_total
    @property
    def total_pagado(self):
        return self.monto_total if self.monto_total else 0.0
    
class HistorialPassword(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False) 
    password_hash = db.Column(db.String(200), nullable=False)
    fecha_registro = db.Column(db.DateTime, default=datetime.now)

class Servicio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    duracion_minutos = db.Column(db.Integer, default=30)

class ConfiguracionPuntos(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rango_min = db.Column(db.Float)
    rango_max = db.Column(db.Float)
    puntos_otorgados = db.Column(db.Integer)

class TurnoAdicional(db.Model): 
    id = db.Column(db.Integer, primary_key=True)
    turno_id = db.Column(db.Integer, db.ForeignKey('turno.id'))
    tipo = db.Column(db.String(20))
    item_id = db.Column(db.Integer) 
    nombre = db.Column(db.String(100))
    precio = db.Column(db.Float)

class ReglaPuntos(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rango_min = db.Column(db.Float, nullable=False)
    rango_max = db.Column(db.Float, nullable=False)
    puntos = db.Column(db.Integer, nullable=False)

class Premio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    puntos_requeridos = db.Column(db.Integer, nullable=False)
    descripcion = db.Column(db.String(200))

class Venta(db.Model):
    __tablename__ = 'ventas'
    id = db.Column(db.Integer, primary_key=True)
    turno_id = db.Column(db.Integer, db.ForeignKey('turno.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, default=1)
    producto = db.relationship('Producto')

class BloqueoDisponibilidad(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey('empleado.id'), nullable=False)
    fecha = db.Column(db.String(10), nullable=False)  # Formato YYYY-MM-DD
    hora_inicio = db.Column(db.String(5), nullable=True) # Formato HH:MM
    hora_fin = db.Column(db.String(5), nullable=True)
    dia_completo = db.Column(db.Boolean, default=False)
    motivo = db.Column(db.String(200), nullable=True)
    empleado = db.relationship('Empleado', backref='bloqueos')  

class HistorialCanje(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    premio_nombre = db.Column(db.String(100), nullable=False)
    puntos_usados = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.now)
    usuario = db.relationship('Usuario', backref=db.backref('canjes_realizados', lazy=True))


# --- FUNCIONES DE APOYO ---

def validar_password(password):
    if len(password) < 8: return "Mínimo 8 caracteres."
    if not re.search(r"[A-Z]", password): return "Falta una mayúscula."
    if not re.search(r"[a-z]", password): return "Falta una minúscula."
    if not re.search(r"\d", password): return "Falta un número."
    if not re.search(r"[!@#$%&*]", password): return "Falta un carácter especial (!@#$%&*)."
    return True

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # El "Guardia": Revisa si hay sesión y si el rol es admin
        if 'usuario_id' not in session or session.get('rol') != 'admin':
            flash("Acceso restringido. Se requieren permisos de administrador.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- RUTAS ---

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/')
def index():
    # Si el usuario ya está logueado, lo enviamos a su dashboard
    if 'usuario_id' in session:
        rol = session.get('rol')
        if rol == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif rol == 'empleado':
            return redirect(url_for('empleado_dashboard'))
        else:
            return redirect(url_for('agendar'))
            
    # Si no hay sesión, mostramos la nueva página de inicio
    return render_template('index.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email').lower()
        password = request.form.get('password')

        # Validación de contraseña
        val = validar_password(password)
        if val is not True:
            flash(f"Seguridad: {val}", "error")
            return redirect(url_for('registro'))

        # Verificar si ya existe el usuario
        if Usuario.query.filter_by(email=email).first():
            flash("El correo ya está registrado.", "error")
            return redirect(url_for('registro'))

        # Crear nuevo usuario
        nuevo = Usuario(
            nombre=nombre,
            email=email,
            password=generate_password_hash(password),
            rol='cliente',
            confirmado=False
        )
        
        try:
            db.session.add(nuevo)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error en base de datos: {e}")
            flash("Error interno al crear la cuenta.", "error")
            return redirect(url_for('registro'))

        # Generar token y link de confirmación
        token = serializer.dumps(email, salt='email-confirm')
        link = url_for('confirmar_email', token=token, _external=True)

        # Preparar el correo
        remitente_seguro = os.getenv('MAIL_USERNAME')
        msg = Message(
            'Confirma tu cuenta - Barbero_1999',
            sender=remitente_seguro,
            recipients=[email]
        )
        msg.body = f'Hola {nombre}, confirma tu cuenta aquí: {link}'

        app_contexto = current_app._get_current_object()
        Thread(target=send_async_email, args=(app_contexto, msg)).start()

        flash("Registro exitoso. ¡Revisa tu correo para confirmar!", "exito")
        return redirect(url_for('login'))

    return render_template('registro.html')

@app.route('/confirmar_email/<token>')
def confirmar_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600) # Expira en 1h
    except:
        flash("El enlace es inválido o expiró.", "error")
        return redirect(url_for('login'))
    
    usuario = Usuario.query.filter_by(email=email).first_or_404()
    usuario.confirmado = True
    db.session.commit()
    flash("¡Cuenta activada correctamente! Ya puedes iniciar sesión.", "exito")
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').lower()
        password = request.form.get('password')
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and check_password_hash(usuario.password, password):
            # BLOQUEO si no ha confirmado su correo
            if not usuario.confirmado:
                flash("Debes confirmar tu correo electrónico antes de entrar.", "error")
                return redirect(url_for('login'))

            session['usuario_id'] = usuario.id
            session['nombre'] = usuario.nombre
            session['rol'] = usuario.rol
            
            if usuario.rol == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif usuario.rol == 'empleado':
                return redirect(url_for('empleado_dashboard'))
            else:
                return redirect(url_for('agendar'))
        
        flash("Correo o contraseña incorrectos", "error")
    return render_template('login.html')

@app.route('/recuperar_password', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        email = request.form.get('email').lower()
        usuario = Usuario.query.filter_by(email=email).first()
        if usuario:
            token = serializer.dumps(email, salt='pass-reset')
            link = url_for('reset_password', token=token, _external=True)
            
            msg = Message(
                'Recuperar Contraseña - Barbero_1999', 
                sender=app.config['MAIL_USERNAME'], # <--- CORREGIDO
                recipients=[email]
            )
            msg.body = f'Para restablecer tu contraseña, haz clic en el siguiente enlace: {link}'
            
            try:
                mail.send(msg)
            except Exception as e:
                print(f"Error enviando correo: {e}")

        # Mensaje genérico por seguridad
        flash("Si el correo existe en nuestro sistema, recibirás instrucciones en breve.", "exito")
        return redirect(url_for('login'))
    return render_template('recuperar.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='pass-reset', max_age=1800) # 30 min
    except:
        flash("El enlace de recuperación ha expirado o es inválido.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        nueva_pass = request.form.get('password')
        
        # Validar seguridad de la nueva clave
        val = validar_password(nueva_pass)
        if val is not True:
            flash(f"Seguridad: {val}", "error")
            return redirect(request.url)

        usuario = Usuario.query.filter_by(email=email).first()
        usuario.password = generate_password_hash(nueva_pass)
        db.session.commit()
        
        flash("Tu contraseña ha sido actualizada exitosamente.", "exito")
        return redirect(url_for('login'))
    return render_template('reset_password.html')

@app.route('/agendar', methods=['GET', 'POST'])
def agendar():
    if 'usuario_id' not in session:
        return redirect(url_for('login'))

    # Datos para mostrar en el GET
    usuario = Usuario.query.get(session['usuario_id'])
    premios = Premio.query.order_by(Premio.puntos_requeridos.asc()).all()
    barberos = Empleado.query.all()
    servicios = Servicio.query.all()
    mis_turnos = Turno.query.filter_by(cliente_id=session['usuario_id']).order_by(Turno.fecha_hora.desc()).all()
    hoy_str_iso = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        turno_id = request.form.get('turno_id') # Presente solo si es reprogramación
        barbero_id = request.form.get('barbero')
        servicio_id = request.form.get('servicio')
        fecha_dia = request.form.get('fecha_dia')
        hora_slot = request.form.get('hora_slot') # Nombre exacto del select en el HTML

        # Validación básica de datos presentes
        if not all([barbero_id, servicio_id, fecha_dia, hora_slot]):
            flash("Error: Faltan datos para completar la reserva.", "error")
            return redirect(url_for('agendar'))

        try:
            # 1. Crear el objeto datetime de la solicitud
            fecha_dt = datetime.strptime(f"{fecha_dia} {hora_slot}", '%Y-%m-%d %H:%M')
            servicio_obj = Servicio.query.get(servicio_id)
            
            # 2. VALIDACIÓN: No permitir fechas pasadas
            if fecha_dt < datetime.now():
                flash("Error: No puedes agendar en una fecha u hora que ya pasó.", "error")
                return redirect(url_for('agendar'))

            # 3. VALIDACIÓN: Evitar solapamientos (Overlap)
            duracion_solicitada = servicio_obj.duracion_minutos if servicio_obj.duracion_minutos else 30
            fin_solicitado = fecha_dt + timedelta(minutes=duracion_solicitada)

            # Buscamos turnos activos del barbero para ese día
            turnos_existentes = Turno.query.filter(
                Turno.empleado_id == barbero_id,
                Turno.estado != 'cancelado',
                db.func.date(Turno.fecha_hora) == fecha_dt.date()
            ).all()

            for t in turnos_existentes:
                # Si estamos reprogramando, ignoramos el turno actual
                if turno_id and t.id == int(turno_id):
                    continue
                    
                duracion_t = t.servicio.duracion_minutos if (t.servicio and t.servicio.duracion_minutos) else 30
                inicio_t = t.fecha_hora
                fin_t = t.fecha_hora + timedelta(minutes=duracion_t)

                # Lógica de choque de rangos
                if fecha_dt < fin_t and fin_solicitado > inicio_t:
                    flash(f"Error: El barbero ya tiene una cita de {inicio_t.strftime('%H:%M')} a {fin_t.strftime('%H:%M')}.", "error")
                    return redirect(url_for('agendar'))

            # 4. PROCESAR (Nuevo o Reprogramar)
            if turno_id: 
                # MODO ACTUALIZAR: Buscamos el turno existente
                t = Turno.query.get(int(turno_id))
                if t:
                    t.fecha_hora = fecha_dt
                    t.barbero_id = barbero_id
                    t.servicio_id = servicio_id
                    t.estado = 'pendiente'
                    flash("Turno reprogramado exitosamente.", "exito")
            else: 
                # MODO NUEVO: Creamos uno nuevo
                nuevo = Turno(
                    nombre_cliente=session['nombre'], 
                    fecha_hora=fecha_dt, 
                    cliente_id=session['usuario_id'], 
                    empleado_id=barbero_id, 
                    servicio_id=servicio_id,
                    estado='pendiente'
                )
                db.session.add(nuevo)
                flash("Turno agendado correctamente.", "exito")
            
            db.session.commit()
            return redirect(url_for('agendar'))

        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            flash("Error al procesar la cita.", "error")
            return redirect(url_for('agendar'))

    # Datos para la vista
    usuario = Usuario.query.get(session['usuario_id'])
    premios = Premio.query.order_by(Premio.puntos_requeridos.asc()).all()
    barberos = Empleado.query.all()
    servicios = Servicio.query.all()
    mis_turnos = Turno.query.filter_by(cliente_id=session['usuario_id']).order_by(Turno.fecha_hora.desc()).all()
    hoy_str_iso = datetime.now().strftime('%Y-%m-%d')

    # Capturamos si estamos editando un turno (parámetro ?edit_id=<id>)
    edit_id = request.args.get('edit_id')
    edit_turno = None
    if edit_id:
        try:
            edit_turno = Turno.query.get(int(edit_id))
            if edit_turno and edit_turno.cliente_id != session['usuario_id']:
                edit_turno = None
        except (ValueError, TypeError):
            edit_turno = None
    # IMPORTANTE: Asegúrate de pasar 'edit_turno' aquí
    return render_template('agendar.html', 
                           usuario=usuario, 
                           premios=premios, 
                           barberos=barberos, 
                           servicios=servicios, 
                           turnos=mis_turnos, 
                           hoy_str_iso=hoy_str_iso,
                           edit_turno=edit_turno)


@app.route('/api/disponibilidad')
def consultar_disponibilidad():
    barbero_id = request.args.get('barbero_id')
    fecha_str = request.args.get('fecha')
    edit_id = request.args.get('edit_id')
    
    if not barbero_id or not fecha_str:
        return jsonify([])

    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        
        # 1. Consultamos los turnos ocupados por clientes
        query = Turno.query.filter(
            Turno.empleado_id == barbero_id,
            Turno.estado != 'cancelado',
            db.func.date(Turno.fecha_hora) == fecha_obj
        )

        if edit_id and edit_id != '' and edit_id != 'None':
            query = query.filter(Turno.id != int(edit_id))
            
        turnos = query.all()

        bloqueados = []
        for t in turnos:
            duracion = t.servicio.duracion_minutos if (t.servicio and t.servicio.duracion_minutos) else 30
            bloqueados.append({
                'inicio': t.fecha_hora.strftime('%H:%M'),
                'fin': (t.fecha_hora + timedelta(minutes=duracion)).strftime('%H:%M')
            })

        # 2. CONSULTAMOS LOS BLOQUEOS MANUALES (Corregido)
        bloqueos = BloqueoDisponibilidad.query.filter_by(
            empleado_id=barbero_id, 
            fecha=fecha_str
        ).all()

        for b in bloqueos:
            if b.dia_completo:
                # Si bloqueó todo el día, cubrimos el rango total
                bloqueados.append({'inicio': '00:00', 'fin': '23:59'})
            else:
                # AQUÍ ESTABA EL ERROR DE IDENTACIÓN: ahora está dentro del for
                bloqueados.append({
                    'inicio': b.hora_inicio,
                    'fin': b.hora_fin
                })
    
        return jsonify(bloqueados)
        
    except Exception as e:
        print(f"Error en API disponibilidad: {e}")
        return jsonify([]), 500


@app.route('/cancelar-turno/<int:id>', methods=['GET', 'POST'])
def cancelar_turno(id):
    if 'usuario_id' not in session:
        return redirect(url_for('login'))

    turno = Turno.query.get_or_404(id)
    
    try:
        turno.estado = 'cancelado' 
        db.session.commit()
        # flash("Turno cancelado exitosamente.", "exito") # Opcional si tienes el bloque flash en HTML
    except Exception as e:
        db.session.rollback()
        # flash("No se pudo cancelar el turno.", "error")
    
    # --- LA MAGIA DE LA REDIRECCIÓN ---
    # 1. Intentamos regresar a la URL exacta de donde vino (mantiene fechas, filtros, etc.)
    if request.referrer and request.host in request.referrer:
        return redirect(request.referrer)
    
    # 2. Si no hay referrer (caso raro), usamos tu lógica de roles
    rol = session.get('rol')
    if rol == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif rol == 'empleado':
        return redirect(url_for('empleado_dashboard'))
    
    return redirect(url_for('index'))

# --- DASHBOARD ADMINISTRADOR ---
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    ahora = datetime.now()
    hoy = ahora.date()
    fecha_query = request.args.get('fecha', ahora.strftime('%Y-%m-%d'))
    Turnos_agenda_fecha = datetime.strptime(fecha_query, '%Y-%m-%d').date()
    
    # --- 1. DATOS DE LA AGENDA (Filtramos por el día seleccionado en el botón) ---
    # Nota: Eliminé la línea duplicada. Esta variable alimenta la tabla.
    turnos_hoy = Turno.query.filter(db.func.date(Turno.fecha_hora) == Turnos_agenda_fecha).order_by(Turno.fecha_hora.asc()).all()
    
    # --- 2. TARJETAS DE ESTADÍSTICAS (Siempre muestran lo de HOY real) ---
    programados_hoy = Turno.query.filter(db.func.date(Turno.fecha_hora) == hoy, Turno.estado == 'pendiente').count()
    completados_hoy = Turno.query.filter(db.func.date(Turno.fecha_hora) == hoy, Turno.estado == 'completado').count()
    total_turnos_historico = Turno.query.filter(Turno.estado != 'cancelado').count()

    # --- 3. SELECTOR DE DÍAS (Traducción manual a Español) ---
    dias_semana = []
    nombres_es = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"] # weekday() 0 es Lunes si usas ISO, pero Python usa 0=Lunes
    nombres_es = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    for i in range(7):
        d = ahora.date() + timedelta(days=i)
        dias_semana.append({
            'fecha': d.strftime('%Y-%m-%d'),
            'nombre': nombres_es[d.weekday()],
            'numero': d.day
    })

    # --- 4. CONTABILIDAD QUINCENAL (Sin cambios, tu lógica es correcta) ---
    if ahora.day <= 15:
        inicio_p = ahora.replace(day=1, hour=0, minute=0, second=0)
        fin_p = ahora.replace(day=15, hour=23, minute=59, second=59)
        nombre_periodo = "1ra Quincena"
    else:
        inicio_p = ahora.replace(day=16, hour=0, minute=0, second=0)
        proximo_mes = ahora.replace(day=28) + timedelta(days=4)
        ultimo_dia = proximo_mes - timedelta(days=proximo_mes.day)
        fin_p = ultimo_dia.replace(hour=23, minute=59, second=59)
        nombre_periodo = "2da Quincena"

    empleados_lista = Empleado.query.all()
    liquidacion = [] 
    for emp in empleados_lista:
        turnos_quincena = Turno.query.filter(
            Turno.empleado_id == emp.id,
            Turno.estado == 'completado',
            Turno.fecha_hora >= inicio_p,
            Turno.fecha_hora <= fin_p
        ).all()
        recaudado_total = sum(t.total_pagado for t in turnos_quincena if t.total_pagado)
        total_productos = sum(ad.precio for t in turnos_quincena for ad in TurnoAdicional.query.filter_by(turno_id=t.id, tipo='producto').all())
        base_comisionable = recaudado_total - total_productos
        porcentaje = getattr(emp, 'comision_porcentaje', 70.0)
        pago_barbero = base_comisionable * (porcentaje / 100)
        
        if recaudado_total > 0:
            liquidacion.append({
                'nombre': emp.nombre,
                'total_recaudado': round(recaudado_total, 2),
                'pago_barbero': round(pago_barbero, 2),
                'ganancia_local': round(recaudado_total - pago_barbero, 2)
            })

    # --- 5. OTROS DATOS ---
    turnos_mes = Turno.query.order_by(Turno.fecha_hora.desc()).limit(50).all()
    todos_los_bloqueos = BloqueoDisponibilidad.query.order_by(BloqueoDisponibilidad.fecha.asc()).all()
    premios_json = [{'id': p.id, 'nombre': p.nombre, 'puntos_requeridos': p.puntos_requeridos} for p in Premio.query.all()]

    return render_template('admin_dashboard.html', 
                           hoy_str=Turnos_agenda_fecha.strftime('%d de %B, %Y'),
                           periodo=nombre_periodo,
                           turnos_hoy=turnos_hoy,
                           dias_semana=dias_semana,
                           fecha_actual=fecha_query,
                           programados_hoy=programados_hoy,
                           completados_hoy=completados_hoy,
                           total_turnos_historico=total_turnos_historico,
                           liquidacion=liquidacion,
                           liquidacion_diaria=liquidacion,
                           turnos_mes=turnos_mes,
                           productos=Producto.query.all(),
                           servicios=Servicio.query.all(),
                           empleados=empleados_lista,
                           todos_los_bloqueos=todos_los_bloqueos,
                           sucursales=Sucursal.query.all(),
                           reglas=ReglaPuntos.query.all(),
                           premios=premios_json)


@app.route('/contabilidad')
def contabilidad():
    if session.get('rol') != 'admin':
        return redirect(url_for('login'))

    hoy = datetime.now()
    # Lógica de periodos quincenales
    if hoy.day <= 15:
        inicio_periodo = hoy.replace(day=1, hour=0, minute=0, second=0)
        fin_periodo = hoy.replace(day=15, hour=23, minute=59, second=59)
        nombre_periodo = "1ra Quincena"
    else:
        inicio_periodo = hoy.replace(day=16, hour=0, minute=0, second=0)
        proximo_mes = hoy.replace(day=28) + timedelta(days=4)
        ultimo_dia = proximo_mes - timedelta(days=proximo_mes.day)
        fin_periodo = ultimo_dia.replace(hour=23, minute=59, second=59)
        nombre_periodo = "2da Quincena"

    barberos = Empleado.query.all()
    liquidacion = []

    for b in barberos:
        # CORRECCIÓN: Filtrar por 'completado' para que coincida con tu Dashboard
        turnos = Turno.query.filter(
            Turno.empleado_id == b.id,
            Turno.estado == 'completado', 
            Turno.fecha_hora >= inicio_periodo,
            Turno.fecha_hora <= fin_periodo
        ).all()

        total_recaudado = 0
        total_productos = 0
        
        for t in turnos:
            # Sumamos servicio base
            total_recaudado += t.servicio.precio if t.servicio else 0
            
            # Sumamos adicionales (TurnoAdicional)
            adicionales = TurnoAdicional.query.filter_by(turno_id=t.id).all()
            for ad in adicionales:
                total_recaudado += ad.precio
                # Si el adicional es un producto, lo restamos de la base comisionable
                if ad.tipo == 'producto':
                    total_productos += ad.precio
        
        # CORRECCIÓN: Usar 'comision_porcentaje' que es el nombre real en tu clase Empleado
        base_comisionable = total_recaudado - total_productos
        pago_barbero = base_comisionable * (b.comision_porcentaje / 100)
        ganancia_local = total_recaudado - pago_barbero

        # Solo agregar a la lista si el barbero tuvo actividad
        if total_recaudado > 0:
            liquidacion.append({
                'nombre': b.nombre,
                'total_recaudado': total_recaudado,
                'pago_barbero': pago_barbero,
                'ganancia_local': ganancia_local
            })

    return render_template('contabilidad.html', 
                           liquidacion=liquidacion, 
                           periodo=nombre_periodo)

@app.route('/admin/reporte/diario')
def reporte_diario_excel():
    hoy = datetime.now().date()
    turnos = Turno.query.filter(db.func.date(Turno.fecha_hora) == hoy, Turno.estado == 'completado').all()
    
    data = []
    for t in turnos:
        # CORRECCIÓN: Usar e.precio y e.nombre directamente, ya que están en el modelo TurnoAdicional
        extras_serv = TurnoAdicional.query.filter_by(turno_id=t.id).all()
        monto_extras = sum([e.precio for e in extras_serv if e.precio])
        
        # Calcular ventas de productos (usando el precio del producto relacionado)
        ventas_prod = Venta.query.filter_by(turno_id=t.id).all()
        monto_productos = sum([v.producto.precio * v.cantidad for v in ventas_prod if v.producto])
        
        total_servicio = (t.servicio.precio if t.servicio else 0) + monto_extras
        
        # CORRECCIÓN: Usar t.barbero (como definiste en el modelo Turno)
        pago_barbero = total_servicio * (t.barbero.comision_porcentaje / 100 if t.barbero else 0.7)
        ganancia_local = (total_servicio - pago_barbero) + monto_productos

        data.append({
            "Hora": t.fecha_hora.strftime('%H:%M'),
            "Barbero": t.barbero.nombre if t.barbero else "N/A",
            "Cliente": t.nombre_cliente,
            "Servicio Base": t.servicio.nombre if t.servicio else "N/A",
            "Precio Base": t.servicio.precio if t.servicio else 0,
            "Extras (Servicios)": ", ".join([e.nombre for e in extras_serv if e.nombre]),
            "Monto Extras": monto_extras,
            "Productos": ", ".join([f"{v.producto.nombre} (x{v.cantidad})" for v in ventas_prod if v.producto]),
            "Venta Productos": monto_productos,
            "Total Bruto": total_servicio + monto_productos,
            "Pago Barbero": pago_barbero,
            "Ganancia Local": ganancia_local
        })

    # Evitar error si no hay datos hoy
    if not data:
        return "No hay datos para reportar hoy", 404

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte Diario')
    
    output.seek(0)
    return make_response(output.getvalue(), 200, {
        "Content-Disposition": f"attachment; filename=Reporte_Diario_{hoy}.xlsx",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    })

@app.route('/admin/reporte/semanal')
def reporte_semanal_excel():
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    hace_una_semana = datetime.now() - timedelta(days=7)
    turnos = Turno.query.filter(Turno.fecha_hora >= hace_una_semana, Turno.estado == 'completado').all()
    
    data = []
    for t in turnos:
        # Extras y Productos
        monto_extras = sum([e.servicio.precio for e in TurnoAdicional.query.filter_by(turno_id=t.id).all() if e.servicio])
        monto_ventas = sum([v.precio_unitario * v.cantidad for v in Venta.query.filter_by(turno_id=t.id).all()])
        
        # Liquidación
        total_servicios = (t.servicio.precio if t.servicio else 0) + monto_extras
        pago_barbero = total_servicios * (t.barbero.comision_porcentaje / 100 if t.barbero else 0.70)
        
        data.append({
            "Fecha": t.fecha_hora.strftime('%Y-%m-%d'),
            "Empleado": t.barbero.nombre if t.barbero else "N/A",
            "Ganancia Servicios": total_servicios,
            "Pago a Empleado": round(pago_barbero, 2),
            "Venta Productos": monto_ventas,
            "Total Bruto": total_servicios + monto_ventas
        })

    df = pd.DataFrame(data)
    # Agrupamos para que sea una tabla práctica como pediste
    resumen = df.groupby(['Fecha', 'Empleado']).sum().reset_index()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        resumen.to_excel(writer, index=False, sheet_name='Resumen Semanal')
    
    output.seek(0)
    return make_response(output.getvalue(), 200, {
        "Content-Disposition": "attachment; filename=Reporte_Semanal.xlsx",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    })

@app.route('/admin/reporte/mensual')
def reporte_mensual_excel():
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    inicio_mes = datetime.now().replace(day=1)
    turnos = Turno.query.filter(Turno.fecha_hora >= inicio_mes, Turno.estado == 'completado').all()
    
    data = []
    for t in turnos:
        monto_extras = sum([e.servicio.precio for e in TurnoAdicional.query.filter_by(turno_id=t.id).all() if e.servicio])
        monto_ventas = sum([v.precio_unitario * v.cantidad for v in Venta.query.filter_by(turno_id=t.id).all()])
        
        total_servicios = (t.servicio.precio if t.servicio else 0) + monto_extras
        pago_barbero = total_servicios * (t.barbero.comision_porcentaje / 100 if t.barbero else 0.70)
        
        # Determinamos el número de semana del mes
        semana_del_mes = (t.fecha_hora.day - 1) // 7 + 1

        data.append({
            "Semana": f"Semana {semana_del_mes}",
            "Empleado": t.barbero.nombre if t.barbero else "N/A",
            "Total Servicios": total_servicios,
            "A Pagar Empleado": round(pago_barbero, 2),
            "Venta Mercancía": monto_ventas,
            "Utilidad Local": (total_servicios - pago_barbero) + monto_ventas
        })

    df = pd.DataFrame(data)
    # Agrupamos por semana y empleado
    resumen_mensual = df.groupby(['Semana', 'Empleado']).sum().reset_index()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        resumen_mensual.to_excel(writer, index=False, sheet_name='Resumen Mensual')
    
    output.seek(0)
    return make_response(output.getvalue(), 200, {
        "Content-Disposition": "attachment; filename=Reporte_Mensual.xlsx",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    })

@app.route('/admin/add-producto', methods=['POST'])
def add_producto():
    # Extraemos los datos del formulario, incluyendo la nueva 'unidad'
    nombre = request.form.get('nombre')
    stock = request.form.get('stock')
    precio = request.form.get('precio')
    unidad = request.form.get('unidad') # <--- Capturamos la medida (uds, ml, etc.)

    nuevo = Producto(
        nombre=nombre,
        stock=int(stock) if stock else 0,
        precio=float(precio) if precio else 0.0,
        unidad=unidad if unidad else "uds" # <--- Si llega vacío, ponemos "uds" por defecto
    )
    
    db.session.add(nuevo)
    db.session.commit()
    flash("Producto añadido al inventario", "exito")
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/eliminar-producto/<int:id>')
def eliminar_producto(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    prod = Producto.query.get_or_404(id)
    db.session.delete(prod)
    db.session.commit()
    flash("Producto eliminado del inventario", "exito")
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/editar-producto/<int:id>', methods=['POST'])
def editar_producto(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    p = Producto.query.get_or_404(id)
    p.nombre = request.form.get('nombre')
    p.unidad = request.form.get('unidad') # Añadido como pediste
    p.precio = float(request.form.get('precio'))
    p.stock = int(request.form.get('stock'))
    db.session.commit()
    flash("Producto actualizado correctamente", "exito")
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/add-servicio', methods=['POST'])
def add_servicio():
    nombre = request.form.get('nombre')
    precio = request.form.get('precio')
    duracion = request.form.get('duracion') # Captura el select del HTML
    
    nuevo = Servicio(nombre=nombre, precio=float(precio), duracion_minutos=int(duracion))
    db.session.add(nuevo)
    db.session.commit()
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/eliminar-servicio/<int:id>')
def eliminar_servicio(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    serv = Servicio.query.get_or_404(id)
    db.session.delete(serv)
    db.session.commit()
    flash("Servicio eliminado", "exito")
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/editar-servicio/<int:id>', methods=['POST'])
def editar_servicio(id):
    s = Servicio.query.get_or_404(id)
    s.nombre = request.form.get('nombre')
    s.precio = float(request.form.get('precio'))
    s.duracion_minutos = int(request.form.get('duracion'))
    db.session.commit()
    return redirect(url_for('admin_dashboard') + '#inventario')

@app.route('/admin/crear-empleado', methods=['POST'])
def crear_empleado():
    if session.get('rol') != 'admin': 
        return redirect(url_for('login'))
    
    # Captura de datos
    nombre = request.form.get('nombre')
    email = request.form.get('email').lower()
    password = request.form.get('password')
    comision = request.form.get('comision', 70.0) 
    sucursal_id = request.form.get('sucursal_id')
    # Valor por defecto ya que no está en tu HTML actual
    especialidad = request.form.get('especialidad', 'Barbero') 

    # Validar existencia
    if Usuario.query.filter_by(email=email).first():
        flash("El correo ya existe", "error")
        return redirect(url_for('admin_dashboard') + '#usuarios')

    try:
        # 1. Crear Usuario (Acceso)
        nuevo_u = Usuario(
            nombre=nombre, 
            email=email, 
            password=generate_password_hash(password), 
            rol='empleado'
        )
        db.session.add(nuevo_u)
        db.session.flush() # flush() obtiene el ID sin cerrar la transacción

        # 2. Crear Empleado (Perfil)
        nuevo_e = Empleado(
            nombre=nombre, 
            especialidad=especialidad, 
            comision_porcentaje=float(comision), 
            usuario_id=nuevo_u.id,
            sucursal_id=int(sucursal_id) if sucursal_id else None
        )
        db.session.add(nuevo_e)
        db.session.commit()
        flash("Empleado creado exitosamente", "exito")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error al crear: {str(e)}", "error")

    return redirect(url_for('admin_dashboard') + '#usuarios')

@app.route('/admin/eliminar-empleado/<int:id>')
def eliminar_empleado(id):
    emp = Empleado.query.get_or_404(id)
    user = Usuario.query.get(emp.usuario_id)
    db.session.delete(emp)
    if user: db.session.delete(user)
    db.session.commit()
    flash("Empleado eliminado", "exito")
    return redirect(url_for('admin_dashboard') + '#usuarios')

@app.route('/admin/eliminar-bloqueo/<int:id>', methods=['POST'])
def admin_eliminar_bloqueo(id):
    if session.get('rol') != 'admin':
        return redirect(url_for('login'))
    
    bloqueo = BloqueoDisponibilidad.query.get_or_404(id)
    
    try:
        db.session.delete(bloqueo)
        db.session.commit()
        flash(f"Bloqueo de {bloqueo.empleado.nombre} eliminado.", "exito")
    except Exception as e:
        db.session.rollback()
        flash("Error al eliminar el bloqueo.", "error")
        
    return redirect(url_for('admin_dashboard') + '#usuarios')

@app.route('/admin/editar-bloqueo/<int:id>')
def editar_bloqueo_form(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    bloqueo = BloqueoDisponibilidad.query.get_or_404(id)
    return render_template('admin_editar_bloqueo.html', b=bloqueo)

@app.route('/admin/actualizar-bloqueo/<int:id>', methods=['POST'])
def actualizar_bloqueo(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    b = BloqueoDisponibilidad.query.get_or_404(id)
    b.fecha = request.form.get('fecha')
    b.motivo = request.form.get('motivo')
    
    # Manejo de jornada completa
    if 'dia_completo' in request.form:
        b.dia_completo = True
        b.hora_inicio = "00:00"
        b.hora_fin = "23:59"
    else:
        b.dia_completo = False
        b.hora_inicio = request.form.get('hora_inicio')
        b.hora_fin = request.form.get('hora_fin')

    db.session.commit()
    flash("Bloqueo actualizado", "exito")
    return redirect(url_for('admin_dashboard') + '#usuarios')

@app.route('/admin/config-puntos', methods=['POST'])
def config_puntos():
    r_min = request.form.get('min') 
    r_max = request.form.get('max')
    pts = request.form.get('puntos')
    
    if r_min and r_max and pts:
        # Usamos los nombres exactos de tu class ReglaPuntos
        nueva_regla = ReglaPuntos(
            rango_min=float(r_min), 
            rango_max=float(r_max), 
            puntos=int(pts)
        )
        db.session.add(nueva_regla)
        db.session.commit()
        flash("Regla de puntos guardada", "exito")
    
    return redirect(url_for('admin_dashboard') + '#puntos')

@app.route('/admin/crear-premio', methods=['POST'])
def crear_premio():
    nombre = request.form.get('nombre')
    costo = int(request.form.get('costo'))
    nuevo = Premio(nombre=nombre, puntos_requeridos=costo)
    db.session.add(nuevo)
    db.session.commit()
    flash("Premio creado exitosamente", "exito")
    return redirect(url_for('admin_dashboard') + '#puntos')

@app.route('/admin/canjear/<int:usuario_id>', methods=['POST'])
def canjear_puntos(usuario_id):
    if session.get('rol') not in ['admin', 'empleado']:
        flash("Acceso denegado.", "error")
        return redirect(url_for('login'))
    
    usuario = Usuario.query.get_or_404(usuario_id)
    puntos_premio = int(request.form.get('puntos'))
    
    # Buscamos el nombre del premio para el historial (opcional pero profesional)
    premio = Premio.query.filter_by(puntos_requeridos=puntos_premio).first()
    nombre_p = premio.nombre if premio else "Premio Especial"

    if usuario.puntos_acumulados >= puntos_premio:
        # 1. Restar puntos
        usuario.puntos_acumulados -= puntos_premio
        
        # 2. Registrar en historial para evitar confusiones
        nuevo_canje = HistorialCanje(
            usuario_id=usuario.id,
            premio_nombre=nombre_p,
            puntos_usados=puntos_premio
        )
        
        db.session.add(nuevo_canje)
        db.session.commit()
        flash(f"Canje exitoso. Cliente: {usuario.nombre}. Saldo: {usuario.puntos_acumulados} pts.", "exito")
    else:
        flash("El cliente no dispone de puntos suficientes para este premio.", "error")
        
    return redirect(url_for('admin_dashboard') + '#puntos')

@app.route('/admin/buscar_cliente_json')
def buscar_cliente_json():
    if session.get('rol') not in ['admin', 'empleado']:
        return jsonify([]), 403
        
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    # Buscamos clientes que coincidan con el nombre o el email
    clientes = Usuario.query.filter(
        (Usuario.rol == 'cliente') & 
        ((Usuario.nombre.ilike(f"%{query}%")) | (Usuario.email.ilike(f"%{query}%")))
    ).all()
    
    return jsonify([{
        'id': c.id, 
        'nombre': c.nombre, 
        'email': c.email, # Enviamos email en lugar de celular
        'puntos': c.puntos_acumulados
    } for c in clientes])

@app.route('/admin/eliminar-regla/<int:id>')
def eliminar_regla(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    regla = ReglaPuntos.query.get_or_404(id)
    db.session.delete(regla)
    db.session.commit()
    flash("Regla eliminada", "exito")
    return redirect(url_for('admin_dashboard') + '#puntos')

@app.route('/admin/eliminar-premio/<int:id>')
def eliminar_premio(id):
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    premio = Premio.query.get_or_404(id)
    db.session.delete(premio)
    db.session.commit()
    flash("Premio eliminado", "exito")
    return redirect(url_for('admin_dashboard') + '#puntos')


# --- DASHBOARD EMPLEADO (BARBERO) ---
@app.route('/empleado/dashboard')
def empleado_dashboard():
    if 'usuario_id' not in session or session.get('rol') != 'empleado':
        return redirect(url_for('login'))
    
    empleado = Empleado.query.filter_by(usuario_id=session['usuario_id']).first()
    ahora = datetime.now()
    hace_90_dias = ahora - timedelta(days=90)
    
    # --- 1. GESTIÓN DE FECHA SELECCIONADA ---
    fecha_query = request.args.get('fecha', ahora.strftime('%Y-%m-%d'))
    fecha_dt = datetime.strptime(fecha_query, '%Y-%m-%d').date()
    
    # Generar lista de 7 días para el selector (Mismo estilo que Admin)
    dias_semana = []
    nombres_es = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    for i in range(7):
        d = ahora.date() + timedelta(days=i)
        dias_semana.append({
            'fecha': d.strftime('%Y-%m-%d'),
            'nombre': nombres_es[d.weekday()],
            'numero': d.day
        })

    # --- 2. HISTORIAL Y COMISIONES (Se mantiene igual) ---
    turnos_completados = Turno.query.filter(
        Turno.empleado_id == empleado.id,
        Turno.estado == 'completado',
        Turno.fecha_hora >= hace_90_dias
    ).order_by(Turno.fecha_hora.desc()).all()

    valor_comision = empleado.comision_porcentaje if empleado.comision_porcentaje else 70.0
    porcentaje = valor_comision / 100
    mensual_estimado = 0
    servicios_totales_mes = 0
    historial_semanal = {}

    for t in turnos_completados:
        monto_comisionable = t.servicio.precio if t.servicio else 0
        servicios_nombres = [t.servicio.nombre] if t.servicio else []
        
        adicionales = TurnoAdicional.query.filter_by(turno_id=t.id).all()
        for ad in adicionales:
            if ad.tipo == 'servicio':
                monto_comisionable += ad.precio
                servicios_nombres.append(ad.nombre)

        comision_turno = monto_comisionable * porcentaje
        
        if t.fecha_hora.month == ahora.month and t.fecha_hora.year == ahora.year:
            mensual_estimado += comision_turno
            servicios_totales_mes += 1

        semana_key = t.fecha_hora.strftime('%U - %Y')
        dia_key = t.fecha_hora.strftime('%A %d/%m')

        if semana_key not in historial_semanal:
            historial_semanal[semana_key] = {'total_servicios_semana': 0, 'comision_total': 0, 'detalles_dias': {}}
        
        S = historial_semanal[semana_key]
        S['total_servicios_semana'] += 1
        S['comision_total'] += comision_turno

        if dia_key not in S['detalles_dias']:
            S['detalles_dias'][dia_key] = {'servicios_lista': [], 'cantidad_dia': 0}
        
        S['detalles_dias'][dia_key]['cantidad_dia'] += 1
        S['detalles_dias'][dia_key]['servicios_lista'].append({
            'hora': t.fecha_hora.strftime('%H:%M'),
            'servicios': ", ".join(servicios_nombres),
            'ganancia': round(comision_turno, 2)
        })

    # --- 3. AGENDA FILTRADA (Usamos fecha_dt en lugar de ahora.date()) ---
    turnos_filtrados = Turno.query.filter(
        Turno.empleado_id == empleado.id,
        db.func.date(Turno.fecha_hora) == fecha_dt, # <--- Cambio clave
        Turno.estado.in_(['pendiente', 'completado'])
    ).order_by(Turno.fecha_hora.asc()).all()
    
    for t in turnos_filtrados:
        total_acumulado = float(t.servicio.precio if t.servicio else 0)
        adicionales_hoy = TurnoAdicional.query.filter_by(turno_id=t.id).all()
        for ad in adicionales_hoy:
            total_acumulado += float(ad.precio)
        t.precio_visual_total = total_acumulado

    # --- 4. BLOQUEOS Y CONTADORES ---
    bloqueos_activos = BloqueoDisponibilidad.query.filter_by(empleado_id=empleado.id).all()
    pendientes = len([t for t in turnos_filtrados if t.estado == 'pendiente'])
    completados = len([t for t in turnos_filtrados if t.estado == 'completado'])

    return render_template('empleado_dashboard.html', 
        empleado=empleado,
        turnos=turnos_filtrados, # Pasamos los turnos de la fecha elegida
        dias_semana=dias_semana, # Para el selector
        fecha_actual=fecha_query, # Para marcar el día activo en el HTML
        hoy_str=fecha_dt.strftime('%d/%m/%Y'), # Fecha formateada para el título
        pendientes=pendientes,
        completados=completados,
        total_hoy=len(turnos_filtrados),
        mensual_estimado=round(mensual_estimado, 2),
        servicios_totales=servicios_totales_mes,
        porcentaje_aplicado=int(valor_comision),
        historial_semanal=historial_semanal,
        productos=Producto.query.filter(Producto.stock > 0).all(),
        servicios_extra=Servicio.query.all(),
        bloqueos_activos=bloqueos_activos
    )

@app.route('/empleado/add-multiple-extra', methods=['POST'])
def add_extra():
    turno_id = request.form.get('turno_id')
    prod_id = request.form.get('producto_id')
    serv_id = request.form.get('servicio_id')

    if prod_id:
        p = Producto.query.get(prod_id)
        nuevo_extra = TurnoAdicional(turno_id=turno_id, tipo='producto', item_id=p.id, nombre=p.nombre, precio=p.precio)
        p.stock -= 1 # Descontar del inventario
        db.session.add(nuevo_extra)
    
    if serv_id:
        s = Servicio.query.get(serv_id)
        nuevo_extra = TurnoAdicional(turno_id=turno_id, tipo='servicio', item_id=s.id, nombre=s.nombre, precio=s.precio)
        db.session.add(nuevo_extra)

    db.session.commit()
    flash("Adicional agregado correctamente", "exito")
    return redirect(url_for('empleado_dashboard'))

@app.route('/completar-turno/<int:id>', methods=['POST'])
def completar_turno(id):
    turno = Turno.query.get_or_404(id)
    if turno.estado == 'completado':
        return redirect(url_for('empleado_dashboard'))

    extra_id = request.form.get('producto_extra')
    if extra_id:
        prod = Producto.query.get(extra_id)
        if prod and prod.stock > 0:
            prod.stock -= 1
            
            # 1. Registro para el cálculo del total del turno
            nuevo_extra = TurnoAdicional(
                turno_id=turno.id, 
                tipo='producto', 
                item_id=prod.id, 
                nombre=prod.nombre, 
                precio=prod.precio
            )
            db.session.add(nuevo_extra)

            # 2. Registro en la tabla Venta (PARA LA CONTABILIDAD)
            nueva_venta = Venta(
                turno_id=turno.id,
                producto_id=prod.id,
                cantidad=1
            )
            db.session.add(nueva_venta)

    # 3. Marcar como completado
    turno.estado = 'completado'

    # 4. Lógica de Puntos Automática (Calculada antes del commit final)
    monto_total = turno.total_pagado
    regla = ReglaPuntos.query.filter(
        ReglaPuntos.rango_min <= monto_total,
        ReglaPuntos.rango_max >= monto_total
    ).first()

    if regla:
        cliente = Usuario.query.get(turno.cliente_id)
        if cliente:
            cliente.puntos_acumulados += regla.puntos
            flash(f"Turno finalizado. ¡Cliente ganó {regla.puntos} puntos!", "exito")
    else:
        flash("Turno finalizado con éxito.", "exito")

    db.session.commit()
    return redirect(url_for('empleado_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

from flask import jsonify, request, flash

@app.route('/guardar_extras_multiples', methods=['POST'])
def guardar_extras_multiples():
    data = request.get_json()
    turno_id = data.get('turno_id')
    extras = data.get('extras', []) 

    try:
        turno = Turno.query.get_or_404(turno_id)
        TurnoAdicional.query.filter_by(turno_id=turno_id).delete()

        monto_acumulado = float(turno.servicio.precio if turno.servicio else 0)

        for item in extras:
            obj = (Servicio.query.get(item['id']) if item['tipo'] == 'servicio' 
                   else Producto.query.get(item['id']))
            
            if obj:
                nuevo = TurnoAdicional(
                    turno_id=turno_id, tipo=item['tipo'],
                    item_id=obj.id, nombre=obj.nombre, precio=obj.precio
                )
                db.session.add(nuevo)
                monto_acumulado += float(obj.precio)

        turno.monto_total = monto_acumulado
        db.session.commit()
        return jsonify({"success": True, "nuevo_total": round(monto_acumulado, 2)})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/get_extras_turno/<int:turno_id>')
def get_extras_turno(turno_id):
    try:
        # Buscamos en la tabla unificada
        adicionales = TurnoAdicional.query.filter_by(turno_id=turno_id).all()
        lista_final = []
        for a in adicionales:
            lista_final.append({
                "id": a.item_id,
                "nombre": a.nombre,
                "tipo": a.tipo
            })
        return jsonify({"extras": lista_final})
    except Exception as e:
        return jsonify({"extras": []})

@app.route('/empleado/inasistencia/<int:id>', methods=['POST'])
def inasistencia_empleado(id):
    if session.get('rol') != 'empleado':
        return redirect(url_for('login'))
    
    t = Turno.query.get_or_404(id)
    t.estado = 'cancelado' # O 'inasistencia' si decides crear ese estado
    db.session.commit()
    
    flash(f"Inasistencia registrada para el cliente: {t.nombre_cliente}", "exito")
    return redirect(url_for('empleado_dashboard'))

@app.route('/empleado/bloquear-disponibilidad', methods=['POST'])
def bloquear_disponibilidad():
    if session.get('rol') != 'empleado':
        return redirect(url_for('login'))
    
    empleado = Empleado.query.filter_by(usuario_id=session['usuario_id']).first()
    fecha = request.form.get('fecha_bloqueo')
    if not fecha:
        flash("Debes seleccionar una fecha obligatoriamente.", "error")
        return redirect(url_for('empleado_dashboard'))
    dia_completo = 'dia_completo' in request.form
    hora_inicio = request.form.get('hora_inicio') if not dia_completo else "00:00"
    hora_fin = request.form.get('hora_fin') if not dia_completo else "23:59"
    motivo = request.form.get('motivo')

    nuevo_bloqueo = BloqueoDisponibilidad(
        empleado_id=empleado.id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        dia_completo=dia_completo,
        motivo=motivo
    )
    
    try:
        db.session.add(nuevo_bloqueo)
        db.session.commit()
        flash("Horario bloqueado correctamente.", "exito")
    except Exception as e:
        db.session.rollback()
        flash(f"Error al guardar el bloqueo: {str(e)}", "error")
    
    return redirect(url_for('empleado_dashboard'))

@app.route('/empleado/eliminar-bloqueo/<int:id>', methods=['POST'])
def eliminar_bloqueo(id):
    if session.get('rol') != 'empleado':
        return redirect(url_for('login'))
    
    bloqueo = BloqueoDisponibilidad.query.get_or_404(id)
    empleado = Empleado.query.filter_by(usuario_id=session['usuario_id']).first()
    
    if bloqueo.empleado_id == empleado.id:
        db.session.delete(bloqueo)
        db.session.commit()
        flash("Disponibilidad restaurada.", "exito")
    
    return redirect(url_for('empleado_dashboard'))

with app.app_context():
        db.create_all()

if __name__ == '__main__':
    app.run(debug=True)



    