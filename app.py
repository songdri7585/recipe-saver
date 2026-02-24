import os
import json
import base64
import re
import google.generativeai as genai
from notion_client import Client
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

RECIPE_PROMPT_JSON = """Return ONLY a valid JSON object with exactly these fields:
{
  "title": "recipe name",
  "isImaginary": false,
  "ingredients": {
    "main": ["ingredient 1", "ingredient 2"],
    "sauce": ["sauce ingredient 1"],
    "spicesAndHerbs": ["spice 1", "herb 1"]
  },
  "steps": [
    {
      "instruction": "step instruction text",
      "ingredients": ["ingredient A", "ingredient B"]
    }
  ],
  "cookTime": "time or null",
  "servings": "servings or null"
}

IMPORTANT CONVERSION RULES:
1. TEMPERATURE: Always convert Fahrenheit to Celsius. Write as "200°C" only. Never use Fahrenheit in output.
2. VOLUME TO WEIGHT: Convert cup/tbsp/tsp/pinch/handful and similar vague units to grams based on the specific ingredient density. Format as: "1 cup flour (120g)" or "2 tbsp olive oil (27g)". Keep the original measure AND add grams in parentheses. If you truly cannot estimate the weight, keep original unit as-is.
3. Apply these conversions to both ingredients AND step instructions.

Set "isImaginary" to true only if guessing from a photo with no recipe text.
Categorize ingredients: main = proteins, vegetables, grains, dairy. sauce = liquids, oils, vinegars, condiments. spicesAndHerbs = dried/fresh spices, herbs, seasonings, salt, pepper.
For each step, list only the ingredients actually used in that step. If none, return empty array.
No markdown, no extra text, just the JSON."""

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/extract', methods=['POST'])
def extract_recipe():
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        recipe = None
        source = "image"

        # --- TEXT MODE ---
        recipe_text = request.form.get('recipe_text', '').strip()
        if recipe_text:
            prompt = f"Extract and structure a recipe from this text.\n\nText:\n{recipe_text}\n\n{RECIPE_PROMPT_JSON}"
            response = model.generate_content(prompt)
            text = response.text.strip().replace('```json', '').replace('```', '').strip()
            recipe = json.loads(text)
            recipe['isImaginary'] = False
            source = "text"

        # --- IMAGE MODE ---
        else:
            if 'images' not in request.files:
                return jsonify({'error': 'Please provide recipe text or upload images.'}), 400
            files = request.files.getlist('images')
            if not files:
                return jsonify({'error': 'No images provided'}), 400
            image_parts = []
            for file in files:
                image_data = file.read()
                image_parts.append({"mime_type": file.content_type, "data": base64.b64encode(image_data).decode('utf-8')})
            prompt = f"""Look at these images carefully.
CASE 1: If the image contains an actual written recipe, extract it exactly.
CASE 2: If the image only shows a food photo without a written recipe, create a realistic recipe for what you see.
{RECIPE_PROMPT_JSON}"""
            parts = [prompt] + [{"inline_data": img} for img in image_parts]
            response = model.generate_content(parts)
            text = response.text.strip().replace('```json', '').replace('```', '').strip()
            recipe = json.loads(text)
            source = "image"

        # --- SAVE TO NOTION ---
        notion_token = os.environ.get("NOTION_TOKEN")
        notion_page_id = os.environ.get("NOTION_PAGE_ID")
        if not notion_token or not notion_page_id:
            return jsonify({'error': 'Notion not configured'}), 500

        notion = Client(auth=notion_token)
        is_imaginary = recipe.get("isImaginary", False)
        ingredients = recipe.get("ingredients", {})
        steps = recipe.get("steps", [])

        if isinstance(ingredients, list):
            main_list, sauce_list, spice_list = ingredients, [], []
        else:
            main_list = ingredients.get("main", [])
            sauce_list = ingredients.get("sauce", [])
            spice_list = ingredients.get("spicesAndHerbs", [])

        def parse_ingredient_rich_text(text):
            # Split "1 cup flour (120g)" into main text and grey parenthetical
            match = re.search(r'(.*?)(\s*\([^)]+\))\s*$', text)
            if match:
                main = match.group(1).strip()
                note = match.group(2).strip()
                return [
                    {"type": "text", "text": {"content": main + " "}},
                    {"type": "text", "text": {"content": note}, "annotations": {"color": "gray", "italic": True}}
                ]
            return [{"type": "text", "text": {"content": text}}]

        def checkbox(text, rich=False):
            rich_text = parse_ingredient_rich_text(text) if rich else [{"type": "text", "text": {"content": text}}]
            return {"object": "block", "type": "to_do", "to_do": {"rich_text": rich_text, "checked": False}}

        def heading3(text):
            return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

        def build_steps(steps):
            blocks = []
            for step in steps:
                instruction = step if isinstance(step, str) else step.get("instruction", "")
                step_ings = [] if isinstance(step, str) else step.get("ingredients", [])
                blocks.append(checkbox(instruction))
                if step_ings:
                    blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "🧂 " + "  ·  ".join(step_ings)}, "annotations": {"color": "blue", "italic": True}}]}})
            return blocks

        children = []
        if is_imaginary:
            children.append({"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": "⚠️ This is an AI-imagined recipe based on a food photo. Use as inspiration only!"}}], "icon": {"emoji": "🤖"}, "color": "yellow_background"}})
        if source == "text":
            children.append({"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": "📝 Extracted from pasted recipe text."}}], "icon": {"emoji": "📝"}, "color": "blue_background"}})

        children += [
            {"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": f"⏱ Cook Time: {recipe.get('cookTime') or 'N/A'}     👥 Servings: {recipe.get('servings') or 'N/A'}"}}], "icon": {"emoji": "🍳"}, "color": "orange_background"}},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🥘 Ingredients"}}]}},
        ]
        if main_list:
            children.append(heading3("Main Ingredients"))
            children.extend([checkbox(i, rich=True) for i in main_list])
        if sauce_list:
            children.append(heading3("Sauce"))
            children.extend([checkbox(i, rich=True) for i in sauce_list])
        if spice_list:
            children.append(heading3("Spices & Herbs"))
            children.extend([checkbox(i, rich=True) for i in spice_list])
        children += [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "👨‍🍳 Steps"}}]}},
            *build_steps(steps),
        ]

        # Add source link at bottom if provided
        source_link = request.form.get('source_link', '').strip()
        if source_link:
            children.append({"object": "block", "type": "divider", "divider": {}})
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "🔗 Source: "}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": source_link, "link": {"url": source_link}}, "annotations": {"color": "blue"}}
                    ]
                }
            })

        title = recipe.get("title", "Untitled Recipe")
        if is_imaginary:
            title = f"✨ {title} (AI Recipe)"

        notion.pages.create(
            parent={"page_id": notion_page_id},
            icon={"emoji": "🤖" if is_imaginary else "🍽️"},
            properties={"title": {"title": [{"text": {"content": title}}]}},
            children=children
        )

        all_ingredients = main_list + sauce_list + spice_list
        flat_steps = [s if isinstance(s, str) else s.get("instruction", "") for s in steps]
        return jsonify({'success': True, 'isImaginary': is_imaginary, 'source': source, 'recipe': {**recipe, 'ingredients': all_ingredients, 'steps': flat_steps}})

    except json.JSONDecodeError:
        return jsonify({'error': 'Could not parse recipe. Try again or rephrase the text.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
