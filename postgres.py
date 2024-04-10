import csv
import psycopg2

# Connect to PostgreSQL database
conn = psycopg2.connect(dbname=..., user=..., password=..., host=...)
cur = conn.cursor()

# Read the CSV file and extract column names
with open('data100.csv', 'r') as csv_file:
    reader = csv.reader(csv_file)
    header = next(reader)  # Get header row
    new_columns = set(header)

# Get existing column names from the database table
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'your_table_name';")
existing_columns = {row[0] for row in cur.fetchall()}

# Identify new columns
missing_columns = new_columns - existing_columns

# Alter table schema to add new columns
for column in missing_columns:
    cur.execute(f"ALTER TABLE your_table_name ADD COLUMN {column} TEXT;")

# Commit changes and close connection
conn.commit()
conn.close()
