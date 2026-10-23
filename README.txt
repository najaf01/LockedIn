LOCKEDIN - DEPLOYMENT PACKAGE

Files:
- flask_app.py: existing LockedIn Flask app (login/signup theme and tools retained)
- requirements.txt: dependencies

Important:
1. Do NOT upload your local lockedin.db if it contains private/testing data unless you intentionally want to migrate it. The app creates a new database on first run.
2. On PythonAnywhere, keep flask_app.py and lockedin.db in the same persistent home/project directory.
3. Set LOCKEDIN_SECRET_KEY in the PythonAnywhere WSGI configuration to a long random secret before public launch.
4. Keep your own backup of lockedin.db and the project files.
5. The current app uses SQLite, which is fine for a small initial project but is not the best database for a high-traffic production site.
