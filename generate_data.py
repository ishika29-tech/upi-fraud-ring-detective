"""
generate_data.py - builds a fake (but realistic) Indian UPI payments graph
and loads it into FalkorDB.

Run:  python generate_data.py

What it creates
---------------
Nodes:  Account, UPI, Phone, Device, IP
Edges:  (Account)-[:HAS_UPI]->(UPI)
        (Account)-[:HAS_PHONE]->(Phone)
        (Account)-[:USES_DEVICE]->(Device)
        (Device)-[:SEEN_ON]->(IP)
        (Account)-[:PAID {txn_id, amount, ts, flagged, flag_reason}]->(Account)

Hidden inside lots of normal traffic are 5 fraud rings + 1 innocent family
(a "trap" - they share a tablet but are NOT fraud). The answer key is saved to
data/answer_key.json so we can check whether the agent found the right people.
"""
import json
import os
import random
import time
from datetime import date, timedelta

from db import get_graph, run_query

random.seed(42)  # same data every time you run it

# ----------------------------------------------------------------------------
# Small lists used to make the data look Indian and realistic
# ----------------------------------------------------------------------------
FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Rahul", "Amit", "Vikram", "Siddharth",
         "Ishaan", "Kabir", "Manish", "Suresh", "Rajesh", "Harpreet", "Gurpreet", "Anil", "Deepak", "Nikhil",
         "Priya", "Ananya", "Diya", "Sneha", "Pooja", "Neha", "Kavya", "Riya", "Simran", "Aisha",
         "Meera", "Shreya", "Tanvi", "Nisha", "Sunita", "Jaspreet", "Lakshmi", "Divya", "Isha", "Fatima"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Reddy", "Nair", "Iyer", "Das",
        "Bansal", "Agarwal", "Mehta", "Joshi", "Chopra", "Malhotra", "Khan", "Gill", "Yadav", "Mishra",
        "Banerjee", "Pillai", "Rao", "Saxena", "Kapoor", "Bhatia", "Sethi", "Chauhan", "Thakur", "Menon"]
CITIES = ["Delhi", "Mumbai", "Bengaluru", "Chandigarh", "Pune", "Hyderabad", "Chennai", "Kolkata",
          "Jaipur", "Ludhiana", "Ahmedabad", "Lucknow", "Indore", "Kochi", "Noida", "Gurugram"]
# bank -> the UPI handle its customers usually get
BANKS = {"SBI": "oksbi", "HDFC Bank": "okhdfcbank", "ICICI Bank": "okicici", "Axis Bank": "okaxis",
         "Kotak Bank": "kotak", "Punjab National Bank": "pnb", "Bank of Baroda": "barodampay",
         "Yes Bank": "yesbank", "IDFC First": "idfcbank", "Canara Bank": "cnrb"}
APP_HANDLES = ["ybl", "paytm", "ibl", "axl"]  # PhonePe / Paytm style handles
PHONES = ["Redmi Note 13", "Samsung Galaxy M34", "iPhone 14", "Vivo Y27", "Oppo A79", "Realme 12",
          "OnePlus Nord CE4", "Samsung Galaxy A15", "Moto G54", "iQOO Z9"]
ISPS = ["Jio Fiber", "Airtel Xstream", "ACT Fibernet", "BSNL", "Hathway", "Excitel"]
SHOPS = ["Annapurna Kirana Store", "Sharma Sweets", "Krishna Medicals", "Balaji Mobile Point", "Green Leaf Cafe",
         "City Fuel Station", "Royal Tailors", "Gupta Electronics", "Fresh Mart", "Hotel Saravana Bhavan",
         "Raj Book Depot", "Sai Hardware", "Metro Chemist", "Punjabi Dhaba", "Shree Ganesh Textiles",
         "Om Sai Dairy", "Lakshmi Jewellers", "Urban Salon", "QuickWash Laundry", "Bikaner Snacks"]

NOW = int(time.time())          # "today" for this dataset
DAY = 24 * 3600
TODAY = date.today()

# Everything we generate goes into these lists, then we load them in bulk.
accounts, upis, phones, devices, ips = [], [], [], [], []
has_upi, has_phone, uses_device, seen_on, payments = [], [], [], [], []

_counter = {"acc": 1000, "txn": 500000, "dev": 100, "ip": 0}
_used_vpas = set()


