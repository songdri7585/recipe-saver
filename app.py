import os
import json
import base64
import google.generativeai as genai
from notion_client import Client
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

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

        image_parts = []
        for file in files:
            image_data = file.read()
            image_parts.append({
                "mime_type": file.content_type,
                "data": base64.b64encode(image_data).decode('utf-8')
            })

        model = genai.GenerativeModel('gemini-2.5-flash-lite')

        prompt = """Look at these images carefully.

CASE 1: If the image contains an actual written recipe (with ingredients and steps listed), extract it exactly.
CASE 2: If the image only shows a food photo without a written recipe, create a realistic recipe for what you see in the photo.

Return ONLY a valid JSON object with exactly these fields:
{
  "title": "recipe name",
  "isImaginary": false,
  "ingredients": {
    "main": ["ingredient 1", "ingredient 2"],
    "sauce": ["sauce ingredient 1"],
    "spicesAndHerbs": ["spice 1", "herb 1"]
  },
  "steps": ["step 1", "step 2"],
  "cookTime": "time or null",
  "servings": "servings or null"
}

Set "isImaginary" to true if you are guessing/creating the recipe from a photo, false if you extracted it from written text.
Categorize ingredients: main = proteins, vegetables, grains, dairy. sauce = liquids, oils, vinegars, condiments. spicesAndHerbs = dried/fresh spices, herbs, seasonings, salt, pepper.
If a category is empty return an empty array. No markdown, no extra text, just the JSON."""

        parts = [prompt]
        for img in image_parts:
            parts.append({"inline_data": img})

        response = model.generate_content(parts)
        text = response.text.strip()
        text = text.replace('```json', '').replace('```', '').strip()
        recipe = json.loads(text)

        notion_token = os.environ.get("NOTION_TOKEN")
        notion_page_id = os.environ.get("NOTION_PAGE_ID")

        if not notion_token or not notion_page_id:
            return jsonify({'error': 'Notion not configured'}), 500

        notion = Client(auth=notion_token)

        is_imaginary = recipe.get("isImaginary", False)
        ingredients = recipe.get("ingredients", {})

        if isinstance(ingredients, list):
            main_blocks = [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": ing}}]}} for ing in ingredients]
            sauce_blocks = []
            spice_blocks = []
        else:
            main_blocks = [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": ing}}]}} for ing in ingredients.get("main", [])]
            sauce_blocks = [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": ing}}]}} for ing in ingredients.get("sauce", [])]
            spice_blocks = [{"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": ing}}]}} for ing in ingredients.get("spicesAndHerbs", [])]

        step_blocks = [{"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"type": "text", "text": {"content": step}}]}} for step in recipe.get("steps", [])]

        def heading3(text):
            return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

        children = []

        if is_imaginary:
            children.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": "⚠️ This is an AI-imagined recipe based on a food photo. Ingredients and steps are estimated and may not reflect the actual dish. Use as inspiration only!"}}],
                    "icon": {"emoji": "🤖"},
                    "color": "yellow_background"
                }
            })

        children += [
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": f"⏱ Cook Time: {recipe.get('cookTime') or 'N/A'}     👥 Servings: {recipe.get('servings') or 'N/A'}"}}],
                    "icon": {"emoji": "🍳"},
                    "color": "orange_background"
                }
            },
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🥘 Ingredients"}}]}},
        ]

        if main_blocks:
            children.append(heading3("Main Ingredients"))
            children.extend(main_blocks)

        if sauce_blocks:
            children.append(heading3("Sauce"))
            children.extend(sauce_blocks)

        if spice_blocks:
            children.append(heading3("Spices & Herbs"))
            children.extend(spice_blocks)

        children += [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "👨‍🍳 Steps"}}]}},
            *step_blocks,
        ]

        title = recipe.get("title", "Untitled Recipe")
        if is_imaginary:
            title = f"✨ {title} (AI Recipe)"

        # Save as subpage under the designated parent page
        notion.pages.create(
            parent={"page_id": notion_page_id},
            icon={"emoji": "🤖" if is_imaginary else "🍽️"},
            properties={
                "title": {"title": [{"text": {"content": title}}]}
            },
            children=children
        )

        all_ingredients = []
        if isinstance(ingredients, list):
            all_ingredients = ingredients
        else:
            all_ingredients = ingredients.get("main", []) + ingredients.get("sauce", []) + ingredients.get("spicesAndHerbs", [])

        return jsonify({'success': True, 'isImaginary': is_imaginary, 'recipe': {**recipe, 'ingredients': all_ingredients}})

    except json.JSONDecodeError:
        return jsonify({'error': 'Could not parse recipe from image. Try a clearer screenshot.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
