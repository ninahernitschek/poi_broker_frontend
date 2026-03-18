from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User
from . import db
import secrets
from datetime import datetime, timedelta, timezone
import os
import smtplib
import logging
from email.message import EmailMessage
from email_validator import validate_email, EmailNotValidError
import ssl


def _utc_now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def _is_reset_token_expired(expires_at) -> bool:
    if expires_at is None:
        return True
    if isinstance(expires_at, datetime):
        expires_epoch = int(expires_at.timestamp())
    else:
        try:
            expires_epoch = int(expires_at)
        except (TypeError, ValueError):
            return True
    return expires_epoch < _utc_now_epoch()

logger = logging.getLogger(__name__)
auth_blueprint = Blueprint('auth', __name__)

#IDEA: Improve emails (body and subject), add HTML version, perhaps use a proper email template, etc.

@auth_blueprint.route('/login')
def login():
    show_reset = request.args.get('forgot_password', default=False, type=bool)
    return render_template('login.html', forgot_password=show_reset)

@auth_blueprint.route('/login', methods=['POST'])
def login_post():
    email_input = request.form.get('email')
    password = request.form.get('password')
    remember = True if request.form.get('remember') else False

    if email_input is None or password is None:
        flash('Please provide an email and a password and try again.')
        return redirect(url_for('auth.login')) # reload the page

    # Normalize email for lookup (no deliverability check on login)
    email = normalize_email(email_input, check_deliverability=False)
    if not email:
        flash('Please provide a valid email address and try again.')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()

    # check if user actually exists & provided the right password (compared to hashed password in database)
    if not user or not check_password_hash(user.password, password):
        flash('Please check your login details and try again.')
        return redirect(url_for('auth.login', forgot_password=True)) # if user doesn't exist or password is wrong, reload the page
    
    # Check if email is verified
    if not user.email_verified:
        flash('Please verify your email before logging in. Check your inbox for the verification link.')
        return redirect(url_for('auth.login'))
    
    # otherwise, we know the user has the right credentials
    login_user(user, remember=remember)
    return redirect(url_for('main.profile'))

@auth_blueprint.route('/signup')
def signup():
    return render_template('signup.html')

def normalize_email(email: str, check_deliverability: bool = False) -> str | None:
    """
    Validates and normalizes an email address for correct syntax.
    Returns the normalized email if valid, None otherwise.
    Uses email_validator for RFC 5321 compliance and domain normalization.
    
    Args:
        email: Email address to validate and normalize.
        check_deliverability: If True, verify MX records (use for account creation as per email_validator docs).
                             If False, skip MX checks (use for login/lookups).
    """
    try:
        valid = validate_email(email, check_deliverability=check_deliverability)
        return valid.email
    except EmailNotValidError:
        return None

@auth_blueprint.route('/signup', methods=['POST'])
def signup_post():
    email_input = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '').strip()

    # Validate and normalize email (check deliverability for new accounts)
    email = normalize_email(email_input, check_deliverability=True)
    if not email:
        flash('Invalid email address.')
        return redirect(url_for('auth.signup'))
    
    # Validate password length
    if not password or len(password) < 8:
        flash('Password must be at least 8 characters.')
        return redirect(url_for('auth.signup'))
    
    if not name:
        flash('Name is required.')
        return redirect(url_for('auth.signup'))

    user = User.query.filter_by(email=email).first() # if this returns a user, then the email already exists in database

    if user: # if a user is found, we want to redirect back to signup page so user can try again  
        flash('Email address already exists')
        return redirect(url_for('auth.signup'))

    # Create user but mark as unverified
    new_user = User(
        email=email,
        name=name,
        password=generate_password_hash(password),
        email_verified=False,
        email_verification_token=secrets.token_urlsafe(32)
    )

    # Send verification email
    verification_link = url_for('auth.verify_email', token=new_user.email_verification_token, _external=True)
    result = send_email(
        message=f'Please verify your email by clicking here: {verification_link}',
        to_email=email,
        subject='Verify your POI Broker account',
        html_text=(
            '<html><body>'
            f'<h3>Welcome to POI Broker!</h3>'
            f'<p>Please verify your email by clicking the link below:</p>'
            f'<p><a href="{verification_link}">Verify Email</a></p>'
            f'<p>Or copy and paste this link: {verification_link}</p>'
            '</body></html>'
        )
    )

    if not result:
        flash('Failed to send verification email. Please try signing up again later.')
        return redirect(url_for('auth.signup'))
    
    #only add the user to the database if the email was sent successfully to avoid creating unverified accounts with invalid emails
    db.session.add(new_user)
    db.session.commit()

    flash('Welcome! A verification email has been sent to your address. Please check your inbox.')
    return redirect(url_for('auth.login'))

@auth_blueprint.route('/verify-email/<token>')
def verify_email(token):
    """Verify user email via token link."""
    user = User.query.filter_by(email_verification_token=token).first()
    
    if not user:
        flash('Invalid or expired verification link.')
        return redirect(url_for('auth.signup'))
    
    if user.email_verified:
        flash('Email is already verified. You can now log in.')
        return redirect(url_for('auth.login'))
    
    # Mark email as verified and clear the token
    user.email_verified = True
    user.email_verification_token = None
    db.session.commit()
    
    flash('Email verified successfully! You can now log in.')
    return redirect(url_for('auth.login'))


@auth_blueprint.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.start'))

@auth_blueprint.route('/forgot-password')
def forgot_password():
    return render_template('forgot_password.html')

@auth_blueprint.route('/forgot-password', methods=['POST'])
def forgot_password_post():
    email_input = request.form.get('email')
    
    # Normalize email for lookup (no deliverability check on password reset)
    email = normalize_email(email_input, check_deliverability=False) if email_input else None
    
    user = User.query.filter_by(email=email).first() if email else None
    
    if user:
        # Generate reset token
        user.reset_token = secrets.token_urlsafe(32)
        user.reset_token_expires = _utc_now_epoch() + int(timedelta(hours=1).total_seconds())
        db.session.commit()
        
        # Send email with reset link and expiration time
        result = send_email(
            f"Reset your password using the following link: {url_for('auth.reset_password', token=user.reset_token, _external=True)}", 
            email,
            expire_time=user.reset_token_expires
        )
        if not result:
            flash('Failed to send password reset email. Please try again later.')
        else:
            flash('Password reset link sent to your email')
    
    return redirect(url_for('auth.login'))

@auth_blueprint.route('/reset-password/<token>')
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    
    if not user or _is_reset_token_expired(user.reset_token_expires):
        flash('Invalid or expired reset token')
        return redirect(url_for('auth.login'))
    
    return render_template('reset_password.html', token=token)

@auth_blueprint.route('/reset-password/<token>', methods=['POST'])
def reset_password_post(token):
    password = request.form.get('password')
    password_confirm = request.form.get('password_confirm')

    if not password or not password_confirm:
        flash('Please provide and confirm your new password')
        return redirect(url_for('auth.reset_password', token=token))

    if password != password_confirm:
        flash('Passwords do not match')
        return redirect(url_for('auth.reset_password', token=token))

    if len(password) < 8:
        flash('Password must be at least 8 characters')
        return redirect(url_for('auth.reset_password', token=token))

    user = User.query.filter_by(reset_token=token).first()
    if not user or _is_reset_token_expired(user.reset_token_expires):
        flash('Invalid or expired reset token')
        return redirect(url_for('auth.login'))

    # Hash and store new password, clear the token fields
    user.password = generate_password_hash(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.session.commit()

    flash('Password updated. Please log in.')
    return redirect(url_for('auth.login'))


def send_email(message, to_email, subject=None, html_text=None, from_email=None, expire_time=None):
    """
    Send a multipart email (plain text + optional HTML).
    Backwards-compatible: legacy calls use send_email(message, email).
    Prefer setting SMTP env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM.
    
    Args:
        expire_time: Optional datetime when the link/token expires. If provided, displays 
                     expiration time in the email body.
    """
    # Backwards compatibility: if called as send_email(message, email)
    if to_email is None:
        raise ValueError("Recipient email address required as second argument.")

    plain_text = str(message)
    if subject is None:
        subject = os.environ.get("SMTP_SUBJECT", "Notification from POI Broker")

    # If no explicit HTML provided, create a simple HTML version
    if html_text is None:
        expire_section = ""
        if expire_time:
            expire_section = f"<p><strong>Expires:</strong> {expire_time.isoformat()} UTC</p>"
        
        html_text = (
            "<html><body>"
            f"<h3>{subject}</h3>"
            f"{expire_section}"
            f"<hr><pre style='white-space:pre-wrap'>{plain_text}</pre>"
            "</body></html>"
        )

    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
    SMTP_USER = os.environ.get("SMTP_USER")  # required for authenticated SMTP
    SMTP_PASS = os.environ.get("SMTP_APP_PASSWORD")
    FROM = from_email or os.environ.get("SMTP_FROM") or SMTP_USER
    LOCAL_HOST = os.environ.get("LOCAL_HOST", "localhost")
    #logger.info("%s %s %s %s %s %s", SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS[0:1] + "***" + SMTP_PASS[-2:-1] if SMTP_PASS else "None", FROM, LOCAL_HOST)

    if not FROM:
        raise RuntimeError("No sender address configured (SMTP_FROM or SMTP_USER)")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM
    msg["To"] = to_email
    msg.set_content(plain_text)
    msg.add_alternative(html_text, subtype="html")
    #logging.info("Email sent:\n%s", msg.as_string())

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, local_hostname=LOCAL_HOST, context=ssl.create_default_context()) as server:
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info("Sent email to %s (subject=%s)", to_email, subject)
        return True
    except Exception as exc:
        logger.exception("Failed to send email to %s: %s", to_email, exc)
        return False