def new_id(kind):
    _counter[kind] += 1
    return _counter[kind]


def random_name():
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def random_phone():
    return "+91 " + random.choice("6789") + "".join(random.choice("0123456789") for _ in range(9))


def random_ip():
    _counter["ip"] += 1
    return f"{random.choice([49, 103, 106, 117, 122, 157, 182])}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"


def make_vpa(name, bank):
    """rohan.sharma@okaxis, rohansharma12@ybl, ..."""
    first, last = name.lower().split()
    handle = BANKS[bank] if random.random() < 0.6 else random.choice(APP_HANDLES)
    base = random.choice([f"{first}.{last}", f"{first}{last}", f"{first}{random.randint(1, 99)}"])
    vpa = f"{base}@{handle}"
    while vpa in _used_vpas:
        vpa = f"{base}{random.randint(100, 999)}@{handle}"
    _used_vpas.add(vpa)
    return vpa


def add_device(model=None, ip=None, isp=None, city=None):
    """Create a device and the IP it was seen on. Returns the device id."""
    dev_id = f"DEV-{new_id('dev'):05d}"
    devices.append({"id": dev_id, "model": model or random.choice(PHONES)})
    if ip is None:
        ip = random_ip()
        ips.append({"addr": ip, "isp": isp or random.choice(ISPS), "city": city or random.choice(CITIES)})
    seen_on.append({"device": dev_id, "ip": ip})
    return dev_id


def add_account(name=None, bank=None, city=None, opened_days_ago=None, kyc="full",
                is_merchant=False, phone=None, device=None, vpa_count=1):
    """
    Create one bank account with its UPI ID(s), phone and device.
    Pass `phone=` or `device=` to make several accounts SHARE them (that is how rings look).
    """
    acc_id = f"ACC{new_id('acc')}"
    name = name or random_name()
    bank = bank or random.choice(list(BANKS))
    days = opened_days_ago if opened_days_ago is not None else random.randint(400, 3500)
    accounts.append({"id": acc_id, "holder": name, "bank": bank, "city": city or random.choice(CITIES),
                     "opened_on": (TODAY - timedelta(days=days)).isoformat(), "kyc": kyc,
                     "is_merchant": is_merchant, "status": "ACTIVE"})
    for _ in range(vpa_count):
        if is_merchant:
            vpa = name.lower().replace(" ", "")[:14] + f"{random.randint(1, 99)}@" + random.choice(["okbizaxis", "paytm", "ybl"])
        else:
            vpa = make_vpa(name, bank)
        upis.append({"vpa": vpa})
        has_upi.append({"acc": acc_id, "vpa": vpa})
    if phone is None:
        phone = random_phone()
        phones.append({"number": phone})
    has_phone.append({"acc": acc_id, "number": phone})
    if device is None:
        device = add_device()
    uses_device.append({"acc": acc_id, "device": device})
    return acc_id


def pay(src, dst, amount, ts, flagged=False, flag_reason=""):
    txn_id = f"TXN{new_id('txn')}"
    payments.append({"src": src, "dst": dst, "txn_id": txn_id, "amount": round(float(amount), 2),
                     "ts": int(ts), "flagged": flagged, "flag_reason": flag_reason})
    return txn_id


def recent(days_back_max=30):
    """A random timestamp in the last N days."""
    return NOW - random.randint(3600, days_back_max * DAY)


