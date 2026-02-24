# Recipe Saver 🍳

Upload screenshots of recipes from Instagram Reels → AI extracts the recipe → Saves to Notion automatically.

## Deploy on Render.com (Free)

1. Create a free account at render.com
2. Click "New" → "Web Service"
3. Connect your GitHub and upload this folder, OR use "Deploy from existing repo"
4. Set these environment variables:
   - GEMINI_API_KEY = your Gemini API key
   - NOTION_TOKEN = your Notion token (starts with ntn_)
   - NOTION_DATABASE_ID = your Notion database ID

## Notion Database Setup
Your Notion database needs these exact column names:
- Name (title type - created by default)
- Ingredients (text type)
- Steps (text type)
- Cook Time (text type)
- Servings (text type)
