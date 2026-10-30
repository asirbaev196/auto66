from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import date, datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    # Роли: client (пользователь), mechanic (механик), admin (администратор)
    role = db.Column(db.String(20), default='client') 
    email = db.Column(db.String(120)) 
    admin_note = db.Column(db.String(255))
    
    cars = db.relationship('Car', backref='owner', lazy=True, cascade="all, delete-orphan")
    appointments = db.relationship('Appointment', backref='client', lazy=True, cascade="all, delete-orphan")

class Car(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    brand = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    vin = db.Column(db.String(17))
    plate = db.Column(db.String(15))
    parts = db.relationship('Part', backref='car', lazy=True, cascade="all, delete-orphan")

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    comment = db.Column(db.String(500)) # Увеличили до 500
    status = db.Column(db.String(20), default='new') 
    created_at = db.Column(db.DateTime, default=datetime.now) 
    
    # Привязка к механику (кто выполняет работу)
    mechanic_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class Component(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    interval = db.Column(db.Integer, default=10000)

class Brand(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    models = db.relationship('Model', backref='brand', lazy=True, cascade="all, delete-orphan")

class Model(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    brand_id = db.Column(db.Integer, db.ForeignKey('brand.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    smtp_user = db.Column(db.String(120), default="твоя_почта@gmail.com")
    smtp_password = db.Column(db.String(120), default="пароль_приложения")

class Part(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    car_id = db.Column(db.Integer, db.ForeignKey('car.id'), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    last_mileage = db.Column(db.Integer, nullable=False)
    last_date = db.Column(db.Date, nullable=False)
    interval_km = db.Column(db.Integer, default=7000)
    interval_months = db.Column(db.Integer, default=12)
    created_at = db.Column(db.DateTime, default=datetime.now)
    price_part = db.Column(db.Integer, default=0)
    price_work = db.Column(db.Integer, default=0)

    def next_due_km(self):
        return self.last_mileage + self.interval_km

    def next_due_date(self):
        import calendar
        month = self.last_date.month - 1 + self.interval_months
        year = self.last_date.year + month // 12
        month = month % 12 + 1
        day = min(self.last_date.day, calendar.monthrange(year, month)[1])
        return datetime.date(year, month, day)

    def days_until_due(self):
        return (self.next_due_date() - date.today()).days

    def is_overdue(self, current_mileage=None):
        if current_mileage and current_mileage >= self.next_due_km():
            return True
        return self.days_until_due() < 0
