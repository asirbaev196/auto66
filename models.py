from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import date, datetime
from sqlalchemy import UniqueConstraint, Index
import calendar

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)  # 255 — на будущее (argon2 длиннее)
    role = db.Column(db.String(10), default='client', nullable=False, index=True)
    email = db.Column(db.String(120))
    admin_note = db.Column(db.String(255))

    cars = db.relationship(
        'Car', backref='owner', lazy=True,
        cascade="all, delete-orphan", passive_deletes=True
    )
    appointments = db.relationship(
        'Appointment', backref='client', lazy=True,
        cascade="all, delete-orphan", passive_deletes=True
    )
    parts = db.relationship(
        'Part', backref='client', lazy=True,
        cascade="all, delete-orphan", passive_deletes=True
    )


class Car(db.Model):
    __tablename__ = 'car'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    brand = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    vin = db.Column(db.String(17))
    plate = db.Column(db.String(15))

    parts = db.relationship(
        'Part', backref='car', lazy=True,
        cascade="all, delete-orphan", passive_deletes=True
    )


class Appointment(db.Model):
    __tablename__ = 'appointment'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    date = db.Column(db.Date, nullable=False, index=True)
    time = db.Column(db.String(5), nullable=False)
    comment = db.Column(db.String(255))
    status = db.Column(db.String(20), default='new', nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    # NEW: чтобы не спамить напоминаниями о визите
    reminder_sent_at = db.Column(db.DateTime, nullable=True)

    # Защита от двойной записи на один слот (race condition)
    __table_args__ = (
        UniqueConstraint('date', 'time', name='uq_appointment_date_time'),
    )


class Component(db.Model):
    __tablename__ = 'component'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    interval = db.Column(db.Integer, default=10000)


class Brand(db.Model):
    __tablename__ = 'brand'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)

    models = db.relationship(
        'Model', backref='brand', lazy=True,
        cascade="all, delete-orphan", passive_deletes=True
    )


class Model(db.Model):
    __tablename__ = 'model'

    id = db.Column(db.Integer, primary_key=True)
    brand_id = db.Column(
        db.Integer, db.ForeignKey('brand.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    name = db.Column(db.String(50), nullable=False)

    __table_args__ = (
        UniqueConstraint('brand_id', 'name', name='uq_model_brand_name'),
    )


class Setting(db.Model):
    __tablename__ = 'setting'

    id = db.Column(db.Integer, primary_key=True)
    smtp_user = db.Column(db.String(120), default="")
    smtp_password = db.Column(db.String(255), default="")


class Part(db.Model):
    __tablename__ = 'part'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    car_id = db.Column(
        db.Integer, db.ForeignKey('car.id', ondelete='CASCADE'),
        nullable=False, index=True
    )
    name = db.Column(db.String(80), nullable=False)
    last_mileage = db.Column(db.Integer, nullable=False)
    last_date = db.Column(db.Date, nullable=False)
    interval_km = db.Column(db.Integer, default=7000)
    interval_months = db.Column(db.Integer, default=12)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    price_part = db.Column(db.Integer, default=0)
    price_work = db.Column(db.Integer, default=0)
    # NEW: чтобы не спамить напоминаниями о замене
    reminder_sent_at = db.Column(db.Date, nullable=True)

    def next_due_km(self):
        return self.last_mileage + self.interval_km

    def next_due_date(self):
        month = self.last_date.month - 1 + self.interval_months
        year = self.last_date.year + month // 12
        month = month % 12 + 1
        day = min(self.last_date.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def days_until_due(self):
        return (self.next_due_date() - date.today()).days

    def is_overdue(self, current_mileage=None):
        if current_mileage and current_mileage >= self.next_due_km():
            return True
        return self.days_until_due() < 0
