import sqlite3
import os
import html
import yaml
import sys

# You can set DB_PATH via environment variable or change the default here
DB_PATH = os.environ.get("DB_PATH", "debug-database.db")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "parse-db.yml")
OUTPUT_HTML = "output.html"

def parse_db_to_html(db_path, config_path, output_path):
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        sys.exit(1)
        
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error reading YAML config: {e}")
        sys.exit(1)
        
    tables_config = config.get("tables", {})
    if not tables_config:
        print("No tables specified in the configuration.")
        return

    html_content = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "    <meta charset=\"UTF-8\">",
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
        "    <title>Database Export</title>",
        "    <style>",
        "        body { font-family: Arial, sans-serif; margin: 20px; }",
        "        table { border-collapse: collapse; width: 100%; margin-bottom: 30px; }",
        "        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }",
        "        th { background-color: #f2f2f2; }",
        "    </style>",
        "</head>",
        "<body>"
    ]

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for table_name, columns in tables_config.items():
            if not columns:
                continue
                
            print(f"Fetching {table_name}...")
            
            # Use double quotes to escape SQL identifiers safely since parameterized variables 
            # cannot be used for column/table identifiers natively in SQL.
            safe_table_name = '"' + table_name.replace('"', '""') + '"'
            safe_columns = ['"' + col.replace('"', '""') + '"' for col in columns]
            columns_str = ", ".join(safe_columns)
            
            query = f"SELECT {columns_str} FROM {safe_table_name};"
            
            try:
                cursor.execute(query)
                rows = cursor.fetchall()
            except sqlite3.Error as e:
                print(f"SQL Error while querying table '{table_name}': {e}")
                sys.exit(1)

            html_content.append(f"    <h1>{html.escape(table_name.capitalize())}</h1>")
            html_content.append("    <table>")
            
            # HTML headers
            html_content.append("        <tr>")
            for col in columns:
                html_content.append(f"<th>{html.escape(str(col))}</th>")
            html_content.append("</tr>")

            # HTML data rows
            for row in rows:
                html_content.append("        <tr>")
                for val in row:
                    val_str = str(val) if val is not None else ""
                    html_content.append(f"<td>{html.escape(val_str)}</td>")
                html_content.append("</tr>")

            html_content.append("    </table>")

        conn.close()

        html_content.append("</body>")
        html_content.append("</html>")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(html_content))
            
        print(f"Data successfully exported to {output_path}")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parse_db_to_html(DB_PATH, CONFIG_PATH, OUTPUT_HTML)
