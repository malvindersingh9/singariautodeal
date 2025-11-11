import os, random, string
from datetime import datetime, timedelta
from io import BytesIO

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from xhtml2pdf import pisa
from twilio.rest import Client

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'devsecretkey')
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

TWILIO_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_FROM = os.environ.get('TWILIO_FROM')

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(120))

class OTP(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(32), nullable=False)
    code = db.Column(db.String(8), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

class InvoiceSequence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    next_invoice = db.Column(db.Integer, nullable=False)

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.Integer, unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(32))
    date = db.Column(db.String(32))
    name = db.Column(db.String(255))
    address = db.Column(db.Text)
    contact_no = db.Column(db.String(64))
    model = db.Column(db.String(128))
    amount_main = db.Column(db.Float, default=0.0)
    gst = db.Column(db.Float, default=0.0)
    other = db.Column(db.Float, default=0.0)
    accessories = db.Column(db.Text)
    total = db.Column(db.Float, default=0.0)
    rupees_in_words = db.Column(db.Text)
    bank_details = db.Column(db.Text)

# Initialize database (Flask 3.x compatible)
with app.app_context():
    db.create_all()
    seq = InvoiceSequence.query.first()
    if not seq:
        seq = InvoiceSequence(next_invoice=10001)
        db.session.add(seq)
        db.session.commit()

def send_otp(mobile, code):
    if TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM:
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(body=f"Your OTP code: {code}", from_=TWILIO_FROM, to=mobile if mobile.startswith('+') else f"+91{mobile}")
            return True
        except Exception as e:
            print("Twilio send failed:", e)
            return False
    else:
        print(f"[DEV OTP] {mobile} -> {code}")
        return True

def get_next_invoice_number():
    seq = InvoiceSequence.query.first()
    if not seq:
        seq = InvoiceSequence(next_invoice=10001)
        db.session.add(seq)
        db.session.commit()
    next_num = seq.next_invoice
    seq.next_invoice = next_num + 1
    db.session.commit()
    return next_num

@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile','').strip()
        if not mobile:
            flash("Enter mobile", "danger")
            return redirect(url_for('login'))
        if mobile.isdigit() and len(mobile)==10:
            send_to = f"+91{mobile}"
        else:
            send_to = mobile
        code = ''.join(random.choices(string.digits, k=6))
        otp = OTP(mobile=send_to, code=code, expires_at=datetime.utcnow()+timedelta(minutes=5))
        db.session.add(otp); db.session.commit()
        send_otp(send_to, code)
        session['pending_mobile'] = send_to
        flash("OTP sent.", "info")
        return redirect(url_for('verify'))
    return render_template('login.html')

@app.route('/verify', methods=['GET','POST'])
def verify():
    mobile = session.get('pending_mobile')
    if not mobile:
        return redirect(url_for('login'))
    if request.method == 'POST':
        code = request.form.get('code','').strip()
        otp = OTP.query.filter_by(mobile=mobile, code=code, used=False).order_by(OTP.expires_at.desc()).first()
        if not otp:
            flash("Invalid OTP", "danger"); return redirect(url_for('verify'))
        if otp.expires_at < datetime.utcnow():
            flash("OTP expired", "danger"); return redirect(url_for('login'))
        otp.used = True; db.session.commit()
        emp = Employee.query.filter_by(mobile=mobile).first()
        if not emp:
            emp = Employee(mobile=mobile); db.session.add(emp); db.session.commit()
        session['user_mobile'] = mobile
        session.pop('pending_mobile', None)
        return redirect(url_for('dashboard'))
    return render_template('verify.html', mobile=mobile)

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_mobile' not in session:
        return redirect(url_for('login'))
    invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(200).all()
    return render_template('dashboard.html', invoices=invoices)

@app.route('/invoice/new', methods=['GET','POST'])
def new_invoice():
    if 'user_mobile' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        form = request.form
        try:
            amount_main = float(form.get('amount_main') or 0)
            gst = float(form.get('gst') or 0)
            other = float(form.get('other') or 0)
        except:
            amount_main = gst = other = 0.0
        total = amount_main + gst + other
        invoice_number = get_next_invoice_number()
        inv = Invoice(
            invoice_number=invoice_number,
            created_by=session.get('user_mobile'),
            date=form.get('date') or datetime.utcnow().strftime("%d/%m/%Y"),
            name=form.get('name'),
            address=form.get('address'),
            contact_no=form.get('contact_no'),
            model=form.get('model'),
            amount_main=amount_main,
            gst=gst,
            other=other,
            accessories=form.get('accessories'),
            total=total,
            rupees_in_words=form.get('rupees_in_words'),
            bank_details=form.get('bank_details') or "Bank : J&K Bank Branch : SMGS Hospital, Jammu. A/c No. : 1203020100000169 IFSC Code : JAKA0EMCJAM"
        )
        db.session.add(inv); db.session.commit()
        flash(f"Invoice {invoice_number} created", "success")
        return redirect(url_for('invoice_pdf', invoice_id=inv.id))
    return render_template('invoice_form.html', today=datetime.utcnow().strftime("%d/%m/%Y"))

@app.route('/invoice/<int:invoice_id>')
def view_invoice(invoice_id):
    if 'user_mobile' not in session:
        return redirect(url_for('login'))
    inv = Invoice.query.get_or_404(invoice_id)
    return render_template('invoice_view.html', inv=inv)

@app.route('/invoice/<int:invoice_id>/pdf')
def invoice_pdf(invoice_id):
    if 'user_mobile' not in session:
        return redirect(url_for('login'))
    inv = Invoice.query.get_or_404(invoice_id)
    html = render_template('invoice_pdf.html', inv=inv)
    result = BytesIO()
    pisa_status = pisa.CreatePDF(src=html, dest=result)
    if pisa_status.err:
        return "Error generating PDF", 500
    result.seek(0)
    return send_file(result, mimetype='application/pdf', as_attachment=True, download_name=f'invoice_{inv.invoice_number}.pdf')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
