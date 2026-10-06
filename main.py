from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify
)

from werkzeug.security import generate_password_hash, check_password_hash

import sqlite3
import hashlib
import json

from database import get_db, init_db


app = Flask(__name__)

app.secret_key = "markethub-demo-secret-key"

init_db()


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    conn = get_db()

    products = conn.execute("""
        SELECT products.*, users.name AS farmer_name
        FROM products
        JOIN users ON products.farmer_id = users.id
        ORDER BY products.created_at DESC
        LIMIT 6
    """).fetchall()

    conn.close()

    return render_template(
        "index.html",
        products=products
    )


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"]
        phone = request.form["phone"]
        role = request.form["role"]

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE phone = ?",
            (phone,)
        ).fetchone()

        if existing:

            conn.close()

            flash("Phone number already registered.", "error")

            return redirect(url_for("register"))

        conn.execute("""
            INSERT INTO users
            (name, phone, role, verified)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            phone,
            role,
            0
        ))

        conn.commit()
        conn.close()

        session["verify_phone"] = phone
        session["verify_role"] = role

        return redirect(url_for("verify_otp"))

    return render_template("register.html")


# =========================================================
# OTP
# =========================================================

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    if request.method == "POST":

        otp = request.form["otp"]

        # DEMO OTP
        if otp == "123456":

            phone = session.get("verify_phone")

            conn = get_db()

            conn.execute("""
                UPDATE users
                SET verified = 1
                WHERE phone = ?
            """, (phone,))

            conn.commit()
            conn.close()

            session.pop("verify_phone", None)

            return redirect(url_for("login"))

        flash("Invalid OTP. Use 123456 for demo.", "error")

    return render_template("verify_otp.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        phone = request.form["phone"]

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE phone = ?
        """, (phone,)).fetchone()

        conn.close()

        if not user:

            flash("User not found. Please register.", "error")

            return redirect(url_for("login"))

        if not user["verified"]:

            flash("Please verify your account first.", "error")

            return redirect(url_for("register"))

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["role"] = user["role"]

        if user["role"] == "farmer":

            return redirect(url_for("farmer_dashboard"))

        if user["role"] == "admin":

            return redirect(url_for("admin_dashboard"))

        return redirect(url_for("marketplace"))

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("index"))


# =========================================================
# FARMER DASHBOARD
# =========================================================

@app.route("/farmer/dashboard")
def farmer_dashboard():

    if session.get("role") != "farmer":

        return redirect(url_for("login"))

    conn = get_db()

    products = conn.execute("""
        SELECT *
        FROM products
        WHERE farmer_id = ?
        ORDER BY created_at DESC
    """, (
        session["user_id"],
    )).fetchall()

    orders = conn.execute("""
        SELECT
            orders.*,
            products.name AS product_name
        FROM orders
        JOIN products
        ON orders.product_id = products.id
        WHERE products.farmer_id = ?
        ORDER BY orders.created_at DESC
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    return render_template(
        "farmer_dashboard.html",
        products=products,
        orders=orders
    )


# =========================================================
# ADD PRODUCT
# =========================================================

@app.route("/farmer/add-product", methods=["GET", "POST"])
def add_product():

    if session.get("role") != "farmer":

        return redirect(url_for("login"))

    if request.method == "POST":

        name = request.form["name"]
        category = request.form["category"]
        price = float(request.form["price"])
        quantity = float(request.form["quantity"])
        location = request.form["location"]
        description = request.form["description"]

        conn = get_db()

        conn.execute("""
            INSERT INTO products
            (
                farmer_id,
                name,
                category,
                price,
                quantity,
                location,
                description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"],
            name,
            category,
            price,
            quantity,
            location,
            description
        ))

        conn.commit()

        product_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.close()

        create_ledger_record(
            "PRODUCT",
            {
                "product_id": product_id,
                "name": name,
                "price": price,
                "quantity": quantity
            }
        )

        check_product_fraud(product_id)

        flash("Product added successfully.", "success")

        return redirect(url_for("farmer_dashboard"))

    return render_template("add_product.html")


# =========================================================
# EDIT PRODUCT
# =========================================================

