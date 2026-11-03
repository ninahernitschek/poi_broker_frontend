"""Reset availability, token revocation, and cross-request credential races."""
import re

import pytest
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash

from poi_broker import db
from poi_broker import auth
from poi_broker.models import User

EMAIL = 'reset-safety@example.com'
OLD_PASSWORD = 'Password123!'


@pytest.fixture
def account(app, user_factory):
    user_factory(email=EMAIL, password=OLD_PASSWORD)
    return app


def _snapshot(app):
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        return user.password, user.password_changed_at, user.reset_token, user.reset_token_expires


def _signed_token(app):
    with app.app_context():
        return auth._create_reset_token(User.query.filter_by(email=EMAIL).first())


def _legacy_token(app):
    token = 'pre-deployment-reset-token'
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        user.reset_token = auth.hash_token(token)
        user.reset_token_expires = auth._utc_now_epoch() + auth.RESET_TOKEN_TTL_SECONDS
        db.session.commit()
    return token


def _reset(client, token, password):
    return client.post('/reset-password/' + token, data={
        'password': password, 'password_confirm': password,
    })


def _login(app):
    client = app.test_client()
    client.post('/login', data={'email': EMAIL, 'password': OLD_PASSWORD})
    assert client.get('/security').status_code == 200
    return client


@pytest.mark.parametrize('mail_outcome', [True, False, 'exception'])
def test_another_email_request_preserves_existing_signed_and_legacy_links(account, monkeypatch, mail_outcome):
    client = account.test_client()
    legacy = _legacy_token(account)
    signed = _signed_token(account)
    before = _snapshot(account)
    sent = []

    def mail(*args, **kwargs):
        sent.append(args[0])
        if mail_outcome == 'exception':
            raise RuntimeError('SMTP unavailable')
        return mail_outcome

    monkeypatch.setattr(auth, 'send_email', mail)
    response = client.post('/forgot-password', data={'email': EMAIL})
    assert response.status_code == 302
    assert _snapshot(account) == before
    assert client.get('/reset-password/' + legacy).status_code == 200
    assert client.get('/reset-password/' + signed).status_code == 200
    emailed_token = re.search(r'/reset-password/([\w.-]+)', sent[0]).group(1)
    assert emailed_token != signed
    assert client.get('/reset-password/' + emailed_token).status_code == 200


def test_new_link_does_not_extend_an_older_links_expiry(account, monkeypatch):
    clock = [1800000000]
    monkeypatch.setattr(auth, '_utc_now_epoch', lambda: clock[0])
    first = _signed_token(account)
    clock[0] += 100
    second = _signed_token(account)
    clock[0] += auth.RESET_TOKEN_TTL_SECONDS - 99
    client = account.test_client()
    assert client.get('/reset-password/' + first).status_code == 302
    assert client.get('/reset-password/' + second).status_code == 200


def test_signed_reset_revokes_all_links_and_get_does_not_consume_them(account):
    client = account.test_client()
    legacy = _legacy_token(account)
    first, second = _signed_token(account), _signed_token(account)
    before = _snapshot(account)
    for token in (first, second, legacy):
        response = client.get('/reset-password/' + token)
        assert response.status_code == 200
        assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert _snapshot(account) == before
    assert _reset(client, first, 'NewPassword123!').status_code == 302
    password, revision, stored_token, expires = _snapshot(account)
    assert check_password_hash(password, 'NewPassword123!')
    assert revision is not None
    assert stored_token is expires is None
    for token in (first, second, legacy):
        assert client.get('/reset-password/' + token).status_code == 302
        _reset(client, token, 'OverwritePassword!')
        assert _snapshot(account)[0] == password


def test_tampered_and_wrong_purpose_tokens_are_rejected(account):
    token = _signed_token(account)
    prefix, signature = token.rsplit('.', 1)
    tampered = prefix + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
    with account.app_context():
        wrong_purpose = auth.RESET_TOKEN_PREFIX + URLSafeTimedSerializer(
            account.config['SECRET_KEY'], salt='another-purpose',
            signer=auth._ResetTokenSigner, signer_kwargs={'digest_method': auth.hashlib.sha256},
        ).dumps({'uid': 1, 'revision': 0, 'nonce': 'x' * 43})
    client = account.test_client()
    for invalid in (tampered, wrong_purpose, 'v1.not-a-signed-token'):
        assert client.get('/reset-password/' + invalid).status_code == 302
    assert client.get('/reset-password/' + token).status_code == 200


@pytest.mark.parametrize('payload', [
    [], {'uid': True, 'revision': 0, 'nonce': 'x' * 43},
    {'uid': 2**80, 'revision': 0, 'nonce': 'x' * 43},
    {'uid': 1, 'revision': False, 'nonce': 'x' * 43},
    {'uid': 1, 'revision': 0},
])
def test_malformed_signed_payload_is_rejected(account, payload):
    with account.app_context():
        token = auth.RESET_TOKEN_PREFIX + auth._reset_serializer().dumps(payload)
    assert account.test_client().get('/reset-password/' + token).status_code == 302