# ----------------------------------------------------------------------------
# 1) Normal people and shops
# ----------------------------------------------------------------------------
def build_normal_world():
    people = [add_account() for _ in range(380)]
    # a few people have a second UPI app on the same account, or a second phone
    for acc in random.sample(people, 60):
        name = next(a["holder"] for a in accounts if a["id"] == acc)
        bank = next(a["bank"] for a in accounts if a["id"] == acc)
        vpa = make_vpa(name, bank)
        upis.append({"vpa": vpa})
        has_upi.append({"acc": acc, "vpa": vpa})
    # small offices / hostels: 2-4 devices on the same wifi IP (normal, not fraud)
    for _ in range(12):
        ip = random_ip()
        ips.append({"addr": ip, "isp": random.choice(ISPS), "city": random.choice(CITIES)})
        for acc in random.sample(people, random.randint(2, 4)):
            dev = add_device(ip=ip)
            uses_device.append({"acc": acc, "device": dev})

    shops = [add_account(name=s, is_merchant=True) for s in SHOPS]

    # everyday payments: groceries, food, friends, rent ...
    for _ in range(2600):
        src = random.choice(people)
        if random.random() < 0.55:
            dst, amount = random.choice(shops), random.lognormvariate(5.6, 0.9)        # ~₹100-₹2000
        else:
            dst, amount = random.choice(people), random.lognormvariate(7.0, 1.0)       # ~₹300-₹10000
        if dst != src:
            pay(src, dst, max(10, min(amount, 45000)), recent())
    # monthly rent: big-ish but normal
    for _ in range(40):
        src, dst = random.sample(people, 2)
        pay(src, dst, random.choice([12000, 15000, 18000, 22000, 25000, 30000]), recent())
    return people, shops


