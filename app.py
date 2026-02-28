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
  "servings": "servings or null",
  "lang": "en"
}
Set "lang" to "fr" if the recipe is in French, "ko" if Korean, "en" for everything else.

IMPORTANT CONVERSION RULES:
1. TEMPERATURE: Always convert Fahrenheit to Celsius. Write as "200°C" only. Never use Fahrenheit in output.
2. VOLUME TO WEIGHT: Convert ALL volume and imperial measurements to grams. This includes: cup, tbsp, tsp, fl oz, oz, lb, pinch, handful, bunch, pack, and any other non-metric unit. For oz specifically: 1 oz = 28.35g, always convert. For lb: 1 lb = 454g, always convert. Use the specific ingredient's density for accuracy. Format ALWAYS as: "original measure ingredient name (Xg)". Examples: "1 cup flour (120g)", "2 tbsp olive oil (27g)", "1/4 cup fresh dill (10g)", "2 (8-oz.) salmon fillets (454g)", "1/2 cup sliced almonds (55g)". NEVER skip a conversion if the ingredient has a measurable weight. Only skip if truly unmeasurable (e.g. "1 bay leaf" is acceptable to skip).
3. Apply these conversions to both ingredients AND step instructions.

LANGUAGE RULES:
- If the recipe is in French or Korean, keep EVERYTHING in that original language (title, ingredients, steps, all text). Do not translate anything.
- If the recipe is in any other language, translate everything to English.
- The section headers (like ingredients, steps) should also match the recipe language. For French use: "Ingrédients principaux", h_sauce, "Épices & Herbes", "Étapes". For Korean use: "주재료", "소스", "양념 & 허브", "조리 방법".

Set "isImaginary" to true if the image contains NO written recipe text and you are creating the recipe yourself based on what the food looks like. Set to false ONLY if there is actual written recipe text visible in the image.
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
- If multiple images are provided and overlap in content, treat them as one continuous recipe — do NOT duplicate or average out quantities.
- Always use the FIRST complete mention of each ingredient's quantity. Do not guess or adjust amounts.
- Include ALL ingredients mentioned including toppings, garnishes, and serving suggestions.
CASE 2: If the image only shows a food photo without a written recipe, create a realistic recipe for what you see.
{RECIPE_PROMPT_JSON}"""
            parts = [prompt] + [{"inline_data": img} for img in image_parts]
            response = model.generate_content(parts)
            text = response.text.strip().replace('```json', '').replace('```', '').strip()
            recipe = json.loads(text)
            source = "image"

            # Double-check if recipe is imaginary with a separate simple question
            check_parts = ["Does this image contain actual written recipe text — meaning a real ingredients list with quantities AND/OR numbered cooking steps? Captions, hashtags, usernames, titles, or short descriptions do NOT count. Answer only YES or NO."] + [{"inline_data": img} for img in image_parts]
            check_response = model.generate_content(check_parts)
            has_text = "YES" in check_response.text.upper()
            if not has_text:
                recipe['isImaginary'] = True

        # --- SAVE TO NOTION ---
        notion_token = os.environ.get("NOTION_TOKEN")
        notion_database_id = os.environ.get("NOTION_DATABASE_ID")
        if not notion_token or not notion_database_id:
            return jsonify({'error': 'Notion not configured'}), 500

        notion = Client(auth=notion_token)
        is_imaginary = recipe.get("isImaginary", False)
        ingredients = recipe.get("ingredients", {})
        steps = recipe.get("steps", [])
        # Section headers always in English
        h_ingredients = "🥘 Ingredients"
        h_main = "Main Ingredients"
        h_sauce = "Sauce"
        h_spices = "Spices & Herbs"
        h_steps = "👨‍🍳 Steps"
        h_history = "📸 Seora's History"
        history_note = "Drop your photos here when you make this recipe! 🍽️"

        if isinstance(ingredients, list):
            main_list, sauce_list, spice_list = ingredients, [], []
        else:
            main_list = ingredients.get("main", [])
            sauce_list = ingredients.get("sauce", [])
            spice_list = ingredients.get("spicesAndHerbs", [])

        def parse_ingredient_rich_text(text):
            # Split "1 cup flour (120g)" into original measure and grey conversion
            match = re.search(r'^(.*?)(\s*\(\s*[\d.]+\s*g\s*\))\s*$', text)
            if match:
                main = match.group(1).strip()
                converted = "    " + match.group(2).strip()
                return [
                    {"type": "text", "text": {"content": main}},
                    {"type": "text", "text": {"content": converted}, "annotations": {"color": "gray"}}
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


        children += [
            {"object": "block", "type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": f"⏱ Cook Time: {recipe.get('cookTime') or 'N/A'}     ⭐ My Rating:  ☆ ☆ ☆ ☆ ☆"}}], "icon": {"emoji": "🍳"}, "color": "orange_background"}},

            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": h_ingredients}}]}},
        ]
        if main_list:
            children.append(heading3(h_main))
            children.extend([checkbox(i, rich=True) for i in main_list])
        if sauce_list:
            children.append(heading3(h_sauce))
            children.extend([checkbox(i, rich=True) for i in sauce_list])
        if spice_list:
            children.append(heading3(h_spices))
            children.extend([checkbox(i, rich=True) for i in spice_list])
        children += [
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": h_steps}}]}},
            *build_steps(steps),
        ]

        # Add Seora's History section
        children += [
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": h_history}, "annotations": {"color": "pink"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": history_note}, "annotations": {"color": "gray", "italic": True}}]
                }
            },
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

        notion_response = notion.pages.create(
            parent={"database_id": notion_database_id},
            icon={"emoji": "🤖" if is_imaginary else "🍽️"},
            properties={"title": {"title": [{"text": {"content": title}}]}},
            children=children
        )

        all_ingredients = main_list + sauce_list + spice_list
        flat_steps = [s if isinstance(s, str) else s.get("instruction", "") for s in steps]
        notion_url = notion_response.get('url', '')
        return jsonify({'success': True, 'isImaginary': is_imaginary, 'source': source, 'notion_url': notion_url, 'recipe': {**recipe, 'ingredients': all_ingredients, 'steps': flat_steps}})

    except json.JSONDecodeError:
        return jsonify({'error': 'Could not parse recipe. Try again or rephrase the text.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
