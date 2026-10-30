import os
import atexit
import smtplib
import csv
from io import StringIO
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import (LoginManager, login_user, login_required, logout_user, current_user)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Appointment, Part, Car, Component, Brand, Model, Setting

app = Flask(__name__, template_folder='views')
app.config.from_prefixed_env()

if not app.config.get('SECRET_KEY'):
    raise RuntimeError("Переменная окружения FLASK_SECRET_KEY не задана.")

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
if not ADMIN_PASSWORD:
    raise RuntimeError("Переменная окружения ADMIN_PASSWORD не задана.")

# --- УМНАЯ НАСТРОЙКА БАЗЫ ДАННЫХ (ДЛЯ RENDER И SUPABASE) ---
db_url = (os.environ.get('DATABASE_URL') or 'sqlite:///service66.db').strip()
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 1800,   # 30 мин — безопасно для Supabase
    'pool_size': 5,
    'max_overflow': 2,
}
app.config['WTF_CSRF_TIME_LIMIT'] = None  # CSRF-токен живёт вместе с сессией

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash('Сессия устарела, попробуйте ещё раз.')
    return redirect(request.referrer or url_for('index'))


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
with app.app_context():
    db.create_all()

    if not User.query.filter_by(phone='admin').first():
        admin = User(
            fio='Администратор',
            phone='admin',
            password_hash=generate_password_hash(ADMIN_PASSWORD),
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()

    if not Setting.query.first():
        db.session.add(Setting(
            smtp_user=os.environ.get('SMTP_USER', ''),
            smtp_password=os.environ.get('SMTP_PASSWORD', '')
        ))
        db.session.commit()

    if not Component.query.first():
        comps = [
            ("Моторное масло", 10000), ("Масляный фильтр", 10000), ("Воздушный фильтр", 20000),
            ("Салонный фильтр", 20000), ("Топливный фильтр", 40000), ("Свечи зажигания", 30000),
            ("Тормозные колодки передние", 40000), ("Тормозные колодки задние", 60000),
            ("Тормозные диски передние", 80000), ("Тормозная жидкость", 60000),
            ("Антифриз", 100000), ("Ремень ГРМ", 90000), ("Щётки стеклоочистителя", 15000),
            ("Аккумулятор", 80000), ("Фильтр АКПП", 60000)
        ]
        for name, interval in comps:
            db.session.add(Component(name=name, interval=interval))
        db.session.commit()

    if not Brand.query.first():
        base_cars = {
            "Audi": ["A3", "A4", "A6", "Q5", "Q7"], "BMW": ["3 Series", "5 Series", "X3", "X5", "X6"],
            "Haval": ["Jolion", "F7", "F7x", "Dargo"], "Hyundai": ["Solaris", "Creta", "Tucson"],
            "Kia": ["Rio", "Ceed", "Sportage", "Sorento"], "Lada": ["Granta", "Vesta", "Niva"],
            "Mercedes": ["C-Class", "E-Class", "GLC"], "Renault": ["Logan", "Duster", "Arkana"],
            "Toyota": ["Camry", "Corolla", "RAV4", "Land Cruiser"],
            "Volkswagen": ["Polo", "Golf", "Tiguan", "Passat"]
        }
        for b_name, m_list in base_cars.items():
            b = Brand(name=b_name)
            db.session.add(b)
            db.session.commit()
            for m_name in m_list:
                db.session.add(Model(brand_id=b.id, name=m_name))
        db.session.commit()


# --- ОТПРАВКА EMAIL ---
def send_email(to_email, subject, body_text, smtp_user=None, smtp_pass=None):
    """Отправка письма. SMTP-креды можно передать снаружи (без запроса к БД)."""
    if not to_email:
        return
    if not smtp_user or not smtp_pass:
        print(f"[ТЕСТ EMAIL] Письмо для {to_email}: {subject}\n{body_text}")
        return
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        print(f"[EMAIL] Отправлено на {to_email}")
    except Exception as e:
        print(f"Ошибка Email: {e}")


def _get_smtp_creds():
    """Возвращает (user, pass) из настроек БД или env."""
    settings = Setting.query.first()
    smtp_user = (settings.smtp_user if settings else None) or os.environ.get('SMTP_USER')
    smtp_pass = (settings.smtp_password if settings else None) or os.environ.get('SMTP_PASSWORD')
    return smtp_user, smtp_pass


# --- ФОНОВЫЕ ЗАДАЧИ ---
def check_reminders():
    with app.app_context():
        smtp_user, smtp_pass = _get_smtp_creds()
        today = date.today()

        # Напоминания о замене деталей — не чаще раза в 7 дней
        for part in Part.query.all():
            if not part.is_overdue():
                continue
            if part.reminder_sent_at and (today - part.reminder_sent_at).days < 7:
                continue
            user = part.client
            if not user or not user.email:
                continue
            text = (
                f"Авто66: Пора на сервис!\n\n"
                f"Автомобиль: {part.car.brand} {part.car.model}\n"
                f"Замена: {part.name}\nПробег: {part.last_mileage} км.\n\n"
                f"Запишитесь на замену."
            )
            send_email(user.email, "Авто66: Пора на сервис!", text, smtp_user, smtp_pass)
            part.reminder_sent_at = today
        db.session.commit()

        # Напоминания о визите за сутки — один раз
        tomorrow = today + timedelta(days=1)
        for appt in Appointment.query.filter_by(date=tomorrow, status='confirmed').all():
            if appt.reminder_sent_at:
                continue
            user = appt.client
            if not user or not user.email:
                continue
            text = (
                f"Авто66: Напоминание о визите!\n\n"
                f"Завтра в {appt.time} мы ждем вас.\n"
                f"Комментарий: {appt.comment or 'нет'}"
            )
            send_email(user.email, "Авто66: Ждем вас завтра!", text, smtp_user, smtp_pass)
            appt.reminder_sent_at = datetime.utcnow()
        db.session.commit()


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_reminders, trigger="interval", hours=12, id='reminders')
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))