@app.route("/farmer/edit-product/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id):

    if session.get("role") != "farmer":

        return redirect(url_for("login"))

    conn = get_db()

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
        AND farmer_id = ?
    """, (
        product_id,
        session["user_id"]
    )).fetchone()

    if not product:

        conn.close()

        return "Product not found", 404

    if request.method == "POST":

        name = request.form["name"]
        price = float(request.form["price"])
        quantity = float(request.form["quantity"])

        conn.execute("""
            UPDATE products
            SET name = ?,
                price = ?,
                quantity = ?
            WHERE id = ?
        """, (
            name,
            price,
            quantity,
            product_id
        ))

        conn.commit()
        conn.close()

        create_ledger_record(
            "PRODUCT_UPDATE",
            {
                "product_id": product_id,
                "name": name,
                "price": price,
                "quantity": quantity
            }
        )

        flash("Product updated.", "success")

        return redirect(url_for("farmer_dashboard"))

    conn.close()

    return render_template(
        "edit_product.html",
        product=product
    )


# =========================================================
# MARKETPLACE
# =========================================================

@app.route("/marketplace")
def marketplace():

    search = request.args.get("search", "")

    category = request.args.get("category", "")

    conn = get_db()

    query = """
        SELECT products.*, users.name AS farmer_name
        FROM products
        JOIN users
        ON products.farmer_id = users.id
        WHERE 1=1
    """

    params = []

    if search:

        query += """
            AND products.name LIKE ?
        """

        params.append(f"%{search}%")

    if category:

        query += """
            AND products.category = ?
        """

        params.append(category)

    query += """
        ORDER BY products.created_at DESC
    """

    products = conn.execute(
        query,
        params
    ).fetchall()

    conn.close()

    return render_template(
        "marketplace.html",
        products=products
    )


# =========================================================
# ORDER
# =========================================================

@app.route("/order/<int:product_id>", methods=["POST"])
def create_order(product_id):

    if not session.get("user_id"):

        return redirect(url_for("login"))

    quantity = float(request.form["quantity"])

    conn = get_db()

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
    """, (product_id,)).fetchone()

    if not product:

        conn.close()

        return "Product not found", 404

    total = product["price"] * quantity

    conn.execute("""
        INSERT INTO orders
        (
            buyer_id,
            product_id,
            quantity,
            total,
            status,
            payment_status
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session["user_id"],
        product_id,
        quantity,
        total,
        "Pending",
        "Held"
    ))

    order_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.commit()

    conn.close()

    create_ledger_record(
        "ORDER",
        {
            "order_id": order_id,
            "product_id": product_id,
            "quantity": quantity,
            "total": total
        }
    )

    flash(
        "Order placed. Payment is safely held in escrow.",
        "success"
    )

    return redirect(url_for("orders"))


# =========================================================
# ORDERS
# =========================================================

@app.route("/orders")
def orders():

    if not session.get("user_id"):

        return redirect(url_for("login"))

    conn = get_db()

    orders = conn.execute("""
        SELECT
            orders.*,
            products.name AS product_name,
            products.location,
            users.name AS farmer_name
        FROM orders

        JOIN products
        ON orders.product_id = products.id

        JOIN users
        ON products.farmer_id = users.id

        WHERE orders.buyer_id = ?

        ORDER BY orders.created_at DESC
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    return render_template(
        "orders.html",
        orders=orders
    )


# =========================================================
# BUYER RECEIVES PRODUCT
# =========================================================

@app.route("/order/<int:order_id>/received")
def received(order_id):

    if not session.get("user_id"):

        return redirect(url_for("login"))

    conn = get_db()

    conn.execute("""
        UPDATE orders

        SET status = ?,
            payment_status = ?

        WHERE id = ?
        AND buyer_id = ?
    """, (
        "Delivered",
        "Released to Farmer",
        order_id,
        session["user_id"]
    ))

    conn.commit()
    conn.close()

    create_ledger_record(
        "PAYMENT_RELEASE",
        {
            "order_id": order_id,
            "status": "Released to Farmer"
        }
    )

    flash(
        "Delivery confirmed. Payment released to farmer.",
        "success"
    )

    return redirect(url_for("orders"))


# =========================================================
# DISPUTE
# =========================================================

@app.route("/order/<int:order_id>/dispute")
def dispute(order_id):

    if not session.get("user_id"):

        return redirect(url_for("login"))

    conn = get_db()

    conn.execute("""
        UPDATE orders

        SET status = ?,
            payment_status = ?

        WHERE id = ?
        AND buyer_id = ?
    """, (
        "Disputed",
        "Held",
        order_id,
        session["user_id"]
    ))

    conn.commit()
    conn.close()

    flash(
        "Dispute submitted. Payment remains held.",
        "success"
    )

    return redirect(url_for("orders"))


# =========================================================
# LEDGER
# =========================================================

def create_ledger_record(record_type, data):

    conn = get_db()

    previous = conn.execute("""
        SELECT current_hash
        FROM ledger
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    previous_hash = (
        previous["current_hash"]
        if previous
        else "GENESIS"
    )

    record_data = json.dumps(
        data,
        sort_keys=True
    )

    hash_input = (
        previous_hash +
        record_type +
        record_data
    )

    current_hash = hashlib.sha256(
        hash_input.encode()
    ).hexdigest()

    conn.execute("""
        INSERT INTO ledger
        (
            record_type,
            record_data,
            current_hash,
            previous_hash
        )
        VALUES (?, ?, ?, ?)
    """, (
        record_type,
        record_data,
        current_hash,
        previous_hash
    ))

    conn.commit()
    conn.close()


def verify_ledger():

    conn = get_db()

    records = conn.execute("""
        SELECT *
        FROM ledger
        ORDER BY id
    """).fetchall()

    previous_hash = "GENESIS"

    valid = True

    for record in records:

        hash_input = (
            previous_hash +
            record["record_type"] +
            record["record_data"]
        )

        calculated_hash = hashlib.sha256(
            hash_input.encode()
        ).hexdigest()

        if calculated_hash != record["current_hash"]:

            valid = False
            break

        previous_hash = record["current_hash"]

    conn.close()

    return valid


# =========================================================
# ADMIN
# =========================================================

@app.route("/admin")
def admin_dashboard():

    if session.get("role") != "admin":

        return redirect(url_for("login"))

    conn = get_db()

    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY created_at DESC
    """).fetchall()

    products = conn.execute("""
        SELECT *
        FROM products
        ORDER BY created_at DESC
    """).fetchall()

    orders = conn.execute("""
        SELECT *
        FROM orders
        ORDER BY created_at DESC
    """).fetchall()

    alerts = conn.execute("""
        SELECT *
        FROM fraud_alerts
        ORDER BY created_at DESC
    """).fetchall()

    conn.close()

    ledger_valid = verify_ledger()

    return render_template(
        "admin_dashboard.html",
        users=users,
        products=products,
        orders=orders,
        alerts=alerts,
        ledger_valid=ledger_valid
    )


# =========================================================
# FRAUD DETECTION
# =========================================================

def check_product_fraud(product_id):

    conn = get_db()

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
    """, (product_id,)).fetchone()

    # Rule 1:
    # suspiciously low price

    average = conn.execute("""
        SELECT AVG(price)
        FROM products
        WHERE category = ?
    """, (
        product["category"],
    )).fetchone()[0]

    if average and product["price"] < average * 0.5:

        conn.execute("""
            INSERT INTO fraud_alerts
            (
                product_id,
                alert_type,
                message
            )
            VALUES (?, ?, ?)
        """, (
            product_id,
            "LOW_PRICE",
            "Product price is significantly below category average."
        ))

    # Rule 2:
    # too many products by same farmer

    count = conn.execute("""
        SELECT COUNT(*)
        FROM products
        WHERE farmer_id = ?
        AND created_at >= datetime('now', '-10 minutes')
    """, (
        product["farmer_id"],
    )).fetchone()[0]

    if count >= 5:

        conn.execute("""
            INSERT INTO fraud_alerts
            (
                product_id,
                alert_type,
                message
            )
            VALUES (?, ?, ?)
        """, (
            product_id,
            "HIGH_ACTIVITY",
            "Farmer created many listings within a short period."
        ))

    conn.commit()
    conn.close()


# =========================================================
# API: VERIFY LEDGER
# =========================================================

@app.route("/api/verify-ledger")
def api_verify_ledger():

    valid = verify_ledger()

    return jsonify({
        "valid": valid,
        "message":
            "Ledger is valid."
            if valid
            else
            "Tampering detected!"
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )