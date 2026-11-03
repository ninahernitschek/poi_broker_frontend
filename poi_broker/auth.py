from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, make_response, session
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from .models import User
from . import db
import secrets
from datetime import datetime, timedelta, timezone
import logging
from email_validator import validate_email, EmailNotValidError
from . import limiter
from .services.email_service import send_email
import hashlib
from dataclasses import dataclass
from itsdangerous import BadData, TimestampSigner, URLSafeTimedSerializer


def hash_token(token: str) -> str:
    """Return a SHA-256 hex digest of a token for safe at-rest storage."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


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

# Same copy + redirect for new signup, duplicate email, and uniqueness races
# so the response is not an account-existence oracle.
SIGNUP_GENERIC_NOTICE = (
    'If this email is available, a verification message will be sent.'
)
FORGOT_PASSWORD_GENERIC_NOTICE = (
    'If an account exists for that email, a password reset link will be sent.'
)
VERIFICATION_TOKEN_TTL_SECONDS = int(timedelta(hours=24).total_seconds())
RESET_TOKEN_TTL_SECONDS = int(timedelta(hours=1).total_seconds())
RESET_TOKEN_PREFIX = 'v1.'
RESET_TOKEN_SALT = 'poi-broker-password-reset-v1'


class _ResetTokenSigner(TimestampSigner):
    def get_timestamp(self) -> int:
        return _utc_now_epoch()


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt=RESET_TOKEN_SALT,
        signer=_ResetTokenSigner,
        signer_kwargs={'digest_method': hashlib.sha256},
    )


def _create_reset_token(user: User) -> str:
    """Issue a link without changing account state or invalidating older links."""
    payload = {
        'uid': user.id,
        'revision': user.password_changed_at or 0,
        'nonce': secrets.token_urlsafe(32),
    }
    return RESET_TOKEN_PREFIX + _reset_serializer().dumps(payload)


@dataclass(frozen=True)
class _ResetGrant:
    user_id: int
    password_hash: str
    revision: int | None
    expires_at: int
    legacy_hash: str | None = None


def _validate_reset_token(token: str) -> _ResetGrant | None:
    """Validate signed links, or unexpired pre-deployment stored token hashes."""
    legacy_hash = None
    if token.startswith(RESET_TOKEN_PREFIX):
        try:
            payload, signed_at = _reset_serializer().loads(
                token[len(RESET_TOKEN_PREFIX):],
                max_age=RESET_TOKEN_TTL_SECONDS,
                return_timestamp=True,
            )
        except BadData:
            return None
        if not isinstance(payload, dict):
            return None
        uid = payload.get('uid')
        revision = payload.get('revision')
        nonce = payload.get('nonce')
        if (
            type(uid) is not int or not 0 < uid <= 2**63 - 1
            or type(revision) is not int or revision < 0
            or not isinstance(nonce, str) or len(nonce) != 43
        ):
            return None
        user = db.session.get(User, uid)
        if user is None or (user.password_changed_at or 0) != revision:
            return None
        expires_at = int(signed_at.timestamp()) + RESET_TOKEN_TTL_SECONDS
    else:
        legacy_hash = hash_token(token)
        user = User.query.filter_by(reset_token=legacy_hash).first()
        if user is None or _is_reset_token_expired(user.reset_token_expires):
            return None
        expires_at = int(user.reset_token_expires)
    return _ResetGrant(user.id, user.password, user.password_changed_at, expires_at, legacy_hash)


def _write_password(
    user_id: int,
    expected_password: str,
    expected_revision: int | None,
    new_password: str,
    *,
    reset_expires_at: int | None = None,
    legacy_hash: str | None = None,
) -> bool:
    """Only the still-current credential may win a password write across workers."""
    new_hash = generate_password_hash(new_password)
    now = _utc_now_epoch()
    # Recheck expiry after the expensive hash, before attempting the write.
    if reset_expires_at is not None and reset_expires_at < now:
        return False
    query = User.query.filter(
        User.id == user_id,
        User.password == expected_password,
        User.password_changed_at == expected_revision,
    )
    if legacy_hash is not None:
        query = query.filter(
            User.reset_token == legacy_hash,
            User.reset_token_expires >= now,
        )
    matched = query.update({
        'password': new_hash,
        'password_changed_at': max(now, (expected_revision or 0) + 1),
        'reset_token': None,
        'reset_token_expires': None,
    }, synchronize_session=False)
    if matched != 1:
        db.session.rollback()
        return False
    db.session.commit()
    return True

# Checked against unknown emails so login burns one password-hash comparison
# either way; otherwise timing reveals whether the account exists (CWE-208).
# Generated with the same defaults as real password hashes.
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def _password_is_too_weak(password: str | None) -> bool:
    """True when a password is missing, whitespace-only, or shorter than 8 chars.

    Outer spaces are part of the secret (login compares the raw value), so
    emptiness is judged on the trimmed copy while length uses the raw value.
    """
    if not isinstance(password, str):
        return True
    return not password.strip() or len(password) < 8


def _absolute_url(endpoint: str, **values) -> str:
    """Absolute URL for email links; PUBLIC_BASE_URL wins over request Host."""
    public_base = (current_app.config.get('PUBLIC_BASE_URL') or '').rstrip('/')
    if public_base:
        return f"{public_base}{url_for(endpoint, **values)}"
    return url_for(endpoint, _external=True, **values)

#IDEA: Improve emails (body and subject), add HTML version, perhaps use a proper email template, etc.


def _signup_accepted_response():
    flash(SIGNUP_GENERIC_NOTICE, 'info')
    return redirect(url_for('auth.login'))

@auth_blueprint.route('/login')
def login():
    show_reset = request.args.get('forgot_password', default=False, type=bool)
    return render_template('login.html', forgot_password=show_reset)

@auth_blueprint.route('/login', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_LOGIN', '10 per minute'))
def login_post():
    email_input = request.form.get('email')
    password = request.form.get('password')
    remember = True if request.form.get('remember') else False

    if email_input is None or password is None:
        flash('Please provide an email and a password and try again.', 'danger')
        return redirect(url_for('auth.login')) # reload the page

    # Normalize email for lookup (no deliverability check on login)
    email = normalize_email(email_input, check_deliverability=False)
    if not email:
        flash('Please provide a valid email address and try again.', 'danger')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()

    # Exactly one password-hash comparison runs on every attempt — known
    # emails against the stored hash, unknown ones against DUMMY_PASSWORD_HASH
    # — so response timing does not reveal whether an account exists (CWE-208).
    credential_revision = (user.password_changed_at or 0) if user else 0
    if user:
        valid_password = check_password_hash(user.password, password)
    else:
        check_password_hash(DUMMY_PASSWORD_HASH, password)
        valid_password = False
    if not valid_password:
        flash('Please check your login details and try again.', 'danger')
        return redirect(url_for('auth.login', forgot_password=True)) # if user doesn't exist or password is wrong, reload the page
    
    # Check if email is verified
    if not user.email_verified:
        flash('Please verify your email before logging in. Check your inbox for the verification link.', 'warning')
        return redirect(url_for('auth.login'))
    
    # otherwise, we know the user has the right credentials
    login_user(user, remember=remember)
    # A login proves the current credential, so stamp past the watermark even
    # in the watermark second (docs/password_reset/adr_session_invalidation.md).
    session['login_at'] = max(_utc_now_epoch(), credential_revision + 1)
    session['password_revision'] = credential_revision
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
        return valid.normalized
    except EmailNotValidError:
        return None

@auth_blueprint.route('/signup', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_SIGNUP', '5 per hour'))
def signup_post():
    email_input = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')

    # Validate and normalize email (check deliverability for new accounts)
    email = normalize_email(email_input, check_deliverability=True)
    if not email:
        flash('Invalid email address.', 'danger')
        return redirect(url_for('auth.signup'))

    # Validate password length. Leading/trailing spaces are part of the
    # password (login compares the raw value), so only the trimmed copy
    # feeds the emptiness check — otherwise 8 spaces would pass len().
    if _password_is_too_weak(password):
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('auth.signup'))
    
    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('auth.signup'))

    user = User.query.filter_by(email=email).first() # if this returns a user, then the email already exists in database

    if user:
        return _signup_accepted_response()

    # Create user but mark as unverified
    raw_verification_token = secrets.token_urlsafe(32)
    verification_expires = _utc_now_epoch() + VERIFICATION_TOKEN_TTL_SECONDS
    new_user = User(
        email=email,
        name=name,
        password=generate_password_hash(password),
        email_verified=False,
        email_verification_token=hash_token(raw_verification_token),
        email_verification_token_expires=verification_expires,
    )

    # Send verification email
    verification_link = _absolute_url('auth.verify_email', token=raw_verification_token)
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
            f'<p>This link expires in 24 hours.</p>'
            '</body></html>'
        ),
        expire_time=verification_expires,
    )

    if not result:
        flash('Failed to send verification email. Please try signing up again later.', 'danger')
        return redirect(url_for('auth.signup'))
    
    #only add the user to the database if the email was sent successfully to avoid creating unverified accounts with invalid emails
    db.session.add(new_user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # uniqueness race; same response as a duplicate signup
        return _signup_accepted_response()
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        flash('Failed to create user. Please try signing up again later.', 'danger')
        return redirect(url_for('auth.signup'))

    return _signup_accepted_response()

# Email links must arrive by GET (they land in browser history, proxy logs and
# link-scanner prefetches), so like reset-password the GET only validates the
# token and shows a confirmation page; the state change happens on POST, which
# also keeps email scanners (e.g. Outlook SafeLinks) from burning the
# one-time token by prefetching it before the user clicks.
@auth_blueprint.route('/verify-email/<token>')
def verify_email(token):
    """Validate the token from the email link and show a confirmation page."""
    user = User.query.filter_by(email_verification_token=hash_token(token)).first()

    if not user or _is_reset_token_expired(user.email_verification_token_expires):
        flash('Invalid or expired verification link.', 'danger')
        return redirect(url_for('auth.signup'))

    if user.email_verified:
        flash('Email is already verified. You can now log in.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('verify_email.html', token=token)


@auth_blueprint.route('/verify-email/<token>', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_VERIFY_EMAIL', '10 per hour'))
def verify_email_post(token):
    """Mark the email verified; the token stays valid until this succeeds."""
    user = User.query.filter_by(email_verification_token=hash_token(token)).first()

    if not user or _is_reset_token_expired(user.email_verification_token_expires):
        flash('Invalid or expired verification link.', 'danger')
        return redirect(url_for('auth.signup'))

    if user.email_verified:
        flash('Email is already verified. You can now log in.', 'info')
        return redirect(url_for('auth.login'))

    # Mark email as verified and clear the token
    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_token_expires = None
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        flash('Failed to verify email. Please try again later.', 'danger')
        return redirect(url_for('auth.verify_email', token=token))

    flash('Email verified successfully! You can now log in.', 'success')
    return redirect(url_for('auth.login'))


@auth_blueprint.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    resp = make_response(redirect(url_for('main.start')))
    # Explicitly clear session cookie with secure flags
    resp.set_cookie(
        current_app.config.get('SESSION_COOKIE_NAME', 'session'),
        '',
        expires=0,
        httponly=True,
        secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
        samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax')
    )
    return resp

@auth_blueprint.route('/forgot-password')
def forgot_password():
    return render_template('forgot_password.html')

@auth_blueprint.route('/forgot-password', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_FORGOT_PASSWORD', '5 per hour'))
def forgot_password_post():
    email_input = request.form.get('email')
    
    # Normalize email for lookup (no deliverability check on password reset)
    email = normalize_email(email_input, check_deliverability=False) if email_input else None
    
    user = User.query.filter_by(email=email).first() if email else None
    
    if user:
        try:
            raw_reset_token = _create_reset_token(user)
            result = send_email(
                f"Reset your password using the following link: {_absolute_url('auth.reset_password', token=raw_reset_token)}",
                email,
                expire_time=_utc_now_epoch() + RESET_TOKEN_TTL_SECONDS,
            )
            if not result:
                logger.error('Failed to send password reset email')
        except Exception:
            logger.exception('Failed to issue password reset email')

    flash(FORGOT_PASSWORD_GENERIC_NOTICE, 'info')
    return redirect(url_for('auth.login'))

@auth_blueprint.route('/reset-password/<token>')
def reset_password(token):
    if _validate_reset_token(token) is None:
        flash('Invalid or expired reset token', 'danger')
        return redirect(url_for('auth.login'))
    
    response = make_response(render_template('reset_password.html', token=token))
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response

@auth_blueprint.route('/reset-password/<token>', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_RESET_PASSWORD', '10 per hour'))
def reset_password_post(token):
    password = request.form.get('password')
    password_confirm = request.form.get('password_confirm')

    if not password or not password_confirm:
        flash('Please provide and confirm your new password', 'danger')
        return redirect(url_for('auth.reset_password', token=token))

    if password != password_confirm:
        flash('Passwords do not match', 'danger')
        return redirect(url_for('auth.reset_password', token=token))

    if _password_is_too_weak(password):
        flash('Password must be at least 8 characters', 'danger')
        return redirect(url_for('auth.reset_password', token=token))

    grant = _validate_reset_token(token)
    if grant is None:
        flash('Invalid or expired reset token', 'danger')
        return redirect(url_for('auth.login'))

    try:
        updated = _write_password(
            grant.user_id, grant.password_hash, grant.revision, password,
            reset_expires_at=grant.expires_at, legacy_hash=grant.legacy_hash,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        flash('Failed to update password. Please try again later.', 'danger')
        return redirect(url_for('auth.reset_password', token=token))

    if not updated:
        flash('Invalid or expired reset token', 'danger')
        return redirect(url_for('auth.login'))

    # The submitting browser may still hold a live session (e.g. the account
    # owner clicking their own reset link); drop it along with the rest.
    if session.get('_user_id'):
        logout_user()

    flash('Password updated. Please log in with your new password.', 'success')
    return redirect(url_for('auth.login'))


@auth_blueprint.route('/security')
@login_required
def security():
    return render_template('security.html')

@auth_blueprint.route('/change-password', methods=['POST'])
@login_required
@limiter.limit(lambda: current_app.config.get('AUTH_RATE_LIMIT_CHANGE_PASSWORD', '10 per hour'))
def change_password():
    """
    Allow authenticated users to change their password.
    Requires current password verification for security.
    """
    from flask_login import current_user
    
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    new_password_confirm = request.form.get('new_password_confirm')
    
    # Validate that all fields are provided
    if not current_password or not new_password or not new_password_confirm:
        flash('Please provide current password and new password.', 'danger')
        return redirect(url_for('auth.security'))
    
    # Verify current password
    if not check_password_hash(current_user.password, current_password):
        flash('Current password is incorrect.', 'danger')
        return redirect(url_for('auth.security'))
    
    # Validate new password matches confirmation
    if new_password != new_password_confirm:
        flash('New passwords do not match.', 'danger')
        return redirect(url_for('auth.security'))
    
    # Validate password length
    if _password_is_too_weak(new_password):
        flash('New password must be at least 8 characters.', 'danger')
        return redirect(url_for('auth.security'))
    
    # Check that new password is different from current
    if check_password_hash(current_user.password, new_password):
        flash('New password must be different from current password.', 'danger')
        return redirect(url_for('auth.security'))
    
    # Update password. Bumping password_changed_at ends every session
    # including this one — the user_loader demands login_at after
    # the change everywhere. logout_user() also clears the remember-me cookie
    # on this browser; tokens left on other devices become inert server-side.
    try:
        updated = _write_password(
            current_user.id, current_user.password,
            current_user.password_changed_at, new_password,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f'Database error during commit: {str(e)}', exc_info=True)
        flash('Failed to change password. Please try again later.', 'danger')
        return redirect(url_for('auth.security'))

    if not updated:
        logout_user()
        flash('Your password changed during this request. Please log in again.', 'warning')
        return redirect(url_for('auth.login'))

    logout_user()
    flash('Password changed. For your security you have been signed out on all '
          'devices — please log in with your new password.', 'success')
    return redirect(url_for('auth.login'))
