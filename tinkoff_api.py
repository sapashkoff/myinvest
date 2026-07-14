import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

BASE_URL = "https://invest-public-api.tinkoff.ru/rest/"
_instrument_cache = {}
_accounts_cache = None


def get_headers():
    token = os.environ.get('TINKOFF_TOKEN', '')
    return {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
        "Content-Type": "application/json"
    }


def money_to_float(money):
    units = int(money.get("units", "0"))
    nano = int(money.get("nano", 0))
    return units + nano / 1_000_000_000


def get_accounts():
    global _accounts_cache
    if _accounts_cache is not None:
        return _accounts_cache
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts",
        headers=get_headers(),
        json={}
    )
    if response.status_code == 200:
        _accounts_cache = response.json().get("accounts", [])
        return _accounts_cache
    else:
        print(f"Ошибка получения счетов: {response.status_code}")
        return []


def get_instrument_name(figi):
    if figi in _instrument_cache:
        return _instrument_cache[figi]
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
        headers=get_headers(),
        json={"idType": "INSTRUMENT_ID_TYPE_FIGI", "id": figi}
    )
    if response.status_code == 200:
        instrument = response.json().get("instrument", {})
        name = instrument.get("name", figi)
    else:
        name = figi
    _instrument_cache[figi] = name
    return name


def get_payments_for_account(account_id, days=30, from_date=None, to_date=None):
    today = datetime.now(timezone.utc)
    if from_date and to_date:
        from_dt = from_date
        to_dt = to_date
    elif days:
        from_dt = today - timedelta(days=days)
        to_dt = today
    else:
        from_dt = today - timedelta(days=30)
        to_dt = today

    body = {
        "accountId": account_id,
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "to": to_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    }

    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
        headers=get_headers(),
        json=body
    )

    if response.status_code != 200:
        print(f"API error {response.status_code}: {response.text[:200]}")
        return []

    data = response.json()
    if not data:
        return []
    operations = data.get("operations", [])

    figis = set()
    for op in operations:
        op_type = op.get("operationType", "")
        if op_type in ["OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_COUPON"]:
            figis.add(op.get("figi", ""))

    names = {}
    for figi in figis:
        names[figi] = get_instrument_name(figi)

    result = []
    for op in operations:
        figi = op.get("figi", "")
        op_type = op.get("operationType", "")
        if op_type in ["OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_COUPON"]:
            amount = money_to_float(op.get("payment", {}))
            date_str = op.get("date", "")[:10]
            result.append({
                "name": names.get(figi, figi),
                "figi": figi,
                "amount": amount,
                "date": date_str,
                "type": "Дивиденд" if op_type == "OPERATION_TYPE_DIVIDEND" else "Купон"
            })
    return result


def get_all_payments(account_ids=None, days=30, from_date=None, to_date=None):
    if account_ids is None:
        accounts = get_accounts()
        account_ids = [acc.get("id") for acc in accounts]
    all_payments = []
    for acc_id in account_ids:
        payments = get_payments_for_account(acc_id, days=days, from_date=from_date, to_date=to_date)
        for p in payments:
            p["account_id"] = acc_id
        all_payments.extend(payments)
    all_payments.sort(key=lambda x: x["date"], reverse=True)
    total = sum(p["amount"] for p in all_payments)
    return all_payments, total


def get_account_name(account_id):
    accounts = get_accounts()
    for acc in accounts:
        if acc.get("id") == account_id:
            return acc.get("name", account_id)
    return str(account_id)


def get_portfolio(account_id):
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
        headers=get_headers(),
        json={"accountId": account_id}
    )
    if response.status_code == 200:
        data = response.json()
        total_amount = data.get("totalAmountShares", {})
        total_bonds = data.get("totalAmountBonds", {})
        total_etf = data.get("totalAmountEtf", {})
        total_currencies = data.get("totalAmountCurrencies", {})
        shares = money_to_float(total_amount) if total_amount else 0
        bonds = money_to_float(total_bonds) if total_bonds else 0
        etf = money_to_float(total_etf) if total_etf else 0
        currencies = money_to_float(total_currencies) if total_currencies else 0
        return {
            "total": round(shares + bonds + etf + currencies, 2),
            "shares": round(shares, 2),
            "bonds": round(bonds, 2),
            "etf": round(etf, 2),
            "currencies": round(currencies, 2)
        }
    return None


def get_portfolio_positions(account_id):
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.OperationsService/GetPositions",
        headers=get_headers(),
        json={"accountId": account_id}
    )
    if response.status_code != 200:
        return []
    data = response.json()
    positions = []
    for pos in data.get("securities", []):
        positions.append({
            "figi": pos.get("figi", ""), "name": pos.get("name", ""),
            "balance": int(pos.get("balance", 0)), "type": "share",
            "instrument_type": pos.get("instrumentType", "")
        })
    for pos in data.get("bonds", []):
        positions.append({
            "figi": pos.get("figi", ""), "name": pos.get("name", ""),
            "balance": int(pos.get("balance", 0)), "type": "bond",
            "instrument_type": "bond"
        })
    for pos in data.get("etfs", []):
        positions.append({
            "figi": pos.get("figi", ""), "name": pos.get("name", ""),
            "balance": int(pos.get("balance", 0)), "type": "etf",
            "instrument_type": "etf"
        })
    for pos in positions:
        inst = pos.get("instrument_type", "").lower()
        if inst == "bond" or "bond" in inst:
            pos["type"] = "bond"
        elif inst == "etf" or "etf" in inst:
            pos["type"] = "etf"
    return positions


