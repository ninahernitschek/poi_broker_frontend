import os
from pathlib import Path
from datetime import datetime, timedelta
import logging
from flask import (Flask, app, render_template, abort, jsonify, request, Response,
                   redirect, url_for, make_response, Blueprint, flash)
from flask_login import LoginManager, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError


#from werkzeug.middleware.profiler import ProfilerMiddleware
#import jinja2
from flask_sqlalchemy import SQLAlchemy
from astropy.time import Time
from .helpers import _env_bool

# Initialize SQLAlchemy instance (outside create_app for import access)
db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    
    """
    # Set up profiling middleware (only in development mode)
    profile_dir = "profiler_output"
    os.makedirs(profile_dir, exist_ok=True)

    # Wrap the app with ProfilerMiddleware
    app.wsgi_app = ProfilerMiddleware(
        app.wsgi_app,
        profile_dir=profile_dir,  # Save .prof files here
        restrictions=[30],        # Show top 30 functions in console
        sort_by=("cumulative",)   # Sort by cumulative time
    )
    """
    logging.basicConfig(handlers=[logging.FileHandler(filename="app.log", 
                                                 encoding='utf-8', mode='a+')],
                    format="%(asctime)s %(name)s:%(levelname)s:%(message)s", 
                    level=logging.INFO)

    # Main DB (alerts)
    base_dir = Path(__file__).resolve().parent  # poi_broker directory
    # Search upward from the parent package folder
    db_path = base_dir.parent.parent / '_broker_db/ztf_alerts_stream.db'
    #print(db_path)
    db_uri = 'sqlite:///{}'.format(db_path)

    # Separate DB for logins
    login_db_path = base_dir.parent.parent / '_broker_db/users.db'
    #print(login_db_path)
    login_db_uri = 'sqlite:///{}'.format(login_db_path)

    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        raise RuntimeError("SECRET_KEY environment variable must be set (generate with: python -c 'import secrets; print(secrets.token_hex(32))')")

    debug_flag = _env_bool(os.environ.get('FLASK_DEBUG'), False)
    testing_flag = _env_bool(os.environ.get('FLASK_TESTING'), False)
    session_cookie_secure = _env_bool(os.environ.get('SESSION_COOKIE_SECURE'), not debug_flag)

    # Load config from environment variables; use safe defaults for development
    app.config.update(
        DEBUG=debug_flag, # Shows detailed error pages with stack traces, and enables auto-reloading of code changes. Disable in production.
        TESTING=testing_flag, # If True, Exceptions propagate rather than being caught by Flask's error handlers. Disable in production.
        TEMPLATES_AUTO_RELOAD=True,
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_BINDS={
            'users': login_db_uri         # second DB for logins
        },
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY=secret_key, #Used by Flask to sign the session cookie data so users can't tamper with it.
        PERMANENT_SESSION_LIFETIME = timedelta(hours=24),
        SESSION_COOKIE_SECURE = session_cookie_secure,  # HTTPS only, unless in debug mode where we allow it for local testing
        SESSION_COOKIE_HTTPONLY = True,  # Prevent XSS
        SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
    )

    if app.debug is True:
        app.jinja_env.auto_reload = True
    else:
        # Enable ProxyFix to trust headers from NGINX reverse proxy in production
        from werkzeug.middleware.proxy_fix import ProxyFix
        # This ensures url_for(..., _external=True) uses the correct X-Forwarded-* headers
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        """
        NOTE: Make sure the NGINX site config passes the correct headers:
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Forwarded-Host $server_name;
        }
        """
    
    # Initialize extensions with app
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # import and register blueprints here to avoid circular imports
    from .app import main_blueprint
    from .observing_tool import observing_tool_blueprint
    from .classification import classification_blueprint
    from .auth import auth_blueprint

    app.register_blueprint(main_blueprint)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(observing_tool_blueprint)
    app.register_blueprint(classification_blueprint)

    # Configure Flask-Login after blueprint registration
    from .models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Set login_view AFTER blueprint registration to ensure the endpoint exists
    login_manager.login_view = 'auth.login'
    
    # Global error handler for CSRF errors raised by Flask-WTF
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        flash('Your form session expired or is invalid. Please reload this page and submit again. If this is a reset link, request a new one.', 'danger')
        if request.referrer:
            return redirect(request.referrer)
        return redirect(url_for('auth.login'))
    
    return app