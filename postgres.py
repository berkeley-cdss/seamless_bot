import pandas as pd
import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# PostgreSQL connection parameters (loaded from .env)
dbname = os.getenv("DB_NAME")
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST")

# Path to your CSV file
csv_file_path = "gradescope.csv"

# Read the CSV file and extract column names
df = pd.read_csv(csv_file_path)
column_names = df.columns.tolist()

# Create table query
table_name = "c88c_gradescope"
creat_table_query = f"CREATE TABLE {table_name} ("
for column in column_names:
    creat_table_query+= f'"{column}" VARCHAR, '
creat_table_query = creat_table_query[:-2] + ");"

# Connect to PostgreSQL and create the table
conn = psycopg2.connect(dbname = dbname,user = user, password = password,host = host)
cur = conn.cursor()
cur.execute(creat_table_query)
conn.commit()

# Close connection
cur.close()
conn.close()