# ----------------------------------------------------------------------------
# 2) The planted fraud rings (the "answer key")
# ----------------------------------------------------------------------------
def build_rings(people):
    key = {"rings": {}, "innocent": {}, "flagged_transactions": {}}
    victims = random.sample(people, 70)  # normal people who get scammed
    v = iter(victims)

    # --- Ring 1: device farm (fan-in). 7 mule accounts + 1 collector, all on 2 phones behind 1 IP.
    farm_ip = random_ip()
    ips.append({"addr": farm_ip, "isp": "Unknown VPN", "city": "Unknown"})
    d1, d2 = add_device("Redmi 9A", ip=farm_ip), add_device("Samsung Galaxy J2", ip=farm_ip)
    collector = add_account(opened_days_ago=41, kyc="min", device=d1)
    mules = [add_account(opened_days_ago=random.randint(10, 45), kyc="min", device=random.choice([d1, d2]))
             for _ in range(7)]
    first_flag = None
    for i, mule in enumerate(mules):
        for _ in range(2):  # every mule receives money from 2 scam victims...
            t = NOW - random.randint(2, 6) * DAY + random.randint(0, 3000)
            amt = random.choice([24999, 35000, 49500, 18000, 65000])
            txn = pay(next(v), mule, amt, t)
            first_flag = first_flag or txn
            pay(mule, collector, amt * 0.97, t + random.randint(300, 1500))  # ...and forwards it fast
    payments_by_id = {p["txn_id"]: p for p in payments}
    payments_by_id[first_flag].update(flagged=True, flag_reason="Victim complaint on 1930: 'fake courier, paid customs fee'")
    key["rings"]["device_farm"] = sorted(mules + [collector])
    key["flagged_transactions"][first_flag] = "device_farm"

    # --- Ring 2: layering chain. victim -> L1 -> L2 -> L3 -> L4 -> L5 (cash-out) within ~30 minutes.
    chain = [add_account(opened_days_ago=random.randint(20, 120), kyc=random.choice(["min", "full"])) for _ in range(5)]
    # light identity overlap, like real gangs: L1 & L3 share a phone, L2 & L5 share a device
    shared_phone = random_phone(); phones.append({"number": shared_phone})
    has_phone.append({"acc": chain[0], "number": shared_phone}); has_phone.append({"acc": chain[2], "number": shared_phone})
    shared_dev = add_device("Vivo Y02")
    uses_device.append({"acc": chain[1], "device": shared_dev}); uses_device.append({"acc": chain[4], "device": shared_dev})
    t = NOW - 1 * DAY
    amount = 185000
    txn = pay(next(v), chain[0], amount, t, True, "Victim complaint: 'digital arrest' video-call scam")
    for a, b in zip(chain, chain[1:]):
        t += random.randint(240, 600)          # 4-10 minutes later
        amount *= random.uniform(0.97, 0.99)   # each mule keeps a small cut
        pay(a, b, amount, t)
    key["rings"]["layering_chain"] = sorted(chain)
    key["flagged_transactions"][txn] = "layering_chain"

    # --- Ring 3: fan-out / structuring. collector splits big money into many payments just under ₹10,000.
    p1, p2 = random_phone(), random_phone(); phones.extend([{"number": p1}, {"number": p2}])
    splitter = add_account(opened_days_ago=33, kyc="min", phone=p1)
    small_mules = [add_account(opened_days_ago=random.randint(15, 60), kyc="min", phone=random.choice([p1, p2])) for _ in range(6)]
    t = NOW - 3 * DAY
    txn = None
    for _ in range(3):
        x = pay(next(v), splitter, random.choice([60000, 75000, 90000]), t, False)
        txn = txn or x
        t += random.randint(600, 1800)
    for _ in range(18):
        t += random.randint(60, 300)
        pay(splitter, random.choice(small_mules), random.randint(9500, 9990), t)
    payments_by_id = {p["txn_id"]: p for p in payments}
    payments_by_id[txn].update(flagged=True, flag_reason="Bank rule: large credit to a 33-day-old min-KYC account")
    key["rings"]["fan_out_structuring"] = sorted([splitter] + small_mules)
    key["flagged_transactions"][txn] = "fan_out_structuring"

    # --- Ring 4: one phone number, many accounts / UPI IDs ("KYC update" SMS scam).
    sim = random_phone(); phones.append({"number": sim})
    sim_accounts = [add_account(opened_days_ago=random.randint(5, 30), kyc="min", phone=sim, vpa_count=2) for _ in range(4)]
    boss = add_account(opened_days_ago=200, phone=sim)
    first = None
    for acc in sim_accounts:
        for _ in range(3):
            tt = NOW - random.randint(1, 8) * DAY
            amt = random.choice([1999, 2999, 4999, 4500])
            x = pay(next(v), acc, amt, tt)
            first = first or x
            pay(acc, boss, amt * 0.98, tt + random.randint(600, 3000))
    payments_by_id = {p["txn_id"]: p for p in payments}
    payments_by_id[first].update(flagged=True, flag_reason="Victim complaint: 'KYC expired' SMS link")
    key["rings"]["one_phone_many_upi"] = sorted(sim_accounts + [boss])
    key["flagged_transactions"][first] = "one_phone_many_upi"

    # --- Ring 5: fake merchant. A "shop" collects from many people and forwards everything to 2 mules.
    fake_dev = add_device("iPhone 11")
    fake_shop = add_account(name="QuickDeal Electronics", is_merchant=True, opened_days_ago=25, kyc="min", device=fake_dev)
    m1 = add_account(opened_days_ago=22, kyc="min", device=fake_dev)
    m2 = add_account(opened_days_ago=19, kyc="min", device=fake_dev)
    first = None
    for _ in range(10):
        tt = NOW - random.randint(1, 10) * DAY
        amt = random.choice([7999, 12499, 15999, 21999])
        x = pay(next(v), fake_shop, amt, tt)
        first = first or x
        pay(fake_shop, random.choice([m1, m2]), amt * 0.99, tt + random.randint(900, 2400))
    payments_by_id = {p["txn_id"]: p for p in payments}
    payments_by_id[first].update(flagged=True, flag_reason="Customer dispute: 'phone never delivered'")
    key["rings"]["fake_merchant"] = sorted([fake_shop, m1, m2])
    key["flagged_transactions"][first] = "fake_merchant"

    # --- Innocent family (TRAP): 3 old full-KYC accounts on one shared tablet. Normal spending.
    tablet = add_device("Samsung Galaxy Tab A9")
    family = [add_account(name=f"{n} Arora", opened_days_ago=random.randint(1500, 3000), city="Chandigarh", device=tablet)
              for n in ["Rakesh", "Seema", "Ishaan"]]
    shops = [a["id"] for a in accounts if a["is_merchant"] and a["holder"] in SHOPS]
    for _ in range(25):
        pay(random.choice(family), random.choice(shops), random.lognormvariate(6, 0.8), recent())
    pay(family[0], family[2], 15000, NOW - 9 * DAY)  # pocket money
    pay(family[2], random.choice(shops), 2400, NOW - 8 * DAY)
    txn = pay(family[0], family[2], 52000, NOW - 1 * DAY, True,
              "Bank rule: ₹50,000+ credit to an account on a shared device")  # college fees
    key["innocent"]["family_shared_tablet"] = sorted(family)
    key["flagged_transactions"][txn] = "innocent_family"

    # --- A normal big payment that a simple rule flags (another false positive)
    a, b = random.sample(people, 2)
    txn = pay(a, b, 60000, NOW - 2 * DAY, True, "Bank rule: amount above ₹50,000")
    key["innocent"]["big_rent_payment"] = sorted([a, b])
    key["flagged_transactions"][txn] = "innocent_big_payment"
    return key