@pytest.mark.parametrize('kind', ['signed', 'different_signed', 'legacy'])
def test_only_one_interleaved_reset_can_write_the_password(account, monkeypatch, kind):
    first = _legacy_token(account) if kind == 'legacy' else _signed_token(account)
    second = _signed_token(account) if kind == 'different_signed' else first
    outer, inner = account.test_client(), account.test_client()
    real_hash = auth.generate_password_hash
    inside = False

    def interleave(password):
        nonlocal inside
        hashed = real_hash(password)
        if not inside:
            inside = True
            _reset(inner, second, 'WinningPassword!')
            assert check_password_hash(_snapshot(account)[0], 'WinningPassword!')
        return hashed

    monkeypatch.setattr(auth, 'generate_password_hash', interleave)
    _reset(outer, first, 'StalePassword!')
    assert check_password_hash(_snapshot(account)[0], 'WinningPassword!')
    assert 'Invalid or expired reset token' in outer.get('/login').get_data(as_text=True)


@pytest.mark.parametrize('winner', ['reset', 'change'])
def test_reset_and_authenticated_change_cannot_overwrite_each_other(account, monkeypatch, winner):
    token = _signed_token(account)
    change_client, reset_client = _login(account), account.test_client()
    real_hash = auth.generate_password_hash
    inside = False

    def change(password):
        return change_client.post('/change-password', data={
            'current_password': OLD_PASSWORD,
            'new_password': password, 'new_password_confirm': password,
        })

    def interleave(password):
        nonlocal inside
        hashed = real_hash(password)
        if not inside:
            inside = True
            if winner == 'reset':
                _reset(reset_client, token, 'WinningPassword!')
            else:
                change('WinningPassword!')
            assert check_password_hash(_snapshot(account)[0], 'WinningPassword!')
        return hashed

    monkeypatch.setattr(auth, 'generate_password_hash', interleave)
    if winner == 'reset':
        change('StalePassword!')
    else:
        _reset(reset_client, token, 'StalePassword!')
    assert check_password_hash(_snapshot(account)[0], 'WinningPassword!')


def test_same_second_rotations_advance_revision_and_revoke_new_sessions(account, monkeypatch):
    now = 1800000000
    monkeypatch.setattr(auth, '_utc_now_epoch', lambda: now)
    client = account.test_client()
    _reset(client, _signed_token(account), OLD_PASSWORD)
    assert _snapshot(account)[1] == now
    other, actor = _login(account), _login(account)
    actor.post('/change-password', data={
        'current_password': OLD_PASSWORD,
        'new_password': 'SecondPassword!', 'new_password_confirm': 'SecondPassword!',
    })
    assert _snapshot(account)[1] == now + 1
    assert other.get('/security').status_code == 302
    token = _signed_token(account)
    _reset(client, token, 'ThirdPassword!')
    assert _snapshot(account)[1] == now + 2


def test_login_overlapping_rotation_cannot_authenticate_with_stale_credential(account, monkeypatch):
    clock = [1800000000]
    monkeypatch.setattr(auth, '_utc_now_epoch', lambda: clock[0])
    token = _signed_token(account)
    real_check = auth.check_password_hash
    inside = False

    def interleave(hashed, password):
        nonlocal inside
        valid = real_check(hashed, password)
        if not inside:
            inside = True
            _reset(account.test_client(), token, 'NewPassword123!')
            clock[0] += 10
        return valid

    monkeypatch.setattr(auth, 'check_password_hash', interleave)
    client = account.test_client()
    client.post('/login', data={'email': EMAIL, 'password': OLD_PASSWORD})
    assert client.get('/security').status_code == 302


def test_token_expiring_during_hashing_cannot_update_password(account, monkeypatch):
    clock = [1800000000]
    monkeypatch.setattr(auth, '_utc_now_epoch', lambda: clock[0])
    token = _signed_token(account)
    before = _snapshot(account)
    real_hash = auth.generate_password_hash

    def slow_hash(password):
        result = real_hash(password)
        clock[0] += auth.RESET_TOKEN_TTL_SECONDS + 1
        return result

    monkeypatch.setattr(auth, 'generate_password_hash', slow_hash)
    _reset(account.test_client(), token, 'NewPassword123!')
    assert _snapshot(account) == before


def test_commit_failure_does_not_consume_reset_link(account, monkeypatch):
    token = _signed_token(account)
    before = _snapshot(account)
    client = account.test_client()
    with monkeypatch.context() as patch:
        def failed_commit():
            raise RuntimeError('simulated commit failure')
        patch.setattr(db.session, 'commit', failed_commit)
        response = _reset(client, token, 'NewPassword123!')
        assert '/reset-password/' in response.location
    assert _snapshot(account) == before
    assert client.get('/reset-password/' + token).status_code == 200
    _reset(client, token, 'NewPassword123!')
    assert check_password_hash(_snapshot(account)[0], 'NewPassword123!')
