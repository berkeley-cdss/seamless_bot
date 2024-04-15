import pandas as pd
import psycopg2

# PostgreSQL connection parameters
dbname = ...
user = ...
password = ...
host = ...

# Path to your CSV file
csv_file_path = ...

# Read the CSV file and extract column names
df = pd.read_csv(csv_file_path)
column_names = df.columns.tolist()

# Create table query
table_name = ...
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
