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
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Appointment, Part, Car, Component, Brand, Model, Setting

app = Flask(__name__, template_folder='views')
app.config.from_prefixed_env()

if not app.config.get('SECRET_KEY'):
    raise RuntimeError("Переменная окружения FLASK_SECRET_KEY не задана.")

# --- УМНАЯ НАСТРОЙКА БАЗЫ ДАННЫХ ДЛЯ RENDER И SUPABASE ---
db_url = os.environ.get('DATABASE_URL', 'sqlite:///service66.db')
db_url = db_url.strip() 
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True, 
    'pool_recycle': 300
}

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
with app.app_context():
    db.create_all()
    
    if not User.query.filter_by(phone='admin').first():
        admin = User(fio='Администратор', phone='admin', password_hash=generate_password_hash(os.environ.get('ADMIN_PASSWORD', 'admin123')), role='admin')
        db.session.add(admin)
        db.session.commit()

    if not Setting.query.first():
        db.session.add(Setting(smtp_user="твоя_почта@gmail.com", smtp_password="пароль_приложения"))
        db.session.commit()

    if not Component.query.first():
        comps = [("Моторное масло", 10000), ("Масляный фильтр", 10000), ("Воздушный фильтр", 20000), 
                 ("Салонный фильтр", 20000), ("Топливный фильтр", 40000), ("Свечи зажигания", 30000), 
                 ("Тормозные колодки передние", 40000), ("Тормозные колодки задние", 60000), 
                 ("Тормозные диски передние", 80000), ("Тормозная жидкость", 60000), 
                 ("Антифриз", 100000), ("Ремень ГРМ", 90000), ("Щётки стеклоочистителя", 15000), 
                 ("Аккумулятор", 80000), ("Фильтр АКПП", 60000)]
        for name, interval in comps:
            db.session.add(Component(name=name, interval=interval))
        db.session.commit()

    if not Brand.query.first():
        base_cars = {
            "Audi": ["A3", "A4", "A6", "Q5", "Q7"], "BMW": ["3 Series", "5 Series", "X3", "X5", "X6"],
            "Haval": ["Jolion", "F7", "F7x", "Dargo"], "Hyundai": ["Solaris", "Creta", "Tucson"],
            "Kia": ["Rio", "Ceed", "Sportage", "Sorento"], "Lada": ["Granta", "Vesta", "Niva"],
            "Mercedes": ["C-Class", "E-Class", "GLC"], "Renault": ["Logan", "Duster", "Arkana"],
            "Toyota": ["Camry", "Corolla", "RAV4", "Land Cruiser"], "Volkswagen": ["Polo", "Golf", "Tiguan", "Passat"]
        }
        for b_name, m_list in base_cars.items():
            b = Brand(name=b_name)
            db.session.add(b)
            db.session.commit()
            for m_name in m_list:
                db.session.add(Model(brand_id=b.id, name=m_name))
        db.session.commit()

# --- ОТПРАВКА EMAIL ---
def send_email(to_email, subject, body_text):
    if not to_email:
        return
    with app.app_context():
        settings = Setting.query.first()
        smtp_user = settings.smtp_user if settings else None
        smtp_pass = settings.smtp_password if settings else None

    if not smtp_user or not smtp_pass or smtp_user == "твоя_почта@gmail.com":
        print(f"[ТЕСТ EMAIL] Письмо для {to_email}: {subject}")
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