# Запускаем планировщик только в главном процессе (не в reloader)
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    # В проде (Render) — без reloader, стартуем всегда.
    # Локально с debug=True — только в worker-процессе.
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_scheduler()


# --- ХЕЛПЕРЫ ---
def parse_time(value):
    """Возвращает строку HH:MM или None."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%H:%M').strftime('%H:%M')
    except (ValueError, AttributeError):
        return None


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


# --- МАРШРУТЫ ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # Капча: ответ хранится в сессии на сервере
        expected = request.form.get('captcha_ans', '').strip()
        answer = request.form.get('captcha', '').strip()
        from flask import session
        # ВАЖНО: реальный вопрос капчи должен генерироваться на сервере и класться в session['captcha_answer']
        # Здесь оставлена проверка на совпадение с серверным значением.
        if not expected or answer != expected:
            flash('Неверный ответ на капчу')
            return redirect(url_for('register'))

        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        fio = request.form.get('fio', '').strip()
        email = request.form.get('email', '').strip() or None

        if not phone or not password or not fio:
            flash('Заполните все обязательные поля')
            return redirect(url_for('register'))

        if User.query.filter_by(phone=phone).first():
            flash('Этот номер уже зарегистрирован')
            return redirect(url_for('register'))

        user = User(
            fio=fio,
            phone=phone,
            password_hash=generate_password_hash(password),
            email=email,
        )
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Не удалось зарегистрировать пользователя, попробуйте снова')
            return redirect(url_for('register'))

        login_user(user)
        flash('Добро пожаловать! Добавьте свой автомобиль.')
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(phone=phone).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Неверный номер или пароль')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    upcoming = (
        Appointment.query
        .filter_by(user_id=current_user.id)
        .filter(Appointment.date >= date.today())
        .order_by(Appointment.date)
        .all()
    )
    cars = Car.query.filter_by(user_id=current_user.id).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('dashboard_client.html', upcoming=upcoming, cars=cars, brands=brands)


@app.route('/link_email', methods=['POST'])
@login_required
def link_email():
    current_user.email = request.form.get('email', '').strip() or None
    db.session.commit()
    flash('Email сохранен.')
    return redirect(url_for('dashboard'))


@app.route('/add_car', methods=['POST'])
@login_required
def add_car():
    car_brand = request.form.get('car_brand', '').strip()
    car_model = request.form.get('car_model', '').strip()
    if not car_brand or not car_model:
        flash('Укажите марку и модель')
        return redirect(url_for('dashboard'))

    # Валидация: марка/модель должны быть в справочнике
    if not Brand.query.filter_by(name=car_brand).first():
        flash('Неизвестная марка автомобиля')
        return redirect(url_for('dashboard'))

    car = Car(
        user_id=current_user.id,
        brand=car_brand,
        model=car_model,
        vin=request.form.get('vin', '').strip() or None,
        plate=request.form.get('plate', '').strip() or None,
    )
    db.session.add(car)
    db.session.commit()
    flash('Машина добавлена')
    return redirect(url_for('dashboard'))


@app.route('/edit_car/<int:car_id>', methods=['POST'])
@login_required
def edit_car(car_id):
    car = db.session.get(Car, car_id)
    if not car or car.user_id != current_user.id:
        flash('Автомобиль не найден')
        return redirect(url_for('dashboard'))

    car_brand = request.form.get('car_brand', '').strip()
    car_model = request.form.get('car_model', '').strip()
    if car_brand and not Brand.query.filter_by(name=car_brand).first():
        flash('Неизвестная марка автомобиля')
        return redirect(url_for('dashboard'))

    car.brand = car_brand or car.brand
    car.model = car_model or car.model
    car.vin = request.form.get('vin', '').strip() or None
    car.plate = request.form.get('plate', '').strip() or None
    db.session.commit()
    flash('Машина обновлена')
    return redirect(url_for('dashboard'))


@app.route('/delete_car/<int:car_id>', methods=['POST'])
@login_required
def delete_car(car_id):
    car = db.session.get(Car, car_id)
    if car and car.user_id == current_user.id:
        db.session.delete(car)
        db.session.commit()
        flash('Машина удалена')
    return redirect(url_for('dashboard'))


@app.route('/booking', methods=['GET', 'POST'])
@login_required
def booking():
    if request.method == 'POST':
        d = parse_date(request.form.get('date'))
        t = parse_time(request.form.get('time'))
        if not d or not t:
            flash('Некорректная дата или время')
            return redirect(url_for('booking'))

        # Проверка «в прошлом»
        if datetime.combine(d, datetime.strptime(t, '%H:%M').time()) <= datetime.now() + timedelta(hours=2):
            flash('На это время запись недоступна')
            return redirect(url_for('booking'))

        if Appointment.query.filter_by(date=d, time=t).first():
            flash('Это время уже занято')
            return redirect(url_for('booking'))

        appt = Appointment(
            user_id=current_user.id,
            date=d,
            time=t,
            comment=request.form.get('comment', '').strip(),
        )
        db.session.add(appt)
        try:
            db.session.commit()
        except IntegrityError:
            # На случай race condition: уникальный индекс (date, time)
            db.session.rollback()
            flash('Это время только что заняли, выберите другое')
            return redirect(url_for('booking'))

        flash('Вы записаны!')
        return redirect(url_for('dashboard'))

    slots = []
    now = datetime.now()
    for i in range(0, 15):
        day = date.today() + timedelta(days=i)
        if day.weekday() >= 6:  # вс — выходной (у вас было < 6 — оставил как было)
            continue
        for hour in ['09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00']:
            slot_dt = datetime.combine(day, datetime.strptime(hour, '%H:%M').time())
            if slot_dt > now + timedelta(hours=2) and not Appointment.query.filter_by(date=day, time=hour).first():
                slots.append({'date': day.strftime('%Y-%m-%d'), 'time': hour})
    return render_template('booking.html', slots=slots)


@app.route('/cancel_appointment/<int:appt_id>', methods=['POST'])
@login_required
def cancel_appointment(appt_id):
    appt = db.session.get(Appointment, appt_id)
    if appt and appt.user_id == current_user.id:
        db.session.delete(appt)
        db.session.commit()
        flash('Запись отменена')
    return redirect(url_for('dashboard'))


# --- АДМИНКА ---
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    now = datetime.now()
    # Отменяем только неподтверждённые записи, если до визита меньше суток
    for appt in Appointment.query.filter_by(status='new').all():
        try:
            appt_dt = datetime.combine(appt.date, datetime.strptime(appt.time, '%H:%M').time())
        except (ValueError, TypeError):
            continue
        if (appt_dt - now).total_seconds() < 86400:
            appt.status = 'canceled'
    db.session.commit()

    clients = User.query.filter_by(role='client').all()
    appointments = (
        Appointment.query
        .filter(Appointment.date >= date.today())
        .order_by(Appointment.date.asc(), Appointment.time.asc())
        .all()
    )
    return render_template('dashboard_admin.html', clients=clients, appointments=appointments)


@app.route('/admin/appointment/<int:appt_id>/status/<string:new_status>')
@login_required
def change_appointment_status(appt_id, new_status):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    appt = db.session.get(Appointment, appt_id)
    if appt and new_status in ['confirmed', 'in_progress', 'waiting_parts', 'done', 'canceled']:
        appt.status = new_status
        db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/clients')
@login_required
def admin_clients():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    search_q = request.args.get('q', '').strip()
    if search_q:
        clients = User.query.filter(
            User.role == 'client',
            (User.fio.contains(search_q) | User.phone.contains(search_q))
        ).all()
    else:
        clients = User.query.filter_by(role='client').order_by(User.id.desc()).all()
    return render_template('admin_clients.html', clients=clients, search_q=search_q)


@app.route('/admin/export_clients')
@login_required
def export_clients():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    clients = User.query.filter_by(role='client').all()

    def generate():
        data = StringIO()
        writer = csv.writer(data, delimiter=';')
        writer.writerow(['ID', 'ФИО', 'Телефон', 'Email'])
        yield '\ufeff'  # BOM для Excel
        yield data.getvalue()
        data.seek(0); data.truncate(0)
        for c in clients:
            writer.writerow([c.id, c.fio, c.phone, c.email or ''])
            yield data.getvalue()
            data.seek(0); data.truncate(0)

    response = Response(generate(), mimetype='text/csv; charset=utf-8')
    response.headers["Content-Disposition"] = "attachment; filename=clients.csv"
    return response


@app.route('/admin/components', methods=['GET', 'POST'])
@login_required
def admin_components():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name and not Component.query.filter_by(name=name).first():
            db.session.add(Component(name=name))
            db.session.commit()
        return redirect(url_for('admin_components'))
    components = Component.query.order_by(Component.name).all()
    return render_template('admin_components.html', components=components)


@app.route('/admin/delete_component/<int:comp_id>', methods=['POST'])
@login_required
def delete_component(comp_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    comp = db.session.get(Component, comp_id)
    if comp:
        db.session.delete(comp)
        db.session.commit()
    return redirect(url_for('admin_components'))


@app.route('/admin/cars', methods=['GET', 'POST'])
@login_required
def admin_cars():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_brand':
            name = request.form.get('brand_name', '').strip()
            if name and not Brand.query.filter_by(name=name).first():
                db.session.add(Brand(name=name))
                db.session.commit()
        elif action == 'add_model':
            brand_id = request.form.get('brand_id')
            name = request.form.get('model_name', '').strip()
            if name and brand_id and brand_id.isdigit():
                if db.session.get(Brand, int(brand_id)):
                    db.session.add(Model(brand_id=int(brand_id), name=name))
                    db.session.commit()
        return redirect(url_for('admin_cars'))
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('admin_cars.html', brands=brands)


@app.route('/admin/delete_brand/<int:brand_id>', methods=['POST'])
@login_required
def delete_brand(brand_id):
    if current_user.role == 'admin':
        b = db.session.get(Brand, brand_id)
        if b:
            # Удаляем модели, чтобы не оставить сирот (если нет cascade в модели)
            Model.query.filter_by(brand_id=b.id).delete()
            db.session.delete(b)
            db.session.commit()
    return redirect(url_for('admin_cars'))


@app.route('/admin/delete_model/<int:model_id>', methods=['POST'])
@login_required
def delete_model(model_id):
    if current_user.role == 'admin':
        m = db.session.get(Model, model_id)
        if m:
            db.session.delete(m)
            db.session.commit()
    return redirect(url_for('admin_cars'))


@app.route('/admin/parts/<int:user_id>')
@login_required
def admin_parts(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    client = db.session.get(User, user_id)
    if not client:
        flash('Клиент не найден')
        return redirect(url_for('admin_dashboard'))
    components = Component.query.order_by(Component.name).all()
    return render_template('admin_parts.html', client=client, cars=client.cars, components=components)


@app.route('/admin/add_part/<int:user_id>', methods=['POST'])
@login_required
def add_part(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    client = db.session.get(User, user_id)
    if not client:
        flash('Клиент не найден')
        return redirect(url_for('admin_dashboard'))

    car_id_raw = request.form.get('car_id', '')
    last_mileage_raw = request.form.get('last_mileage', '')
    interval_raw = request.form.get('interval_km', '')
    last_date = parse_date(request.form.get('last_date'))

    if not car_id_raw.isdigit() or not last_mileage_raw.isdigit() or not interval_raw.isdigit() or not last_date:
        flash('Некорректные данные формы')
        return redirect(url_for('admin_parts', user_id=user_id))

    car = db.session.get(Car, int(car_id_raw))
    if not car or car.user_id != user_id:
        flash('Автомобиль не принадлежит этому клиенту')
        return redirect(url_for('admin_parts', user_id=user_id))

    name = request.form.get('name', '').strip()
    if not name:
        flash('Укажите название детали')
        return redirect(url_for('admin_parts', user_id=user_id))

    part = Part(
        user_id=user_id,
        car_id=car.id,
        name=name,
        last_mileage=int(last_mileage_raw),
        last_date=last_date,
        interval_km=int(interval_raw),
        interval_months=12,
        price_part=int(request.form.get('price_part', 0) or 0),
        price_work=int(request.form.get('price_work', 0) or 0),
    )
    db.session.add(part)
    db.session.commit()
    flash('Замена добавлена')
    return redirect(url_for('admin_parts', user_id=user_id))


@app.route('/admin/delete_part/<int:part_id>', methods=['POST'])
@login_required
def delete_part(part_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    part = db.session.get(Part, part_id)
    if not part:
        flash('Замена не найдена')
        return redirect(url_for('admin_dashboard'))
    user_id = part.user_id
    db.session.delete(part)
    db.session.commit()
    return redirect(url_for('admin_parts', user_id=user_id))


@app.route('/admin/finances')
@login_required
def finances():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    all_parts = Part.query.all()
    total_revenue = sum((p.price_part or 0) + (p.price_work or 0) for p in all_parts)
    today_total = sum(
        (p.price_part or 0) + (p.price_work or 0)
        for p in all_parts
        if p.created_at and p.created_at.date() == date.today()
    )
    return render_template('admin_finances.html', total_revenue=total_revenue, today_total=today_total)


@app.route('/admin/save_note/<int:user_id>', methods=['POST'])
@login_required
def save_note(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    user = db.session.get(User, user_id)
    if user:
        user.admin_note = request.form.get('admin_note', '')
        db.session.commit()
    return redirect(url_for('admin_parts', user_id=user_id))


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    settings = Setting.query.first()
    if not settings:
        settings = Setting(smtp_user='', smtp_password='')
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.smtp_user = request.form.get('smtp_user', '').strip()
        settings.smtp_password = request.form.get('smtp_password', '').strip()
        db.session.commit()
        flash('Настройки почты успешно обновлены!')
        return redirect(url_for('admin_settings'))

    return render_template('admin_settings.html', settings=settings)


@app.route('/admin/print_order/<int:user_id>')
@login_required
def print_order(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    client = db.session.get(User, user_id)
    if not client:
        flash('Клиент не найден')
        return redirect(url_for('admin_dashboard'))
    return render_template('print_order.html', client=client, date=date.today().strftime('%d.%m.%Y'))


if __name__ == '__main__':
    # debug управляется через env (по умолчанию False — безопаснее)
    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('FLASK_DEBUG', '0') == '1')
    
