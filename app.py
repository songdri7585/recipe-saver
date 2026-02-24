import os
import json
import base64
import google.generativeai as genai
from notion_client import Client
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

# Configure Gemini
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/extract', methods=['POST'])
def extract_recipe():
    try:
        if 'images' not in request.files:
            return jsonify({'error': 'No images provided'}), 400

        files = request.files.getlist('images')
        if not files:
            return jsonify({'error': 'No images provided'}), 400

        # 1. Prepare images for Gemini
        image_parts = []
        for file in files:
            image_data = file.read()
            image_parts.append({
                "mime_type": file.content_type,
                "data": base64.b64encode(image_data).decode('utf-8')
            })

        # 2. Call Gemini
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        prompt = """Extract the recipe from these images. Return ONLY a valid JSON object with exactly these fields:
{
  "title": "recipe name",
  "ingredients": ["ingredient 1", "ingredient 2"],
  "steps": ["step 1", "step 2"],
  "cookTime": "time or null",
  "servings": "servings or null"
}
No markdown, no extra text, just the JSON."""

        parts = [prompt]
        for img in image_parts:
            parts.append({"inline_data": img})

        response = model.generate_content(parts)
        text = response.text.strip()
        text = text.replace('```json', '').replace('```', '').strip()
        recipe = json.loads(text)

        # 3. Save to Notion
        notion_token = os.environ.get("NOTION_TOKEN")
        notion_db_id = os.environ.get("NOTION_DATABASE_ID")

        if not notion_token or not notion_db_id:
            return jsonify({'error': 'Notion not configured'}), 500

        notion = Client(auth=notion_token)
        
        notion.pages.create(
            parent={"database_id": notion_db_id},
            properties={
                "Name": {
                    "title": [{"text": {"content": recipe.get("title", "Untitled Recipe")}}]
                },
                "Ingredients": {
                    "rich_text": [{"text": {"content": "\n".join(recipe.get("ingredients", []))}}]
                },
                "Steps": {
                    "rich_text": [{"text": {"content": "\n".join(recipe.get("steps", []))}}]
                },
                "Cook Time": {
                    "rich_text": [{"text": {"content": recipe.get("cookTime") or "N/A"}}]
                },
                "Servings": {
                    "rich_text": [{"text": {"content": recipe.get("servings") or "N/A"}}]
                }
            }
        )

        return jsonify({'success': True, 'recipe': recipe})

    except json.JSONDecodeError as e:
        return jsonify({'error': 'Could not parse recipe from image. Try a clearer screenshot.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
