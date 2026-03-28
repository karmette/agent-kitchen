import sqlite3
import os
import html

# You can set DB_PATH via environment variable or change the default here
DB_PATH = os.environ.get("DB_PATH", "debug-database.db")
OUTPUT_HTML = "output.html"

def parse_db_to_html(db_path, output_path):
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        return
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Fetch Posts
        print("Fetching posts...")
        cursor.execute("SELECT post_id, user_id, content FROM post;")
        posts = cursor.fetchall()

        # Fetch Comments
        print("Fetching comments...")
        cursor.execute("SELECT comment_id, post_id, user_id, content FROM comment;")
        comments = cursor.fetchall()

        conn.close()

        # Generate HTML
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
            "<body>",
            "    <h1>Posts</h1>",
            "    <table>",
            "        <tr><th>Post ID</th><th>User ID</th><th>Content</th></tr>"
        ]

        for post in posts:
            post_id, user_id, content = (post[0], post[1], post[2]) if post else ("", "", "")
            html_content.append(f"        <tr><td>{html.escape(str(post_id))}</td><td>{html.escape(str(user_id))}</td><td>{html.escape(str(content))}</td></tr>")

        html_content.append("    </table>")

        html_content.append("    <h1>Comments</h1>")
        html_content.append("    <table>")
        html_content.append("        <tr><th>Comment ID</th><th>Post ID</th><th>User ID</th><th>Comment</th></tr>")

        for comment in comments:
            comment_id, post_id, user_id, comment_text = (comment[0], comment[1], comment[2], comment[3]) if comment else ("", "", "", "")
            html_content.append(f"        <tr><td>{html.escape(str(comment_id))}</td><td>{html.escape(str(post_id))}</td><td>{html.escape(str(user_id))}</td><td>{html.escape(str(comment_text))}</td></tr>")

        html_content.append("    </table>")
        html_content.append("</body>")
        html_content.append("</html>")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(html_content))
            
        print(f"Data successfully exported to {output_path}")

    except sqlite3.Error as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    parse_db_to_html(DB_PATH, OUTPUT_HTML)
