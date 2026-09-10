import duckdb

# read parquet，output as CSV
duckdb.query("""
    COPY (SELECT * FROM '/Users/lucia/Downloads/bc_sample.parquet')
    TO '/Users/lucia/Downloads/bc_sample.csv' (FORMAT CSV, HEADER)
""")
print("done, saved to ~/Downloads/bc_sample.csv")