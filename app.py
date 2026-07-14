from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Фикс для совместимости
import hmac, hashlib
def _safe_check_password_hash(pwhash, password):
    try:
        return check_password_hash(pwhash, password)
    except:
        # Fallback для старых хешей
        return False
from models import db, User
from tinkoff_api import (
    get_accounts, get_all_payments, get_payments_for_account,
    get_account_name, get_portfolio, get_future_payments, get_avg_coupon
)
from datetime import datetime, timedelta, timezone
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Создаём таблицы при первом запуске
with app.app_context():
    db.create_all()


# ==================== СТРАНИЦЫ АВТОРИЗАЦИИ ====================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        if not email or not password:
            flash('Заполните все поля', 'error')
            return render_template('register.html')

        if password != password2:
            flash('Пароли не совпадают', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов', 'error')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            flash('Пользователь с таким email уже существует', 'error')
            return render_template('register.html')

        user = User(
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()

        login_user(user)
        return redirect(url_for('settings'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and _safe_check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))

        flash('Неверный email или пароль', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        token = request.form.get('tinkoff_token', '').strip()
        if token:
            current_user.set_token(token)
            db.session.commit()
            flash('Токен сохранён!', 'success')
        else:
            flash('Введите токен', 'error')
        return redirect(url_for('settings'))

    has_token = current_user.tinkoff_token_encrypted is not None
    return render_template('settings.html', has_token=has_token)


# ==================== ОСНОВНЫЕ СТРАНИЦЫ ====================

@app.route('/')
@login_required
def index():
    if not current_user.tinkoff_token_encrypted:
        return redirect(url_for('settings'))
    # Передаём токен в tinkoff_api через временную переменную окружения
    _set_token_for_request()
    accounts = get_accounts()
    return render_template('index.html', accounts=accounts)


# ==================== API ====================

def _set_token_for_request():
    """Временно подставляет токен пользователя и очищает кеш"""
    token = current_user.get_token()
    if token:
        os.environ['TINKOFF_TOKEN'] = token
        from tinkoff_api import clear_cache
        clear_cache()


@app.route('/api/payments')
@login_required
def api_payments():
    _set_token_for_request()
    account_id = request.args.get("account_id")
    period = request.args.get("period", "month")
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    today = datetime.now(timezone.utc)
    days = None
    from_dt = None
    to_dt = None

    if period == "day":
        days = 1
    elif period == "week":
        days = 7
    elif period == "month":
        days = 30
    elif period == "year":
        days = 365
    elif period == "custom":
        if from_date and to_date:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            to_dt = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        else:
            days = 30
    else:
        days = 30

    if account_id:
        payments = get_payments_for_account(account_id, days=days, from_date=from_dt, to_date=to_dt)
        for p in payments:
            p["account_name"] = get_account_name(account_id)
        total = sum(p["amount"] for p in payments)
    else:
        payments, total = get_all_payments(days=days, from_date=from_dt, to_date=to_dt)
        for p in payments:
            p["account_name"] = get_account_name(p.get("account_id", ""))

    return jsonify({"payments": payments, "total": round(total, 2)})


@app.route('/api/analytics')
@login_required
def api_analytics():
    _set_token_for_request()
    account_id = request.args.get("account_id")
    period = request.args.get("period", "year")

    period_days = {"month": 30, "quarter": 90, "year": 365, "all": 3650}
    days = period_days.get(period, 365)

    if account_id:
        payments = get_payments_for_account(account_id, days=days)
        for p in payments:
            p["account_name"] = get_account_name(account_id)
    else:
        payments, _ = get_all_payments(days=days)
        for p in payments:
            p["account_name"] = get_account_name(p.get("account_id", ""))

    if not payments:
        return jsonify({"has_data": False, "total": 0, "count": 0})

    total = sum(p["amount"] for p in payments)
    count = len(payments)

    unique_days = len(set(p["date"] for p in payments))
    avg_day = round(total / unique_days, 2) if unique_days else 0
    months_set = set(p["date"][:7] for p in payments)
    avg_month = round(total / len(months_set), 2) if months_set else 0
    weeks_set = set()
    for p in payments:
        d = datetime.strptime(p["date"], "%Y-%m-%d")
        weeks_set.add(f"{d.year}-W{d.isocalendar()[1]:02d}")
    avg_week = round(total / len(weeks_set), 2) if weeks_set else 0

    monthly_data = {}
    for p in payments:
        mk = p["date"][:7]
        monthly_data[mk] = monthly_data.get(mk, 0) + p["amount"]
    monthly = [{"month": k, "total": round(v, 2)} for k, v in sorted(monthly_data.items())]

    coupon_total = round(sum(p["amount"] for p in payments if p["type"] == "Купон"), 2)
    dividend_total = round(sum(p["amount"] for p in payments if p["type"] == "Дивиденд"), 2)

    instruments = {}
    for p in payments:
        nm = p["name"]
        if nm not in instruments:
            instruments[nm] = {"total": 0, "count": 0}
        instruments[nm]["total"] += p["amount"]
        instruments[nm]["count"] += 1
    top = sorted(instruments.items(), key=lambda x: x[1]["total"], reverse=True)[:10]
    top_instruments = [{"name": n, "total": round(d["total"], 2), "count": d["count"]} for n, d in top]

    acc_data = {}
    for p in payments:
        an = p.get("account_name", "?")
        if an.isdigit() or an == "?":
            an = get_account_name(p.get("account_id", an))
        acc_data[an] = acc_data.get(an, 0) + p["amount"]
    by_account = [{"name": k, "total": round(v, 2)} for k, v in acc_data.items()]

    portfolios = {}
    total_pf = 0
    accounts_list = get_accounts()

    if account_id:
        acc_name = get_account_name(account_id)
        pf = get_portfolio(account_id)
        if pf:
            portfolios[acc_name] = pf["total"]
            total_pf = pf["total"]
    else:
        for acc in accounts_list:
            pf = get_portfolio(acc["id"])
            if pf:
                portfolios[acc["name"]] = pf["total"]
                total_pf += pf["total"]

    annual_income = total * (365 / days) if days < 365 and days > 0 else total
    yield_pct = round((annual_income / total_pf) * 100, 2) if total_pf > 0 else 0

    yield_by_account = []
    for acc_name, pf_value in portfolios.items():
        acc_pmts = [p for p in payments if p.get("account_name") == acc_name]
        acc_t = sum(p["amount"] for p in acc_pmts)
        acc_ann = acc_t * (365 / days) if days < 365 and days > 0 else acc_t
        acc_yld = round((acc_ann / pf_value) * 100, 2) if pf_value > 0 else 0
        yield_by_account.append({
            "name": acc_name,
            "portfolio_value": round(pf_value, 2),
            "annual_income": round(acc_ann, 2),
            "yield_pct": acc_yld
        })

    future = get_future_payments(account_id if account_id else None)
    avg_coupon = get_avg_coupon(account_id if account_id else None)

    return jsonify({
        "has_data": True,
        "total": round(total, 2),
        "count": count,
        "avg_day": avg_day,
        "avg_week": avg_week,
        "avg_month": avg_month,
        "monthly": monthly,
        "coupon_total": coupon_total,
        "dividend_total": dividend_total,
        "top_instruments": top_instruments,
        "by_account": by_account,
        "total_portfolio_value": round(total_pf, 2),
        "annual_income": round(annual_income, 2),
        "yield_pct": yield_pct,
        "yield_by_account": yield_by_account,
        "avg_coupon": avg_coupon,
        "future_next_month": future["next_month"][:20],
        "future_year": future["till_year_end"][:50],
        "total_next_month": future["total_next_month"],
        "total_year_end": future["total_year_end"]
    })


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Сервер запущен!")
    print("Открой в браузере: http://127.0.0.1:8080")
    print("=" * 60)
    app.run(debug=True, host="0.0.0.0", port=8080)