def get_bond_coupons(figi, from_date, to_date):
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.InstrumentsService/GetBondCoupons",
        headers=get_headers(),
        json={
            "figi": figi,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
    )
    if response.status_code == 200:
        events = response.json().get("events", [])
        coupons = []
        for ev in events:
            coupon_date = ev.get("couponDate", "")[:10]
            pay_one = ev.get("payOneBond", {})
            coupons.append({
                "figi": ev.get("figi", figi),
                "date": coupon_date,
                "pay_per_one": money_to_float(pay_one) if pay_one else 0,
                "total_pay": 0,
                "currency": pay_one.get("currency", "rub") if pay_one else "rub"
            })
        return coupons
    return []


def get_dividends(figi, from_date, to_date):
    response = requests.post(
        f"{BASE_URL}tinkoff.public.invest.api.contract.v1.InstrumentsService/GetDividends",
        headers=get_headers(),
        json={
            "figi": figi,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
    )
    if response.status_code == 200:
        dividends = response.json().get("dividends", [])
        result = []
        for div in dividends:
            result.append({
                "figi": div.get("figi", figi),
                "date": div.get("paymentDate", "")[:10] or div.get("declaredDate", "")[:10],
                "pay_per_one": money_to_float(div.get("dividendNet", {})),
                "total_pay": 0,
                "currency": div.get("currency", "rub")
            })
        return result
    return []


def get_future_payments(account_id=None):
    today = datetime.now(timezone.utc)
    month_end = today + timedelta(days=30)
    year_end = datetime(today.year, 12, 31, tzinfo=timezone.utc)
    if account_id:
        account_ids = [account_id]
    else:
        accounts = get_accounts()
        account_ids = [acc.get("id") for acc in accounts]
    all_positions = {}
    for acc_id in account_ids:
        positions = get_portfolio_positions(acc_id)
        for pos in positions:
            figi = pos["figi"]
            if figi not in all_positions:
                all_positions[figi] = {"name": pos["name"], "balance": 0, "type": pos["type"]}
            all_positions[figi]["balance"] += pos["balance"]
    next_month_payments = []
    year_end_payments = []
    for figi, info in all_positions.items():
        balance = info["balance"]
        if balance <= 0:
            continue
        name = info["name"] or get_instrument_name(figi)
        if info["type"] == "bond":
            coupons = get_bond_coupons(figi, today, year_end)
            for c in coupons:
                c["name"] = name
                c["total_pay"] = round(c["pay_per_one"] * balance, 2)
                year_end_payments.append(c)
                if c["date"] <= month_end.strftime("%Y-%m-%d"):
                    next_month_payments.append(c)
        elif info["type"] == "share":
            divs = get_dividends(figi, today, year_end)
            for d in divs:
                d["name"] = name
                d["total_pay"] = round(d["pay_per_one"] * balance, 2)
                year_end_payments.append(d)
                if d["date"] <= month_end.strftime("%Y-%m-%d"):
                    next_month_payments.append(d)
    next_month_payments.sort(key=lambda x: x["date"])
    year_end_payments.sort(key=lambda x: x["date"])
    total_next = round(sum(p["total_pay"] for p in next_month_payments), 2)
    total_year = round(sum(p["total_pay"] for p in year_end_payments), 2)
    return {
        "next_month": next_month_payments,
        "till_year_end": year_end_payments,
        "total_next_month": total_next,
        "total_year_end": total_year
    }


def get_avg_coupon(account_id=None):
    today = datetime.now(timezone.utc)
    six_months_ago = today - timedelta(days=180)
    if account_id:
        payments = get_payments_for_account(account_id, days=180, from_date=six_months_ago, to_date=today)
    else:
        payments, _ = get_all_payments(days=180, from_date=six_months_ago, to_date=today)
    coupon_payments = [p for p in payments if p["type"] == "Купон"]
    if not coupon_payments:
        return 0
    by_figi = {}
    for p in coupon_payments:
        figi = p["figi"]
        if figi not in by_figi:
            by_figi[figi] = {"total": 0, "count": 0, "name": p["name"]}
        by_figi[figi]["total"] += p["amount"]
        by_figi[figi]["count"] += 1
    total_annual_coupon = 0
    total_instruments = 0
    for figi, data in by_figi.items():
        avg_per_payment = data["total"] / data["count"] if data["count"] > 0 else 0
        payments_per_year = data["count"] * 2
        annual_coupon = avg_per_payment * payments_per_year
        total_annual_coupon += annual_coupon
        total_instruments += 1
    if total_instruments > 0:
        return round(total_annual_coupon / total_instruments, 2)
    return 0