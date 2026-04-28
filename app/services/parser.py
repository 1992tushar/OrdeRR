from google import genai
from dotenv import load_dotenv
import os
import json

load_dotenv()

# Configure Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def parse_order(customer_phone: str, message: str) -> dict:
    """
    Parse a WhatsApp order message using deep chicken industry knowledge.
    No hardcoded product list — AI understands order naturally.
    Works for ANY chicken processing plant automatically.
    """

    prompt = f"""
You are a 20-year veteran of the Indian chicken and poultry wholesale trade.
You have worked with chicken processing plants, hotels, restaurants and caterers across India.
You deeply understand how buyers communicate orders in Hindi, English and Hinglish.
You know every cut, every term, every abbreviation used in this industry.

YOUR DEEP INDUSTRY KNOWLEDGE:
- Boiler / Broiler = Whole broiler chicken
- Tandoor cut / Tandoori cut = Large skin-on pieces for tandoor cooking
- CC = Curry Cut = Standard curry pieces
- Breast = Chicken breast
- Boneless / BL = Boneless chicken pieces
- DS / Drumstick = Lower leg portion
- Kheema / Seekh = Minced chicken
- Liver / Kaleji = Chicken liver
- Gizzard / Petha = Chicken gizzard
- WS = With Skin
- WOS / NS = Without Skin
- Nos / pcs = piece count
- Kg = weight
- 1100gm chi = whole chicken of 1100 grams weight
- Lollipop = Drumstick trimmed into lollipop shape
- Malai / Reshmi = Boneless breast cubes
- Full bird / Whole bird = Whole dressed chicken
- Half bird = Half chicken
- Spring chicken = Small young chicken 400-600gm
- Biryani cut = Larger curry cut pieces for biryani
- Changezi cut = Very large pieces
- Afghani cut = Large skin-on pieces
- Kal / Kal subah = Tomorrow morning
- Aaj / Aaj shaam = Today evening
- Subah = Morning
- Shaam = Evening
- Baje = O'clock (6 baje = 6 o'clock)

YOUR ONLY JOB:
1. Understand exactly what the customer is ordering using your industry knowledge
2. Extract each item with correct product name, quantity and unit
3. Extract delivery date and time if mentioned
4. Use standard English product names in output
5. Only mark unclear if message is genuinely unreadable or completely irrelevant to chicken

IMPORTANT RULES:
- NEVER reject an order just because it seems unusual
- A chicken plant can process ANY chicken product
- Your job is to understand and extract — not to validate
- If customer says "Boilers 5" → product is "Whole Broiler Chicken", quantity 5, unit pcs
- If customer says "Tandoor cut 10 nos" → product is "Tandoor Cut Chicken", quantity 10, unit pcs
- If customer says "1100gm chi 20" → product is "Whole Chicken 1100gm", quantity 20, unit pcs
- Always use clear standard English product names
- For units follow this STRICT priority:
  1. If customer EXPLICITLY says kg/pcs/nos/pieces → use that ALWAYS
  2. If customer gives NO unit → use these industry defaults:
     * Whole Broiler / Whole Chicken / Full Bird = pcs
     * Curry Cut / Biryani Cut / Tandoor Cut = kg
     * Drumsticks = kg
     * Breast Boneless = kg
     * Kheema = kg
     * Liver = kg
     * Gizzard = kg
     * Lollipop = pcs
     * Spring Chicken = pcs
     * Any other cut with no unit = kg

CUSTOMER PHONE: {customer_phone}
ORDER MESSAGE: {message}

Respond ONLY with this exact JSON, no other text, no markdown:
{{
    "customer_phone": "{customer_phone}",
    "items": [
        {{
            "product": "clear standard English product name",
            "quantity": 0,
            "unit": "kg or pcs",
            "notes": "any special instructions or weight specifications"
        }}
    ],
    "delivery_date": "today or tomorrow or YYYY-MM-DD or null",
    "delivery_time": "HH:MM or null",
    "is_unclear": false,
    "unclear_reason": "only fill if message is completely unreadable or irrelevant to chicken"
}}
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        raw = response.text.strip()

        # Clean response if Gemini adds markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        parsed = json.loads(raw)
        return parsed

    except Exception as e:
        return {
            "customer_phone": customer_phone,
            "items": [],
            "delivery_date": None,
            "delivery_time": None,
            "is_unclear": True,
            "unclear_reason": f"AI parsing failed: {str(e)}"
        }