# ----------------------------------------------------------------------------
# 3) Load everything into FalkorDB
# ----------------------------------------------------------------------------
def load_into_falkordb():
    graph = get_graph()
    try:
        graph.delete()  # start fresh every run
    except Exception:
        pass  # graph did not exist yet

    # Indexes make "find account by id" instant
    for label, prop in [("Account", "id"), ("UPI", "vpa"), ("Phone", "number"), ("Device", "id"), ("IP", "addr")]:
        run_query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    run_query("CREATE INDEX FOR ()-[t:PAID]-() ON (t.txn_id)")

    def load(rows, cypher, batch=500):
        for i in range(0, len(rows), batch):
            run_query(cypher, {"rows": rows[i:i + batch]})

    # UNWIND = "for each row in this list, do ..."  (much faster than one query per row)
    load(accounts, "UNWIND $rows AS r CREATE (:Account {id: r.id, holder: r.holder, bank: r.bank, city: r.city, "
                   "opened_on: r.opened_on, kyc: r.kyc, is_merchant: r.is_merchant, status: r.status})")
    load(upis, "UNWIND $rows AS r CREATE (:UPI {vpa: r.vpa})")
    load(phones, "UNWIND $rows AS r MERGE (:Phone {number: r.number})")
    load(devices, "UNWIND $rows AS r CREATE (:Device {id: r.id, model: r.model})")
    load(ips, "UNWIND $rows AS r MERGE (:IP {addr: r.addr, isp: r.isp, city: r.city})")
    load(has_upi, "UNWIND $rows AS r MATCH (a:Account {id: r.acc}), (u:UPI {vpa: r.vpa}) CREATE (a)-[:HAS_UPI]->(u)")
    load(has_phone, "UNWIND $rows AS r MATCH (a:Account {id: r.acc}), (p:Phone {number: r.number}) CREATE (a)-[:HAS_PHONE]->(p)")
    load(uses_device, "UNWIND $rows AS r MATCH (a:Account {id: r.acc}), (d:Device {id: r.device}) CREATE (a)-[:USES_DEVICE]->(d)")
    load(seen_on, "UNWIND $rows AS r MATCH (d:Device {id: r.device}), (i:IP {addr: r.ip}) CREATE (d)-[:SEEN_ON]->(i)")
    load(payments, "UNWIND $rows AS r MATCH (a:Account {id: r.src}), (b:Account {id: r.dst}) "
                   "CREATE (a)-[:PAID {txn_id: r.txn_id, amount: r.amount, ts: r.ts, flagged: r.flagged, "
                   "flag_reason: r.flag_reason}]->(b)")


def shuffle_account_ids(answer_key):
    """Give accounts random-looking ids so fraud rings are not ACC1401, ACC1402, ... in a row."""
    old_ids = [a["id"] for a in accounts]
    new_numbers = random.sample(range(10000, 99999), len(old_ids))
    rename = {old: f"ACC{n}" for old, n in zip(old_ids, new_numbers)}
    for a in accounts:
        a["id"] = rename[a["id"]]
    for row in has_upi + has_phone + uses_device:
        row["acc"] = rename[row["acc"]]
    for p in payments:
        p["src"], p["dst"] = rename[p["src"]], rename[p["dst"]]
    for group in ("rings", "innocent"):
        for name, ids in answer_key[group].items():
            answer_key[group][name] = sorted(rename[i] for i in ids)


if __name__ == "__main__":
    people, shops = build_normal_world()
    answer_key = build_rings(people)
    shuffle_account_ids(answer_key)
    print(f"Generated {len(accounts)} accounts, {len(payments)} payments, {len(devices)} devices.")
    load_into_falkordb()
    os.makedirs("data", exist_ok=True)
    with open("data/answer_key.json", "w") as f:
        json.dump(answer_key, f, indent=2)
    counts = run_query("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC")
    print("Loaded into FalkorDB:", {c["label"]: c["n"] for c in counts})
    print("Flagged transactions to investigate:")
    for txn, ring in answer_key["flagged_transactions"].items():
        print(f"  {txn}  ->  (answer key: {ring})")