# --- ФОНОВЫЕ ЗАДАЧИ ---
def check_reminders():
    with app.app_context():
        for part in Part.query.all():
            if part.is_overdue():
                user = part.client
                text = (f"Авто66: Пора на сервис!\n\nАвтомобиль: {part.car.brand} {part.car.model}\n"
                        f"Замена: {part.name}\nПробег: {part.last_mileage} км.\n\nЗапишитесь на замену.")
                if user.email:
                    send_email(user.email, "Авто66: Пора на сервис!", text)
        
        tomorrow = date.today() + timedelta(days=1)
        for appt in Appointment.query.filter_by(date=tomorrow, status='confirmed').all():
            user = appt.client
            if user.email:
                text = (f"Авто66: Напоминание о визите!\n\nЗавтра в {appt.time} мы ждем вас.\n"
                        f"Комментарий: {appt.comment or 'нет'}")
                send_email(user.email, "Авто66: Ждем вас завтра!", text)

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_reminders, trigger="interval", hours=12)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# --- МАРШРУТЫ ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if request.form.get('captcha') != request.form.get('captcha_ans'):
            flash('Неверный ответ на капчу')
            return redirect(url_for('register'))
            
        phone = request.form['phone'].strip()
        if User.query.filter_by(phone=phone).first():
            flash('Этот номер уже зарегистрирован')
            return redirect(url_for('register'))
        
        user = User(
            fio=request.form['fio'], 
            phone=phone, 
            password_hash=generate_password_hash(request.form['password']),
            email=request.form.get('email', '').strip()
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Добро пожаловать! Добавьте свой автомобиль.')
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(phone=request.form['phone'].strip()).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
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
    upcoming = Appointment.query.filter_by(user_id=current_user.id).filter(Appointment.date >= date.today()).order_by(Appointment.date).all()
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
    car = Car(user_id=current_user.id, brand=request.form.get('car_brand'), model=request.form.get('car_model'), vin=request.form.get('vin'), plate=request.form.get('plate'))
    db.session.add(car)
    db.session.commit()
    flash('Машина добавлена')
    return redirect(url_for('dashboard'))

@app.route('/edit_car/<int:car_id>', methods=['POST'])
@login_required
def edit_car(car_id):
    car = db.session.get(Car, car_id)
    if car and car.user_id == current_user.id:
        car.brand = request.form.get('car_brand')
        car.model = request.form.get('car_model')
        car.vin = request.form.get('vin')
        car.plate = request.form.get('plate')
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
        d = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        t = request.form['time']
        if Appointment.query.filter_by(date=d, time=t).first():
            flash('Это время уже занято')
        else:
            db.session.add(Appointment(user_id=current_user.id, date=d, time=t, comment=request.form.get('comment', '')))
            db.session.commit()
            flash('Вы записаны!')
            return redirect(url_for('dashboard'))
            
    slots = []
    now = datetime.now()
    for i in range(0, 15):
        day = date.today() + timedelta(days=i)
        if day.weekday() < 6:
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
    for appt in Appointment.query.filter_by(status='new').all():
        appt_datetime = datetime.combine(appt.date, datetime.strptime(appt.time, '%H:%M').time())
        if (appt_datetime - now).total_seconds() < 86400:
            appt.status = 'canceled'
    db.session.commit()

    clients = User.query.filter_by(role='client').all()
    appointments = Appointment.query.filter(Appointment.date >= date.today()).order_by(Appointment.date.asc(), Appointment.time.asc()).all()
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
        clients = User.query.filter(User.role == 'client', (User.fio.contains(search_q) | User.phone.contains(search_q))).all()
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
        yield data.getvalue()
        data.seek(0); data.truncate(0)
        for c in clients:
            writer.writerow([c.id, c.fio, c.phone, c.email or ''])
            yield data.getvalue()
            data.seek(0); data.truncate(0)
    response = Response(generate(), mimetype='text/csv')
    response.headers.set("Content-Disposition", "attachment", filename="clients.csv")
    return response

@app.route('/admin/components', methods=['GET', 'POST'])
@login_required
def admin_components():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form['name'].strip()
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
            if name and brand_id:
                db.session.add(Model(brand_id=brand_id, name=name))
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
        return redirect(url_for('admin_dashboard'))
    components = Component.query.order_by(Component.name).all()
    return render_template('admin_parts.html', client=client, cars=client.cars, components=components)

@app.route('/admin/add_part/<int:user_id>', methods=['POST'])
@login_required
def add_part(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))
    part = Part(
        user_id=user_id, car_id=int(request.form['car_id']), name=request.form['name'],
        last_mileage=int(request.form['last_mileage']),
        last_date=datetime.strptime(request.form['last_date'], '%Y-%m-%d').date(),
        interval_km=int(request.form['interval_km']), interval_months=12,
        price_part=int(request.form.get('price_part', 0) or 0),
        price_work=int(request.form.get('price_work', 0) or 0)
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
    if part:
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
    total_revenue = sum(p.price_part + p.price_work for p in all_parts)
    today_total = sum(p.price_part + p.price_work for p in all_parts if p.created_at.date() == date.today())
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
    if request.method == 'POST':
        settings.smtp_user = request.form['smtp_user'].strip()
        settings.smtp_password = request.form['smtp_password'].strip()
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
    return render_template('print_order.html', client=client, date=date.today().strftime('%d.%m.%Y'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
