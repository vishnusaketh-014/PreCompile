import sys
sys.path.insert(0, ".")
import os
import tempfile
import sqlite3
from dashboard.reader import DashboardDataReader

with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tf:
    temp_path = tf.name

try:
    # Seed database
    c = sqlite3.connect(temp_path)
    c.execute('CREATE TABLE builds (id INT, duration REAL);')
    c.execute('INSERT INTO builds VALUES (1, 2.04);')
    c.commit()
    c.close()

    # Access via DashboardDataReader in read-only mode
    r = DashboardDataReader(db_path=temp_path)
    conn = r.connect_ro()

    print("Attempting write: INSERT INTO builds VALUES (2, 8.38)...")
    try:
        conn.execute('INSERT INTO builds VALUES (2, 8.38);')
        print("FAIL: write succeeded unexpectedly")
    except sqlite3.OperationalError as e:
        print("PASS: Write strictly rejected by SQLite with OperationalError:")
        print(f"      {e}")
    finally:
        conn.close()
finally:
    if os.path.exists(temp_path):
        os.remove(temp_path)
